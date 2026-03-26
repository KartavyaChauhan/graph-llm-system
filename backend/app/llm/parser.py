"""
Controlled natural-language -> structured intent translator (Gemini, Groq, OpenRouter, ...).

This is **not** a conversational agent: one-shot classification + parameter extraction into a closed
schema, then deterministic validation before any query execution.

Prompt strategy (summary)
-------------------------
1. **Closed world** — The model may only emit JSON matching our schema; intents are an enumerated set
   identical to :data:`app.query.types.SUPPORTED_INTENTS`.
2. **Two-stage semantics inside one call** — ``status=rejected`` for anything outside Order-to-Cash
   analytics on *this* dataset; ``status=ok`` only when a single supported intent applies.
3. **Low temperature + JSON MIME** — Reduces rambling; ``response_mime_type=application/json`` forces
   parseable output when the SDK/model supports it.
4. **Few-shot anchoring** — Paraphrases for the same intent reduce drift; explicit *reject* examples
   teach the off-topic boundary.
5. **Server-side validation** — Pydantic + allow-list checks + parameter coercion; invalid LLM output
  never reaches :class:`~app.query.engine.QueryEngine` (safe error path).

Hallucination risk reduction
----------------------------
- **No numeric facts in the prompt** about the dataset (no fake row counts), so the model cannot
  "remember" statistics — only *intent* and *extracted identifiers* from the user text.
- **forbid execution** on unknown intents or malformed payloads.
- **Merge ``filters`` into ``parameters``** only after key allow-listing per intent (strip unknown keys).
- **Optional regex assist** for SAP-style document numbers only when the model returns ``ok`` but
  forgot ``order_id`` — narrow digit pattern, never invent identifiers not present in user text.

Environment
-----------
- ``LLM_PROVIDER`` — ``gemini`` (default), ``groq``, or ``openrouter``
- **Gemini** (default): ``GEMINI_API_KEY``, optional ``GEMINI_MODEL``, ``GEMINI_MODEL_FALLBACKS``
- **Groq** (OpenAI-compatible): ``GROQ_API_KEY``, optional ``GROQ_MODEL``, ``GROQ_BASE_URL``
- **OpenRouter**: ``OPENROUTER_API_KEY``, optional ``OPENROUTER_MODEL``, ``OPENROUTER_BASE_URL``

Model policy (free tier / stability)
------------------------------------
Google marks **Gemini 2.0 Flash** as deprecated; keys on the free tier often see 404 or quota quirks
on older aliases. This module **defaults to Gemini 2.5 Flash** and only cycles through a small
**allow-list** of models that are commonly exposed on the consumer Gemini API. See
https://ai.google.dev/gemini-api/docs/models
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.query.engine import QueryEngine
from app.query.types import SUPPORTED_INTENTS

logger = logging.getLogger("app.llm.parser")

# Models allowed for this app (consumer Gemini API / typical free-tier access).
# Order in DEFAULT_MODEL_TRY_ORDER is the automatic fallback chain when the primary fails.
# Last entries are deprecated but kept as last resort when newer IDs are unavailable on an account.
ALLOWED_GEMINI_MODELS: frozenset[str] = frozenset(
    {
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-2.0-flash",
    }
)

DEFAULT_MODEL_TRY_ORDER: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
    "gemini-2.0-flash",
)


def _normalize_model_name(name: str) -> str:
    return (name or "").strip()


def _build_model_try_list(primary: Optional[str]) -> list[str]:
    """Primary first (if allowed), then defaults, then GEMINI_MODEL_FALLBACKS — deduped, allow-list only."""
    seen: set[str] = set()
    out: list[str] = []

    def add(m: str) -> None:
        m = _normalize_model_name(m)
        if not m or m in seen:
            return
        if m not in ALLOWED_GEMINI_MODELS:
            logger.warning("gemini.model_not_allowlisted skipped=%r", m)
            return
        seen.add(m)
        out.append(m)

    p = _normalize_model_name(primary or os.environ.get("GEMINI_MODEL") or "")
    if p:
        add(p)
    else:
        add(DEFAULT_MODEL_TRY_ORDER[0])

    extra = os.environ.get("GEMINI_MODEL_FALLBACKS", "")
    if extra.strip():
        for part in extra.split(","):
            add(part.strip())

    for m in DEFAULT_MODEL_TRY_ORDER:
        add(m)

    return out


def _is_model_availability_error(msg: str) -> bool:
    s = msg.lower()
    return any(
        x in s
        for x in (
            "404",
            "not found",
            "unsupported",
            "does not exist",
            "invalid model",
            "unknown model",
            "model_name",
            "is not found",
            "503",
            "service unavailable",
        )
    )


def _is_rate_limit_error(msg: str) -> bool:
    s = msg.lower()
    return any(x in s for x in ("429", "rate limit", "quota", "resource exhausted", "too many requests"))


# --- Output schema (LLM + API) -------------------------------------------------

StatusLiteral = Literal["ok", "rejected", "error"]


class StructuredIntentJson(BaseModel):
    """
    Public JSON shape for consumers (LLM output normalized by :func:`parse_natural_language`).

    * ``filters`` is accepted as a synonym for ``parameters`` (user-facing name); both are merged
      then allow-listed into ``parameters`` for execution.
    """

    model_config = ConfigDict(extra="ignore")

    status: StatusLiteral = "error"

    @field_validator("status", mode="before")
    @classmethod
    def _norm_status(cls, v: Any) -> Any:
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("ok", "rejected", "error"):
                return s
        return v

    intent: Optional[str] = None
    entity: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reject_reason: Optional[str] = None
    user_guidance: Optional[str] = None


def _pick(d: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: d[k] for k in keys if k in d and d[k] is not None}


_SUPPORTED_INTENTS_BULLET = "\n".join(f"  - {x}" for x in SUPPORTED_INTENTS)

_SYSTEM_PROMPT = f"""You are a strict intent translator for an internal Order-to-Cash (O2C) analytics API.
You do NOT answer questions from general knowledge. You do NOT invent data, IDs, counts, or amounts.

