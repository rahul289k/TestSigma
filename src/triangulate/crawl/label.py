"""Human-readable screen names for crawled states (LLM, batched, text-only).

Labels exist so a QA lead can read "Request builder - Headers tab" instead of S-1a2b3c.
They are never used as linking evidence. To respect a limited API quota we label from a
compact text summary of each state (route, how it was reached, open dialogs, selected tabs,
headings, new controls) rather than sending one screenshot per state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm.client import LLM

SYSTEM = """You name screens of a web app (Hoppscotch, an API client) for a QA lead who knows the product but not the code.
For each screen give a short name (max 8 words, e.g. "REST request - Headers tab" or "Environment selector menu")
and a one-sentence purpose. Use only what the summary shows. Keep names distinct when screens differ."""


class ScreenLabel(BaseModel):
    fp: str = Field(description="The screen id exactly as given")
    label: str
    purpose: str


class LabelBatch(BaseModel):
    screens: list[ScreenLabel]


def summarize(state: dict, parent_sigs: set[str]) -> str:
    els = state["elements"]
    dialogs = sorted({c for e in els for c in e["context"].split(">") if c.startswith("dialog:") or c == "popover"})
    selected = sorted({e["name"] for e in els if e.get("selected") and e["role"] in ("button", "tab", "link")})[:8]
    heads = sorted({e["name"] for e in els if e["role"] in ("heading", "label")})[:8]
    new = [e["name"] for e in els if e["sig"] not in parent_sigs][:25]
    path = " > ".join(a["name"] for a in state["path"]) or "(start)"
    return (f"<screen id=\"{state['fp']}\">route: {state['route']}\nreached by: {path}\n"
            f"open overlays: {', '.join(dialogs) or 'none'}\nselected: {', '.join(selected) or 'none'}\n"
            f"headings: {', '.join(heads) or 'none'}\nnew controls: {', '.join(new) or 'none'}\n</screen>")


def label_states(states: list[dict], llm: LLM, per_call: int, task: str = "label") -> dict[str, dict]:
    by_fp = {s["fp"]: s for s in states}
    out: dict[str, dict] = {}
    ordered = sorted(states, key=lambda s: (s["seed"], s["order"]))
    for i in range(0, len(ordered), per_call):
        chunk = ordered[i:i + per_call]
        parts = []
        for s in chunk:
            parent = by_fp.get(s["parent"]) if s.get("parent") else None
            parts.append(summarize(s, {e["sig"] for e in parent["elements"]} if parent else set()))
        res = llm.json(f"{task}:{i // per_call}", "Name these screens.\n\n" + "\n\n".join(parts), LabelBatch, system=SYSTEM)
        wanted = {s["fp"] for s in chunk}
        for sl in res.screens:
            if sl.fp in wanted:
                out[sl.fp] = {"label": sl.label.strip(), "purpose": sl.purpose.strip()}
        missing = wanted - set(out)
        if missing:
            raise RuntimeError(f"[{task}] labeler skipped {len(missing)} screens, e.g. {sorted(missing)[:3]}")
    return out
