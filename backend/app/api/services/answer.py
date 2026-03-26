"""Deterministic, data-backed answer strings for API responses (no generative embellishment)."""

from __future__ import annotations

from typing import Any


def build_answer_from_pipeline(pipeline: dict[str, Any]) -> str:
    """
    Produce a concise ``answer`` from ``run_natural_language_query`` output.

    All claims are derived from ``parse`` / ``query_result`` fields present in ``pipeline``.
    """
    parse = pipeline.get("parse") or pipeline.get("nl_parse") or {}
    status = parse.get("status") or pipeline.get("parse_status")

    if status == "rejected":
        return (parse.get("user_guidance") or parse.get("reject_reason") or "Query is outside the Order-to-Cash dataset scope.").strip()

    if status == "error":
        err = pipeline.get("error") or {}
        phase = err.get("phase", "parse")
        if phase == "parse":
            return (parse.get("user_guidance") or parse.get("reject_reason") or "Could not interpret the query.").strip()
        return (err.get("user_guidance") or err.get("message") or str(err) or "Query processing failed.").strip()

    if not pipeline.get("success"):
        err = pipeline.get("error") or {}
        if err.get("phase") == "query":
            detail = err.get("detail") or {}
            msg = detail.get("message") if isinstance(detail, dict) else str(detail)
            return msg or "The data query did not complete successfully."
        return "The data query did not complete successfully."

    intent = parse.get("intent") or ""
    qr = pipeline.get("query_result") or {}
    result = qr.get("result") or {}

    if intent == "find_top_products_by_billing":
        rows = result.get("rows") or []
        if not rows:
            return "No billing line items matched the aggregation (empty result)."
        top = rows[0]
        mat = top.get("material", "")
        cnt = top.get("billing_line_count", "")
        return (
            f"Top billed materials (by billing line count): showing {len(rows)} row(s). "
            f"Leading material: {mat} with {cnt} billing line(s). Full rows are in data.result.rows."
        )

    if intent == "trace_order_flow":
        if not result.get("found"):
            return result.get("note") or "Order was not found in the loaded graph."
        npaths = result.get("path_count", 0)
        life = result.get("lifecycle") or {}
        missing = life.get("missing_links") or []
        base = f"Traced order flow: {npaths} path(s) enumerated in the graph."
        if missing:
            return base + f" Gaps noted: {'; '.join(missing)}."
        return base

    if intent == "find_incomplete_orders":
        matched = result.get("matched_count", 0)
        scanned = result.get("orders_scanned", 0)
        crit = (result.get("filter") or {}).get("criteria")
        c = ", ".join(crit) if isinstance(crit, list) else str(crit)
        return f"Incomplete-order scan: {matched} order(s) matched criteria [{c}] out of {scanned} order node(s) examined."

    return "Query completed. See data.result for structured output."
