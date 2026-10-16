"""Plain-English report text (LLM), constrained by a validator.

The writer sees only the computed impact set - never the repository - so it cannot add risk that
the graph did not find. The validator then checks: every cited id exists, every High flow and every
requirement that loses coverage is mentioned, and no code jargon appears in reader-facing text.
One corrective retry (same model, error list attached) is allowed; after that the run fails.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, create_model

from ..llm.client import LLM

SYSTEM = """You write pull-request risk briefs for a QA lead who knows the product (Hoppscotch, an API client)
but not its code. Use plain product language: screens, buttons, tabs, what a user does.
Never mention file names, code identifiers, components, functions, keys, or programming terms.
Only talk about items given to you, and cite them by their ids exactly as given."""


class RetestItem(BaseModel):
    flow_id: str
    why: str = Field(description="One sentence: what could break for the user and why we think so")


class Note(BaseModel):
    id: str
    note: str


class Question(BaseModel):
    about: list[str]
    question: str


class ReportText(BaseModel):
    headline: str = Field(description="One sentence summary of what this change puts at risk")
    what_changed: str = Field(description="2-4 sentences in product language describing the change")
    retest: list[RetestItem]
    screen_notes: list[Note]
    requirement_notes: list[Note]
    questions: list[Question]


JARGON = re.compile(r"(\.vue\b|\.ts\b|\.json\b|src/|\bcomponents?/|(?-i:[a-z]+[A-Z][a-zA-Z]+\()|\b[a-z]+_[a-z_]+\b|\(\)|"
                    r"\bcomponent\b|\bi18n\b|\btranslation key\b|\bcomposable\b|\brefactor)", re.I)
# Not "function": it is also an English verb ("actions function correctly"); calls are caught as name( and ().
# Code identifiers such as CollectionsDocumentationResponse (case-sensitive: three or more capitalised humps).
IDENTIFIER = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+){2,}\b")


def build_prompt(pr: dict, impact: dict) -> str:
    lines = [f"PULL REQUEST: {pr['title']}", f"DESCRIPTION (author's words, may be technical):\n{(pr.get('body') or '')[:1500]}", ""]
    lines.append("CHANGE AREAS (plain): " + ("; ".join(sorted({c['area'] for c in impact['change_areas']})) or "none"))
    lines.append(f"OVERALL RISK (already computed): {impact['summary']['overall_risk']}")
    lines.append("\nFLOWS TO RETEST (id | risk | name | steps):" + ("" if impact["flows"] else " none"))
    for f in impact["flows"]:
        lines.append(f"  {f['id']} | {f['band']} | {f['name']} | {' > '.join(f['steps']) or 'start screen'}")
    lines.append("\nSCREENS AT RISK (id | risk | screen | controls at risk):" + ("" if impact["screens"] else " none"))
    for s in impact["screens"][:12]:
        lines.append(f"  {s['fp']} | {s['band']} | {s['label']} | {', '.join(e['label'] for e in s['elements'][:6])}")
    lines.append("\nREQUIREMENTS (id | status | statement):" + ("" if impact["requirements"] else " none"))
    for q in impact["requirements"]:
        lines.append(f"  {q['id']} | {q['status']} | {q['statement']}")
    lines.append("\nCHANGES WE COULD NOT CONNECT TO A SCREEN (plain reason):")
    for u in impact["unmapped"] + impact["outside"]:
        lines.append(f"  - {u['why']}")
    lines.append("\nOPEN QUESTIONS FOR A HUMAN (id | question):")
    for h in impact["needs_human"]:
        lines.append(f"  {h['id']} | {h['question']}")
    lines.append("\nWrite the brief. Include every High flow in retest. Add a requirement note for every requirement listed. "
                 "Turn each open question into a plain question (cite its id in 'about').")
    return "\n".join(lines)


def validate(text: ReportText, impact: dict) -> list[str]:
    errs = []
    flow_ids = {f["id"] for f in impact["flows"]}
    state_ids = {s["fp"] for s in impact["screens"]}
    req_ids = {q["id"] for q in impact["requirements"]}
    human_ids = {h["id"] for h in impact["needs_human"]}
    for r in text.retest:
        if r.flow_id not in flow_ids:
            errs.append(f"retest cites unknown flow {r.flow_id}")
    for n in text.screen_notes:
        if n.id not in state_ids:
            errs.append(f"screen note cites unknown screen {n.id}")
    for n in text.requirement_notes:
        if n.id not in req_ids:
            errs.append(f"requirement note cites unknown requirement {n.id}")
    for q in text.questions:
        for a in q.about:
            if a not in human_ids:
                errs.append(f"question cites unknown item {a}")
    missing_high = {f["id"] for f in impact["flows"] if f["band"] == "High"} - {r.flow_id for r in text.retest}
    if missing_high:
        errs.append(f"High flows missing from retest: {sorted(missing_high)}")
    missing_req = req_ids - {n.id for n in text.requirement_notes}
    if missing_req:
        errs.append(f"requirements without a note: {sorted(missing_req)}")
    missing_q = human_ids - {a for q in text.questions for a in q.about}
    if missing_q:
        errs.append(f"open questions not asked: {sorted(missing_q)}")
    reader_text = [text.headline, text.what_changed] + [r.why for r in text.retest] + [n.note for n in text.screen_notes] \
        + [n.note for n in text.requirement_notes] + [q.question for q in text.questions]
    for t in reader_text:
        m = JARGON.search(t) or IDENTIFIER.search(t)
        if m:
            errs.append(f"code jargon '{m.group(0)}' in: {t[:80]}")
    return errs


def response_model(impact: dict) -> type[BaseModel]:
    """Schema for THIS report: every id field is an enum of the ids that exist, and a section with nothing
    in it is not in the schema at all, so the model cannot cite an item the graph did not produce."""
    fields: dict = {"headline": (str, Field(description="One sentence summary of what this change puts at risk")),
                    "what_changed": (str, Field(description="2-4 sentences in product language describing the change"))}
    flows = [f["id"] for f in impact["flows"]]
    screens = [s["fp"] for s in impact["screens"][:12]]
    reqs = [q["id"] for q in impact["requirements"]]
    humans = [h["id"] for h in impact["needs_human"]]
    if flows:
        item = create_model("Retest", flow_id=(Literal[tuple(flows)], ...),
                            why=(str, Field(description="One sentence: what could break for the user and why")))
        fields["retest"] = (list[item], ...)
    if screens:
        fields["screen_notes"] = (list[create_model("ScreenNote", id=(Literal[tuple(screens)], ...), note=(str, ...))], ...)
    if reqs:
        fields["requirement_notes"] = (list[create_model("ReqNote", id=(Literal[tuple(reqs)], ...), note=(str, ...))], ...)
    if humans:
        fields["questions"] = (list[create_model("Ask", about=(list[Literal[tuple(humans)]], ...), question=(str, ...))], ...)
    return create_model("ReportBrief", **fields)


def _to_text(obj: BaseModel) -> ReportText:
    d = obj.model_dump()
    return ReportText(headline=d["headline"], what_changed=d["what_changed"],
                      retest=[RetestItem(**x) for x in d.get("retest", [])],
                      screen_notes=[Note(**x) for x in d.get("screen_notes", [])],
                      requirement_notes=[Note(**x) for x in d.get("requirement_notes", [])],
                      questions=[Question(**x) for x in d.get("questions", [])])


def write_report_text(llm: LLM, pr: dict, impact: dict, task: str) -> tuple[ReportText, list[str]]:
    prompt = build_prompt(pr, impact)
    model = response_model(impact)
    text = _to_text(llm.json(task, prompt, model, system=SYSTEM))
    errs = validate(text, impact)
    if not errs:
        return text, []
    retry = prompt + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED FOR THESE REASONS - fix all of them:\n- " + "\n- ".join(errs)
    text = _to_text(llm.json(task + ":fix", retry, model, system=SYSTEM))
    errs2 = validate(text, impact)
    if errs2:
        raise RuntimeError(f"[{task}] report text failed validation twice: {errs2}")
    return text, errs
