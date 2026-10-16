"""Stage 4a/4b: DOM element -> i18n key -> Vue component. Deterministic.

Hoppscotch renders every UI string through i18n: t('action.send') -> "Send". So the visible
label of an element points to a key, and the key points to the components that call t() with
it. When several components use the key, we keep only those that can render on the element's
route (the route's render tree), then break remaining ties by which candidate also uses the keys
of the element's nearest on-screen neighbours. No LLM is involved.
"""

from __future__ import annotations

import json
import math
import re

from ..config import ROOT, Config
from ..runstore import RunStore
from .uidata import UIData, load_ui

_SHORTCUT = re.compile(r"[⌘⌥⇧↩⏎⌃↵]")
# Hoppscotch's button component sets aria-label="button"; such placeholders carry no meaning.
PLACEHOLDER_LABELS = {"button", "link", "icon"}


def ntext(s: str) -> str:
    s = _SHORTCUT.sub(" ", (s or "").lower())
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("…", "...")
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(".:").strip()


class KeyIndex:
    def __init__(self, i18n: dict[str, str]):
        self.exact: dict[str, list[str]] = {}
        self.patterns: list[tuple[re.Pattern, str]] = []
        for key, text in sorted(i18n.items()):
            t = ntext(text)
            if not t:
                continue
            if "{" in t:
                rx = re.escape(t)
                rx = re.sub(r"\\\{[^}]*\\\}", r"(.+?)", rx)
                if re.sub(r"\(\.\+\?\)", "", rx).strip():
                    self.patterns.append((re.compile(f"^{rx}$"), key))
            else:
                self.exact.setdefault(t, []).append(key)

    def lookup(self, text: str) -> tuple[list[str], str]:
        """Return (keys, match kind) for a visible string."""
        t = ntext(text)
        if not t:
            return [], ""
        if t in self.exact:
            return self.exact[t], "exact"
        hits = [k for rx, k in self.patterns if rx.match(t)]
        if hits:
            return sorted(hits), "placeholder"
        words = t.split()
        # Trim shortcut hints or prefixes the UI adds around a translated label (at most 2 words each side).
        for cut in range(1, min(3, len(words))):
            for cand in (" ".join(words[:-cut]), " ".join(words[cut:])):
                if len(cand) >= 3 and cand in self.exact:
                    return self.exact[cand], "trimmed"
        # Menu items render "label + description": take the longest leading run of words that is a full string.
        for n in range(len(words) - 1, 1, -1):
            cand = " ".join(words[:n])
            if cand in self.exact:
                return self.exact[cand], "prefix"
        return [], ""


def load_priors(cfg: Config) -> dict[str, float]:
    priors = dict(cfg.link.method_priors)
    calib = ROOT / "eval" / "calibration.json"
    if calib.exists():
        priors.update(json.loads(calib.read_text()).get("dom_code", {}))
    return priors


def _neighbours(state: dict, eid: str, k: int = 12) -> list[dict]:
    """The k elements nearest on screen within the same context (same dialog/popover/landmark)."""
    els = state["elements"]
    me = next((e for e in els if e["eid"] == eid), None)
    if me is None:
        return []
    cx, cy = me["bbox"][0] + me["bbox"][2] / 2, me["bbox"][1] + me["bbox"][3] / 2
    others = [e for e in els if e["eid"] != eid and e["context"] == me["context"]]
    others.sort(key=lambda e: (math.hypot(e["bbox"][0] + e["bbox"][2] / 2 - cx, e["bbox"][1] + e["bbox"][3] / 2 - cy), e["eid"]))
    return others[:k]


