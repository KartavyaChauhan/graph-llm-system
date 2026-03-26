"""
High-level graph API: lifecycle, dependency direction, and data-quality scans.

Directional convention (downstream / forward)
----------------------------------------------
``CUSTOMER_PLACED_ORDER``: Customer → Order  
``ORDER_HAS_DELIVERY``: Order → Delivery  
``DELIVERY_HAS_INVOICE``: Delivery → Invoice  
``INVOICE_HAS_PAYMENT``: Invoice → Payment  
``ORDER_CONTAINS_PRODUCT``: Order → Product  

*Downstream* follows outgoing edges in this chain (toward payment / fulfillment).  
*Upstream* follows incoming edges (back toward customer).

Address nodes are not linked with a typed edge (assignment lists only five edge types). They are
reachable via :meth:`addresses_for_customer` using metadata written at build time.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Collection, Optional

import networkx as nx

from app.graph.store import IGraphStore, NetworkXGraphStore
from app.graph.types import (
    EdgeType,
    FlowIssue,
    FlowIssueKind,
    GraphBuildReport,
    NodeType,
    OrderLifecycle,
    Severity,
    address_node_id,
    customer_node_id,
    delivery_node_id,
    invoice_node_id,
    order_node_id,
    parse_stripped_id,
    payment_node_id,
    product_node_id,
)


class GraphManager:
    """
    Application-facing façade over :class:`IGraphStore`.

    Keeps traversal and quality rules in one place so APIs and LLM tools call stable methods instead
    of manipulating NetworkX directly.
    """

    __slots__ = ("_store", "build_report")

    _O2C_FORWARD: frozenset[EdgeType] = frozenset(
        {
            EdgeType.CUSTOMER_PLACED_ORDER,
            EdgeType.ORDER_HAS_DELIVERY,
            EdgeType.DELIVERY_HAS_INVOICE,
            EdgeType.INVOICE_HAS_PAYMENT,
            EdgeType.ORDER_CONTAINS_PRODUCT,
        }
    )

    def __init__(self, store: IGraphStore, *, build_report: Optional[GraphBuildReport] = None) -> None:
        self._store = store
        self.build_report = build_report

    @property
    def store(self) -> IGraphStore:
        return self._store

    # --- Node resolution helpers ---

    @staticmethod
    def normalize_order_node_id(key: str) -> str:
        s = key.strip()
        if s.startswith("order:"):
            return s
        return order_node_id(s)

    @staticmethod
    def normalize_delivery_node_id(key: str) -> str:
        s = key.strip()
        if s.startswith("delivery:"):
            return s
        return delivery_node_id(s)

    @staticmethod
    def normalize_invoice_node_id(key: str) -> str:
        s = key.strip()
        if s.startswith("invoice:"):
            return s
        return invoice_node_id(s)

    def get_node(self, node_id: str) -> Optional[dict]:
        """``{"type": NodeType, "metadata": dict}``."""
        return self._store.get_node(node_id)

    def outgoing_relationships(self, node_id: str) -> list[dict[str, Any]]:
        """Typed edges leaving ``node_id`` (for APIs / LLM tools)."""
        rows: list[dict[str, Any]] = []
        for _u, v, data in self._store.out_edges(node_id):
            et = data.get("edge_type")
            attrs = {k: val for k, val in data.items() if k != "edge_type"}
            rows.append({"edge_type": et, "target_id": v, "attributes": attrs})
        return rows

    def incoming_relationships(self, node_id: str) -> list[dict[str, Any]]:
        """Typed edges entering ``node_id``."""
        rows: list[dict[str, Any]] = []
        for u, _v, data in self._store.in_edges(node_id):
            et = data.get("edge_type")
            attrs = {k: val for k, val in data.items() if k != "edge_type"}
            rows.append({"edge_type": et, "source_id": u, "attributes": attrs})
        return rows

    def counts_by_node_type(self) -> dict[str, int]:
        """Histogram of :class:`NodeType` values (string keys)."""
        hist: dict[str, int] = {}
        for _nid, payload in self._store.iter_nodes():
            t = payload.get("type")
            key = getattr(t, "value", str(t))
            hist[key] = hist.get(key, 0) + 1
        return dict(sorted(hist.items()))

    def counts_by_edge_type(self) -> dict[str, int]:
        """Histogram of :class:`EdgeType` over all edges (may be slow on huge graphs)."""
        hist: dict[str, int] = {}
        g = self._store.to_networkx()
        for _u, _v, _k, data in g.edges(keys=True, data=True):
            et = data.get("edge_type")
            key = getattr(et, "value", str(et))
            hist[key] = hist.get(key, 0) + 1
        return dict(sorted(hist.items()))

    def iter_nodes_by_type(self, node_type: NodeType):
        for nid, payload in self._store.iter_nodes():
            if payload.get("type") == node_type:
                yield nid, payload["metadata"]

    # --- Addresses (metadata linkage, not a typed edge) ---

    def addresses_for_customer(self, customer_number: str) -> list[tuple[str, dict]]:
        """Return ``(address_node_id, metadata)`` pairs for a customer master number."""
        cid = customer_node_id(customer_number)
        raw = self._store.get_node(cid)
        if not raw:
            return []
        meta = raw.get("metadata") or {}
        ids = meta.get("address_node_ids") or []
        out: list[tuple[str, dict]] = []
        for aid in ids:
            n = self._store.get_node(aid)
            if n:
                out.append((aid, n.get("metadata") or {}))
        return out

    # --- Lifecycle ---

    def order_lifecycle(self, order_key: str) -> OrderLifecycle:
        """
        Aggregate downstream O2C entities for a sales order plus customer and addresses.

        ``order_key`` may be ``\"740506\"`` or ``\"order:740506\"``.
        """
        oid = self.normalize_order_node_id(order_key)
        missing: list[str] = []

        if not self._store.has_node(oid):
            return OrderLifecycle(
                order_id=oid,
                customer_id=None,
                delivery_ids=[],
                invoice_ids=[],
                payment_ids=[],
                product_ids=[],
                address_ids=[],
                missing_links=["order node not found in graph"],
            )

        customers = self._store.predecessors(oid, edge_type=EdgeType.CUSTOMER_PLACED_ORDER)
        customer_id = customers[0] if customers else None
        if not customer_id:
            missing.append("no CUSTOMER_PLACED_ORDER predecessor")

        deliveries = self._store.successors(oid, edge_type=EdgeType.ORDER_HAS_DELIVERY)
        if not deliveries:
            missing.append("no deliveries (ORDER_HAS_DELIVERY)")

        products = self._store.successors(oid, edge_type=EdgeType.ORDER_CONTAINS_PRODUCT)
        if not products:
            missing.append("no products on order (ORDER_CONTAINS_PRODUCT)")

        invoices: list[str] = []
        seen_inv: set[str] = set()
        for d in deliveries:
            for inv in self._store.successors(d, edge_type=EdgeType.DELIVERY_HAS_INVOICE):
                if inv not in seen_inv:
                    seen_inv.add(inv)
                    invoices.append(inv)

        if deliveries and not invoices:
            missing.append("no invoices linked from deliveries (DELIVERY_HAS_INVOICE)")

        payments: list[str] = []
        seen_pay: set[str] = set()
        for inv in invoices:
            for p in self._store.successors(inv, edge_type=EdgeType.INVOICE_HAS_PAYMENT):
                if p not in seen_pay:
                    seen_pay.add(p)
                    payments.append(p)

        if invoices and not payments:
            missing.append("no payments linked from invoices (INVOICE_HAS_PAYMENT)")

        address_ids: list[str] = []
        if customer_id:
            cmeta = (self._store.get_node(customer_id) or {}).get("metadata") or {}
            address_ids = list(cmeta.get("address_node_ids") or [])

        return OrderLifecycle(
            order_id=oid,
            customer_id=customer_id,
            delivery_ids=deliveries,
            invoice_ids=invoices,
            payment_ids=payments,
            product_ids=products,
            address_ids=address_ids,
            missing_links=missing,
        )

    # --- Upstream / downstream ---

    def downstream_nodes(
        self,
        start_node_id: str,
        *,
        edge_types: Optional[Collection[EdgeType]] = None,
    ) -> set[str]:
        """All nodes reachable via **outgoing** edges whose type is in ``edge_types`` (default: all O2C types)."""
        et = frozenset(edge_types) if edge_types is not None else self._O2C_FORWARD
        return self._walk(start_node_id, forward=True, edge_types=et)

    def upstream_nodes(
        self,
        start_node_id: str,
        *,
        edge_types: Optional[Collection[EdgeType]] = None,
    ) -> set[str]:
        """All nodes reachable via **incoming** edges whose type is in ``edge_types`` (default: all O2C types)."""
        et = frozenset(edge_types) if edge_types is not None else self._O2C_FORWARD
        return self._walk(start_node_id, forward=False, edge_types=et)

    def _walk(self, start: str, *, forward: bool, edge_types: Collection[EdgeType]) -> set[str]:
        seen: set[str] = set()
        dq: deque[str] = deque([start])
        while dq:
            n = dq.popleft()
            if n in seen:
                continue
            seen.add(n)
            if forward:
                for et in edge_types:
                    for m in self._store.successors(n, edge_type=et):
                        if m not in seen:
                            dq.append(m)
            else:
                for et in edge_types:
                    for m in self._store.predecessors(n, edge_type=et):
                        if m not in seen:
                            dq.append(m)
        return seen

    # --- Broken flows ---

    def detect_broken_flows(self) -> list[FlowIssue]:
        """
        Scan the graph for common O2C gaps. Does not mutate the graph.

        Severity is heuristic: structural breaks are ``warning``; possible business-valid states
        (e.g. unpaid invoice snapshot) are ``info``.
        """
        issues: list[FlowIssue] = []

        for oid, _ in self.iter_nodes_by_type(NodeType.ORDER):
            life = self.order_lifecycle(oid)
            if "no CUSTOMER_PLACED_ORDER predecessor" in life.missing_links:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.MISSING_CUSTOMER,
                        severity=Severity.WARNING,
                        message="Order has no linked customer node via CUSTOMER_PLACED_ORDER.",
                        node_id=oid,
                    )
                )
            if "no deliveries (ORDER_HAS_DELIVERY)" in life.missing_links:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.ORDER_NO_DELIVERY,
                        severity=Severity.WARNING,
                        message="Order has no outbound delivery in this extract.",
                        node_id=oid,
                    )
                )
            if "no products on order (ORDER_CONTAINS_PRODUCT)" in life.missing_links:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.ORDER_NO_PRODUCT,
                        severity=Severity.WARNING,
                        message="Order has no product lines materialized as ORDER_CONTAINS_PRODUCT edges.",
                        node_id=oid,
                    )
                )
            if "no invoices linked from deliveries (DELIVERY_HAS_INVOICE)" in life.missing_links:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.DELIVERY_NO_INVOICE,
                        severity=Severity.WARNING,
                        message="Order has deliveries but no billing documents reference them.",
                        node_id=oid,
                        related_node_ids=tuple(life.delivery_ids),
                    )
                )
            if "no payments linked from invoices (INVOICE_HAS_PAYMENT)" in life.missing_links:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.INVOICE_NO_PAYMENT,
                        severity=Severity.INFO,
                        message="Invoices exist but no payment/clearing nodes are linked (open items or extract gap).",
                        node_id=oid,
                        related_node_ids=tuple(life.invoice_ids),
                    )
                )

        for did, _ in self.iter_nodes_by_type(NodeType.DELIVERY):
            preds = self._store.predecessors(did, edge_type=EdgeType.ORDER_HAS_DELIVERY)
            if not preds:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.DELIVERY_NO_ORDER,
                        severity=Severity.WARNING,
                        message="Delivery is not linked to any order (ORDER_HAS_DELIVERY).",
                        node_id=did,
                    )
                )
            succ = self._store.successors(did, edge_type=EdgeType.DELIVERY_HAS_INVOICE)
            if not succ:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.DELIVERY_NO_INVOICE,
                        severity=Severity.WARNING,
                        message="Delivery has no linked invoice.",
                        node_id=did,
                    )
                )

        for iid, _ in self.iter_nodes_by_type(NodeType.INVOICE):
            preds = self._store.predecessors(iid, edge_type=EdgeType.DELIVERY_HAS_INVOICE)
            if not preds:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.INVOICE_NO_DELIVERY,
                        severity=Severity.WARNING,
                        message="Invoice is not linked from any delivery (may be manual/credit note path).",
                        node_id=iid,
                    )
                )

        for pid, _ in self.iter_nodes_by_type(NodeType.PAYMENT):
            preds = self._store.predecessors(pid, edge_type=EdgeType.INVOICE_HAS_PAYMENT)
            if not preds:
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.PAYMENT_NO_INVOICE,
                        severity=Severity.INFO,
                        message="Payment/clearing node has no linked invoice in this graph projection.",
                        node_id=pid,
                    )
                )

        for aid, meta in self.iter_nodes_by_type(NodeType.ADDRESS):
            cust = (meta or {}).get("business_partner")
            if not cust or not self._store.has_node(customer_node_id(str(cust))):
                issues.append(
                    FlowIssue(
                        kind=FlowIssueKind.ORPHAN_ADDRESS,
                        severity=Severity.WARNING,
                        message="Address node missing customer metadata or customer node absent.",
                        node_id=aid,
                    )
                )

        return issues


def graph_manager_from_networkx(
    g: Optional[nx.MultiDiGraph] = None, *, build_report: Optional[GraphBuildReport] = None
) -> GraphManager:
    """Convenience for tests: wrap an existing NetworkX graph."""
    return GraphManager(NetworkXGraphStore(g or nx.MultiDiGraph()), build_report=build_report)
