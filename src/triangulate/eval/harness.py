"""Eval: invariants (every run), gold-set accuracy (hand-labelled), calibration and repeatability.

Gold sets live in eval/gold/*.yaml and are keyed by things that survive re-runs (route, label,
role, requirement wording), never by run-specific ids.
"""

from __future__ import annotations

import json

import yaml

from ..config import ROOT, load_secrets
from ..graph.db import Graph
from ..ingest.docs import normalize
from ..runstore import RunStore

GOLD = ROOT / "eval" / "gold"


# ------------------------------------------------------------------------------- invariants
def invariants(run: RunStore, g: Graph) -> list[dict]:
    res = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        res.append({"invariant": name, "ok": bool(ok), "detail": detail})

    secs = {s["id"]: s for s in run.read_jsonl("ingest/sections.jsonl")}
    bad_quotes = [r["id"] for r in run.read_jsonl("ingest/requirements.jsonl")
                  if normalize(r["quote"]) not in normalize(secs[r["section_id"]]["text"])]
    check("every requirement quote is verbatim in its docs section", not bad_quotes, f"{len(bad_quotes)} bad")
    rows = g.run("MATCH (r:Requirement) WITH r, EXISTS { (r)-[:COVERED_BY]->() } AS c, EXISTS { (r)-[:HAS_GAP]->() } AS gp "
                 "WHERE c = gp RETURN r.id AS id")
    check("every requirement is covered XOR has exactly one explained gap", not rows, f"{len(rows)} violations")
    rows = g.run("MATCH (r:Requirement)-[:HAS_GAP]->(x) WITH r, count(x) AS n WHERE n > 1 RETURN r.id AS id")
    check("no requirement has more than one gap", not rows, f"{len(rows)} violations")
    rows = g.run("MATCH ()-[e:IMPLEMENTED_BY|COVERED_BY|IMPLEMENTED_IN]->() WHERE e.method IS NULL OR e.confidence IS NULL "
                 "RETURN count(e) AS n")
    check("every cross-layer edge carries method and confidence", rows[0]["n"] == 0, f"{rows[0]['n']} missing")
    rows = g.run("MATCH ()-[e:IMPLEMENTED_BY]->() WHERE e.evidence IS NULL OR e.evidence = '' RETURN count(e) AS n")
    check("every DOM->code edge carries its evidence", rows[0]["n"] == 0, f"{rows[0]['n']} missing")
    rows = g.run("MATCH (f:Flow) WHERE f.replay_verified <> true RETURN count(f) AS n")
    check("every flow in the graph was replayed successfully", rows[0]["n"] == 0, f"{rows[0]['n']} unverified")
    rows = g.run("MATCH (e:Element) WHERE NOT EXISTS { (:State)-[:CONTAINS]->(e) } RETURN count(e) AS n")
    check("no orphan elements", rows[0]["n"] == 0, f"{rows[0]['n']} orphans")
    links = run.read_jsonl("link/req_links.jsonl")
    outside = [x["req_id"] for x in links if not set(x["ui_elements"]) <= set(x["ui_candidates"]) and x["ui_method"] != "human"]
    check("every judged UI citation was among the retrieved candidates", not outside, f"{len(outside)} violations")
    rows = g.run("MATCH (gp:Gap) WHERE NOT EXISTS { (gp)-[:ASSERTED_IN]->(:CrawlRun) } RETURN count(gp) AS n")
    check("every gap is tied to the crawl run that asserts it", rows[0]["n"] == 0, f"{rows[0]['n']} untied")
    meta = run.read_json("crawl/meta.json")
    check("app version unchanged during the crawl", meta["app_version"] == run.manifest["app"]["version"],
          f"{run.manifest['app']['version']} -> {meta['app_version']}")
    return res


# ------------------------------------------------------------------------------- gold sets
def _load(name: str) -> list[dict]:
    p = GOLD / name
    return (yaml.safe_load(p.read_text()) or []) if p.exists() else []