Your only job: classify the user's message into ONE of the supported intents OR reject it.

Supported intents (exact strings):
{_SUPPORTED_INTENTS_BULLET}

Rules:
1. Output a single JSON object only. No markdown fences, no prose outside JSON.
2. Field "status" must be "ok" or "rejected".
3. If status is "rejected": set "intent" to null, "parameters" and "filters" to {{}}, and explain briefly in "reject_reason".
4. If status is "ok": set "intent" to exactly one supported intent string; fill "parameters" with only keys relevant to that intent.
5. Use "entity" as an optional short hint about what the user is focused on: order | delivery | invoice | payment | customer | product | address | mixed | unknown
6. "filters" is optional and means the same as "parameters" — prefer populating "parameters" only to avoid duplication.
7. Reject (status=rejected) if the user asks for creative writing, general knowledge, jokes, coding unrelated to this dataset, or anything not about O2C data exploration.
8. Never fabricate document numbers. Extract order/invoice numbers ONLY if they appear in the user text; otherwise choose an intent that does not require them or reject with guidance.
9. For find_incomplete_orders, "criteria" is a list of strings chosen ONLY from:
   missing_customer, missing_delivery, missing_invoice, missing_payment, missing_product_lines
   Map user phrases: "delivered but not billed" -> missing_invoice; "not paid" -> missing_payment; "no delivery" -> missing_delivery.
10. For find_top_products_by_billing, sort_by must be "billing_line_count" or "net_amount_sum" if present.

JSON schema shape (all keys optional except status):
{{
  "status": "ok" | "rejected",
  "intent": string | null,
  "entity": string | null,
  "parameters": object,
  "filters": object,
  "reject_reason": string | null,
  "user_guidance": string | null
}}
"""

_USER_PROMPT_TEMPLATE = """## Few-shot style examples (follow the same JSON discipline)

User: Which products are most billed?
Assistant JSON:
{{"status":"ok","intent":"find_top_products_by_billing","entity":"product","parameters":{{"limit":10,"sort_by":"billing_line_count","language":"EN"}},"reject_reason":null,"user_guidance":null}}

User: Find incomplete orders
Assistant JSON:
{{"status":"ok","intent":"find_incomplete_orders","entity":"order","parameters":{{"limit":200}},"reject_reason":null,"user_guidance":null}}

User: Show orders that were delivered but never invoiced
Assistant JSON:
{{"status":"ok","intent":"find_incomplete_orders","entity":"order","parameters":{{"criteria":["missing_invoice"],"limit":200}},"reject_reason":null,"user_guidance":null}}

User: Trace the flow for sales order 740506
Assistant JSON:
{{"status":"ok","intent":"trace_order_flow","entity":"order","parameters":{{"order_id":"740506"}},"reject_reason":null,"user_guidance":null}}

User: What is the capital of France?
Assistant JSON:
{{"status":"rejected","intent":null,"entity":null,"parameters":{{}},"filters":{{}},"reject_reason":"not_order_to_cash","user_guidance":"This system only answers questions about the provided O2C dataset."}}

