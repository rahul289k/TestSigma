"""Gemini client with a content-addressed response cache, per-model daily ledger, per-run budget
and rate limiter.

Design rules (see docs/DESIGN.md, "No fallback"):
- Each task family runs on ONE model fixed in config (llm.models + llm.task_tiers). There is no
  runtime switching: if that model is overloaded or out of quota, the call fails loudly.
- Transient errors (per-minute 429, 5xx, timeouts) are retried on the same model with backoff.
  A daily-quota 429 is not transient: it raises QuotaExhausted immediately.
- Every response is cached by a hash of (model, system, prompt, schema, sampling params, images).
  Replaying a run is bit-identical and costs no quota. Offline mode turns a cache miss into an error.
- When Google reports a model's daily quota as exhausted, the "retry after" time it gives is stored
  (cache/llm/_quota.json) and calls to that model fail immediately until then, without spending a
  request. A ledger counts live calls per model per UTC day for reporting.
- Structured output is enforced twice: by Gemini's responseSchema and by Pydantic validation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import CACHE_DIR, LlmCfg
from ..runstore import RunStore
from .schema import to_gemini_schema

T = TypeVar("T", bound=BaseModel)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    pass


class BudgetExhausted(LLMError):
    pass


class QuotaExhausted(LLMError):
    pass


class CacheMiss(LLMError):
    pass


class LLM:
    def __init__(self, cfg: LlmCfg, api_key: str | None, run: RunStore | None, offline: bool = False,
                 cache_dir: Path | None = None):
        self.cfg = cfg
        self.api_key = api_key
        self.run = run
        self.offline = offline
        self.cache_dir = cache_dir or (CACHE_DIR / "llm")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._windows: dict[str, deque[float]] = {}
        self.live_calls = self._count_previous_live_calls()
        self._http = httpx.Client(timeout=cfg.timeout_s)

    # ---- routing -----------------------------------------------------------------------
    def model_for(self, task: str) -> str:
        family = task.split(":")[0]
        tier = self.cfg.task_tiers.get(family, "default")
        return self.cfg.models[tier]

    # ---- public ------------------------------------------------------------------------
    def json(self, task: str, prompt: str, schema: type[T], system: str | None = None,
             images: list[tuple[str, bytes]] | None = None, temperature: float | None = None,
             seed: int | None = None) -> T:
        model = self.model_for(task)
        temperature = self.cfg.temperature if temperature is None else temperature
        seed = self.cfg.seed if seed is None else seed
        gschema = to_gemini_schema(schema)
        key = self._key(model, system, prompt, gschema, temperature, seed, images)
        cached = self._cache_get(key)
        started = time.time()
        if cached is not None:
            text, meta = cached["response_text"], cached.get("meta", {})
            self._log(task, model, key, cached=True, latency=0.0, meta=meta)
        else:
            if self.offline:
                raise CacheMiss(f"[{task}] no cached response for request {key[:12]} and offline mode is on. "
                                "Run once online to record it.")
            if not self.api_key:
                raise LLMError("GEMINI_API_KEY is not set")
            if self.live_calls >= self.cfg.max_calls_per_run:
                raise BudgetExhausted(f"[{task}] LLM budget of {self.cfg.max_calls_per_run} live calls for this run is "
                                      "used up. Cached results are kept; re-running resumes from the cache.")
            until = self._blocked_until(model)
            if until > time.time():
                raise QuotaExhausted(f"[{task}] {model} daily quota is exhausted until "
                                     f"{datetime.fromtimestamp(until, UTC).isoformat(timespec='minutes')} (from Google's last "
                                     "429). Cached results are kept; resume after that time.")
            text, meta = self._call(model, system, prompt, gschema, temperature, seed, images, task)
            self.live_calls += 1
            self._ledger_add(model)
            self._cache_put(key, task, model, system, prompt, gschema, temperature, seed, text, meta)
            self._log(task, model, key, cached=False, latency=time.time() - started, meta=meta)
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            raise LLMError(f"[{task}] response did not match schema {schema.__name__}: {e}") from e

    # ---- network -----------------------------------------------------------------------
    def _call(self, model, system, prompt, gschema, temperature, seed, images, task) -> tuple[str, dict]:
        parts: list[dict[str, Any]] = []
        for mime, data in images or []:
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}})
        parts.append({"text": prompt})
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "seed": seed, "responseMimeType": "application/json",
                                 "responseSchema": gschema, "maxOutputTokens": self.cfg.max_output_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = API.format(model=model)
        last_err = ""
        for attempt in range(self.cfg.retries + 1):
            self._rate_limit(model)
            try:
                r = self._http.post(url, json=body, headers={"x-goog-api-key": self.api_key})
            except httpx.TransportError as e:
                last_err = f"transport error: {e.__class__.__name__}"
                self._backoff(attempt, None)
                continue
            if r.status_code == 200:
                return self._parse(r.json(), task)
            last_err = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code == 429 and self._is_daily_quota(r):
                self._block(model, time.time() + (self._retry_delay(r) or 3600))
                raise QuotaExhausted(f"[{task}] {model}: daily free-tier quota exhausted (Google says retry in "
                                     f"{self._retry_delay(r) or '?'} s). Cached results are kept; resume after the reset.")
            if r.status_code in (429, 500, 502, 503, 504):
                print(f"[llm] {task}: {model} HTTP {r.status_code}, attempt {attempt + 1}/{self.cfg.retries + 1}, "
                      "retrying same model", file=sys.stderr, flush=True)
                self._backoff(attempt, self._retry_delay(r))
                continue
            raise LLMError(f"[{task}] Gemini request failed: {last_err}")
        raise LLMError(f"[{task}] Gemini request failed after {self.cfg.retries + 1} attempts: {last_err}")

    @staticmethod
    def _parse(data: dict, task: str) -> tuple[str, dict]:
        cands = data.get("candidates") or []
        if not cands:
            raise LLMError(f"[{task}] Gemini returned no candidates (promptFeedback={data.get('promptFeedback')})")
        cand = cands[0]
        finish = cand.get("finishReason")
        if finish != "STOP":
            raise LLMError(f"[{task}] Gemini stopped with finishReason={finish}; not using a partial answer")
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        meta = {"model_version": data.get("modelVersion"), "usage": data.get("usageMetadata", {})}
        return text, meta

    @staticmethod
    def _details(r: httpx.Response) -> list[dict]:
        try:
            return r.json().get("error", {}).get("details", []) or []
        except Exception:  # body not JSON
            return []

    def _is_daily_quota(self, r: httpx.Response) -> bool:
        for d in self._details(r):
            for v in d.get("violations", []) or []:
                if "PerDay" in (v.get("quotaId") or ""):
                    return True
        return False

    def _retry_delay(self, r: httpx.Response) -> float | None:
        if r.headers.get("retry-after"):
            try:
                return float(r.headers["retry-after"])
            except ValueError:
                pass
        for d in self._details(r):
            if d.get("@type", "").endswith("RetryInfo") and str(d.get("retryDelay", "")).endswith("s"):
                return float(d["retryDelay"][:-1])
        return None

    @staticmethod
    def _backoff(attempt: int, hinted: float | None) -> None:
        time.sleep(min(hinted if hinted is not None else 4.0 * (2 ** attempt), 120.0))

    def _rate_limit(self, model: str) -> None:
        rpm = max(1, self.cfg.requests_per_minute)
        w = self._windows.setdefault(model, deque())
        now = time.time()
        while w and now - w[0] > 60:
            w.popleft()
        if len(w) >= rpm:
            time.sleep(60 - (now - w[0]) + 0.2)
        w.append(time.time())

    # ---- quota state + ledger ------------------------------------------------------------
    def _quota_path(self) -> Path:
        return self.cache_dir / "_quota.json"

    def _blocked_until(self, model: str) -> float:
        p = self._quota_path()
        return float((json.loads(p.read_text()) if p.exists() else {}).get(model, 0))

    def _block(self, model: str, until: float) -> None:
        p = self._quota_path()
        data = json.loads(p.read_text()) if p.exists() else {}
        data[model] = round(until)
        p.write_text(json.dumps(data, indent=1, sort_keys=True))

    def _ledger_add(self, model: str) -> None:
        p = self.cache_dir / "_ledger.json"
        data = json.loads(p.read_text()) if p.exists() else {}
        day = data.setdefault(datetime.now(UTC).strftime("%Y-%m-%d"), {})
        day[model] = int(day.get(model, 0)) + 1
        p.write_text(json.dumps(data, indent=1, sort_keys=True))

    # ---- cache + log ---------------------------------------------------------------------
    @staticmethod
    def _key(model, system, prompt, gschema, temperature, seed, images) -> str:
        blob = json.dumps({
            "model": model, "system": system, "prompt": prompt, "schema": gschema,
            "temperature": temperature, "seed": seed,
            "images": [hashlib.sha256(b).hexdigest() for _, b in images or []],
        }, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _cache_get(self, key: str) -> dict | None:
        p = self._cache_path(key)
        return json.loads(p.read_text()) if p.exists() else None

    def _cache_put(self, key, task, model, system, prompt, gschema, temperature, seed, text, meta) -> None:
        p = self._cache_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "task": task, "model": model, "temperature": temperature, "seed": seed,
            "system": system, "prompt": prompt, "schema": gschema,
            "response_text": text, "meta": meta,
        }, sort_keys=True, ensure_ascii=False, indent=1))

    def _log(self, task: str, model: str, key: str, cached: bool, latency: float, meta: dict) -> None:
        if self.run is None:
            return
        usage = meta.get("usage", {})
        self.run.append_jsonl("llm_calls.jsonl", {
            "task": task, "model": model, "key": key[:16], "cached": cached, "latency_s": round(latency, 2),
            "model_version": meta.get("model_version"),
            "prompt_tokens": usage.get("promptTokenCount"), "output_tokens": usage.get("candidatesTokenCount"),
            "thought_tokens": usage.get("thoughtsTokenCount"), "ts": round(time.time(), 1),
        })

    def _count_previous_live_calls(self) -> int:
        if self.run is None or not self.run.exists("llm_calls.jsonl"):
            return 0
        return sum(1 for r in self.run.read_jsonl("llm_calls.jsonl") if not r.get("cached"))
