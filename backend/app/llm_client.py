"""Thin OpenAI-compatible chat client — transport only, no negotiation logic.

Provider-swappable via env:
  LLM_MODE = "openai" (default) | "nvcf"
  OPENAI mode:  POST {LLM_BASE_URL}/chat/completions   (OpenAI-compatible)
  NVCF mode:    POST {LLM_BASE_URL}                      (NVCF function-exec,
                LLM_MODEL holds the NVCF function id; async poll for result)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

import app.config  # noqa: F401 — load .env before os.environ reads
from app.schemas import CounterOffer

_TIMEOUT_S = 120.0
_BACKOFF_S = 0.5
_NVCF_POLL_S = 1.0
_NVCF_POLL_MAX = 60  # up to ~60s for cold-start GPU functions


def counter_offer_tools() -> list[dict[str, Any]]:
    """OpenAI tool list for ``counter_offer``, fields sourced from ``CounterOffer``."""
    schema = CounterOffer.model_json_schema()
    return [
        {
            "type": "function",
            "function": {
                "name": "counter_offer",
                "description": (
                    "Propose the next counter-offer with all five negotiation variables."
                ),
                "parameters": {
                    "type": "object",
                    "properties": schema["properties"],
                    "required": schema.get("required", list(CounterOffer.model_fields)),
                    "additionalProperties": False,
                },
            },
        }
    ]


class LLMClient:
    """Provider-swappable via LLM_MODE / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL."""

    def __init__(self) -> None:
        self.mode = os.environ.get("LLM_MODE", "openai").strip().lower()
        self.base_url = os.environ["LLM_BASE_URL"].rstrip("/")
        self.api_key = os.environ["LLM_API_KEY"]
        self.model = os.environ["LLM_MODEL"]

    def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Chat with tools. Returns first tool call ``{name, arguments}``, or None if text-only."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "tools": tools,
        }
        if self.mode == "nvcf":
            data = self._nvcf_invoke(self.base_url, payload)
        else:
            data = self._post(f"{self.base_url}/chat/completions", payload)

        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return None

        fn = tool_calls[0]["function"]
        raw_args = fn.get("arguments", "{}")
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        arguments = _coerce_tool_args(arguments)
        return {"name": fn["name"], "arguments": arguments}

    # --- providers ---------------------------------------------------------

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST once; one retry on timeout / 5xx. 4xx fails immediately."""
        last_timeout: httpx.TimeoutException | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=_TIMEOUT_S) as client:
                    resp = client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
            except httpx.TimeoutException as exc:
                last_timeout = exc
                if attempt == 0:
                    time.sleep(_BACKOFF_S)
                    continue
                raise

            if resp.status_code >= 500:
                if attempt == 0:
                    time.sleep(_BACKOFF_S)
                    continue
                resp.raise_for_status()

            if resp.status_code >= 400:
                resp.raise_for_status()

            return resp.json()

        if last_timeout is not None:
            raise last_timeout
        raise RuntimeError("LLM POST retry exhausted")

    def _nvcf_invoke(self, invoke_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """NVCF function-exec: async submit + poll for the chat/completions result.

        ``LLM_MODEL`` is the NVCF function id. The response mirrors the
        OpenAI ``choices[0].message`` shape once the invocation completes.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # 1) submit
        submit = self._raw_post(invoke_url, headers, payload, expect_async=True)
        request_id = submit.get("requestId") or submit.get("request_id")
        if not request_id:
            # Some deployments return the result inline (sync).
            if "choices" in submit:
                return submit  # type: ignore[return-value]
            raise RuntimeError(f"NVCF submit returned no requestId: {submit!r}")
        # 2) poll
        status_url = f"{invoke_url.rsplit('/functions/', 1)[0]}/status/{request_id}"
        for _ in range(_NVCF_POLL_MAX):
            time.sleep(_NVCF_POLL_S)
            st = self._raw_get(status_url, headers)
            if st.get("status") == "COMPLETED" or "choices" in st:
                return st  # type: ignore[return-value]
            if st.get("status") in ("FAILED", "ERROR"):
                raise RuntimeError(f"NVCF invocation failed: {st!r}")
        raise RuntimeError("NVCF poll timed out waiting for completion")

    def _raw_post(self, url: str, headers: dict[str, str], payload: dict[str, Any], expect_async: bool = False) -> dict[str, Any]:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            resp.raise_for_status()
        return resp.json()  # type: ignore[return-value]

    def _raw_get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code >= 400:
            resp.raise_for_status()
        return resp.json()  # type: ignore[return-value]


def _coerce_tool_args(args: dict[str, Any]) -> dict[str, Any]:
    """LLMs frequently return JSON strings for numeric/bool tool fields.

    Coerce defensively so a harmless type quirk (e.g. ``"min_volume": "6000"``)
    does not get misread as a malformed proposal. Genuine garbage (non-numeric
    text) is left untouched so schema validation rejects it with a clear error.
    """
    for key, value in list(args.items()):
        if not isinstance(value, str):
            continue
        lowered = value.strip().lower()
        if lowered in ("true", "false"):
            args[key] = lowered == "true"
        else:
            try:
                args[key] = int(value) if "." not in value else float(value)
            except ValueError:
                pass  # leave as-is; Pydantic/CounterOffer validation rejects real garbage
    return args