User: Write me a poem about graphs
Assistant JSON:
{{"status":"rejected","intent":null,"entity":null,"parameters":{{}},"filters":{{}},"reject_reason":"off_topic","user_guidance":"Ask about orders, deliveries, billing, or payments in the dataset."}}

---

Now translate the following user message. Output JSON only.

User: {user_text}
"""


# --- Translator ----------------------------------------------------------------


class IntentTranslatorBase(ABC):
    """
    Shared NL guardrails, JSON validation, and intent allow-listing.
    Subclasses implement :meth:`_complete_structured_json` (Gemini, Groq, OpenRouter, ...).
    """

    _DOMAIN_KEYWORDS = re.compile(
        r"\b(order|sales|delivery|shipment|invoice|billing|payment|clearing|customer|product|material|"
        r"o2c|order[-\s]?to[-\s]?cash|accounts?\s*receivable|ar\b|sap|fulfillment|billed|unpaid|"
        r"incomplete|trace|lifecycle|graphs?)\b",
        re.I,
    )
    _OFF_TOPIC_HINTS = re.compile(
        r"\b(write|poem|story|joke|essay|lyrics|hack|malware|ignore (all|previous) instructions|"
        r"capital of|who is the president|recipe|dating advice)\b",
        re.I,
    )
    _ORDER_ID_IN_TEXT = re.compile(r"\b(\d{5,10})\b")

    def __init__(self, *, temperature: float = 0.08) -> None:
        self._temperature = float(temperature)

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @property
    @abstractmethod
    def primary_model_name(self) -> str:
        ...

    @abstractmethod
    def _complete_structured_json(self, user_text: str) -> tuple[str, Optional[str]]:
        """Return (raw_json_str, error_message). Empty string means failure."""

    def parse(self, user_text: str) -> StructuredIntentJson:
        text = (user_text or "").strip()
        if not text:
            return StructuredIntentJson(
                status="error",
                reject_reason="empty_input",
                user_guidance="Provide a non-empty question about orders, deliveries, billing, or payments in the dataset.",
            )
        if len(text) > 6000:
            return StructuredIntentJson(
                status="error",
                reject_reason="input_too_long",
                user_guidance="Shorten the question (max ~6000 characters).",
            )

        if self._OFF_TOPIC_HINTS.search(text) and not self._DOMAIN_KEYWORDS.search(text):
            return StructuredIntentJson(
                status="rejected",
                entity=None,
                reject_reason="off_topic_or_unsafe_pattern",
                user_guidance="This system only answers Order-to-Cash questions about the loaded dataset.",
            )

        if not self.is_configured():
            logger.warning("llm.missing_api_key provider=%s", type(self).__name__)
            return StructuredIntentJson(
                status="error",
                reject_reason="llm_not_configured",
                user_guidance=(
                    "Configure an LLM API key: GEMINI_API_KEY (default), or set LLM_PROVIDER=groq with "
                    "GROQ_API_KEY, or LLM_PROVIDER=openrouter with OPENROUTER_API_KEY. See backend/.env.example."
                ),
            )

        raw_json, model_error = self._complete_structured_json(text)
        if model_error:
            logger.info("llm.call_failed error=%s", model_error)
            return StructuredIntentJson(
                status="error",
                reject_reason="llm_call_failed",
                user_guidance="The language model could not produce a valid response. Try rephrasing.",
            )

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.info("llm.json_decode_failed err=%s raw_head=%r", e, raw_json[:200])
            return StructuredIntentJson(
                status="error",
                reject_reason="invalid_json_from_model",
                user_guidance="Could not parse model output safely.",
            )

        try:
            parsed = StructuredIntentJson.model_validate(data)
        except Exception as e:
            logger.info("llm.schema_validate_failed err=%s", e)
            return StructuredIntentJson(
                status="error",
                reject_reason="schema_validation_failed",
                user_guidance="Model output did not match the expected schema.",
            )

        return self._post_validate(text, parsed)

    def _post_validate(self, original_text: str, p: StructuredIntentJson) -> StructuredIntentJson:
        if p.status == "rejected":
            p.intent = None
            p.parameters = {}
            p.filters = {}
            if not p.reject_reason:
                p.reject_reason = "not_applicable_to_dataset"
            if not p.user_guidance:
                p.user_guidance = "Ask about orders, deliveries, billing, payments, or products in this dataset only."
            return p

        if p.status != "ok":
            return p

        intent = (p.intent or "").strip()
        if intent not in SUPPORTED_INTENTS:
            return StructuredIntentJson(
                status="rejected",
                reject_reason="unknown_or_unsupported_intent",
                user_guidance=f"Supported intents are: {', '.join(SUPPORTED_INTENTS)}.",
                entity=p.entity,
            )

        merged: dict[str, Any] = {}
        merged.update(p.filters or {})
        merged.update(p.parameters or {})

        if intent == "find_top_products_by_billing":
            clean = _pick(merged, {"limit", "sort_by", "language"})
            clean.setdefault("limit", 10)
            p.parameters = clean
        elif intent == "trace_order_flow":
            clean = _pick(merged, {"order_id", "sales_order", "include_node_metadata", "max_paths"})
            oid = clean.get("order_id") or clean.get("sales_order")
            if oid is None or str(oid).strip() == "":
                m = self._ORDER_ID_IN_TEXT.search(original_text)
                if m:
                    clean["order_id"] = m.group(1)
                else:
                    return StructuredIntentJson(
                        status="error",
                        intent=intent,
                        entity=p.entity or "order",
                        reject_reason="missing_order_id",
                        user_guidance="Specify a sales order number to trace (e.g. trace order 740506).",
                        parameters={},
                        filters={},
                    )
            p.parameters = clean
        elif intent == "find_incomplete_orders":
            clean = _pick(merged, {"criteria", "limit"})
            p.parameters = clean
        elif intent == "trace_billing_flow":
            clean = _pick(merged, {"billing_document", "invoice_id", "billing_id", "include_node_metadata", "max_paths"})
            bid = clean.get("billing_document") or clean.get("invoice_id") or clean.get("billing_id")
            if bid is None or str(bid).strip() == "":
                return StructuredIntentJson(
                    status="error",
                    intent=intent,
                    entity=p.entity or "invoice",
                    reject_reason="missing_billing_document",
                    user_guidance="Specify a billing document number to trace (e.g. trace billing 91150187).",
                    parameters={},
                    filters={},
                )
            clean["billing_document"] = str(bid).strip()
            clean.pop("invoice_id", None)
            clean.pop("billing_id", None)
            p.parameters = clean
        else:
            p.parameters = {}

        p.filters = dict(p.parameters)
        p.status = "ok"
        p.intent = intent

        if p.entity:
            p.entity = str(p.entity).strip().lower()
        return p


class GeminiIntentTranslator(IntentTranslatorBase):
    """Google Gemini — same schema as other providers."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.08,
    ) -> None:
        super().__init__(temperature=temperature)
        self._api_key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
        raw = _normalize_model_name(model_name or os.environ.get("GEMINI_MODEL") or "")
        if raw and raw not in ALLOWED_GEMINI_MODELS:
            logger.warning(
                "gemini.model_not_allowlisted using_default instead=%r allowed=%s",
                raw,
                sorted(ALLOWED_GEMINI_MODELS),
            )
            raw = ""
        self._model_name = raw or DEFAULT_MODEL_TRY_ORDER[0]

    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def primary_model_name(self) -> str:
        return self._model_name

    def _complete_structured_json(self, user_text: str) -> tuple[str, Optional[str]]:
        try:
            import google.generativeai as genai
        except ImportError as e:
            return "", f"import_error:{e}"

        genai.configure(api_key=self._api_key)
        system_instruction = _SYSTEM_PROMPT
        user_block = _USER_PROMPT_TEMPLATE.format(user_text=user_text)

        candidates = _build_model_try_list(self._model_name)
        last_error: Optional[str] = None

        for model_name in candidates:
            text, err = self._generate_one_model(genai, model_name, system_instruction, user_block)
            if text:
                if model_name != self._model_name:
                    logger.info("gemini.model_fallback_used requested=%r used=%r", self._model_name, model_name)
                else:
                    logger.info("gemini.model_ok model=%r", model_name)
                return text, None
            last_error = err
            if err and _is_rate_limit_error(err):
                return "", err
            retryable = err and (
                _is_model_availability_error(err) or err == "empty_or_blocked_response"
            )
            if retryable:
                logger.warning("gemini.model_skip model=%r reason=%s", model_name, (err or "")[:300])
                continue
            if err:
                return "", err

        return "", last_error or "all_models_failed"

    def _generate_one_model(
        self,
        genai: Any,
        model_name: str,
        system_instruction: str,
        user_block: str,
    ) -> tuple[str, Optional[str]]:
        """Try JSON MIME first; on failure retry plain text (some accounts/models differ)."""
        try:
            gen_cfg_json = genai.GenerationConfig(
                temperature=self._temperature,
                response_mime_type="application/json",
            )
        except Exception:
            gen_cfg_json = {"temperature": self._temperature, "response_mime_type": "application/json"}

        try:
            gen_cfg_plain = genai.GenerationConfig(temperature=self._temperature)
        except Exception:
            gen_cfg_plain = {"temperature": self._temperature}

        combined_prompt: Optional[str] = None
        try:
            model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
        except TypeError:
            model = genai.GenerativeModel(model_name)
            combined_prompt = f"{system_instruction}\n\n{user_block}"

        prompt = combined_prompt if combined_prompt is not None else user_block

        for cfg_name, cfg in (("json", gen_cfg_json), ("plain", gen_cfg_plain)):
            try:
                try:
                    resp = model.generate_content(prompt, generation_config=cfg)
                except TypeError:
                    resp = model.generate_content(prompt)
            except Exception as e:
                return "", str(e)

            try:
                text = (resp.text or "").strip()
            except Exception:
                text = ""
            if text:
                if cfg_name == "plain":
                    logger.info("gemini.used_plain_generation model=%r (json_mime unsupported or empty)", model_name)
                return text, None

        return "", "empty_or_blocked_response"


