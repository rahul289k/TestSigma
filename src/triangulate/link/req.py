"""Stage 4c/4d: requirement -> UI elements and requirement -> code, via retrieval + an LLM judge.

Retrieval is deterministic (Neo4j full-text / Lucene BM25). The judge (Gemini, batched) only
chooses among the retrieved candidates, by short ids that code maps back and validates. A
citation outside the candidate list is dropped and becomes a review item; it is never "fixed".
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..config import Config
from ..graph.db import Graph, lucene_query
from ..llm.client import LLM
from ..runstore import RunStore

STOP = set("""a an the and or of to in on for with from by at as is are be can you your user users app hoppscotch
this that these those it its into via using use used allows allow lets let any all each also when which will
their there then than more other such them they we our not no yes""".split())

SYSTEM = """You verify whether documented product requirements are implemented, for a QA team.
For each requirement you get candidate UI elements observed in the live web app (ids U1, U2, ...) and candidate
code components from its repository (ids C1, C2, ...).
UI verdict:
- COVERED: one or more listed UI elements clearly let a user perform or see this capability.
- PARTIAL: listed elements show part of it (e.g. the entry point exists but not the full behaviour).
- NOT_FOUND: none of the listed elements implement it. Do not stretch: a generic button is not evidence.
Code verdict:
- PRESENT: a listed component clearly implements this capability; ABSENT otherwise.
Cite only ids from that requirement's own lists. If a list is marked "not applicable", answer NOT_FOUND / ABSENT
for that part and cite nothing."""


class ReqJudgement(BaseModel):
    req_id: str
    ui_verdict: Literal["COVERED", "PARTIAL", "NOT_FOUND"]
    ui_ids: list[str] = Field(description="Ids like U3 from this requirement's UI candidates")
    ui_rationale: str
    code_verdict: Literal["PRESENT", "ABSENT"]
    code_ids: list[str] = Field(description="Ids like C2 from this requirement's code candidates")
    code_rationale: str


class JudgeBatch(BaseModel):
    judgements: list[ReqJudgement]


def query_terms(req: dict) -> list[str]:
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9-]+", req["statement"]) if w.lower() not in STOP and len(w) > 2]
    terms = list(dict.fromkeys([*req.get("ui_cues", []), *words]))
    return terms[:16]


def retrieve_ui(g: Graph, req: dict, k: int) -> list[dict]:
    """Top-k elements by Lucene BM25. Screen names are collected per element and ordered in Python:
    Cypher's collect() has no defined order, and prompt bytes must be identical across runs."""
    q = lucene_query(query_terms(req))
    if not q:
        return []
    rows = g.run(
        # Over-fetch, then cut in Python: which of several equally scored documents Lucene returns at the
        # k-th place depends on internal document ids that change on every reload.
        "CALL db.index.fulltext.queryNodes('elementText', $q, {limit: $limit}) YIELD node, score "
        "OPTIONAL MATCH (s:State)-[:CONTAINS]->(node) "
        "RETURN node.id AS eid, node.label AS label, node.role AS role, node.route AS route, node.tooltip AS tooltip, "
        "score, s.order AS sorder, s.label AS slabel", q=q, limit=max(200, k * 10))
    by: dict[str, dict] = {}
    for r in rows:
        e = by.setdefault(r["eid"], {"eid": r["eid"], "label": r["label"], "role": r["role"], "route": r["route"],
                                     "tooltip": r["tooltip"], "score": round(r["score"], 6), "_screens": set()})
        if r["slabel"] is not None:
            e["_screens"].add((r["sorder"], r["slabel"]))
    out = []
    for e in by.values():
        names: list[str] = []
        for _, label in sorted(e.pop("_screens")):
            if label not in names:
                names.append(label)
        e["screens"] = names[:3]
        out.append(e)
    return sorted(out, key=lambda e: (-e["score"], e["eid"]))[:k]