def link_elements(ui: UIData, code: dict, priors: dict[str, float]) -> tuple[list[dict], list[dict]]:
    idx = KeyIndex(code["i18n"])
    key_users: dict[str, set[str]] = {}
    pattern_users: list[tuple[str, str]] = []
    comp_keys: dict[str, set[str]] = {}
    literal_only: dict[str, set[str]] = {}
    testid_users: dict[str, set[str]] = {}
    for c in code["components"]:
        comp_keys[c["id"]] = set(c["keys"])
        literal_only[c["id"]] = set(c.get("literal_keys", []))
        for k in c["keys"]:
            key_users.setdefault(k, set()).add(c["id"])
        for p in c["key_patterns"]:
            pattern_users.append((p, c["id"]))
        for t in c["testids"]:
            testid_users.setdefault(t, set()).add(c["id"])
    reachable = {r["path"]: set(r["reachable"]) for r in code["routes"]}

    def keys_for(el: dict) -> tuple[list[str], str, str]:
        for field in ("name", "tooltip", "placeholder", "aria_label", "title", "text"):
            if (el.get(field) or "").strip().lower() in PLACEHOLDER_LABELS:
                continue
            keys, kind = idx.lookup(el.get(field, ""))
            if keys:
                return keys, field, kind
        return [], "", ""

    def candidates(el: dict) -> tuple[set[str], list[str], str, str]:
        keys, field, kind = keys_for(el)
        if not keys:
            return set(), keys, field, kind
        cands = set().union(*(key_users.get(k, set()) for k in keys))
        if not cands:
            # Dynamic t(`prefix.${x}`) calls only count when no component uses the key literally:
            # a broad prefix like "environment." would otherwise claim every environment string.
            cands = {c for k in keys for p, c in pattern_users if k.startswith(p)}
        return cands, keys, field, kind

    # Pass 1: components that are uniquely identified by at least one element of a state are
    # "anchored" (known to be on screen) in that state.
    anchored: dict[str, set[str]] = {}
    for fp, st in ui.states.items():
        route_set = reachable.get(st["route"], set())
        a: set[str] = set()
        for e in st["elements"]:
            c, _, _, _ = candidates(e)
            inr = c & route_set
            if len(inr) == 1:
                a |= inr
        anchored[fp] = a

    def score(el_eid: str, fp: str, cands_in_route: list[str], keys: list[str]) -> dict[str, float]:
        state = ui.states[fp]
        nkeys: set[str] = set()
        for n in _neighbours(state, el_eid):
            nk, _, _ = keys_for(n)
            nkeys |= set(nk)
        out = {}
        for c in cands_in_route:
            ck = comp_keys.get(c, set())
            sim = len(ck & nkeys) / math.sqrt(len(ck)) if ck else 0.0
            # A component that calls t() with the key renders the text; one that only holds it as data may not.
            renders_text = 0.5 if any(k in ck and k not in literal_only.get(c, set()) for k in keys) else 0.0
            out[c] = round(sim + renders_text + (1.0 if c in anchored.get(fp, set()) else 0.0), 6)
        return out

    linked, unmapped = [], []
    for eid in sorted(ui.elements):
        el = ui.elements[eid]
        route_set = reachable.get(el["route"], set())
        if el.get("testid") and el["testid"] in testid_users:
            comps = sorted(testid_users[el["testid"]])
            linked.append({"eid": eid, "route": el["route"], "name": el["name"], "context": el["context"], "method": "testid", "keys": [],
                           "matched_field": "testid", "match_kind": "exact",
                           "components": [{"component": c, "confidence": round(priors.get("testid", 0.97) / len(comps), 4)} for c in comps],
                           "evidence": f"data-testid '{el['testid']}'", "candidates_total": len(comps), "candidates_in_route": len(comps)})
            continue
        cands, keys, field, kind = candidates(el)
        if not keys:
            unmapped.append({"eid": eid, "route": el["route"], "name": el["name"], "role": el["role"],
                             "reason": "label matches no i18n string (user data, literal text or icon)"})
            continue
        if not cands:
            unmapped.append({"eid": eid, "route": el["route"], "name": el["name"], "role": el["role"],
                             "reason": f"i18n key(s) {keys[:3]} are not used by any component (defined in TS or library)",
                             "keys": keys})
            continue
        in_route = sorted(cands & route_set)
        detail = ""
        if len(in_route) == 1:
            method, chosen = "i18n_route_unique", in_route
        elif len(in_route) > 1:
            # Aggregate the tie-break over every state the element appears in.
            totals: dict[str, float] = {c: 0.0 for c in in_route}
            for fp in el["states"]:
                for c, v in score(eid, fp, in_route, keys).items():
                    totals[c] += v
            best = max(totals.values())
            top = sorted(c for c, v in totals.items() if v == best)
            detail = "; tie-break scores " + ", ".join(f"{c.split('/')[-1]}={totals[c]:.2f}" for c in sorted(totals, key=lambda c: -totals[c])[:3])
            if len(top) == 1 and best > 0:
                method, chosen = "i18n_route_tiebreak", top
            else:
                method, chosen = "i18n_route_ambiguous", top
        else:
            chosen = sorted(cands)
            method = "i18n_global_unique" if len(chosen) == 1 else "i18n_global_ambiguous"
        base = priors[method]
        conf = round(base / len(chosen), 4) if method.endswith("ambiguous") else base
        ev = (f"{field} '{el.get(field, '')}' = i18n {', '.join(keys[:3])}; used by {len(cands)} component(s), "
              f"{len(in_route)} on route {el['route']}{detail}")
        linked.append({"eid": eid, "route": el["route"], "name": el["name"], "context": el["context"], "method": method, "keys": keys,
                       "matched_field": field, "match_kind": kind,
                       "components": [{"component": c, "confidence": conf} for c in chosen], "evidence": ev,
                       "candidates_total": len(cands), "candidates_in_route": len(in_route)})
    return linked, unmapped


def run_dom_code(cfg: Config, run: RunStore) -> dict:
    ui = load_ui(run)
    code = run.read_json("code/code_graph.json")
    linked, unmapped = link_elements(ui, code, load_priors(cfg))
    run.write_jsonl("link/dom_code.jsonl", linked)
    run.write_jsonl("link/dom_code_unmapped.jsonl", unmapped)
    by_method: dict[str, int] = {}
    for r in linked:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1
    total = len(linked) + len(unmapped)
    return {"elements": total, "mapped": len(linked), "unmapped": len(unmapped),
            "mapped_share": round(len(linked) / total, 3) if total else 0.0, "by_method": dict(sorted(by_method.items()))}
