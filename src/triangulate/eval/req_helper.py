"""Helper for building the requirement->UI gold set (G2) by hand.

Prints a deterministic sample of web-testable requirements with the doc quote, the judge's verdict,
the elements it cited and the retrieved candidates. The human decides, independently, whether the
capability exists in the signed-out web app (checking the live app where needed), and records that
decision in eval/gold/req_ui.yaml - keyed by a verbatim doc phrase so it survives re-runs.

Usage: python -m triangulate.eval.req_helper <run_id> [n]
"""

from __future__ import annotations

import hashlib
import sys

from ..runstore import RunStore
from ..link.uidata import load_ui


def main(run_id: str, n: int = 30) -> None:
    run = RunStore.open(run_id)
    ui = load_ui(run)
    reqs = {r["id"]: r for r in run.read_jsonl("ingest/requirements.jsonl")}
    links = [x for x in run.read_jsonl("link/req_links.jsonl") if x["testability"] == "UI_OBSERVABLE"]
    links.sort(key=lambda x: hashlib.sha1(x["req_id"].encode()).hexdigest())
    for i, lk in enumerate(links[:n], 1):
        r = reqs[lk["req_id"]]
        print(f"\n#{i} {r['id']} [{r['feature']}] verdict={lk['ui_verdict']} via {lk['ui_method']}")
        print(f"   statement: {r['statement']}")
        print(f"   quote: \"{r['quote']}\"")
        print(f"   cited: {[ui.elements[e]['name'] for e in lk['ui_elements'] if e in ui.elements]}")
        print(f"   candidates: {[ui.elements[e]['name'][:30] for e in lk['ui_candidates'][:10] if e in ui.elements]}")
        print(f"   rationale: {lk['ui_rationale'][:160]}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30)
