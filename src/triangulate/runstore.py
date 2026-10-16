"""Run directory: every stage writes its output here before anything touches Neo4j.

Layout: runs/<run_id>/<stage>/... plus manifest.json. JSON is written with sorted keys so
that identical content always produces identical bytes, which makes stage hashes a
reliable way to compare runs.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import RUNS_DIR


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1)


class RunStore:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.id = run_dir.name

    # ---- creation / lookup -------------------------------------------------------
    @classmethod
    def create(cls, profile: str) -> "RunStore":
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = RUNS_DIR / f"{stamp}-{profile}"
        run_dir.mkdir(parents=False, exist_ok=False)
        latest = RUNS_DIR / "latest"
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        os.symlink(run_dir.name, latest)
        return cls(run_dir)

    @classmethod
    def open(cls, run: str = "latest") -> "RunStore":
        path = RUNS_DIR / run
        if not path.exists():
            raise FileNotFoundError(f"Run '{run}' not found under {RUNS_DIR}")
        return cls(path.resolve())

    # ---- io ----------------------------------------------------------------------
    def path(self, rel: str) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def exists(self, rel: str) -> bool:
        return (self.dir / rel).exists()

    def write_json(self, rel: str, obj: Any) -> Path:
        p = self.path(rel)
        p.write_text(dumps(obj) + "\n")
        return p

    def read_json(self, rel: str) -> Any:
        p = self.dir / rel
        if not p.exists():
            raise FileNotFoundError(f"{rel} is missing in run {self.id}. Run the stage that produces it first.")
        return json.loads(p.read_text())

    def write_jsonl(self, rel: str, rows: Iterable[dict]) -> Path:
        p = self.path(rel)
        with p.open("w") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
        return p

    def append_jsonl(self, rel: str, row: dict) -> None:
        with self.path(rel).open("a") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")

    def read_jsonl(self, rel: str) -> list[dict]:
        p = self.dir / rel
        if not p.exists():
            raise FileNotFoundError(f"{rel} is missing in run {self.id}. Run the stage that produces it first.")
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    # ---- manifest + hashes ---------------------------------------------------------
    @property
    def manifest(self) -> dict:
        return self.read_json("manifest.json")

    def update_manifest(self, **fields: Any) -> None:
        m = self.read_json("manifest.json") if self.exists("manifest.json") else {}
        m.update(fields)
        self.write_json("manifest.json", m)

    def stage_hash(self, stage: str) -> str | None:
        """sha256 over the stage's content outputs. Excluded, because they are presentation rather than content:
        images (pixel output is not deterministic), element geometry (`bbox`: an app banner such as "You're using
        Hoppscotch offline" shifts every control down without changing any of them) and meta.json (telemetry such
        as timings)."""
        base = self.dir / stage
        if not base.exists():
            return None
        h = hashlib.sha256()
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in {".json", ".jsonl"} and p.name != "meta.json":
                h.update(str(p.relative_to(base)).encode())
                h.update(_without_geometry(p))
        return h.hexdigest()[:16]


def _without_geometry(path: Path) -> bytes:
    """A file's bytes with every "bbox" field removed (files without one are hashed unchanged)."""
    raw = path.read_bytes()
    if b'"bbox"' not in raw:
        return raw

    def strip(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if k != "bbox"}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v
    if path.suffix == ".jsonl":
        return b"\n".join(json.dumps(strip(json.loads(line)), sort_keys=True).encode() for line in raw.splitlines() if line.strip())
    return json.dumps(strip(json.loads(raw)), sort_keys=True).encode()
