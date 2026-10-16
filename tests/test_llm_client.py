from typing import Literal

import pytest
from pydantic import BaseModel

from triangulate.config import load_config
from triangulate.llm.client import LLM, BudgetExhausted, CacheMiss
from triangulate.llm.schema import to_gemini_schema


class Item(BaseModel):
    name: str
    kind: Literal["A", "B"]
    note: str | None = None


class Out(BaseModel):
    items: list[Item]


def test_schema_conversion():
    s = to_gemini_schema(Out)
    item = s["properties"]["items"]["items"]
    assert s["type"] == "OBJECT" and item["properties"]["kind"] == {"type": "STRING", "enum": ["A", "B"]}
    assert item["properties"]["note"]["nullable"] is True and item["required"] == ["name", "kind"]


def make(tmp_path, **kw):
    cfg = load_config().llm.model_copy(update=kw)
    return LLM(cfg, "fake-key", None, cache_dir=tmp_path)


def test_cache_replay_and_offline(tmp_path, monkeypatch):
    llm = make(tmp_path)
    calls = []

    def fake_call(self, *a):
        calls.append(1)
        return '{"items": [{"name": "x", "kind": "A"}]}', {"model_version": "m"}

    monkeypatch.setattr(LLM, "_call", fake_call)
    assert llm.json("t", "prompt", Out).items[0].name == "x"
    assert llm.json("t", "prompt", Out).items[0].name == "x"
    assert len(calls) == 1  # second answer came from the cache
    off = make(tmp_path)
    off.offline = True
    assert off.json("t", "prompt", Out).items[0].kind == "A"
    with pytest.raises(CacheMiss):
        off.json("t", "a different prompt", Out)


def test_budget_is_enforced(tmp_path, monkeypatch):
    llm = make(tmp_path, max_calls_per_run=1)
    monkeypatch.setattr(LLM, "_call", lambda self, *a: ('{"items": []}', {}))
    llm.json("t", "p1", Out)
    with pytest.raises(BudgetExhausted):
        llm.json("t", "p2", Out)


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, str(body)

    def json(self):
        return self._body


def test_daily_quota_fails_fast_and_is_remembered(tmp_path, monkeypatch):
    from triangulate.llm.client import QuotaExhausted
    llm = make(tmp_path)
    body = {"error": {"code": 429, "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3600s"}]}}
    posts = []
    monkeypatch.setattr(llm._http, "post", lambda *a, **k: posts.append(1) or FakeResp(429, body))
    with pytest.raises(QuotaExhausted):
        llm.json("judge:0", "p", Out)
    assert len(posts) == 1                       # no retries for a daily quota
    with pytest.raises(QuotaExhausted):
        llm.json("judge:1", "another prompt", Out)
    assert len(posts) == 1                       # remembered: the second call never hits the network


def test_tasks_route_to_their_configured_model(tmp_path):
    llm = make(tmp_path)
    assert llm.model_for("judge:0:v0") == llm.cfg.models["default"]
    assert llm.model_for("label:crawl:0") == llm.cfg.models["light"]
