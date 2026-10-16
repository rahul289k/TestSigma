"""Load run artifacts into Neo4j. The graph is always rebuilt from artifacts, never edited in place,
so loading the same run twice yields the same graph."""

from __future__ import annotations

import json
import re

from ..runstore import RunStore
from ..link.uidata import load_ui
from .db import Graph


def _camel_words(name: str) -> str:
    return " ".join(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name))


def load_base(g: Graph, run: RunStore) -> dict:
    """Requirements, UI and code layers (no cross-layer links yet)."""
    g.ensure_schema()
    g.wipe()
    m = run.manifest
    g.merge_nodes("CrawlRun", [{"id": run.id, "app_version": m["app"]["version"], "code_sha": m["code"]["sha"],
                                "docs_sha": m["docs"]["sha"], "config_hash": m["config_hash"], "profile": m["profile"]}])

    # ---- requirements layer
    sections = run.read_jsonl("ingest/sections.jsonl")
    reqs = run.read_jsonl("ingest/requirements.jsonl")
    feats = {}
    for s in sections:
        feats[s["page"]] = {"id": s["page"], "name": s["page_title"], "url": s["url"].split("#")[0]}
    g.merge_nodes("Feature", list(feats.values()))
    g.merge_nodes("Section", [{"id": s["id"], "heading": s["heading"], "url": s["url"], "text": s["text"]} for s in sections])
    g.merge_rels("PART_OF", "Section", "Feature", [{"a": s["id"], "b": s["page"], "props": {}} for s in sections])
    g.merge_nodes("Requirement", [{"id": r["id"], "statement": r["statement"], "quote": r["quote"], "testability": r["testability"],
                                   "testability_reason": r["testability_reason"], "cues": r["ui_cues"], "url": r["url"],
                                   "feature": r["feature"]} for r in reqs])
    g.merge_rels("DERIVED_FROM", "Requirement", "Section", [{"a": r["id"], "b": r["section_id"], "props": {}} for r in reqs])
    g.merge_rels("IN_FEATURE", "Requirement", "Feature", [{"a": r["id"], "b": r["page"], "props": {}} for r in reqs])

    # ---- code layer
    code = run.read_json("code/code_graph.json")
    g.merge_nodes("I18nKey", [{"key": k, "text": v} for k, v in sorted(code["i18n"].items())])
    g.merge_nodes("File", [{"path": f["path"], "lang": f["lang"]} for f in code["files"]])
    comps = []
    for c in code["components"]:
        b = c["blocks"]
        comps.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "search_name": _camel_words(c["name"]),
                      "template": b["template"], "script": b["scriptSetup"] or b["script"],
                      "styles": json.dumps(b["styles"]), "dynamic_component": c["dynamic_component"]})
    g.merge_nodes("Component", comps)
    g.merge_rels("DEFINED_IN", "Component", "File", [{"a": c["id"], "b": c["id"], "props": {}} for c in code["components"]])
    g.merge_rels("RENDERS", "Component", "Component",
                 [{"a": c["id"], "b": t, "props": {"dynamic": False}} for c in code["components"] for t in c["renders"]])
    g.merge_rels("RENDERS", "Component", "Component",
                 [{"a": c["id"], "b": t, "props": {"dynamic": True}} for c in code["components"]
                  for t in c.get("renders_dynamic", [])])
    g.merge_rels("USES_KEY", "Component", "I18nKey",
                 [{"a": c["id"], "b": k, "props": {}} for c in code["components"] for k in c["keys"]])
    g.merge_rels("IMPORTS", "File", "File",
                 [{"a": f["path"], "b": i["target"], "props": {"names": i["names"], "namespace": i["namespace"],
                                                               "default": i["default"] or ""}}
                  for f in code["files"] for i in f["imports"]])
    syms = [{"id": f"{f['path']}#{s['name']}", "name": s["name"], "kind": s["kind"], "start": s["start"], "end": s["end"],
             "exported": s["exported"], "file": f["path"]} for f in code["files"] for s in f["symbols"]]
    g.merge_nodes("Symbol", syms)
    g.merge_rels("DEFINED_IN", "Symbol", "File", [{"a": s["id"], "b": s["file"], "props": {}} for s in syms])
    g.merge_nodes("Route", [{"path": r["path"], "layout": r["layout"]} for r in code["routes"]])
    g.merge_rels("RENDERED_BY", "Route", "Component",
                 [{"a": r["path"], "b": f, "props": {"order": i}} for r in code["routes"]
                  for i, f in enumerate(["src/App.vue", r["layout"], *r["chain"]])])

    # ---- UI layer
    ui = load_ui(run)
    pages = sorted({s["route"] for s in ui.states.values()})
    g.merge_nodes("Page", [{"route": p} for p in pages])
    code_routes = {r["path"] for r in code["routes"]}
    g.merge_rels("AT_ROUTE", "Page", "Route", [{"a": p, "b": p, "props": {}} for p in pages if p in code_routes])
    g.merge_nodes("State", [{"fp": s["fp"], "route": s["route"], "depth": s["depth"], "source": s["source"],
                             "screenshot": s["screenshot"], "url": s["url"], "order": s["order"],
                             "label": ui.labels.get(s["fp"], {}).get("label", ""),
                             "purpose": ui.labels.get(s["fp"], {}).get("purpose", ""),
                             "path": json.dumps([a["name"] for a in s["path"]])} for s in ui.states.values()])
    g.merge_rels("HAS_STATE", "Page", "State", [{"a": s["route"], "b": s["fp"], "props": {}} for s in ui.states.values()])
    g.merge_rels("ASSERTED_IN", "State", "CrawlRun", [{"a": s["fp"], "b": run.id, "props": {}} for s in ui.states.values()])
    g.merge_rels("TRANSITION", "State", "State",
                 [{"a": t["from"], "b": t["to"], "props": {"aid": f"{t['source']}:{t['order']}", "kind": t["action"]["kind"],
                                                           "eid": t["action"]["eid"] or "", "name": t["action"]["name"],
                                                           "value": t["action"].get("value") or "", "source": t["source"]}}
                  for t in ui.transitions if t["from"] in ui.states and t["to"] in ui.states], rel_key="aid")
    els = []
    for e in ui.elements.values():
        state_labels = sorted({ui.state_label(fp) for fp in e["states"][:3]})
        search = " ".join(x for x in [e["name"], e.get("tooltip", ""), e.get("placeholder", ""), *state_labels] if x)
        els.append({"id": e["eid"], "label": e["name"], "role": e["role"], "kind": e["kind"], "route": e["route"],
                    "context": e["context"], "tooltip": e.get("tooltip", ""), "placeholder": e.get("placeholder", ""),
                    "search_text": search})
    g.merge_nodes("Element", els)
    contains = []
    for s in ui.states.values():
        for e in s["elements"]:
            contains.append({"a": s["fp"], "b": e["eid"], "props": {"bbox": e["bbox"], "occ": e["occ"],
                                                                     "selected": bool(e.get("selected"))}})
    g.merge_rels("CONTAINS", "State", "Element", contains)
    g.run("CALL db.awaitIndexes(120)")
    return {"requirements": len(reqs), "states": len(ui.states), "elements": len(els), "components": len(comps)}