def retrieve_code(g: Graph, req: dict, k: int) -> list[dict]:
    """Components that use the best-matching i18n strings, scored by the summed match score (aggregated
    in Python, in a fixed order, so float rounding cannot reorder ties between runs)."""
    q = lucene_query(query_terms(req))
    if not q:
        return []
    rows = g.run(
        "CALL db.index.fulltext.queryNodes('keyText', $q, {limit: 400}) YIELD node, score "
        "RETURN node.key AS key, node.text AS text, score", q=q)
    # The 40 best strings, ties broken by key (not by Lucene's internal order), then their components.
    top = sorted(rows, key=lambda r: (-round(r["score"], 6), r["key"]))[:40]
    if not top:
        return []
    users = g.run("MATCH (c:Component)-[:USES_KEY]->(k:I18nKey) WHERE k.key IN $keys "
                  "RETURN c.id AS id, c.name AS name, k.key AS key", keys=[r["key"] for r in top])
    score_of = {r["key"]: (round(r["score"], 6), r["text"]) for r in top}
    rows = [{"id": u["id"], "name": u["name"], "key": u["key"], "text": score_of[u["key"]][1],
             "score": score_of[u["key"]][0]} for u in users]
    by: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (r["id"], r["key"])):
        c = by.setdefault(r["id"], {"id": r["id"], "name": r["name"], "score": 0.0, "_texts": []})
        c["score"] += round(r["score"], 6)
        c["_texts"].append((r["key"], r["text"]))
    out = []
    for c in by.values():
        c["texts"] = [t for _, t in sorted(c.pop("_texts"))][:4]
        c["score"] = round(c["score"], 6)
        out.append(c)
    return sorted(out, key=lambda c: (-c["score"], c["id"]))[:k]


def _prompt(items: list[dict]) -> str:
    out = ["Judge each requirement.\n"]
    for it in items:
        r = it["req"]
        out.append(f"<requirement id=\"{r['id']}\">\nstatement: {r['statement']}\ndoc quote: \"{r['quote']}\"\n"
                   f"testable in web UI without login: {'yes' if r['testability'] == 'UI_OBSERVABLE' else 'no'}")
        if it["ui"] is None:
            out.append("UI candidates: not applicable")
        else:
            out.append("UI candidates:" + ("" if it["ui"] else " none"))
            for i, u in enumerate(it["ui"]):
                tip = f" (tooltip: {u['tooltip']})" if u.get("tooltip") and u["tooltip"] != u["label"] else ""
                out.append(f"  U{i + 1}: [{u['role']}] '{u['label']}'{tip} on {u['route']}; screens: {', '.join(s for s in u['screens'] if s)}")
        out.append("Code candidates:" + ("" if it["code"] else " none"))
        for i, c in enumerate(it["code"]):
            out.append(f"  C{i + 1}: {c['name']} ({c['id']}); shows: {', '.join(repr(t) for t in c['texts'])}")
        out.append("</requirement>\n")
    return "\n".join(out)


