"""Stage 0: pin every input to an exact version.

The code layer is only meaningful if it describes the build we crawl, so we read the release
version from the live JS bundle and check out exactly that git tag. The docs are pinned to a
commit SHA. Nothing here is "close enough": an unreadable version or a missing tag stops the run.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import DATA_DIR, Config
from .runstore import RunStore


class PinError(RuntimeError):
    pass


def read_live_version(app_url: str, pattern: str) -> str:
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "triangulate/0.1"}) as http:
        html = http.get(app_url).text
        scripts = re.findall(r'<script[^>]+type="module"[^>]+src="([^"]+)"', html)
        scripts += [s for s in re.findall(r'<script[^>]+src="([^"]+index[^"]*\.js)"', html) if s not in scripts]
        if not scripts:
            raise PinError(f"No module script found in {app_url}")
        versions: set[str] = set()
        for src in scripts:
            url = src if src.startswith("http") else app_url.rstrip("/") + "/" + src.lstrip("/")
            versions |= set(re.findall(pattern, http.get(url).text))
    if len(versions) != 1:
        raise PinError(f"Expected exactly one version string in the live bundle, found {sorted(versions) or 'none'}")
    return versions.pop()


def _git(*args: str, cwd: Path | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise PinError(f"git {' '.join(args)} failed: {res.stderr.strip()[:400]}")
    return res.stdout.strip()


def ls_remote(repo: str, ref: str) -> str:
    out = _git("ls-remote", f"https://github.com/{repo}.git", ref)
    lines = [ln.split("\t") for ln in out.splitlines() if ln.strip()]
    if not lines:
        raise PinError(f"Ref '{ref}' not found in {repo}")
    # Prefer the peeled tag commit (refs/tags/x^{}) when present.
    peeled = [sha for sha, name in lines if name.endswith("^{}")]
    return peeled[0] if peeled else lines[0][0]


def checkout_commit(repo: str, sha: str, dest: Path, sparse_paths: list[str] | None = None) -> Path:
    """Shallow, optionally sparse checkout of one exact commit. Re-used if already present."""
    if (dest / ".git").exists():
        if _git("rev-parse", "HEAD", cwd=dest) == sha:
            return dest
        raise PinError(f"{dest} exists at a different commit; delete it to re-pin")
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=dest)
    _git("remote", "add", "origin", f"https://github.com/{repo}.git", cwd=dest)
    if sparse_paths:
        _git("config", "core.sparseCheckout", "true", cwd=dest)
        info = dest / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "sparse-checkout").write_text("".join(f"/{p.strip('/')}{'/' if '.' not in p.split('/')[-1] else ''}\n"
                                                      for p in sparse_paths))
        _git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", sha, cwd=dest)
    else:
        _git("fetch", "-q", "--depth", "1", "origin", sha, cwd=dest)
    _git("checkout", "-q", "FETCH_HEAD", cwd=dest)
    got = _git("rev-parse", "HEAD", cwd=dest)
    if got != sha:
        raise PinError(f"Checked out {got}, expected {sha}")
    return dest


def run_pin(cfg: Config, run: RunStore) -> dict:
    version = read_live_version(cfg.app.url, cfg.app.version_pattern)
    code_sha = ls_remote(cfg.code.repo, f"refs/tags/{version}")
    code_dir = checkout_commit(cfg.code.repo, code_sha, DATA_DIR / "repos" / f"hoppscotch-{version}",
                               cfg.code.sparse_paths)
    docs_ref = cfg.docs.ref
    docs_sha = docs_ref if re.fullmatch(r"[0-9a-f]{40}", docs_ref) else ls_remote(cfg.docs.repo, f"refs/heads/{docs_ref}")
    docs_dir = checkout_commit(cfg.docs.repo, docs_sha, DATA_DIR / "repos" / f"docs-{docs_sha[:12]}")
    manifest = {
        "run_id": run.id,
        "profile": cfg.profile,
        "config_hash": cfg.hash(),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "app": {"url": cfg.app.url, "version": version},
        "code": {"repo": cfg.code.repo, "tag": version, "sha": code_sha, "path": str(code_dir)},
        "docs": {"repo": cfg.docs.repo, "ref": docs_ref, "sha": docs_sha, "path": str(docs_dir)},
        "llm": {"provider": cfg.llm.provider, "models": cfg.llm.models, "task_tiers": cfg.llm.task_tiers},
    }
    run.update_manifest(**manifest)
    return manifest
