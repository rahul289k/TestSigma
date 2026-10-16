"""Blast radius: from changed code to UI elements, screens, flows and requirements. Deterministic.

Risk of an item = its strongest evidence chain: change weight x link confidence x decay^hops.
We take the max over chains (not a noisy-OR): chains through the same component are correlated,
and the single strongest chain is something we can show a person.
"""

from __future__ import annotations

import hashlib
import json

from ..config import Config
from ..graph.db import Graph
from .plain import area_name, route_name


UNIQUE_METHODS = {"i18n_route_unique", "testid", "human"}


def band(risk: float, bands: dict[str, float]) -> str:
    return "High" if risk >= bands["high"] else "Medium" if risk >= bands["medium"] else "Low"


def _rid(*parts: str) -> str:
    return "RV-" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def compute_impact(cfg: Config, g: Graph, code: dict, mapped: dict, crawled_routes: set[str]) -> dict:
    rc = cfg.reason
    w = rc.change_weights
    el_risk: dict[str, dict] = {}
    comp_seed: dict[str, dict] = {}
    unmapped: list[dict] = []
    needs_human: list[dict] = []

    def bump(eid: str, risk: float, chain: list[str], kind: str, ambiguous: bool,
             comp: str | None = None, unique: bool = True) -> None:
        cur = el_risk.get(eid)
        if cur is None or risk > cur["risk"]:
            el_risk[eid] = {"risk": round(risk, 4), "chain": chain, "kind": kind, "ambiguous": ambiguous, "lost": False,
                            "comp": comp, "unique": unique}

    def seed_component(cid: str, weight: float, chain: list[str], kind: str) -> None:
        cur = comp_seed.get(cid)
        if cur is None or weight > cur["weight"]:
            comp_seed[cid] = {"weight": weight, "chain": chain, "kind": kind}

    comp_by_id = {c["id"]: c for c in code["components"]}
    drift_files = [c["file"] for c in mapped["changes"] if c["drift"]]
    if drift_files:
        needs_human.append({"id": _rid("drift", *drift_files), "trigger": "base_drift", "about": drift_files,
                            "question": "Some changed files were already different between the release we crawled and "
                                        "this PR's starting point. The risk shown for them may be out of date - please confirm."})

    # ---- seeds from components, TS symbols and i18n keys
    removed_keys: set[str] = set()        # removed from the locale file: gone everywhere
    changed_keys: set[str] = set()
    comp_removed: dict[str, set[str]] = {}  # a component stopped showing these keys: only its own elements
    deleted_components: set[str] = set()
    for ch in mapped["changes"]:
        if ch["type"] == "test":
            continue
        if ch["type"] == "component":
            cid = ch["previous"] or ch["file"]
            if "deleted" in ch["kinds"]:
                deleted_components.add(cid)
            if ch.get("keys_removed"):
                comp_removed.setdefault(cid, set()).update(ch["keys_removed"])
            kinds = [k for k in ch["kinds"] if k != "added"]
            if not kinds:
                continue
            weight = max(w.get("key_removed" if k == "deleted" else k, w["whole_file"]) for k in kinds)
            name = comp_by_id.get(cid, {}).get("name", cid)
            seed_component(cid, weight, [f"changed: {name} ({', '.join(kinds)})"], ",".join(kinds))
            if "template" in kinds or "deleted" in kinds:
                comp = comp_by_id.get(cid, {})
                for child in comp.get("renders", []) + comp.get("renders_dynamic", []):
                    seed_component(child, weight * rc.child_factor,
                                   [f"changed: {name} ({', '.join(kinds)})", f"which places {comp_by_id[child]['name']}"],
                                   "child-of-changed")
        elif ch["type"] == "ts" and "imports_or_comments_only" in ch["kinds"]:
            unmapped.append({"file": ch["file"], "why": f"{area_name(ch['file'])}: only imports or comments changed (no behaviour change)"})
        elif ch["type"] == "ts" and "added" not in ch["kinds"]:
            symbols = set(ch.get("symbols", []))
            whole = "whole_file" in ch["kinds"] or "deleted" in ch["kinds"]
            frontier = [(ch["previous"] or ch["file"], 0)]
            seen = {frontier[0][0]}
            reached = False
            while frontier:
                path, hop = frontier.pop(0)
                if hop >= rc.max_import_hops:
                    continue
                for row in g.run("MATCH (f:File)-[i:IMPORTS]->(:File {path: $p}) RETURN f.path AS importer, i.names AS names, "
                                 "i.namespace AS ns, i.default AS d ORDER BY importer", p=path):
                    if hop == 0 and not whole and symbols and row["names"] and not row["ns"] and not row["d"] \
                            and not (set(row["names"]) & symbols):
                        continue
                    a = row["importer"]
                    if a in seen:
                        continue
                    seen.add(a)
                    weight = w["symbol"] * (rc.hop_decay ** (hop + 1))
                    via = f"changed: {ch['file'].split('/')[-1]}" + (f" ({', '.join(sorted(symbols))})" if symbols else "")
                    if a.endswith(".vue"):
                        reached = True
                        seed_component(a, weight, [via, f"used by {comp_by_id.get(a, {}).get('name', a)} ({hop + 1} import hop(s))"],
                                       "behaviour-via-import")
                    frontier.append((a, hop + 1))
            if not reached:
                unmapped.append({"file": ch["file"], "why": f"{area_name(ch['file'])}: shared logic that no screen we visited uses "
                                                           f"(within {rc.max_import_hops} steps)"})
        elif ch["type"] == "i18n":
            removed_keys |= set(ch["keys_removed"])
            changed_keys |= set(ch["keys_changed"])
        elif ch["type"] == "other":
            unmapped.append({"file": ch["file"], "why": f"{area_name(ch['file'])}: not code that draws the UI (asset or config)"})

    # ---- component seeds -> elements
    for cid, s in sorted(comp_seed.items()):
        rows = g.run("MATCH (e:Element)-[r:IMPLEMENTED_BY]->(:Component {id: $c}) "
                     "RETURN e.id AS eid, e.label AS label, r.confidence AS conf, r.method AS method ORDER BY eid", c=cid)
        for r in rows:
            amb = r["method"].endswith("ambiguous")
            bump(r["eid"], s["weight"] * r["conf"], s["chain"] + [f"draws '{r['label']}' ({r['method'].replace('_', ' ')}, {r['conf']:.2f})"],
                 s["kind"], amb, comp=cid, unique=r["method"] in UNIQUE_METHODS)
        if not rows and s["kind"] != "child-of-changed":
            reach = [r["path"] for r in code["routes"] if cid in r["reachable"]]
            crawled = [p for p in reach if p in crawled_routes]
            area = area_name(cid)
            if not reach:
                why = f"{area}: not part of any page a signed-out user can open (sign-in, desktop app or not linked)"
            elif not crawled:
                why = f"{area}: appears on {', '.join(route_name(p) for p in reach[:3])}, which this crawl did not visit"
            else:
                pages = ", ".join(route_name(p) for p in crawled[:2])
                why = f"{area}: can appear on {pages}, but the crawl never showed any of its controls"
                needs_human.append({"id": _rid("unreached", cid), "trigger": "unmapped_ui_change", "about": [cid],
                                    "question": f"This change touches '{area}', which can appear on {pages}, but we never saw it "
                                                "on screen. Which screen shows it, and does it need a retest?"})
            unmapped.append({"file": cid, "why": why})

    # ---- i18n seeds -> elements
    for key in sorted(removed_keys | changed_keys):
        kind = "key_removed" if key in removed_keys else "key_changed"
        for r in g.run("MATCH (e:Element)-[:DISPLAYS]->(:I18nKey {key: $k}) RETURN e.id AS eid, e.label AS label ORDER BY eid", k=key):
            what = "text removed" if kind == "key_removed" else "wording changed"
            bump(r["eid"], w[kind], [f"{what} for '{r['label']}' (translation key {key})"], kind, False)
            if kind == "key_removed":
                el_risk[r["eid"]]["lost"] = True

    # Keys a changed component stopped using: only that component's own elements lose their text.
    for cid, keys in sorted(comp_removed.items()):
        for r in g.run("MATCH (e:Element)-[:DISPLAYS]->(k:I18nKey) WHERE k.key IN $keys "
                       "MATCH (e)-[:IMPLEMENTED_BY]->(:Component {id: $c}) RETURN DISTINCT e.id AS eid, e.label AS label "
                       "ORDER BY eid", keys=sorted(keys), c=cid):
            bump(r["eid"], w["key_removed"], [f"changed: {comp_by_id.get(cid, {}).get('name', cid)} no longer shows '{r['label']}'"],
                 "key_removed", False)
            el_risk[r["eid"]]["lost"] = True

    # Elements whose every implementing component is deleted are lost too.
    if deleted_components:
        for r in g.run("MATCH (e:Element)-[:IMPLEMENTED_BY]->(c:Component) WITH e, collect(c.id) AS cs "
                       "WHERE all(x IN cs WHERE x IN $d) RETURN e.id AS eid", d=sorted(deleted_components)):
            if r["eid"] in el_risk:
                el_risk[r["eid"]]["lost"] = True

    # ---- presence: is the changed component really on a given screen?
    # One generic control ("Wrap lines", "More") is shared by many components and proves nothing. A component counts
    # as present on a screen if a control there is uniquely attributed to it, or if two or more distinct controls
    # there are attributed to it.
    comps = sorted({x["comp"] for x in el_risk.values() if x["comp"]})
    seen: dict[tuple[str, str], set[str]] = {}
    unique_on: set[tuple[str, str]] = set()
    for r in g.run("MATCH (s:State)-[:CONTAINS]->(e:Element)-[r:IMPLEMENTED_BY]->(c:Component) WHERE c.id IN $comps "
                   "RETURN s.fp AS fp, c.id AS c, e.id AS eid, r.method AS m", comps=comps):
        seen.setdefault((r["fp"], r["c"]), set()).add(r["eid"])
        if r["m"] in UNIQUE_METHODS:
            unique_on.add((r["fp"], r["c"]))

    def present(fp: str, eid: str) -> bool:
        x = el_risk[eid]
        if x["comp"] is None or x["unique"]:
            return True
        return (fp, x["comp"]) in unique_on or len(seen.get((fp, x["comp"]), ())) >= 2

    state_rows = g.run("MATCH (s:State)-[c:CONTAINS]->(e:Element) WHERE e.id IN $ids "
                       "RETURN s.fp AS fp, s.label AS label, s.route AS route, s.screenshot AS shot, s.depth AS depth, "
                       "s.path AS path, e.id AS eid, e.label AS el, c.bbox AS bbox ORDER BY fp, eid", ids=sorted(el_risk))
    confirmed: dict[str, int] = {}
    for r in state_rows:
        if present(r["fp"], r["eid"]):
            confirmed[r["eid"]] = confirmed.get(r["eid"], 0) + 1
    for eid, x in el_risk.items():
        if not confirmed.get(eid):
            x["risk"] = round(x["risk"] * 0.5, 4)
            x["chain"] = x["chain"] + ["but no crawled screen shows this part of the code together with it (risk halved)"]
            x["confirmed"] = False
        else:
            x["confirmed"] = True

    bands = rc.bands
    eids = sorted(el_risk)
    elements = []
    for r in g.run("MATCH (e:Element) WHERE e.id IN $ids RETURN e.id AS eid, e.label AS label, e.role AS role, e.route AS route "
                   "ORDER BY eid", ids=eids):
        x = el_risk[r["eid"]]
        elements.append({**r, **x, "band": band(x["risk"], bands)})
        if x["ambiguous"] and band(x["risk"], bands) == "High":
            needs_human.append({"id": _rid("ambig", r["eid"]), "trigger": "high_risk_weak_link", "about": [r["eid"]],
                                "question": f"'{r['label']}' is rated high risk, but we could not pin it to a single part of "
                                            "the code. Please confirm it is affected."})
    elements.sort(key=lambda e: (-e["risk"], e["label"], e["eid"]))

    # ---- screens
    screens: dict[str, dict] = {}
    for r in state_rows:
        if not present(r["fp"], r["eid"]):
            continue
        s = screens.setdefault(r["fp"], {"fp": r["fp"], "label": r["label"], "route": r["route"], "screenshot": r["shot"],
                                         "depth": r["depth"], "path": json.loads(r["path"] or "[]"), "risk": 0.0, "elements": []})
        risk = el_risk[r["eid"]]["risk"]
        s["risk"] = max(s["risk"], risk)
        s["elements"].append({"eid": r["eid"], "label": r["el"], "bbox": r["bbox"], "risk": risk})
    screen_list = sorted(screens.values(), key=lambda s: (-s["risk"], s["depth"], s["fp"]))
    for s in screen_list:
        s["band"] = band(s["risk"], bands)
        s["elements"].sort(key=lambda e: (-e["risk"], e["label"]))

    # ---- flows
    flows: dict[str, dict] = {}
    for r in g.run("MATCH (f:Flow)-[st:STEP]->(e:Element) WHERE e.id IN $ids "
                   "OPTIONAL MATCH (f)-[:EXERCISES]->(q:Requirement) "
                   "RETURN f.id AS id, f.name AS name, f.steps AS steps, f.start_route AS start, f.target_state AS target, "
                   "e.id AS eid, collect(DISTINCT q.id) AS reqs ORDER BY id, eid", ids=eids):
        if not present(r["target"], r["eid"]):
            continue
        f = flows.setdefault(r["id"], {"id": r["id"], "name": r["name"], "start_route": r["start"], "target_state": r["target"],
                                       "steps": [s["name"] for s in json.loads(r["steps"])], "requirements": sorted(r["reqs"]),
                                       "risk": 0.0, "touches": []})
        f["risk"] = max(f["risk"], el_risk[r["eid"]]["risk"])
        if r["eid"] not in f["touches"]:
            f["touches"].append(r["eid"])
    flow_list = sorted(flows.values(), key=lambda f: (-f["risk"], f["name"], f["id"]))
    for f in flow_list:
        f["band"] = band(f["risk"], bands)

    # ---- requirements
    reqs: dict[str, dict] = {}
    for r in g.run("MATCH (q:Requirement)-[cv:COVERED_BY]->(e:Element) WHERE e.id IN $ids "
                   "MATCH (q)-[:COVERED_BY]->(all:Element) "
                   "OPTIONAL MATCH (f:Flow)-[:EXERCISES]->(q) "
                   "RETURN q.id AS id, q.statement AS statement, q.quote AS quote, q.url AS url, q.feature AS feature, "
                   "cv.confidence AS conf, e.id AS eid, collect(DISTINCT all.id) AS evidence, "
                   "collect(DISTINCT f.target_state) AS targets ORDER BY id, eid", ids=eids):
        # The requirement is at risk only where it is exercised: its flows' end screens must show the changed code.
        targets = sorted(t for t in r["targets"] if t)
        if targets and not any(present(t, r["eid"]) for t in targets):
            continue
        if not targets and not el_risk[r["eid"]]["confirmed"]:
            continue
        q = reqs.setdefault(r["id"], {"id": r["id"], "statement": r["statement"], "quote": r["quote"], "url": r["url"],
                                      "feature": r["feature"], "risk": 0.0, "evidence": sorted(r["evidence"]), "via": []})
        q["risk"] = max(q["risk"], el_risk[r["eid"]]["risk"] * r["conf"])
        q["via"].append(r["eid"])
    req_list = []
    for q in reqs.values():
        lost = all(el_risk.get(e, {}).get("lost") for e in q["evidence"])
        q["status"] = "LOSES_COVERAGE" if lost else "AT_RISK" if q["risk"] >= bands["medium"] else "WEAK_SIGNAL"
        q["band"] = band(q["risk"], bands)
        q["risk"] = round(q["risk"], 4)
        req_list.append(q)
    req_list.sort(key=lambda q: (q["status"] != "LOSES_COVERAGE", -q["risk"], q["id"]))

    # ---- gaps touched (unverified features whose code this PR changes)
    changed_ids = sorted(comp_seed)
    gaps = g.run("MATCH (q:Requirement)-[:HAS_GAP]->(gp:Gap)-[:EXPECTED_IN]->(c:Component) WHERE c.id IN $ids "
                 "RETURN gp.id AS gap_id, gp.kind AS kind, q.id AS req_id, q.statement AS statement, collect(c.name) AS components "
                 "ORDER BY gap_id", ids=changed_ids)
    for gp in gaps:
        gp["components"] = sorted(gp["components"])

    worst = max([e["risk"] for e in elements] + [0.0])
    summary = {"overall_risk": band(worst, bands) if elements else "Low", "elements_at_risk": len(elements),
               "screens": len(screen_list), "flows": len(flow_list), "requirements": len(req_list),
               "requirements_losing_coverage": sum(1 for q in req_list if q["status"] == "LOSES_COVERAGE"),
               "high": sum(1 for e in elements if e["band"] == "High"), "medium": sum(1 for e in elements if e["band"] == "Medium"),
               "unmapped_changes": len(unmapped) + len(mapped["outside"]), "needs_human": len(needs_human)}
    return {"elements": elements, "screens": screen_list, "flows": flow_list, "requirements": req_list, "gaps_touched": gaps,
            "unmapped": unmapped, "outside": mapped["outside"], "needs_human": needs_human, "summary": summary}