# --- Pipeline: NL -> intent -> QueryEngine --------------------------------------


def parse_natural_language(user_text: str, *, translator: Optional[IntentTranslatorBase] = None) -> StructuredIntentJson:
    t = translator or GeminiIntentTranslator()
    return t.parse(user_text)


def run_natural_language_query(
    engine: QueryEngine,
    user_text: str,
    *,
    translator: Optional[IntentTranslatorBase] = None,
    with_timings: bool = False,
) -> dict[str, Any]:
    """
    Full pipeline: NL -> structured intent -> :meth:`QueryEngine.execute`.

    Returns a single JSON-serializable dict suitable for APIs / LLM final response formatting.

    * ``success`` — ``True`` only when the structured query ran and ``query_result.success`` is ``True``.
    * ``parse`` — normalized intent JSON (alias of ``nl_parse``).
    * ``parse_status`` — ``ok`` | ``rejected`` | ``error`` from the translator.

    When ``with_timings`` is ``True``, ``timings_ms`` includes ``parse``, ``execute`` (if run), ``total``.
    """
    t0 = time.perf_counter()
    t = translator or GeminiIntentTranslator()
    t_parse0 = time.perf_counter()
    parsed = t.parse(user_text)
    t_parse1 = time.perf_counter()

    envelope: dict[str, Any] = {
        "success": False,
        "parse_status": parsed.status,
        "nl_parse": parsed.model_dump(mode="json"),
        "parse": parsed.model_dump(mode="json"),
        "query_result": None,
        "executed_query": False,
    }

    if parsed.status != "ok" or not parsed.intent:
        envelope["error"] = {
            "phase": "parse",
            "status": parsed.status,
            "reject_reason": parsed.reject_reason,
            "user_guidance": parsed.user_guidance,
        }
        if with_timings:
            envelope["timings_ms"] = {
                "parse": (t_parse1 - t_parse0) * 1000,
                "execute": 0.0,
                "total": (time.perf_counter() - t0) * 1000,
            }
        return envelope

    t_exec0 = time.perf_counter()
    q = engine.execute(parsed.intent, parsed.parameters)
    t_exec1 = time.perf_counter()
    envelope["query_result"] = q
    envelope["executed_query"] = True
    envelope["success"] = bool(q.get("success"))
    if not envelope["success"]:
        envelope["error"] = {"phase": "query", "detail": q.get("error")}
    if with_timings:
        envelope["timings_ms"] = {
            "parse": (t_parse1 - t_parse0) * 1000,
            "execute": (t_exec1 - t_exec0) * 1000,
            "total": (time.perf_counter() - t0) * 1000,
        }
    return envelope


def _smoke() -> None:
    logging.basicConfig(level=logging.INFO)
    from app.db.loader import load_o2c_bundle
    from app.graph.graph_builder import build_graph_from_bundle

    b = load_o2c_bundle()
    g, _ = build_graph_from_bundle(b)
    eng = QueryEngine(b, g)
    tr = GeminiIntentTranslator()
    if not tr.is_configured():
        print("GEMINI_API_KEY not set; testing local reject path only.")
        print(parse_natural_language("What is the capital of France?").model_dump(mode="json"))
        return
    out = run_natural_language_query(eng, "Which products are most billed? limit 5", translator=tr)
    print(json.dumps(out, indent=2, default=str)[:2500])


if __name__ == "__main__":
    _smoke()
