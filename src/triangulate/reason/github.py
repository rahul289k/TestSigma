"""GitHub access for PR analysis, cached on disk so replays are deterministic and rate-limit free.

PR metadata and file lists come from the REST API (token optional). File contents at a given
commit come from raw.githubusercontent.com, which is not counted against the API rate limit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

from ..config import CACHE_DIR


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str | None, offline: bool = False):
        self.repo = repo
        self.offline = offline
        self.cache = CACHE_DIR / "github"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "triangulate/0.1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(timeout=60, headers=headers, follow_redirects=True)

    def _cached(self, key: str, fetch) -> str:
        p = self.cache / self.repo.replace("/", "__") / (hashlib.sha1(key.encode()).hexdigest() + ".txt")
        if p.exists():
            return p.read_text()
        if self.offline:
            raise GitHubError(f"offline and not cached: {key}")
        text = fetch()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return text

    def _api(self, path: str) -> str:
        def fetch() -> str:
            r = self.http.get(f"https://api.github.com/repos/{self.repo}/{path}")
            if r.status_code != 200:
                raise GitHubError(f"GitHub API {path}: HTTP {r.status_code} {r.text[:200]}")
            return r.text
        return self._cached(f"api:{path}", fetch)

    def pull(self, number: int) -> dict:
        return json.loads(self._api(f"pulls/{number}"))

    def pull_files(self, number: int) -> list[dict]:
        files: list[dict] = []
        page = 1
        while True:
            batch = json.loads(self._api(f"pulls/{number}/files?per_page=100&page={page}"))
            files.extend(batch)
            if len(batch) < 100:
                return files
            page += 1

    def file_at(self, sha: str, path: str) -> str | None:
        """File content at a commit, or None if it does not exist there."""
        def fetch() -> str:
            r = self.http.get(f"https://raw.githubusercontent.com/{self.repo}/{sha}/{path}")
            if r.status_code == 404:
                return "\x00MISSING\x00"
            if r.status_code != 200:
                raise GitHubError(f"raw {sha[:8]}/{path}: HTTP {r.status_code}")
            return r.text
        text = self._cached(f"raw:{sha}:{path}", fetch)
        return None if text == "\x00MISSING\x00" else text


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
