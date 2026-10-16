"""Stage 6b: flows - the shortest recorded way to reach each covered requirement's evidence.

A flow is the action path the crawler (or probe) actually used to first reach a state that shows
the evidence element. Every flow is replayed in a fresh browser before it is kept, so a flow in
the graph is a proven, executable test path - not a guess. The LLM only gives it a readable name.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel

from ..config import Config
from ..crawl.browser import ActionFailed, Session
from ..llm.client import LLM
from ..runstore import RunStore
from .uidata import load_ui


class FlowName(BaseModel):
    id: str
    name: str


class FlowNames(BaseModel):
    flows: list[FlowName]


SYSTEM = """Name user journeys in a web app (Hoppscotch, an API client) for a QA lead.
Each name is an imperative phrase of at most 9 words describing what the user does, e.g.
"Open the Headers tab of a REST request". Use the steps and the goal; no code words."""


def plan_flows(run: RunStore) -> list[dict]:
    ui = load_ui(run)
    links = run.read_jsonl("link/req_links.jsonl")
    flows: dict[str, dict] = {}
    for lk in links:
        if lk["ui_verdict"] not in ("COVERED", "PARTIAL"):
            continue
        for eid in lk["ui_elements"]:
            el = ui.elements.get(eid)
            if not el:
                continue
            cands = sorted(el["states"], key=lambda fp: (len(ui.states[fp]["path"]), ui.states[fp]["order"]))
            st = ui.states[cands[0]]
            steps = [{k: a.get(k) for k in ("kind", "value", "sig", "occ", "eid", "name", "role")} for a in st["path"]]
            key = st["seed"] + "|" + "|".join(f"{s['kind']}:{s['sig']}:{s['occ']}" for s in steps) + "|" + eid
            fid = "FLOW-" + hashlib.sha1(key.encode()).hexdigest()[:10]
            f = flows.setdefault(fid, {"id": fid, "start_route": st["seed"], "steps": steps, "target_state": st["fp"],
                                       "target_eid": eid, "target_name": el["name"], "requirements": []})
            if lk["req_id"] not in f["requirements"]:
                f["requirements"].append(lk["req_id"])
    return sorted(flows.values(), key=lambda f: f["id"])


def verify_flows(cfg: Config, flows: list[dict]) -> tuple[list[dict], list[dict]]:
    ok, failed = [], []
    session = Session(cfg.app.url, cfg.crawl)
    try:
        for f in flows:
            err = None
            try:
                session.reset(f["start_route"])
            except ActionFailed as e:
                failed.append({**f, "error": f"start page did not load: {e}"})
                continue
            for step in f["steps"]:
                obs = session.observe()
                try:
                    if step["kind"] == "press":
                        session.press(step.get("value") or "Escape")
                        continue
                    el = session.find(obs, step["sig"], step["occ"])
                    if el is None:
                        err = f"step '{step['name']}' not found"
                        break
                    session.act(el, step["kind"], step.get("value"))
                except ActionFailed as e:
                    err = str(e)
                    break
            if err is None:
                obs = session.observe()
                if not any(e["eid"] == f["target_eid"] for e in obs["elements"]):
                    err = f"target '{f['target_name']}' not visible after replay"
            if err:
                failed.append({**f, "error": err})
            else:
                ok.append({**f, "replay_verified": True})
    finally:
        session.close()
    return ok, failed


def name_flows(flows: list[dict], reqs: dict[str, dict], llm: LLM, per_call: int) -> None:
    for i in range(0, len(flows), per_call):
        chunk = flows[i:i + per_call]
        lines = []
        for f in chunk:
            steps = " > ".join(s["name"] for s in f["steps"]) or "(nothing, it is on the start screen)"
            goal = "; ".join(reqs[r]["statement"] for r in f["requirements"] if r in reqs)
            lines.append(f"<flow id=\"{f['id']}\">start: {f['start_route']}\nsteps: {steps}\n"
                         f"then look at: {f['target_name']}\ngoal: {goal}\n</flow>")
        res = llm.json(f"flownames:{i // per_call}", "Name these flows.\n\n" + "\n".join(lines), FlowNames, system=SYSTEM)
        names = {x.id: x.name.strip() for x in res.flows}
        for f in chunk:
            if f["id"] not in names:
                raise RuntimeError(f"flow namer skipped {f['id']}")
            f["name"] = names[f["id"]]


def run_flows(cfg: Config, run: RunStore, llm: LLM) -> dict:
    planned = plan_flows(run)
    ok, failed = verify_flows(cfg, planned)
    reqs = {r["id"]: r for r in run.read_jsonl("ingest/requirements.jsonl")}
    if ok:
        name_flows(ok, reqs, llm, cfg.llm.flow_names_per_call)
    run.write_jsonl("link/flows.jsonl", ok)
    run.write_jsonl("link/flows_failed.jsonl", failed)
    return {"planned": len(planned), "verified": len(ok), "failed_replay": len(failed)}