def eval_dom_code(run: RunStore) -> dict:
    gold = _load("dom_code.yaml")
    links = run.read_jsonl("link/dom_code.jsonl")
    by_key: dict[tuple, list[dict]] = {}
    for r in links:
        by_key.setdefault((r["route"], r["name"].strip().lower()), []).append(r)
    per_method: dict[str, list[float]] = {}
    rows, missing = [], 0
    for item in gold:
        cands = by_key.get((item["route"], item["label"].strip().lower()), [])
        if item.get("context") is not None:
            cands = [c for c in cands if c.get("context") == item["context"]]
        if not cands:
            missing += 1
            rows.append({**item, "found": False})
            continue
        r = cands[0]
        chosen = [c["component"] for c in r["components"]]
        expected = item["component"] if isinstance(item["component"], list) else [item["component"]]
        credit = sum(1 for c in chosen if c in expected) / len(chosen)
        per_method.setdefault(r["method"], []).append(credit)
        rows.append({**item, "found": True, "method": r["method"], "chosen": chosen, "credit": round(credit, 3)})
    precision = {m: round(sum(v) / len(v), 3) for m, v in per_method.items()}
    credit = {m: round(sum(v), 3) for m, v in per_method.items()}
    all_credits = [c for v in per_method.values() for c in v]
    return {"items": len(gold), "found_in_run": len(gold) - missing,
            "precision_overall": round(sum(all_credits) / len(all_credits), 3) if all_credits else None,
            "precision_by_method": precision, "credit_by_method": credit,
            "n_by_method": {m: len(v) for m, v in per_method.items()}, "rows": rows}


def eval_req_ui(run: RunStore) -> dict:
    gold = _load("req_ui.yaml")
    reqs = run.read_jsonl("ingest/requirements.jsonl")
    links = {x["req_id"]: x for x in run.read_jsonl("link/req_links.jsonl")}
    tp = fp = fn = tn = 0
    rows = []
    for item in gold:
        needle = normalize(item["match"]).lower()
        match = [r for r in reqs if needle in normalize(r["statement"] + " " + r["quote"]).lower()]
        if not match:
            rows.append({**item, "found": False})
            continue
        lk = links.get(match[0]["id"], {})
        pred = lk.get("ui_verdict") in ("COVERED", "PARTIAL")
        exp = item["expected"] == "COVERED"
        tp += pred and exp
        fp += pred and not exp
        fn += (not pred) and exp
        tn += (not pred) and not exp
        rows.append({**item, "found": True, "req_id": match[0]["id"], "predicted": lk.get("ui_verdict")})
    return {"items": len(gold), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "absence_precision": round(tn / (tn + fn), 3) if tn + fn else None, "rows": rows}


def eval_prs(run: RunStore) -> dict:
    gold = _load("prs.yaml")
    out = []
    for item in gold:
        p = f"reason/pr-{item['pr']}/impact.json"
        if not run.exists(p):
            out.append({"pr": item["pr"], "ran": False})
            continue
        imp = run.read_json(p)
        flagged = {e["label"].strip().lower() for e in imp["elements"] if e["band"] in ("High", "Medium")}
        expected = [x.lower() for x in item.get("expected_controls", [])]
        hit = [x for x in expected if x in flagged]
        must_not = [x.lower() for x in item.get("must_not_flag", [])]
        false_alarms = [x for x in must_not if x in flagged]
        unmapped_ok = None
        if "expect_unmapped" in item:
            unmapped_ok = (len(imp["unmapped"]) + len(imp["outside"]) > 0) == item["expect_unmapped"]
        out.append({"pr": item["pr"], "ran": True, "expected_controls": len(expected), "recall": round(len(hit) / len(expected), 3) if expected else None,
                    "missed": sorted(set(expected) - set(hit)), "false_alarms": false_alarms, "unmapped_as_expected": unmapped_ok,
                    "flagged_high_medium": len(flagged), "overall_risk": imp["summary"]["overall_risk"],
                    "expected_overall": item.get("expected_overall"),
                    "overall_ok": (imp["summary"]["overall_risk"] == item["expected_overall"]) if item.get("expected_overall") else None})
    return {"prs": out}


