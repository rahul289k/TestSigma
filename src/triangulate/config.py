"""Configuration: one YAML file (+ optional profile overlay) plus secrets from .env.

Everything a run depends on is captured here and hashed into the run manifest, so two
runs with the same config hash and the same pinned versions are directly comparable.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"
CACHE_DIR = ROOT / "cache"


class AppCfg(BaseModel):
    url: str
    seed_routes: list[str]
    version_pattern: str


class CodeCfg(BaseModel):
    repo: str
    sparse_paths: list[str]
    common_root: str
    components_dir: str
    pages_dir: str
    layouts_dir: str
    locale_file: str
    aliases: dict[str, str] = Field(default_factory=dict)
    external_tag_prefixes: list[str] = Field(default_factory=list)


class DocsCfg(BaseModel):
    repo: str
    ref: str
    include: list[str]
    base_url: str
    login_hints: list[str] = Field(default_factory=list)


class CrawlCfg(BaseModel):
    max_states: int
    max_states_per_route: int
    max_depth: int
    max_minutes: float
    viewport: dict[str, int]
    locale: str
    timezone: str
    settle_ms: int
    settle_timeout_ms: int
    nav_timeout_ms: int
    max_actions_per_state: int
    deny_names: list[str]
    block_hosts: list[str]
    network_ignore_hosts: list[str] = Field(default_factory=list)
    network_host_aliases: dict[str, str] = Field(default_factory=dict)
    transient_labels: list[str] = Field(default_factory=list)
    fixtures: dict[str, str] = Field(default_factory=dict)


class ProbeCfg(BaseModel):
    enabled: bool
    max_requirements: int
    max_steps: int


class LlmCfg(BaseModel):
    provider: str
    models: dict[str, str]
    task_tiers: dict[str, str]
    temperature: float
    seed: int
    max_calls_per_run: int
    max_output_tokens: int
    requests_per_minute: int
    timeout_s: int
    retries: int
    ingest_pages_per_call: int
    judge_requirements_per_call: int
    label_states_per_call: int
    flow_names_per_call: int = 80
    judge_votes: int


class LinkCfg(BaseModel):
    retrieval_top_k: int
    method_priors: dict[str, float]


class ReasonCfg(BaseModel):
    hop_decay: float
    max_import_hops: int
    child_factor: float
    change_weights: dict[str, float]
    bands: dict[str, float]


class Config(BaseModel):
    app: AppCfg
    code: CodeCfg
    docs: DocsCfg
    crawl: CrawlCfg
    probe: ProbeCfg
    llm: LlmCfg
    link: LinkCfg
    reason: ReasonCfg
    profile: str = "default"

    def hash(self) -> str:
        blob = json.dumps(self.model_dump(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


class Secrets(BaseModel):
    gemini_api_key: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    github_token: str | None = None


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(profile: str | None = None) -> Config:
    data = yaml.safe_load((CONFIG_DIR / "target.yaml").read_text())
    if profile and profile != "default":
        overlay_path = CONFIG_DIR / f"profile.{profile}.yaml"
        if not overlay_path.exists():
            raise FileNotFoundError(f"Unknown profile '{profile}': {overlay_path} does not exist")
        data = _deep_merge(data, yaml.safe_load(overlay_path.read_text()) or {})
    data["profile"] = profile or "default"
    return Config.model_validate(data)


def load_secrets(require_llm: bool = True) -> Secrets:
    load_dotenv(ROOT / ".env", override=False)

    def need(name: str, required: bool = True) -> str:
        val = os.environ.get(name, "").strip()
        if required and not val:
            raise RuntimeError(f"Missing {name}. Put it in {ROOT / '.env'} (see .env.example).")
        return val

    return Secrets(
        gemini_api_key=need("GEMINI_API_KEY", require_llm),
        neo4j_uri=need("NEO4J_URI"),
        neo4j_user=need("NEO4J_USER"),
        neo4j_password=need("NEO4J_PASSWORD"),
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j").strip() or "neo4j",
        github_token=os.environ.get("GITHUB_TOKEN", "").strip() or None,
    )