def judge(cfg: Config, run: RunStore, llm: LLM, g: Graph, reqs: list[dict], task: str) -> tuple[list[dict], list[dict]]:
    k = cfg.link.retrieval_top_k
    items = []
    for r in reqs:
        if r["testability"] == "NOT_WEB_UI":
            continue
        ui = retrieve_ui(g, r, k) if r["testability"] == "UI_OBSERVABLE" else None
        items.append({"req": r, "ui": ui, "code": retrieve_code(g, r, k)})
    results, review = [], []
    per = max(1, cfg.llm.judge_requirements_per_call)
    votes = max(1, cfg.llm.judge_votes)
    for i in range(0, len(items), per):
        chunk = items[i:i + per]
        prompt = _prompt(chunk)
        ballots: list[dict[str, ReqJudgement]] = []
        for v in range(votes):
            res = llm.json(f"{task}:{i // per}:v{v}", prompt, JudgeBatch, system=SYSTEM,
                           temperature=None if votes == 1 else 0.6, seed=None if votes == 1 else cfg.llm.seed + v)
            ballots.append({j.req_id: j for j in res.judgements})
        for it in chunk:
            r = it["req"]
            js = [b[r["id"]] for b in ballots if r["id"] in b]
            if not js:
                raise RuntimeError(f"[{task}] judge returned no verdict for {r['id']}")
            verdicts = [j.ui_verdict for j in js]
            top = max(sorted(set(verdicts)), key=verdicts.count)
            agree = verdicts.count(top) / len(js)
            j = next(x for x in js if x.ui_verdict == top)
            ui_map = {f"U{n + 1}": u["eid"] for n, u in enumerate(it["ui"] or [])}
            code_map = {f"C{n + 1}": c["id"] for n, c in enumerate(it["code"])}
            bad = [x for x in j.ui_ids if x not in ui_map] + [x for x in j.code_ids if x not in code_map]
            ui_els = sorted({ui_map[x] for x in j.ui_ids if x in ui_map})
            code_cs = sorted({code_map[x] for x in j.code_ids if x in code_map}) if j.code_verdict == "PRESENT" else []
            ui_verdict = top
            if ui_verdict in ("COVERED", "PARTIAL") and not ui_els:
                review.append({"trigger": "judge_no_evidence", "req_id": r["id"],
                               "question": f"The judge said '{r['statement']}' is {ui_verdict} but cited no valid element. Is it covered?",
                               "evidence": {"ui_ids": j.ui_ids}})
                ui_verdict = "NOT_FOUND"
            if bad:
                review.append({"trigger": "judge_invalid_citation", "req_id": r["id"],
                               "question": f"The judge cited ids that were not candidates for '{r['statement']}': {bad}",
                               "evidence": {"bad_ids": bad}})
            if votes > 1 and agree < 1.0:
                review.append({"trigger": "judge_split", "req_id": r["id"],
                               "question": f"Judges disagreed on whether '{r['statement']}' is in the UI ({verdicts}). Which is right?",
                               "evidence": {"verdicts": verdicts}})
            prior = cfg.link.method_priors["llm_judge"]
            results.append({
                "req_id": r["id"], "testability": r["testability"],
                "ui_verdict": ui_verdict if it["ui"] is not None else "NOT_APPLICABLE", "ui_elements": ui_els,
                "ui_rationale": j.ui_rationale, "ui_method": "llm_judge", "ui_votes": verdicts,
                "ui_confidence": round(prior * agree, 4), "ui_candidates": [u["eid"] for u in it["ui"] or []],
                "code_verdict": j.code_verdict if code_cs else "ABSENT", "code_components": code_cs,
                "code_rationale": j.code_rationale, "code_confidence": prior,
                "code_candidates": [c["id"] for c in it["code"]],
            })
    return results, review


def run_req_links(cfg: Config, run: RunStore, llm: LLM, g: Graph, only: set[str] | None = None,
                  task: str = "judge") -> dict:
    reqs = [r for r in run.read_jsonl("ingest/requirements.jsonl") if only is None or r["id"] in only]
    results, review = judge(cfg, run, llm, g, reqs, task)
    if only is not None and run.exists("link/req_links.jsonl"):
        keep = [x for x in run.read_jsonl("link/req_links.jsonl") if x["req_id"] not in only]
        for x in results:
            x["ui_method"] = "probe+llm_judge"
        results = keep + results
        old_review = [x for x in run.read_jsonl("link/review_items_judge.jsonl") if x["req_id"] not in only] \
            if run.exists("link/review_items_judge.jsonl") else []
        review = old_review + review
    results.sort(key=lambda x: x["req_id"])
    run.write_jsonl("link/req_links.jsonl", results)
    run.write_jsonl("link/review_items_judge.jsonl", review)
    by: dict[str, int] = {}
    for x in results:
        by[x["ui_verdict"]] = by.get(x["ui_verdict"], 0) + 1
    return {"judged": len(results), "ui_verdicts": by,
            "code_present": sum(1 for x in results if x["code_verdict"] == "PRESENT"), "review_items": len(review)}
