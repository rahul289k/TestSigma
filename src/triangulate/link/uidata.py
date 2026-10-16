"""One view over the UI layer: crawl + probe states, transitions, labels and deduplicated elements."""

from __future__ import annotations

from dataclasses import dataclass

from ..runstore import RunStore


@dataclass
class UIData:
    states: dict[str, dict]
    transitions: list[dict]
    elements: dict[str, dict]          # eid -> element props (first observation) + "states": [fp...]
    labels: dict[str, dict]            # fp -> {label, purpose}

    def state_label(self, fp: str) -> str:
        return self.labels.get(fp, {}).get("label") or fp


def load_ui(run: RunStore) -> UIData:
    states: dict[str, dict] = {}
    transitions: list[dict] = []
    labels: dict[str, dict] = {}
    for stage in ("crawl", "probe"):
        if run.exists(f"{stage}/states.jsonl"):
            for s in run.read_jsonl(f"{stage}/states.jsonl"):
                states.setdefault(s["fp"], s)
            transitions.extend(run.read_jsonl(f"{stage}/transitions.jsonl"))
        if run.exists(f"{stage}/labels.json"):
            labels.update(run.read_json(f"{stage}/labels.json"))
    elements: dict[str, dict] = {}
    for fp in sorted(states, key=lambda f: states[f]["order"]):
        for e in states[fp]["elements"]:
            el = elements.get(e["eid"])
            if el is None:
                el = {k: e[k] for k in ("eid", "sig", "role", "name", "kind", "context", "tag", "text", "placeholder",
                                       "title", "aria_label", "tooltip", "testid", "editor") if k in e}
                el["route"] = states[fp]["route"]
                el["states"] = []
                elements[e["eid"]] = el
            if fp not in el["states"]:
                el["states"].append(fp)
    return UIData(states=states, transitions=transitions, elements=elements, labels=labels)
