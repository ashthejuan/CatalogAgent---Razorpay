"""Unit tests for the Phase 4 LLM transport wrapper (no live provider calls)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm_client import LLMClient, counter_offer_tools
from app.schemas import CounterOffer


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")


def test_counter_offer_tools_match_schema_fields():
    tools = counter_offer_tools()
    assert len(tools) == 1
    fn = tools[0]["function"]
    assert fn["name"] == "counter_offer"
    props = fn["parameters"]["properties"]
    assert set(props) == set(CounterOffer.model_fields)
    assert set(fn["parameters"]["required"]) == set(CounterOffer.model_fields)


def test_chat_with_tools_returns_parsed_tool_call(llm_env, monkeypatch):
    calls = {"n": 0}
    args = {
        "unit_price": 9.5,
        "min_volume": 1000,
        "payment_terms_days": 15,
        "delivery_days": 7,
        "recurring": False,
    }
    args_json = json.dumps(args)

    def fake_post(self, url, headers=None, json=None):
        calls["n"] += 1
        assert json["model"] == "test-model"
        assert json["messages"][0]["role"] == "system"
        body = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "counter_offer",
                                    "arguments": args_json,
                                }
                            }
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr("app.llm_client.time.sleep", lambda _s: None)

    result = LLMClient().chat_with_tools(
        "sys", [{"role": "user", "content": "hi"}], counter_offer_tools()
    )
    assert result == {"name": "counter_offer", "arguments": args}
    assert calls["n"] == 1


def test_chat_with_tools_text_only_returns_none(llm_env, monkeypatch):
    def fake_post(self, url, headers=None, json=None):
        body = {"choices": [{"message": {"content": "thinking out loud", "tool_calls": None}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    assert LLMClient().chat_with_tools("sys", [], []) is None


def test_retries_once_on_5xx_then_succeeds(llm_env, monkeypatch):
    attempts = {"n": 0}

    def fake_post(self, url, headers=None, json=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, text="busy", request=httpx.Request("POST", url))
        body = {"choices": [{"message": {"content": "ok"}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr("app.llm_client.time.sleep", lambda _s: None)

    assert LLMClient().chat_with_tools("sys", [], []) is None
    assert attempts["n"] == 2


def test_no_retry_on_4xx(llm_env, monkeypatch):
    attempts = {"n": 0}

    def fake_post(self, url, headers=None, json=None):
        attempts["n"] += 1
        return httpx.Response(401, text="bad key", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr("app.llm_client.time.sleep", lambda _s: None)

    with pytest.raises(httpx.HTTPStatusError):
        LLMClient().chat_with_tools("sys", [], [])
    assert attempts["n"] == 1


def test_retries_once_on_timeout_then_raises(llm_env, monkeypatch):
    attempts = {"n": 0}

    def fake_post(self, url, headers=None, json=None):
        attempts["n"] += 1
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr("app.llm_client.time.sleep", lambda _s: None)

    with pytest.raises(httpx.TimeoutException):
        LLMClient().chat_with_tools("sys", [], [])
    assert attempts["n"] == 2


def test_coerces_string_typed_tool_args(llm_env, monkeypatch):
    args_json = json.dumps(
        {
            "unit_price": "10.5",
            "min_volume": "6000",
            "payment_terms_days": "30",
            "delivery_days": "21",
            "recurring": "true",
        }
    )

    def fake_post(self, url, headers=None, json=None):
        body = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "counter_offer", "arguments": args_json}}
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = LLMClient().chat_with_tools("sys", [], [])
    assert result is not None
    a = result["arguments"]
    assert a["unit_price"] == 10.5
    assert a["min_volume"] == 6000
    assert a["payment_terms_days"] == 30
    assert a["delivery_days"] == 21
    assert a["recurring"] is True


def test_leaves_garbage_strings_for_schema_validation(llm_env, monkeypatch):
    args_json = json.dumps({"unit_price": "not_a_number", "min_volume": "6000"})

    def fake_post(self, url, headers=None, json=None):
        body = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "counter_offer", "arguments": args_json}}
                        ]
                    }
                }
            ]
        }
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = LLMClient().chat_with_tools("sys", [], [])
    # non-numeric garbage stays a string → CounterOffer validation will reject it
    assert result["arguments"]["unit_price"] == "not_a_number"
