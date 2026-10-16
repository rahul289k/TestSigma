"""Stage 5: the probe agent.

For each documented, web-testable requirement that the crawl gave no UI evidence for, a bounded
LLM agent drives the browser to look for it before we declare it absent.

Each decision call returns a short plan (up to 4 actions) or a verdict (found / give_up). Plans
are executed by element signature, so they stay valid when the screen changes; execution stops at
the first step whose element is no longer on screen, and the agent sees the new screen next turn.
Plans exist because the API quota is a few calls per day: one call per click would spend the
whole budget on a single requirement.

Guardrails are enforced in code: the agent can only choose elements that already passed the
crawl's safety filter, so it cannot click sign-in, delete, external links, etc. Everything it
observes is recorded as ordinary states and transitions (source = "probe").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..config import Config
from ..llm.client import LLM
from ..runstore import RunStore
from .browser import ActionFailed, Session
from .model import is_actionable

SYSTEM = """You are a careful QA engineer exploring a web app (Hoppscotch, an API client) to find whether a documented
feature exists in the UI. Each turn you see the current screen as numbered lists of controls.
- outcome "continue": give 1-4 actions to perform in order, using indexes from the ACTIONS list
  (click a control, fill a text field, or escape to close a menu or dialog).
- outcome "found": give found_index from the ALL ELEMENTS list when an element on screen clearly is the feature.
- outcome "give_up": when you are confident the feature is not reachable from here.
Never claim "found" unless the element clearly implements the documented capability."""


class ProbeAction(BaseModel):
    action: Literal["click", "fill", "escape"]
    element_index: int | None = Field(default=None, description="Index from the ACTIONS list (not needed for escape)")
    text: str | None = Field(default=None, description="Text to type for fill")


class ProbeDecision(BaseModel):
    outcome: Literal["continue", "found", "give_up"]
    actions: list[ProbeAction] = Field(description="1-4 actions when outcome is continue, else empty")
    found_index: int | None = Field(default=None, description="Index from ALL ELEMENTS when outcome is found")
    reason: str


def _screen(obs: dict, actions: list[tuple[dict, str, str | None]]) -> str:
    act_lines = [f"{i}. [{el['role']}] {el['name']}" + (f"  (in {el['context']})" if el['context'] else "")
                 for i, (el, _, _) in enumerate(actions)]
    all_lines = [f"{i}. [{e['role']}] {e['name']}" for i, e in enumerate(obs["elements"])]
    return f"ROUTE: {obs['route']}\nACTIONS:\n" + "\n".join(act_lines) + "\nALL ELEMENTS:\n" + "\n".join(all_lines)


def run_probe(cfg: Config, run: RunStore, llm: LLM, targets: list[dict]) -> dict:
    """targets: [{"req": requirement dict, "start_route": str}]"""
    session = Session(cfg.app.url, cfg.crawl)
    states: dict[str, dict] = {s["fp"]: s for s in run.read_jsonl("crawl/states.jsonl")}
    new_states: list[dict] = []
    transitions: list[dict] = []
    episodes: list[dict] = []
    order = max((s["order"] for s in states.values()), default=0) + 1

    def register(obs: dict, seed: str, path: list[dict], parent: str | None) -> None:
        nonlocal order
        if obs["fp"] in states:
            return
        shot = f"probe/screens/{obs['fp']}.jpg"
        session.screenshot(run.path(shot))
        row = {"fp": obs["fp"], "route": obs["route"], "seed": seed, "depth": len(path), "order": order, "path": path,
               "parent": parent, "screenshot": shot, "url": obs["url"], "title": obs["title"], "source": "probe",
               "elements": [{k: v for k, v in e.items() if k != "tri"} for e in obs["elements"]]}
        order += 1
        states[obs["fp"]] = row
        new_states.append(row)

    try:
        for t in targets:
            req, start = t["req"], t["start_route"]
            try:
                session.reset(start)
            except ActionFailed as e:
                episodes.append({"req_id": req["id"], "start_route": start, "steps": [], "outcome": "error",
                                 "found": None, "error": str(e)})
                continue
            path: list[dict] = []
            history: list[str] = []
            result = {"req_id": req["id"], "start_route": start, "steps": [], "outcome": "give_up", "found": None}
            obs = session.observe()
            register(obs, start, [], None)
            for step in range(cfg.probe.max_steps):
                acts = [(e, *is_actionable(e, cfg.crawl.deny_names, cfg.crawl.fixtures)) for e in obs["elements"]]
                acts = [(e, k, v) for e, k, v in acts if k is not None]
                prompt = (f"FEATURE TO FIND: {req['statement']}\nDOC QUOTE: \"{req['quote']}\"\n"
                          f"WORDS TO LOOK FOR: {', '.join(req['ui_cues'])}\n"
                          f"WHAT HAPPENED SO FAR: {'; '.join(history) or 'nothing yet'}\n"
                          f"TURN {step + 1} of {cfg.probe.max_steps}"
                          + (" (last turn: answer found or give_up)" if step + 1 == cfg.probe.max_steps else "")
                          + "\n\n" + _screen(obs, acts))
                d = llm.json(f"probe:{req['id']}:{step}", prompt, ProbeDecision, system=SYSTEM)
                rec: dict = {"step": step, "state": obs["fp"], "decision": d.model_dump(), "executed": []}
                result["steps"].append(rec)
                if d.outcome == "give_up":
                    break
                if d.outcome == "found":
                    idx = d.found_index
                    if idx is None or not (0 <= idx < len(obs["elements"])):
                        rec["error"] = "found: index out of range"
                        break
                    el = obs["elements"][idx]
                    result.update(outcome="found", found={"state": obs["fp"], "eid": el["eid"], "name": el["name"]})
                    break
                # Resolve the whole plan against the screen it was made for, then execute by signature.
                plan = []
                for a in d.actions[:4]:
                    if a.action == "escape":
                        plan.append(("escape", None, None))
                    elif a.element_index is not None and 0 <= a.element_index < len(acts):
                        el, kind, val = acts[a.element_index]
                        if a.action == "fill" and el["role"] == "textbox":
                            kind, val = "fill", a.text or val or ""
                        elif a.action == "fill":
                            rec["executed"].append({"skipped": "fill on a non-text element"})
                            continue
                        plan.append((kind, el, val))
                    else:
                        rec["executed"].append({"skipped": f"invalid index {a.element_index}"})
                for kind, el, val in plan:
                    before = obs["fp"]
                    if kind == "escape":
                        session.press("Escape")
                        action = {"kind": "press", "value": "Escape", "sig": "", "occ": 0, "eid": None, "name": "Escape", "role": "key"}
                        network: list[str] = []
                    else:
                        live = session.find(obs, el["sig"], el["occ"])
                        if live is None:
                            rec["executed"].append({"stopped": f"'{el['name']}' no longer on screen"})
                            history.append(f"'{el['name']}' disappeared before it could be clicked")
                            break
                        action = {"kind": kind, "value": val, "sig": el["sig"], "occ": el["occ"], "eid": el["eid"],
                                  "name": el["name"], "role": el["role"]}
                        try:
                            network = session.act(live, kind, val)
                        except ActionFailed as e:
                            rec["executed"].append({"failed": str(e)})
                            history.append(f"{kind} '{el['name']}' failed")
                            break
                    obs = session.observe()
                    path = path + [action]
                    register(obs, start, path, before)
                    transitions.append({"order": len(transitions), "from": before, "to": obs["fp"],
                                        "action": action, "network": network, "source": "probe"})
                    rec["executed"].append({"action": action["name"], "kind": action["kind"], "changed": obs["fp"] != before})
                    history.append(f"{action['kind']} '{action['name']}' -> {'new screen' if obs['fp'] != before else 'no change'}")
            episodes.append(result)
    finally:
        session.close()
    run.write_jsonl("probe/states.jsonl", new_states)
    run.write_jsonl("probe/transitions.jsonl", transitions)
    run.write_jsonl("probe/episodes.jsonl", episodes)
    return {"episodes": len(episodes), "found": sum(1 for e in episodes if e["outcome"] == "found"),
            "new_states": len(new_states), "transitions": len(transitions),
            "decision_calls": sum(len(e["steps"]) for e in episodes)}
