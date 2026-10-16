"""Stage 1: docs -> atomic, testable requirements.

The LLM proposes requirements; code verifies them. A requirement is kept only if its quote is a
verbatim span of the section it cites (after markup/whitespace normalisation). Rejections are
logged with the reason and never repaired.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, conlist, create_model

from ..config import Config
from ..llm.client import LLM
from ..runstore import RunStore
from .docs import load_pages, normalize, section_dicts

Testability = Literal["UI_OBSERVABLE", "NEEDS_LOGIN", "DESKTOP_ONLY", "NOT_WEB_UI"]


class ExtractedRequirement(BaseModel):
    section_id: str = Field(description="The id of the section the quote comes from, exactly as given")
    statement: str = Field(description="One atomic capability, e.g. 'User can duplicate an environment'. Max 20 words.")
    quote: str = Field(description="Verbatim contiguous text copied from that section (5-40 words) that states it")
    testability: Testability
    testability_reason: str = Field(description="Short reason for the testability label")
    ui_cues: list[str] = Field(description="1-6 short words or labels a user would see on screen for this capability")


class ExtractionBatch(BaseModel):
    requirements: list[ExtractedRequirement]


SYSTEM = """You turn product documentation into atomic, testable product requirements for a QA team.
Rules:
- One requirement = one capability or behaviour a user could verify. Skip definitions, marketing, navigation hints
  and links to other pages. Merge sentences that restate the same capability. Aim for 1-4 requirements per section.
- statement: plain English, starts with "User can" or "The app", max 20 words.
- quote: copy a contiguous span of 5-40 words from the cited section EXACTLY, character for character (you may drop
  markdown symbols like ** or `). Never paraphrase inside quote.
- testability:
  UI_OBSERVABLE = visible or operable in the web app (hoppscotch.io) WITHOUT signing in. Signed-out users already get
    personal collections, global and personal environments, history, settings, scripts, and REST/GraphQL/realtime clients.
  NEEDS_LOGIN = only when the text involves teams, shared or workspace members, roles (owner/editor/viewer), cloud sync,
    share links, access tokens, admin settings or an account.
  DESKTOP_ONLY = only in the desktop app.
  NOT_WEB_UI = CLI, self-hosting, backend/API behaviour, or a non-functional property.
- For API reference listings (many functions of one namespace), write ONE requirement per namespace, not one per function.
- ui_cues: words a tester would look for on screen (button, tab or menu labels), not explanations."""


def batch_model(sections: list[dict]) -> tuple[type[BaseModel], dict[str, str]]:
    """Schema for one batch: one REQUIRED field per page, each needing at least one requirement whose
    section_id is an enum of that page's sections. A model cannot skip a page or cite a wrong section."""
    fields, names = {}, {}
    for page in sorted({x["page"] for x in sections}):
        sec_ids = tuple(x["id"] for x in sections if x["page"] == page)
        item = create_model(f"Req_{len(fields)}", section_id=(Literal[sec_ids], ...),
                            statement=(str, Field(description="One atomic capability, max 20 words")),
                            quote=(str, Field(description="Verbatim span (5-40 words) from that section")),
                            testability=(Testability, ...), testability_reason=(str, ...),
                            ui_cues=(list[str], Field(description="1-6 on-screen words or labels")))
        fname = "page_" + "".join(ch if ch.isalnum() else "_" for ch in page)
        names[fname] = page
        fields[fname] = (conlist(item, min_length=1), Field(description=f"Requirements from page '{page}'"))
    return create_model("PagesBatch", **fields), names


def _prompt(sections: list[dict]) -> str:
    parts = ["Extract requirements from these documentation sections. Cite each by its section id.\n"
             "Every page must get its own list of requirements.\n"]
    for s in sections:
        parts.append(f"<section id=\"{s['id']}\" title=\"{' > '.join(s['heading_path'])}\">\n{s['text']}\n</section>\n")
    return "\n".join(parts)


def requirement_id(page: str, statement: str, quote: str) -> str:
    h = hashlib.sha1(f"{normalize(quote)}|{normalize(statement).lower()}".encode()).hexdigest()[:8]
    return f"REQ-{page}-{h}"


def verify(reqs: list[ExtractedRequirement], sections_by_id: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    kept: dict[str, dict] = {}
    rejected: list[dict] = []
    for r in reqs:
        sec = sections_by_id.get(r.section_id)
        if sec is None:
            rejected.append({**r.model_dump(), "reason": "unknown section_id"})
            continue
        q = normalize(r.quote)
        if len(q.split()) < 3:
            rejected.append({**r.model_dump(), "reason": "quote too short"})
            continue
        if q not in normalize(sec["text"]):
            rejected.append({**r.model_dump(), "reason": "quote not found verbatim in section"})
            continue
        rid = requirement_id(sec["page"], r.statement, r.quote)
        if rid in kept:
            continue
        stmt_key = normalize(r.statement).lower()
        if any(normalize(k["statement"]).lower() == stmt_key and k["page"] == sec["page"] for k in kept.values()):
            rejected.append({**r.model_dump(), "reason": "duplicate statement on same page"})
            continue
        kept[rid] = {
            "id": rid, "page": sec["page"], "feature": sec["page_title"], "section_id": sec["id"],
            "heading_path": sec["heading_path"], "url": sec["url"], "statement": r.statement.strip(),
            "quote": r.quote.strip(), "testability": r.testability, "testability_reason": r.testability_reason,
            "ui_cues": sorted({c.strip() for c in r.ui_cues if c.strip()}),
        }
    return sorted(kept.values(), key=lambda x: x["id"]), rejected


def run_ingest(cfg: Config, run: RunStore, llm: LLM) -> dict:
    docs_root = Path(run.manifest["docs"]["path"])
    sections = section_dicts(load_pages(docs_root, cfg.docs.include, cfg.docs.base_url))
    run.write_jsonl("ingest/sections.jsonl", sections)
    by_id = {s["id"]: s for s in sections}
    pages = sorted({s["page"] for s in sections})
    extracted: list[ExtractedRequirement] = []
    step = max(1, cfg.llm.ingest_pages_per_call)
    system = SYSTEM + ("\nKnown sign-in-only features of this app (label them NEEDS_LOGIN): "
                       + "; ".join(cfg.docs.login_hints) if cfg.docs.login_hints else "")
    for i in range(0, len(pages), step):
        group = pages[i:i + step]
        secs = [s for s in sections if s["page"] in group]
        model, _ = batch_model(secs)
        batch = llm.json(f"ingest:{','.join(group)}", _prompt(secs), model, system=system)
        for fname in batch.model_fields:
            for r in getattr(batch, fname):
                extracted.append(ExtractedRequirement(**r.model_dump()))
    kept, rejected = verify(extracted, by_id)
    run.write_jsonl("ingest/requirements.jsonl", kept)
    run.write_jsonl("ingest/rejected.jsonl", rejected)
    by_t: dict[str, int] = {}
    for r in kept:
        by_t[r["testability"]] = by_t.get(r["testability"], 0) + 1
    return {"pages": len(pages), "sections": len(sections), "proposed": len(extracted),
            "kept": len(kept), "rejected": len(rejected), "by_testability": by_t}
