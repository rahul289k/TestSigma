"""Map a PR diff onto the code graph: which components (and which of their blocks), which TS
symbols, which i18n keys. Deterministic; uses the same Node extractor as Stage 3 on the base and
head versions of each changed file."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..code.graph import flatten_i18n, run_extractor
from ..config import Config
from .github import GitHub, write_tree

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)

PACKAGE_KIND = {
    "hoppscotch-cli": "CLI (not part of the web UI)",
    "hoppscotch-backend": "backend server (not part of the web UI)",
    "hoppscotch-desktop": "desktop app shell (not part of the web UI)",
    "hoppscotch-sh-admin": "self-host admin dashboard (not crawled)",
    "hoppscotch-agent": "local agent (not part of the web UI)",
    "hoppscotch-selfhost-web": "self-host web shell",
    "hoppscotch-data": "shared data library",
    "hoppscotch-js-sandbox": "script sandbox library",
    "hoppscotch-kernel": "platform kernel library",
}


# Lines that do not change what a module's exports do: imports, comments and blank lines.
NON_BEHAVIOURAL = re.compile(r"^\s*(import\b|export\s+(\*|\{[^}]*\})\s+from\b|//|/\*|\*|<!--|$)")


def parse_hunks(patch: str | None, behavioural_only: bool = False) -> list[tuple[int, int]]:
    """Base-file lines actually changed: deleted/replaced lines, plus the line after which an insertion
    lands. Context lines in the hunk are ignored, so an edit next to a block boundary is not
    attributed to the neighbouring block."""
    lines: list[int] = []
    old: int | None = None
    for line in (patch or "").splitlines():
        m = HUNK.match(line)
        if m:
            start, length = int(m.group(1)), int(m.group(2) or 1)
            # For a zero-length hunk git gives the line AFTER which the insertion lands.
            old = start + 1 if length == 0 else start
            continue
        if old is None or line.startswith("\\"):
            continue
        if line.startswith("-"):
            if not (behavioural_only and NON_BEHAVIOURAL.match(line[1:])):
                lines.append(old)
            old += 1
        elif line.startswith("+"):
            if not (behavioural_only and NON_BEHAVIOURAL.match(line[1:])):
                lines.append(max(old - 1, 1))
        else:
            old += 1
    spans: list[tuple[int, int]] = []
    for n in sorted(set(lines)):
        if spans and n == spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], n)
        else:
            spans.append((n, n))
    return spans


def overlaps(span: tuple[int, int], rng: list[int] | None) -> bool:
    return bool(rng) and not (span[1] < rng[0] or span[0] > rng[1])


def classify_vue(spans: list[tuple[int, int]], blocks: dict) -> list[str]:
    kinds = set()
    script = blocks.get("scriptSetup") or blocks.get("script")
    for sp in spans:
        hit = False
        if overlaps(sp, blocks.get("template")):
            kinds.add("template")
            hit = True
        if overlaps(sp, script) or overlaps(sp, blocks.get("script")):
            kinds.add("script")
            hit = True
        for st in blocks.get("styles", []):
            if overlaps(sp, st):
                kinds.add("style")
                hit = True
        if not hit:
            kinds.add("whole_file")
    return sorted(kinds)


def map_changes(cfg: Config, gh: GitHub, pr: dict, files: list[dict], tag_root: Path, work: Path) -> dict:
    root = cfg.code.common_root.rstrip("/") + "/"
    base_sha, head_sha = pr["base"]["sha"], pr["head"]["sha"]
    changes: list[dict] = []
    outside: list[dict] = []
    base_files, head_files = {}, {}
    for f in files:
        path, status = f["filename"], f["status"]
        prev = f.get("previous_filename") or path
        if not path.startswith(root) and not prev.startswith(root):
            pkg = path.split("/")[1] if path.startswith("packages/") else "(repo root)"
            outside.append({"file": path, "status": status, "why": PACKAGE_KIND.get(pkg, "outside the web app package")})
            continue
        rel = path[len(root):]
        rel_prev = prev[len(root):]
        base = gh.file_at(base_sha, prev) if status != "added" else None
        head = gh.file_at(head_sha, path) if status != "removed" else None
        tag_file = tag_root / root / rel_prev
        tag_text = tag_file.read_text() if tag_file.exists() else None
        drift = base is not None and tag_text != base
        rec = {"file": rel, "previous": rel_prev if rel_prev != rel else None, "status": status,
               "spans": parse_hunks(f.get("patch"), behavioural_only=True),
               "spans_all": parse_hunks(f.get("patch")), "drift": drift, "patch_missing": f.get("patch") is None,
               "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
        if base is not None:
            base_files[rel_prev] = base
        if head is not None:
            head_files[rel] = head
        changes.append(rec)

    # Re-extract the changed .vue/.ts files at base and head with the real compilers.
    def extract(texts: dict[str, str], name: str) -> dict:
        d = work / name
        if d.exists():
            shutil.rmtree(d)
        code_files = {k: v for k, v in texts.items() if k.endswith((".vue", ".ts"))}
        if not code_files:
            return {"components": [], "tsFiles": []}
        write_tree(d, code_files)
        return run_extractor(d, cfg.code.aliases, work / f"{name}.json", files=sorted(code_files))

    base_x = extract(base_files, "base")
    head_x = extract(head_files, "head")
    base_by = {x["file"]: x for x in base_x["components"] + base_x["tsFiles"]}
    head_by = {x["file"]: x for x in head_x["components"] + head_x["tsFiles"]}

    locale = cfg.code.locale_file
    for rec in changes:
        f, prev = rec["file"], rec["previous"] or rec["file"]
        if f.endswith(".vue"):
            b, h = base_by.get(prev), head_by.get(f)
            rec["type"] = "component"
            rec["kinds"] = ["deleted"] if rec["status"] == "removed" else ["added"] if rec["status"] == "added" \
                else (classify_vue(rec["spans"], b["blocks"]) or ["whole_file"]) if b else ["whole_file"]
            rec["keys_removed"] = sorted(set(b["keys"]) - set(h["keys"])) if b and h else (sorted(b["keys"]) if b and not h else [])
            rec["keys_added"] = sorted(set(h["keys"]) - set(b["keys"])) if b and h else []
        elif f.endswith(".ts"):
            b = base_by.get(prev)
            rec["type"] = "ts"
            syms = []
            if b and rec["status"] not in ("removed", "added"):
                for s in b["symbols"]:
                    if any(overlaps(sp, [s["start"], s["end"]]) for sp in rec["spans"]):
                        syms.append(s["name"].split(".")[0])
                inside = any(any(overlaps(sp, [s["start"], s["end"]]) for s in b["symbols"]) for sp in rec["spans"])
                outside_any = any(not any(overlaps(sp, [s["start"], s["end"]]) for s in b["symbols"]) for sp in rec["spans"])
                rec["kinds"] = (["symbol"] if inside else []) + (["whole_file"] if outside_any else [])
                if not rec["spans"]:
                    rec["kinds"] = ["whole_file"] if rec["patch_missing"] else ["imports_or_comments_only"]
            else:
                rec["kinds"] = ["deleted"] if rec["status"] == "removed" else ["added"]
            rec["symbols"] = sorted(set(syms))
        elif f == locale:
            rec["type"] = "i18n"
            b = flatten_i18n(json.loads(base_files[prev])) if prev in base_files else {}
            h = flatten_i18n(json.loads(head_files[f])) if f in head_files else {}
            rec["keys_removed"] = sorted(set(b) - set(h))
            rec["keys_changed"] = sorted(k for k in set(b) & set(h) if b[k] != h[k])
            rec["keys_added"] = sorted(set(h) - set(b))
            rec["kinds"] = ["i18n"]
        else:
            rec["type"] = "other"
            rec["kinds"] = ["asset_or_test" if ("__tests__" in f or f.endswith((".spec.ts", ".test.ts"))) else "other"]
        # Test files never reach users.
        if "__tests__" in f or re.search(r"\.(spec|test)\.ts$", f):
            rec["type"] = "test"
    return {"base_sha": base_sha, "head_sha": head_sha, "changes": changes, "outside": outside}
