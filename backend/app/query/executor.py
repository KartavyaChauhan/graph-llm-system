"""
Query execution: runs :class:`ExecutionPlan` steps against bundle + graph.

All numbers and paths are derived from loaded data — no canned answers.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Optional

from app.db.loader import O2CDataBundle
from app.graph.manager import GraphManager
from app.graph.types import EdgeType, NodeType
from app.query.types import (
    INTENT_FIND_INCOMPLETE_ORDERS,
    INTENT_FIND_TOP_PRODUCTS_BY_BILLING,
    INTENT_TRACE_ORDER_FLOW,
    ExecutionPlan,
    QueryError,
)


class QueryExecutor:
    def __init__(self, bundle: O2CDataBundle, graph: GraphManager) -> None:
        self._bundle = bundle
        self._graph = graph

    def run(self, plan: ExecutionPlan) -> Any | QueryError:
        if not plan.steps:
            return QueryError(code="empty_plan", message="Plan has no steps.", details={})
        # Single-composite intents today: dispatch by first step op
        op = plan.steps[0].get("op")
        if plan.intent == INTENT_FIND_TOP_PRODUCTS_BY_BILLING and op == "aggregate_billing_items_by_material":
            return self._exec_top_products(plan.steps[0])
        if plan.intent == INTENT_TRACE_ORDER_FLOW and op == "multi_hop_order_to_cash_paths":
            return self._exec_trace_order(plan.steps[0], plan.steps[1] if len(plan.steps) > 1 else None)
        if plan.intent == INTENT_FIND_INCOMPLETE_ORDERS and op == "filter_orders_by_lifecycle_gaps":
            return self._exec_incomplete_orders(plan.steps[0])
        return QueryError(code="unsupported_plan", message=f"Unsupported step op {op!r}.", details={"plan": plan.intent})

    def _exec_top_products(self, step: dict[str, Any]) -> dict[str, Any]:
        limit = int(step["limit"])
        sort_by = str(step["sort_by"])
        language = str(step["language"])

        counts: dict[str, int] = defaultdict(int)
        sums: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

        for _k, line in self._bundle.billing_items.items():
            mat = (line.material or "").strip()
            if not mat:
                continue
            counts[mat] += 1
            if line.net_amount is not None:
                sums[mat] += line.net_amount

        rows: list[tuple[str, int, Decimal]] = [(m, counts[m], sums[m]) for m in counts]
        if sort_by == "net_amount_sum":
            rows.sort(key=lambda r: (-r[2], -r[1], r[0]))
        else:
            rows.sort(key=lambda r: (-r[1], -r[2], r[0]))

        top = rows[:limit]
        out_rows: list[dict[str, Any]] = []
        for material, c, s in top:
            desc = self._bundle.product_descriptions.get((material, language))
            text = desc.product_description if desc else None
            out_rows.append(
                {
                    "material": material,
                    "billing_line_count": c,
                    "net_amount_sum": str(s),
                    "product_description": text,
                    "description_language": language if text else None,
                }
            )

        return {
            "aggregation": {
                "group_by": "billing_document_item.material",
                "metrics": ["billing_line_count", "net_amount_sum"],
                "sort_by": sort_by,
                "limit": limit,
                "distinct_materials_in_result": len(out_rows),
            },
            "rows": out_rows,
            "source_row_counts": {
                "billing_items_scanned": len(self._bundle.billing_items),
            },
        }

    def _exec_trace_order(self, step: dict[str, Any], lifecycle_step: Optional[dict[str, Any]]) -> dict[str, Any]:
        order_key = str(step["order_key"])
        include_meta = bool(step.get("include_node_metadata", True))
        max_paths = int(step.get("max_paths", 256))

        oid = self._graph.normalize_order_node_id(order_key)
        if not self._graph.store.has_node(oid):
            return {
                "order_node_id": oid,
                "found": False,
                "paths": [],
                "lifecycle": None,
                "note": "Order node not present in graph (no such sales order in loaded extract or graph build skipped it).",
            }

        paths = self._enumerate_o2c_paths(oid, max_paths=max_paths)
        path_payload: list[dict[str, Any]] = []
        for chain in paths:
            path_payload.append(
                {
                    "node_ids": chain,
                    "edge_sequence": self._edge_labels_for_path(chain),
                    "nodes": [self._node_snapshot(nid, include_meta) for nid in chain],
                }
            )

        life = None
        if lifecycle_step and lifecycle_step.get("op") == "order_lifecycle_snapshot":
            life_obj = self._graph.order_lifecycle(oid)
            life = {
                "order_id": life_obj.order_id,
                "customer_id": life_obj.customer_id,
                "delivery_ids": life_obj.delivery_ids,
                "invoice_ids": life_obj.invoice_ids,
                "payment_ids": life_obj.payment_ids,
                "product_ids": life_obj.product_ids,
                "address_ids": life_obj.address_ids,
                "missing_links": life_obj.missing_links,
            }

        return {
            "order_node_id": oid,
            "found": True,
            "paths": path_payload,
            "path_count": len(path_payload),
            "lifecycle": life,
        }

    def _enumerate_o2c_paths(self, order_node_id: str, *, max_paths: int) -> list[list[str]]:
        out: list[list[str]] = []
        g = self._graph.store
        deliveries = g.successors(order_node_id, edge_type=EdgeType.ORDER_HAS_DELIVERY)
        for d in deliveries:
            if len(out) >= max_paths:
                break
            invoices = g.successors(d, edge_type=EdgeType.DELIVERY_HAS_INVOICE)
            if not invoices:
                out.append([order_node_id, d])
                continue
            for inv in invoices:
                if len(out) >= max_paths:
                    break
                pays = g.successors(inv, edge_type=EdgeType.INVOICE_HAS_PAYMENT)
                if not pays:
                    out.append([order_node_id, d, inv])
                    continue
                for pay in pays:
                    if len(out) >= max_paths:
                        break
                    out.append([order_node_id, d, inv, pay])
        return out

    def _edge_labels_for_path(self, chain: list[str]) -> list[str]:
        if len(chain) < 2:
            return []
        labels: list[str] = []
        g = self._graph.store
        for i in range(len(chain) - 1):
            u, v = chain[i], chain[i + 1]
            found = ""
            for _uu, _vv, data in g.out_edges(u):
                if _vv == v:
                    et = data.get("edge_type")
                    found = getattr(et, "value", str(et))
                    break
            labels.append(found or "UNKNOWN")
        return labels

    def _node_snapshot(self, node_id: str, include_metadata: bool) -> dict[str, Any]:
        raw = self._graph.get_node(node_id)
        if not raw:
            return {"node_id": node_id, "type": None, "metadata": None}
        t = raw.get("type")
        typ = getattr(t, "value", str(t))
        if not include_metadata:
            return {"node_id": node_id, "type": typ, "metadata": None}
        meta = raw.get("metadata") or {}
        return {"node_id": node_id, "type": typ, "metadata": meta}

    def _exec_incomplete_orders(self, step: dict[str, Any]) -> dict[str, Any]:
        criteria = list(step["criteria"])
        limit = int(step["limit"])
        matched: list[dict[str, Any]] = []

        def has_crit(key: str) -> bool:
            return key in criteria

        for oid, _meta in self._graph.iter_nodes_by_type(NodeType.ORDER):
            if len(matched) >= limit:
                break
            life = self._graph.order_lifecycle(oid)
            flags: dict[str, bool] = {}
            if has_crit("missing_customer") and not life.customer_id:
                flags["missing_customer"] = True
            if has_crit("missing_delivery") and not life.delivery_ids:
                flags["missing_delivery"] = True
            if has_crit("missing_invoice") and bool(life.delivery_ids) and not life.invoice_ids:
                flags["missing_invoice"] = True
            if has_crit("missing_payment") and bool(life.invoice_ids) and not life.payment_ids:
                flags["missing_payment"] = True
            if has_crit("missing_product_lines") and not life.product_ids:
                flags["missing_product_lines"] = True

            if flags:
                matched.append(
                    {
                        "order_node_id": oid,
                        "flags": flags,
                        "lifecycle": {
                            "customer_id": life.customer_id,
                            "delivery_ids": life.delivery_ids,
                            "invoice_ids": life.invoice_ids,
                            "payment_ids": life.payment_ids,
                            "product_ids": life.product_ids,
                        },
                        "missing_links": life.missing_links,
                    }
                )

        return {
            "filter": {"criteria": criteria, "logic": "any_flag_true"},
            "orders_scanned": self._graph.counts_by_node_type().get(NodeType.ORDER.value, 0),
            "matched_count": len(matched),
            "limit": limit,
            "rows": matched,
        }
