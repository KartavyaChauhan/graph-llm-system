"""
OpenAI-compatible chat completions (Groq, OpenRouter, and similar).

Used for the same closed JSON intent schema as Gemini — no behavior change to the query engine.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal, Optional

import httpx

from app.llm.parser import IntentTranslatorBase, _SYSTEM_PROMPT, _USER_PROMPT_TEMPLATE

logger = logging.getLogger("app.llm.openai_provider")

ProviderName = Literal["groq", "openrouter"]


class OpenAICompatibleTranslator(IntentTranslatorBase):
    """Intent translator using POST /v1/chat/completions (Groq or OpenRouter)."""

    def __init__(self, *, provider: ProviderName) -> None:
        super().__init__()
        self._provider: ProviderName = provider
        if provider == "groq":
            self._api_key = (os.environ.get("GROQ_API_KEY") or "").strip()
            base = (os.environ.get("GROQ_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/")
            self._model = _normalize(
                os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile",
            )
        else:
            self._api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
            base = (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
            self._model = _normalize(
                os.environ.get("OPENROUTER_MODEL") or "google/gemini-2.0-flash-001",
            )
        self._url = f"{base}/chat/completions"
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if provider == "openrouter":
            ref = os.environ.get("OPENROUTER_HTTP_REFERER", "http://localhost:8000")
            title = os.environ.get("OPENROUTER_APP_TITLE", "Graph LLM O2C")
            self._headers["HTTP-Referer"] = ref
            self._headers["X-Title"] = title

    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def primary_model_name(self) -> str:
        return self._model

    def _complete_structured_json(self, user_text: str) -> tuple[str, Optional[str]]:
        if not self.is_configured():
            return "", "missing_api_key"

        system_instruction = _SYSTEM_PROMPT
        user_block = _USER_PROMPT_TEMPLATE.format(user_text=user_text)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_block},
        ]

        for use_json_mode in (True, False):
            payload: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "temperature": float(self._temperature),
            }
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                with httpx.Client(timeout=120.0) as client:
                    r = client.post(self._url, json=payload, headers=self._headers)
            except Exception as e:
                return "", str(e)

            if r.status_code >= 400:
                err_head = (r.text or "")[:800]
                if use_json_mode and (
                    "response_format" in err_head.lower()
                    or r.status_code == 400
                    and "json" in err_head.lower()
                ):
                    logger.warning(
                        "openai_compat.json_mode_rejected provider=%s retry_plain status=%s",
                        self._provider,
                        r.status_code,
                    )
                    continue
                return "", f"http_{r.status_code}:{err_head}"

            try:
                data = r.json()
            except json.JSONDecodeError:
                return "", "invalid_response_json"

            text = _extract_chat_content(data)
            if text:
                if not use_json_mode:
                    logger.info("openai_compat.used_plain_json provider=%s model=%r", self._provider, self._model)
                return text, None

        return "", "empty_or_blocked_response"


def _normalize(s: str) -> str:
    return (s or "").strip()


def _extract_chat_content(data: dict[str, Any]) -> str:
    try:
        choices = data.get("choices")
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            return "".join(parts).strip()
    except (IndexError, KeyError, TypeError):
        return ""
    return ""
