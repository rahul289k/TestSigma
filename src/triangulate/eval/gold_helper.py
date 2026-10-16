"""Helper for building the DOM->code gold set by hand.

Prints a deterministic, method-stratified sample of linked elements with the evidence a human
needs to decide the correct component: the screen it was seen on, every candidate component on
that route, and the template lines where each candidate uses the matched i18n key. The human
decision (not the linker's) is what goes into eval/gold/dom_code.yaml.

Usage: python -m triangulate.eval.gold_helper <run_id> [per_method]
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from ..runstore import RunStore
from ..link.uidata import load_ui


def main(run_id: str, per_method: int = 10) -> None:
    run = RunStore.open(run_id)
    ui = load_ui(run)
    code = run.read_json("code/code_graph.json")
    root = Path(run.manifest["code"]["path"]) / "packages/hoppscotch-common"
    key_users: dict[str, list[str]] = {}
    for c in code["components"]:
        for k in c["keys"]:
            key_users.setdefault(k, []).append(c["id"])
    reach = {r["path"]: set(r["reachable"]) for r in code["routes"]}
    rows = run.read_jsonl("link/dom_code.jsonl")
    by_method: dict[str, list[dict]] = {}
    for r in rows:
        by_method.setdefault(r["method"], []).append(r)
    for method, items in sorted(by_method.items()):
        items.sort(key=lambda r: hashlib.sha1(r["eid"].encode()).hexdigest())
        print(f"\n######## {method} ({len(items)} links, showing {min(per_method, len(items))})")
        for r in items[:per_method]:
            el = ui.elements[r["eid"]]
            print(f"\n--- '{el['name']}' [{el['role']}] route={el['route']} context={el['context']}")
            print(f"    screen: {ui.state_label(el['states'][0])}; linker chose: {[c['component'] for c in r['components']]}")
            cands = sorted({c for k in r["keys"] for c in key_users.get(k, [])} & reach.get(el["route"], set()))
            for cid in cands:
                lines = (root / cid).read_text().splitlines()
                hits = [f"{i + 1}: {ln.strip()[:110]}" for i, ln in enumerate(lines) if any(k in ln for k in r["keys"])]
                print(f"      {cid}")
                for h in hits[:3]:
                    print(f"         {h}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10)
