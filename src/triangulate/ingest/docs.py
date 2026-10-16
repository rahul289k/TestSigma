"""Deterministic MDX parsing: page -> cleaned text -> heading sections with stable ids.

The cleaned section text is what the LLM sees AND what quotes are verified against, so a
verified quote is guaranteed to be a real span of the published docs (modulo markup).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from markdown_it import MarkdownIt

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_MDX_COMMENT = re.compile(r"\{/\*.*?\*/\}", re.S)
_IMPORT = re.compile(r"^(import|export)\s.+$", re.M)
_FENCE = re.compile(r"^```.*?^```[ \t]*$", re.S | re.M)
_SELF_CLOSING = re.compile(r"<[A-Za-z][^<>]*?/>", re.S)
_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9.]*(\s[^<>]*)?>", re.S)
_IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_MD = re.compile(r"\[([^\]]+)\]\([^)]*\)")


@dataclass
class Section:
    id: str
    page: str
    page_title: str
    heading: str
    heading_path: list[str]
    anchor: str
    url: str
    text: str


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)


def clean_mdx(src: str) -> tuple[dict, str]:
    meta: dict = {}
    m = _FRONTMATTER.match(src)
    if m:
        meta = yaml.safe_load(m.group(1)) or {}
        src = src[m.end():]
    src = _MDX_COMMENT.sub("", src)
    src = _IMPORT.sub("", src)
    src = _FENCE.sub("", src)
    src = _IMG_MD.sub("", src)
    src = _SELF_CLOSING.sub("", src)
    src = _TAG.sub("", src)
    src = _LINK_MD.sub(r"\1", src)
    # MDX components (<Tabs>, <Accordion>, ...) indent their content; once the tags are gone that
    # indentation would turn headings and prose into Markdown code blocks, so drop it.
    src = re.sub(r"^[ \t]+", "", src, flags=re.M)
    src = re.sub(r"[ \t]+\n", "\n", src)
    src = re.sub(r"\n{3,}", "\n\n", src)
    return meta, src.strip() + "\n"


def normalize(text: str) -> str:
    """Normalisation used for quote verification: markup and whitespace insensitive, case sensitive."""
    t = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    t = t.replace(" ", " ")
    t = re.sub(r'[*_`"]', "", t)
    t = re.sub(r"^\s*(\d+\.|[-+])\s+", " ", t, flags=re.M)
    t = t.replace("|", " ")
    return re.sub(r"\s+", " ", t).strip()


def split_sections(page: str, title: str, cleaned: str, base_url: str) -> list[Section]:
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(cleaned)
    lines = cleaned.splitlines()
    heads: list[tuple[int, int, str]] = []  # (line index, level, text)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.map:
            level = int(tok.tag[1])
            heads.append((tok.map[0], level, tokens[i + 1].content.strip()))
    sections: list[Section] = []
    starts = [(0, 1, title)] + heads
    stack: list[tuple[int, str]] = []
    page_url = f"{base_url.rstrip('/')}/documentation/features/{page}"
    for idx, (line, level, text) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body_start = line + 1 if idx > 0 else 0
        body = "\n".join(lines[body_start:end]).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        if not body:
            continue
        anchor = slugify(text) if idx > 0 else ""
        sid = f"{page}#{anchor or 'intro'}"
        if any(s.id == sid for s in sections):
            sid = f"{sid}-{idx}"
        sections.append(Section(
            id=sid, page=page, page_title=title, heading=text, heading_path=[h for _, h in stack],
            anchor=anchor, url=page_url + (f"#{anchor}" if anchor else ""), text=body,
        ))
    return sections


def load_pages(docs_root: Path, include: list[str], base_url: str) -> list[Section]:
    files = sorted(p for p in docs_root.rglob("*.mdx")
                   if any(fnmatch.fnmatch(str(p.relative_to(docs_root)), pat) for pat in include))
    if not files:
        raise FileNotFoundError(f"No docs matched {include} under {docs_root}")
    out: list[Section] = []
    for f in files:
        meta, cleaned = clean_mdx(f.read_text())
        page = f.stem
        title = str(meta.get("title") or meta.get("sidebarTitle") or page)
        out.extend(split_sections(page, title, cleaned, base_url))
    return out


def section_dicts(sections: list[Section]) -> list[dict]:
    return [asdict(s) for s in sections]
