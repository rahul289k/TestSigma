"""Stage 6a: absence as evidence. Deterministic rules over each requirement's presence signature.

Every requirement ends up either COVERED (has COVERED_BY edges) or with exactly one Gap that says
why it is not covered. Strong claims ("not found anywhere", "outside the crawl and unverified in
code") always create a review item: the system never calls something missing on its own.
"""

from __future__ import annotations

from ..runstore import RunStore


def build_gaps(reqs: list[dict], links: dict[str, dict], episodes: dict[str, dict], n_states: int) -> tuple[list[dict], list[dict]]:
    gaps, review = [], []
    for r in reqs:
        lk = links.get(r["id"], {})
        t = r["testability"]
        code = lk.get("code_components", [])
        gid = "GAP-" + r["id"].removeprefix("REQ-")
        base = {"id": gid, "req_id": r["id"], "searched_states": n_states}
        if t == "NOT_WEB_UI":
            gaps.append({**base, "kind": "NOT_UI_TESTABLE", "reason": r["testability_reason"], "expected_in": []})
            continue
        if t in ("NEEDS_LOGIN", "DESKTOP_ONLY"):
            why = "needs sign-in" if t == "NEEDS_LOGIN" else "desktop app only"
            if code:
                gaps.append({**base, "kind": "OUTSIDE_CRAWL_BOUNDARY", "expected_in": code,
                             "reason": f"Not reachable by a logged-out web crawl ({why}); code implements it in {len(code)} component(s)."})
            else:
                gaps.append({**base, "kind": "OUTSIDE_BOUNDARY_UNVERIFIED", "expected_in": [],
                             "reason": f"Not reachable by a logged-out web crawl ({why}) and no implementing component was found."})
                review.append({"trigger": "boundary_unverified", "req_id": r["id"],
                               "question": f"'{r['statement']}' needs {why} and we found no code for it. Does it exist?",
                               "evidence": {"testability_reason": r["testability_reason"]}})
            continue
        if lk.get("ui_verdict") in ("COVERED", "PARTIAL") and lk.get("ui_elements"):
            continue
        ep = episodes.get(r["id"])
        probe = ""
        if ep:
            probe = (f"probe agent: {ep['outcome']} after {len(ep['steps'])} step(s) from {ep['start_route']}; "
                     f"last reason: {ep['steps'][-1]['decision']['reason'] if ep['steps'] else 'n/a'}")
        if code:
            gaps.append({**base, "kind": "UNOBSERVED_IN_UI", "expected_in": code, "probe_summary": probe,
                         "reason": "Implemented in code but not reached by the crawl or the probe (deep state, flag, or budget)."})
        else:
            gaps.append({**base, "kind": "NOT_FOUND_ANYWHERE", "expected_in": [], "probe_summary": probe,
                         "reason": "Documented, but no UI evidence and no implementing component were found."})
            review.append({"trigger": "not_found_anywhere", "req_id": r["id"],
                           "question": f"We found no trace of '{r['statement']}' in the UI or the code. Is this a real product gap?",
                           "evidence": {"probe": probe}})
    return gaps, review


def run_gaps(run: RunStore) -> dict:
    reqs = run.read_jsonl("ingest/requirements.jsonl")
    links = {x["req_id"]: x for x in run.read_jsonl("link/req_links.jsonl")}
    episodes = {e["req_id"]: e for e in run.read_jsonl("probe/episodes.jsonl")} if run.exists("probe/episodes.jsonl") else {}
    n_states = len(run.read_jsonl("crawl/states.jsonl")) + (len(run.read_jsonl("probe/states.jsonl")) if run.exists("probe/states.jsonl") else 0)
    gaps, review = build_gaps(reqs, links, episodes, n_states)
    run.write_jsonl("link/gaps.jsonl", gaps)
    run.write_jsonl("link/review_items_gaps.jsonl", review)
    by: dict[str, int] = {}
    for x in gaps:
        by[x["kind"]] = by.get(x["kind"], 0) + 1
    covered = len(reqs) - len(gaps)
    ui_testable = sum(1 for r in reqs if r["testability"] == "UI_OBSERVABLE")
    return {"requirements": len(reqs), "covered": covered, "ui_testable": ui_testable,
            "ui_coverage": round(covered / ui_testable, 3) if ui_testable else 0.0, "gaps": by}
