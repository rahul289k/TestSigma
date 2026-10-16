"""Runs the stages in order. Each stage reads artifacts from the run directory and writes its own;
Neo4j is (re)built from artifacts at the points where retrieval needs it and at the end."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Callable

import yaml
from rich.console import Console

from .code.graph import run_code_graph
from .config import CONFIG_DIR, Config, Secrets, load_config, load_secrets
from .crawl.explorer import run_crawl
from .crawl.label import label_states
from .crawl.probe import run_probe
from .graph.db import Graph
from .graph.loader import load_base, load_links
from .ingest.extract import run_ingest
from .link.dom_code import run_dom_code
from .link.flows import run_flows
from .link.gaps import run_gaps
from .link.req import run_req_links
from .llm.client import LLM
from .pin import run_pin
from .runstore import RunStore

console = Console()

STAGES = ["pin", "ingest", "code", "crawl", "label", "load", "domcode", "judge", "probe", "probe_post",
          "gaps", "flows", "review", "final_load"]


def load_overrides() -> dict:
    p = CONFIG_DIR / "overrides.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def apply_overrides(run: RunStore) -> int:
    """Human decisions (config/overrides.yaml) replace machine verdicts, with method 'human'."""
    ov = load_overrides()
    n = 0
    elem_ov = ov.get("elements", {}) or {}
    if elem_ov and run.exists("link/dom_code.jsonl"):
        from .link.uidata import load_ui
        rows = run.read_jsonl("link/dom_code.jsonl")
        elements = load_ui(run).elements
        seen = set()
        for r in rows:
            if r["eid"] in elem_ov:
                r.update(method="human", components=[{"component": elem_ov[r["eid"]]["component"], "confidence": 1.0}],
                         evidence="human decision: " + elem_ov[r["eid"]].get("note", ""))
                seen.add(r["eid"])
                n += 1
        for eid in sorted(set(elem_ov) - seen):     # elements the linker could not map at all
            el = elements.get(eid)
            if el is None:
                continue
            rows.append({"eid": eid, "route": el["route"], "name": el["name"], "context": el["context"], "method": "human",
                         "keys": [], "matched_field": "human", "match_kind": "human",
                         "components": [{"component": elem_ov[eid]["component"], "confidence": 1.0}],
                         "evidence": "human decision: " + elem_ov[eid].get("note", ""), "candidates_total": 1,
                         "candidates_in_route": 1})
            n += 1
        rows.sort(key=lambda r: r["eid"])
        run.write_jsonl("link/dom_code.jsonl", rows)
    req_ov = ov.get("requirements", {}) or {}
    if req_ov and run.exists("link/req_links.jsonl"):
        rows = run.read_jsonl("link/req_links.jsonl")
        for r in rows:
            o = req_ov.get(r["req_id"])
            if not o:
                continue
            if o["status"] == "covered" and o.get("elements"):
                r.update(ui_verdict="COVERED", ui_elements=sorted(o["elements"]), ui_method="human", ui_confidence=1.0,
                         ui_rationale="human decision: " + o.get("note", ""))
            elif o["status"] == "not_covered":
                r.update(ui_verdict="NOT_FOUND", ui_elements=[], ui_method="human", ui_confidence=1.0,
                         ui_rationale="human decision: " + o.get("note", ""))
            n += 1
        run.write_jsonl("link/req_links.jsonl", rows)
    return n


def merge_review(run: RunStore) -> dict:
    items = []
    for part in ("link/review_items_judge.jsonl", "link/review_items_gaps.jsonl"):
        if run.exists(part):
            items.extend(run.read_jsonl(part))
    decided = set((load_overrides().get("requirements") or {}).keys())
    out = []
    for it in items:
        if it.get("req_id") in decided:
            continue
        it["id"] = "RV-" + hashlib.sha1(json.dumps([it["trigger"], it.get("req_id"), it["question"]]).encode()).hexdigest()[:10]
        out.append(it)
    out.sort(key=lambda x: x["id"])
    run.write_jsonl("link/review_items.jsonl", out)
    by: dict[str, int] = {}
    for it in out:
        by[it["trigger"]] = by.get(it["trigger"], 0) + 1
    return {"open_items": len(out), "by_trigger": by}


def probe_targets(cfg: Config, run: RunStore) -> list[dict]:
    reqs = {r["id"]: r for r in run.read_jsonl("ingest/requirements.jsonl")}
    links = run.read_jsonl("link/req_links.jsonl")
    code = run.read_json("code/code_graph.json")
    missing = [lk for lk in links if lk["testability"] == "UI_OBSERVABLE" and lk["ui_verdict"] == "NOT_FOUND"]
    missing.sort(key=lambda lk: (0 if lk["code_components"] else 1, lk["req_id"]))
    targets = []
    for lk in missing[: cfg.probe.max_requirements]:
        start = cfg.app.seed_routes[0]
        for seed in cfg.app.seed_routes:
            reach = next((set(r["reachable"]) for r in code["routes"] if r["path"] == seed), set())
            if reach & set(lk["code_components"]):
                start = seed
                break
        targets.append({"req": reqs[lk["req_id"]], "start_route": start})
    return targets


class Pipeline:
    def __init__(self, profile: str, offline: bool, run: RunStore | None = None):
        self.cfg = load_config(profile)
        self.secrets: Secrets = load_secrets(require_llm=not offline)
        self.run = run or RunStore.create(self.cfg.profile)
        self.llm = LLM(self.cfg.llm, self.secrets.gemini_api_key or None, self.run, offline=offline)
        self.graph: Graph | None = None
        self.stats: dict = {}

    def g(self) -> Graph:
        if self.graph is None:
            self.graph = Graph(self.secrets)
        return self.graph

    def stage(self, name: str) -> dict:
        cfg, run, llm = self.cfg, self.run, self.llm
        fns: dict[str, Callable[[], dict]] = {
            "pin": lambda: {k: v for k, v in run_pin(cfg, run).items() if k in ("app", "code", "docs")},
            "ingest": lambda: run_ingest(cfg, run, llm),
            "code": lambda: {k: v for k, v in run_code_graph(cfg, run).items() if k != "extractor_errors"},
            "crawl": lambda: run_crawl(cfg, run),
            "label": self._label_crawl,
            "load": lambda: load_base(self.g(), run),
            "domcode": self._domcode,
            "judge": lambda: {**run_req_links(cfg, run, llm, self.g()), "overrides_applied": apply_overrides(run)},
            "probe": self._probe,
            "probe_post": self._probe_post,
            "gaps": lambda: run_gaps(run),
            "flows": lambda: run_flows(cfg, run, llm),
            "review": lambda: merge_review(run),
            "final_load": self._final_load,
        }
        t = time.time()
        console.print(f"[bold cyan]▶ {name}[/]")
        out = fns[name]()
        out["seconds"] = round(time.time() - t, 1)
        self.stats[name] = out
        console.print(f"  {json.dumps(out)[:400]}")
        run.update_manifest(stage_stats=self.stats)
        return out

    def _domcode(self) -> dict:
        from .link.dom_code import load_priors
        # Record the confidences this run links with: calibration changes between runs by design, and an exact
        # replay of this run needs the same values.
        self.run.update_manifest(link_priors=load_priors(self.cfg))
        return {**run_dom_code(self.cfg, self.run), "overrides_applied": apply_overrides(self.run)}

    def _label_crawl(self) -> dict:
        states = self.run.read_jsonl("crawl/states.jsonl")
        labels = label_states(states, self.llm, self.cfg.llm.label_states_per_call, task="label:crawl")
        self.run.write_json("crawl/labels.json", labels)
        return {"labeled": len(labels)}

    def _probe(self) -> dict:
        targets = probe_targets(self.cfg, self.run) if self.cfg.probe.enabled else []
        self.run.write_json("probe/targets.json", [{"req_id": t["req"]["id"], "start_route": t["start_route"]} for t in targets])
        if not targets:
            for f in ("probe/states.jsonl", "probe/transitions.jsonl", "probe/episodes.jsonl"):
                self.run.write_jsonl(f, [])
            return {"episodes": 0, "skipped": None if self.cfg.probe.enabled else "probe disabled"}
        return run_probe(self.cfg, self.run, self.llm, targets)

    def _probe_post(self) -> dict:
        new_states = self.run.read_jsonl("probe/states.jsonl")
        labels = label_states(new_states, self.llm, self.cfg.llm.label_states_per_call, task="label:probe") if new_states else {}
        self.run.write_json("probe/labels.json", labels)
        targets = {t["req_id"] for t in self.run.read_json("probe/targets.json")} if self.run.exists("probe/targets.json") else set()
        if not targets:
            return {"rejudged": 0}
        load_base(self.g(), self.run)
        dc = run_dom_code(self.cfg, self.run)
        rj = run_req_links(self.cfg, self.run, self.llm, self.g(), only=targets, task="rejudge")
        apply_overrides(self.run)
        return {"probe_states_labeled": len(labels), "domcode_mapped": dc["mapped"], "rejudged": len(targets),
                "ui_verdicts": rj["ui_verdicts"]}

    def _final_load(self) -> dict:
        base = load_base(self.g(), self.run)
        links = load_links(self.g(), self.run)
        hashes = {s: self.run.stage_hash(s) for s in ("ingest", "code", "crawl", "probe", "link")}
        self.run.update_manifest(stage_hashes=hashes)
        return {**base, **links}

    def run_all(self, until: str | None = None, start: str | None = None) -> dict:
        names = STAGES[STAGES.index(start) if start else 0: STAGES.index(until) + 1 if until else None]
        try:
            for name in names:
                self.stage(name)
        finally:
            if self.graph:
                self.graph.close()
        live = sum(1 for r in self.run.read_jsonl("llm_calls.jsonl") if not r["cached"]) if self.run.exists("llm_calls.jsonl") else 0
        cached = sum(1 for r in self.run.read_jsonl("llm_calls.jsonl") if r["cached"]) if self.run.exists("llm_calls.jsonl") else 0
        self.run.update_manifest(llm_usage={"live_calls": live, "cached_calls": cached})
        return self.stats
