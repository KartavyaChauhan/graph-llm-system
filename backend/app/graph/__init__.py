"""O2C graph abstraction: types, NetworkX-backed store, manager, builder."""

from typing import Any

from app.graph.manager import GraphManager, graph_manager_from_networkx
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
    payment_node_id,
    product_node_id,
)

__all__ = [
    "EdgeType",
    "FlowIssue",
    "FlowIssueKind",
    "GraphBuildReport",
    "GraphManager",
    "IGraphStore",
    "NetworkXGraphStore",
    "NodeType",
    "OrderLifecycle",
    "Severity",
    "address_node_id",
    "build_graph_from_bundle",
    "customer_node_id",
    "delivery_node_id",
    "graph_manager_from_networkx",
    "invoice_node_id",
    "order_node_id",
    "payment_node_id",
    "product_node_id",
]


def __getattr__(name: str) -> Any:
    if name == "build_graph_from_bundle":
        from app.graph.graph_builder import build_graph_from_bundle as fn

        return fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
