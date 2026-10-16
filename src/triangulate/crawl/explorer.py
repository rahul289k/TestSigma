"""Stage 2: the systematic explorer.

A deterministic frontier search over UI states. No LLM decides anything here.

- A state is identified by its fingerprint (route + structural elements, data masked).
- From each new state we only try elements that are NEW relative to its parent (everything on a
  seed's root). Elements present in the parent were already exercised there.
- Each element is exercised once per route.
- We keep acting in the current state while it has untried actions (cheap), undo overlays with
  Escape when possible, and otherwise open a fresh context and replay the recorded path (slow
  but reproducible: Hoppscotch keeps tabs in localStorage, so a clean context is a clean slate).
- Actions are ranked by novelty + BM25 relevance to the documented requirements, so the budget
  is spent near documented features first.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from ..config import Config
from ..pin import read_live_version
from ..runstore import RunStore
from .browser import ActionFailed, Session
from .model import is_actionable, norm_name

_TOKEN = re.compile(r"[a-z][a-z0-9]+")


def tokens(s: str) -> list[str]:
    return _TOKEN.findall((s or "").lower())


@dataclass
class CState:
    fp: str
    route: str
    seed: str
    depth: int
    order: int
    path: list[dict]
    parent: str | None
    obs: dict
    pending: list[dict] = field(default_factory=list)
    screenshot: str = ""
    source: str = "crawl"


class Relevance:
    def __init__(self, requirements: list[dict]):
        docs = [tokens(" ".join(r.get("ui_cues", [])) + " " + r.get("statement", "")) for r in requirements]
        docs = [d for d in docs if d]
        self.bm25 = BM25Okapi(docs) if docs else None

    def score(self, text: str) -> float:
        if not self.bm25:
            return 0.0
        q = tokens(text)
        if not q:
            return 0.0
        s = max(self.bm25.get_scores(q))
        return float(s / (s + 1.0)) if s > 0 else 0.0


class Explorer:
    def __init__(self, cfg: Config, run: RunStore, requirements: list[dict]):
        self.cfg = cfg
        self.ccfg = cfg.crawl
        self.run = run
        self.rel = Relevance([r for r in requirements if r.get("testability") == "UI_OBSERVABLE"])
        self.states: dict[str, CState] = {}
        self.transitions: list[dict] = []
        self.failed: list[dict] = []
        self.tried: set[tuple[str, str]] = set()
        self.tried_names: set[str] = set()
        self.replay_mismatches = 0
        self.order = 0
        self.session: Session | None = None

    # ---- registration ------------------------------------------------------------------
    def _register(self, obs: dict, seed: str, depth: int, path: list[dict], parent: CState | None,
                  source: str = "crawl") -> CState:
        st = CState(fp=obs["fp"], route=obs["route"], seed=seed, depth=depth, order=self.order, path=path,
                    parent=parent.fp if parent else None, obs=obs, source=source)
        self.order += 1
        shot = f"crawl/screens/{st.fp}.jpg"
        assert self.session is not None
        self.session.screenshot(self.run.path(shot))
        st.screenshot = shot
        self.states[st.fp] = st
        if depth < self.ccfg.max_depth and st.route == seed:
            st.pending = self._candidate_actions(st, parent)
        return st

    def _candidate_actions(self, st: CState, parent: CState | None) -> list[dict]:
        parent_sigs = {e["sig"] for e in parent.obs["elements"]} if parent else set()
        cands = []
        for e in st.obs["elements"]:
            if e["sig"] in parent_sigs or (st.route, e["sig"]) in self.tried:
                continue
            kind, value = is_actionable(e, self.ccfg.deny_names, self.ccfg.fixtures)
            if kind is None:
                continue
            nm = norm_name(e["name"])
            score = self.rel.score(e["name"] + " " + e.get("tooltip", "")) + (0.5 if nm not in self.tried_names else 0.0)
            score += 0.2 if e["role"] in ("tab", "menuitem") else 0.0
            cands.append({"kind": kind, "value": value, "sig": e["sig"], "occ": e["occ"], "eid": e["eid"],
                          "name": e["name"], "role": e["role"], "score": round(score, 4)})
        cands.sort(key=lambda a: (-a["score"], a["sig"], a["occ"]))
        seen, out = set(), []
        for a in cands:
            if a["sig"] in seen:
                continue
            seen.add(a["sig"])
            out.append(a)
        return out[: self.ccfg.max_actions_per_state]

    # ---- navigation --------------------------------------------------------------------
    def _replay(self, target: CState) -> dict | None:
        s = self.session
        assert s is not None
        try:
            s.reset(target.seed)
        except ActionFailed as e:
            self.failed.append({"state": target.fp, "action": None, "error": f"replay reset: {e}"})
            return None
        for step in target.path:
            obs = s.observe()
            el = s.find(obs, step["sig"], step["occ"])
            if el is None:
                return None
            try:
                s.act(el, step["kind"], step.get("value"))
            except ActionFailed:
                return None
        return s.observe()

    def _goto(self, target: CState, current_fp: str | None) -> dict | None:
        s = self.session
        assert s is not None
        if current_fp and current_fp in self.states:
            cur = self.states[current_fp]
            overlay = any(("dialog" in e["context"] or "popover" in e["context"]) for e in cur.obs["elements"])
            if overlay:
                s.press("Escape")
                obs = s.observe()
                self._record(cur, {"kind": "press", "value": "Escape", "sig": "", "occ": 0, "eid": None,
                                   "name": "Escape", "role": "key"}, obs, [], register=False)
                if obs["fp"] == target.fp:
                    return obs
        obs = self._replay(target)
        if obs is None or obs["fp"] != target.fp:
            self.replay_mismatches += 1
            return None
        return obs

    def _record(self, frm: CState, action: dict, obs: dict, network: list[str], register: bool = True,
                source: str = "crawl") -> CState | None:
        new = None
        if obs["fp"] not in self.states and register:
            new = self._register(obs, frm.seed, frm.depth + 1, frm.path + [action], frm, source=source)
        if obs["fp"] in self.states or new:
            self.transitions.append({"order": len(self.transitions), "from": frm.fp, "to": obs["fp"],
                                     "action": {k: action[k] for k in ("kind", "value", "sig", "occ", "eid", "name", "role")},
                                     "network": network, "source": source})
        return new

    # ---- main loop ---------------------------------------------------------------------
    def _pick(self, seed: str, current_fp: str | None) -> CState | None:
        cur = self.states.get(current_fp) if current_fp else None
        if cur and cur.seed == seed and cur.pending:
            return cur
        cands = [s for s in self.states.values() if s.seed == seed and s.pending]
        return min(cands, key=lambda s: (s.depth, s.order)) if cands else None

    def run_crawl(self) -> dict:
        t0 = time.time()
        version_start = self.run.manifest["app"]["version"]
        deadline = t0 + self.ccfg.max_minutes * 60
        self.session = Session(self.cfg.app.url, self.ccfg)
        seeds = self.cfg.app.seed_routes
        per_seed: dict[str, dict] = {}
        try:
            for i, seed in enumerate(seeds):
                seed_start = time.time()
                seed_deadline = seed_start + (deadline - seed_start) / (len(seeds) - i)
                st0 = len(self.states)
                self.session.reset(seed)
                obs = self.session.observe()
                if obs["fp"] in self.states:
                    # Already reached from another seed (e.g. via a nav link): adopt it as this seed's root.
                    root = self.states[obs["fp"]]
                    if root.seed != seed and not root.pending:
                        root.seed, root.depth, root.path, root.parent = seed, 0, [], None
                        root.pending = self._candidate_actions(root, None)
                else:
                    root = self._register(obs, seed, 0, [], None)
                current = root.fp
                actions = 0
                # Why this seed stopped. Only "explored" and the state caps are deterministic; "time" means the
                # run was cut by wall-clock and is not comparable with another run (the eval flags it).
                while True:
                    if time.time() >= seed_deadline:
                        stopped = "time"
                        break
                    if len(self.states) >= self.ccfg.max_states:
                        stopped = "max_states"
                        break
                    if len([s for s in self.states.values() if s.seed == seed]) >= self.ccfg.max_states_per_route:
                        stopped = "route_cap"
                        break
                    target = self._pick(seed, current)
                    if target is None:
                        stopped = "explored"
                        break
                    if current != target.fp:
                        obs = self._goto(target, current)
                        if obs is None:
                            target.pending.clear()
                            current = None
                            continue
                        current = target.fp
                    else:
                        obs = self.session.observe()
                        if obs["fp"] != target.fp:
                            current = obs["fp"] if obs["fp"] in self.states else None
                            continue
                    action = target.pending.pop(0)
                    self.tried.add((target.route, action["sig"]))
                    self.tried_names.add(norm_name(action["name"]))
                    el = self.session.find(obs, action["sig"], action["occ"])
                    if el is None:
                        self.failed.append({"state": target.fp, "action": action, "error": "element not found"})
                        continue
                    lang_before = self.session.lang()
                    try:
                        network = self.session.act(el, action["kind"], action.get("value"))
                    except ActionFailed as e:
                        self.failed.append({"state": target.fp, "action": action, "error": str(e)})
                        current = None
                        continue
                    if self.session.lang() != lang_before:
                        # A preference change (UI language) would invalidate every later observation; never keep it.
                        self.failed.append({"state": target.fp, "action": action, "error": "denied: changes the UI language"})
                        current = None
                        continue
                    actions += 1
                    after = self.session.observe()
                    self._record(target, action, after, network)
                    current = after["fp"] if after["fp"] in self.states else None
                per_seed[seed] = {"states": len([s for s in self.states.values() if s.seed == seed]),
                                  "new_states": len(self.states) - st0, "actions": actions,
                                  "seconds": round(time.time() - seed_start, 1), "stopped": stopped}
        finally:
            stats_session = {"resets": self.session.resets, "blocked_navigations": self.session.blocked_navigations,
                             "popups_closed": self.session.popups_closed, "load_retries": self.session.load_retries}
            self.session.close()
        version_end = read_live_version(self.cfg.app.url, self.cfg.app.version_pattern)
        if version_end != version_start:
            raise RuntimeError(f"App version changed during the crawl ({version_start} -> {version_end}); run is invalid")
        self._write()
        return {"states": len(self.states), "transitions": len(self.transitions), "failed_actions": len(self.failed),
                "replay_mismatches": self.replay_mismatches, "minutes": round((time.time() - t0) / 60, 1),
                "per_seed": per_seed, **stats_session, "app_version": version_end}

    def _write(self) -> None:
        rows = []
        for st in sorted(self.states.values(), key=lambda s: s.order):
            rows.append({"fp": st.fp, "route": st.route, "seed": st.seed, "depth": st.depth, "order": st.order,
                         "path": st.path, "parent": st.parent, "screenshot": st.screenshot, "url": st.obs["url"],
                         "title": st.obs["title"], "source": st.source,
                         "elements": [{k: v for k, v in e.items() if k != "tri"} for e in st.obs["elements"]]})
        self.run.write_jsonl("crawl/states.jsonl", rows)
        self.run.write_jsonl("crawl/transitions.jsonl", self.transitions)
        self.run.write_jsonl("crawl/failed_actions.jsonl", self.failed)


def run_crawl(cfg: Config, run: RunStore) -> dict:
    reqs = run.read_jsonl("ingest/requirements.jsonl") if run.exists("ingest/requirements.jsonl") else []
    stats = Explorer(cfg, run, reqs).run_crawl()
    run.write_json("crawl/meta.json", stats)
    return stats