# ------------------------------------------------------------------------------- repeatability
def repeatability(run: RunStore, others: list[str]) -> dict:
    stages = ("ingest", "code", "crawl", "probe", "link")
    mine = {s: run.stage_hash(s) for s in stages}
    comp = {}
    for o in others:
        ro = RunStore.open(o)
        comp[o] = {s: (ro.stage_hash(s) == mine[s]) for s in stages}
    calls = run.read_jsonl("llm_calls.jsonl") if run.exists("llm_calls.jsonl") else []
    meta = run.read_json("crawl/meta.json") if run.exists("crawl/meta.json") else {}
    # A route cut by wall-clock is not comparable across runs; report it next to the hashes.
    time_cut = sorted(r for r, v in meta.get("per_seed", {}).items() if v.get("stopped") == "time")
    return {"stage_hashes": mine, "matches": comp, "crawl_routes_cut_by_time": time_cut, "llm_calls": {"live": sum(1 for c in calls if not c["cached"]),
                                                                    "cached": sum(1 for c in calls if c["cached"])}}


# ------------------------------------------------------------------------------- verdict
def verdict(res: dict, th: dict) -> dict:
    """Is this run correct? Every invariant passes and every gold-set score meets eval/thresholds.yaml."""
    failed = [f"invariant {i['invariant']}" for i in res["invariants"] if not i["ok"]]
    dc, ru = res["dom_code"], res["req_ui"]
    checks = [
        ("G1 route-unique precision", dc["precision_by_method"].get("i18n_route_unique"), th["dom_code"]["route_unique_precision"]),
        ("G1 overall precision", dc["precision_overall"], th["dom_code"]["overall_precision"]),
        ("G2 precision", ru["precision"], th["req_ui"]["precision"]),
        ("G2 recall", ru["recall"], th["req_ui"]["recall"]),
    ]
    failed += [f"{name} {got} < {need}" for name, got, need in checks if got is None or got < need]
    for p in res["prs"]:
        if not p["ran"]:
            failed.append(f"PR {p['pr']} has no report")
            continue
        if len(p["false_alarms"]) > th["prs"]["max_false_alarms"]:
            failed.append(f"PR {p['pr']}: {len(p['false_alarms'])} false alarms")
        if th["prs"]["expected_overall_must_match"] and p["overall_ok"] is False:
            failed.append(f"PR {p['pr']}: overall risk {p['overall_risk']} != {p['expected_overall']}")
    return {"correct": not failed, "failed": failed}


def run_eval(run: RunStore, compare: list[str], update_calibration: bool = False) -> dict:
    from ..graph.loader import load_base, load_links
    g = Graph(load_secrets(require_llm=False))
    try:
        load_base(g, run)          # invariants must check THIS run's graph, not whatever is loaded
        load_links(g, run)
        inv = invariants(run, g)
    finally:
        g.close()
    dc = eval_dom_code(run)
    res = {"run": run.id, "invariants_ok": all(i["ok"] for i in inv), "invariants": inv,
           "dom_code": {k: v for k, v in dc.items() if k != "rows"}, "req_ui": {k: v for k, v in eval_req_ui(run).items() if k != "rows"},
           "prs": eval_prs(run)["prs"], "repeatability": repeatability(run, compare)}
    res["verdict"] = verdict(res, yaml.safe_load((ROOT / "eval" / "thresholds.yaml").read_text()))
    # Calibration: measured precision replaces the prior for methods with enough labelled samples.
    # Posterior mean under a uniform prior, (correct + 1) / (n + 2): 12/12 correct becomes 0.93, not 1.0.
    calib = {m: round((dc["credit_by_method"][m] + 1) / (n + 2), 3)
             for m, n in dc["n_by_method"].items() if n >= 5}
    res["calibration_measured"] = calib
    # The next run links with eval/calibration.json, so adopting a new measurement is a deliberate step: writing it
    # on every eval would silently change the next run and break exact replays of this one.
    if calib and update_calibration:
        path = ROOT / "eval" / "calibration.json"
        path.write_text(json.dumps({"dom_code": calib, "source_run": run.id, "n": dc["n_by_method"]}, indent=1, sort_keys=True) + "\n")
        res["calibration_written"] = calib
    run.write_json("eval/results.json", res)
    run.write_json("eval/dom_code_rows.json", dc["rows"])
    return res