def load_links(g: Graph, run: RunStore) -> dict:
    """Cross-layer edges, gaps, flows and review items produced by the link stages."""
    counts = {}
    if run.exists("link/dom_code.jsonl"):
        rows = run.read_jsonl("link/dom_code.jsonl")
        g.merge_rels("DISPLAYS", "Element", "I18nKey",
                     [{"a": r["eid"], "b": k, "props": {"field": r["matched_field"]}} for r in rows for k in r["keys"]])
        g.merge_rels("IMPLEMENTED_BY", "Element", "Component",
                     [{"a": r["eid"], "b": c["component"], "props": {"method": r["method"], "confidence": c["confidence"],
                                                                      "evidence": r["evidence"]}}
                      for r in rows for c in r["components"]])
        counts["dom_code"] = len(rows)
    if run.exists("link/req_links.jsonl"):
        rows = run.read_jsonl("link/req_links.jsonl")
        g.merge_rels("COVERED_BY", "Requirement", "Element",
                     [{"a": r["req_id"], "b": eid, "props": {"method": r["ui_method"], "confidence": r["ui_confidence"],
                                                             "rationale": r["ui_rationale"], "partial": r["ui_verdict"] == "PARTIAL",
                                                             "votes": r["ui_votes"]}}
                      for r in rows if r["ui_verdict"] in ("COVERED", "PARTIAL") for eid in r["ui_elements"]])
        g.merge_rels("IMPLEMENTED_IN", "Requirement", "Component",
                     [{"a": r["req_id"], "b": c, "props": {"method": "llm_judge", "confidence": r["code_confidence"],
                                                          "rationale": r["code_rationale"]}}
                      for r in rows for c in r["code_components"]])
        counts["req_links"] = len(rows)
    if run.exists("link/gaps.jsonl"):
        gaps = run.read_jsonl("link/gaps.jsonl")
        g.merge_nodes("Gap", [{"id": x["id"], "kind": x["kind"], "reason": x["reason"],
                               "probe_summary": x.get("probe_summary", ""), "searched_states": x.get("searched_states", 0)}
                              for x in gaps])
        g.merge_rels("HAS_GAP", "Requirement", "Gap", [{"a": x["req_id"], "b": x["id"], "props": {}} for x in gaps])
        g.merge_rels("ASSERTED_IN", "Gap", "CrawlRun", [{"a": x["id"], "b": run.id, "props": {}} for x in gaps])
        g.merge_rels("EXPECTED_IN", "Gap", "Component",
                     [{"a": x["id"], "b": c, "props": {}} for x in gaps for c in x.get("expected_in", [])])
        counts["gaps"] = len(gaps)
    if run.exists("link/flows.jsonl"):
        flows = run.read_jsonl("link/flows.jsonl")
        g.merge_nodes("Flow", [{"id": f["id"], "name": f["name"], "start_route": f["start_route"],
                                "steps": json.dumps(f["steps"]), "replay_verified": f["replay_verified"],
                                "target_state": f["target_state"]} for f in flows])
        g.merge_rels("STEP", "Flow", "Element",
                     [{"a": f["id"], "b": st["eid"], "props": {"order": i, "action": st["kind"]}}
                      for f in flows for i, st in enumerate(f["steps"]) if st.get("eid")], rel_key="order")
        g.merge_rels("STEP", "Flow", "Element",
                     [{"a": f["id"], "b": f["target_eid"], "props": {"order": len(f["steps"]), "action": "verify"}}
                      for f in flows], rel_key="order")
        g.merge_rels("EXERCISES", "Flow", "Requirement", [{"a": f["id"], "b": r, "props": {}} for f in flows for r in f["requirements"]])
        g.merge_rels("STARTS_AT", "Flow", "Page", [{"a": f["id"], "b": f["start_route"], "props": {}} for f in flows])
        counts["flows"] = len(flows)
    if run.exists("link/review_items.jsonl"):
        items = run.read_jsonl("link/review_items.jsonl")
        g.merge_nodes("ReviewItem", [{"id": i["id"], "trigger": i["trigger"], "question": i["question"],
                                      "evidence": json.dumps(i["evidence"])} for i in items])
        g.merge_rels("ABOUT", "ReviewItem", "Requirement",
                     [{"a": i["id"], "b": i["req_id"], "props": {}} for i in items if i.get("req_id")])
        counts["review_items"] = len(items)
    return counts
