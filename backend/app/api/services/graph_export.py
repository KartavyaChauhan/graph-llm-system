"""Serialize :class:`~app.graph.manager.GraphManager` for API / viz clients."""

from __future__ import annotations

import json
from typing import Any

from app.api.schemas import GraphEdgeDTO, GraphNodeDTO, GraphResponse
from app.graph.manager import GraphManager


def _json_safe(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


def _node_label(node_id: str, node_type: str, meta: dict[str, Any]) -> str:
    if node_type == "Order" and meta.get("salesOrder"):
        return f"SO {meta['salesOrder']}"
    if node_type == "Delivery" and meta.get("deliveryDocument"):
        return f"DEL {meta['deliveryDocument']}"
    if node_type == "Invoice" and meta.get("billingDocument"):
        return f"INV {meta['billingDocument']}"
    if node_type == "Payment":
        parts = node_id.replace("payment:", "").split("|")
        if len(parts) >= 3:
            return f"PAY {parts[-1]}"
    if node_type == "Customer":
        return meta.get("businessPartnerName") or meta.get("businessPartner") or node_id
    if node_type == "Product":
        return meta.get("product") or node_id.replace("product:", "")
    if node_type == "Address":
        c = meta.get("cityName") or ""
        return f"ADDR {c}".strip() or node_id
    return node_id


def _trim_metadata(meta: dict[str, Any], max_chars: int) -> dict[str, Any]:
    if max_chars <= 0:
        return {}
    out: dict[str, Any] = {}
    size = 0
    for k in sorted(meta.keys(), key=str):
        v = _json_safe(meta[k])
        chunk = json.dumps({k: v}, ensure_ascii=False)
        if size + len(chunk) > max_chars:
            break
        out[str(k)] = v
        size += len(chunk)
    return out


def build_graph_response(
    manager: GraphManager,
    *,
    max_nodes: int,
    max_edges: int,
    metadata_max_chars: int,
) -> GraphResponse:
    g = manager.store.to_networkx()
    warnings: list[str] = []
    total_n = g.number_of_nodes()
    total_e = g.number_of_edges()

    truncated = total_n > max_nodes or total_e > max_edges
    if truncated:
        warnings.append(
            f"Graph truncated for API limits: nodes {total_n}->{min(total_n, max_nodes)}, "
            f"edges {total_e}->{min(total_e, max_edges)}."
        )

    node_items = list(g.nodes(data=True))
    if len(node_items) > max_nodes:
        node_items = node_items[:max_nodes]

    nodes: list[GraphNodeDTO] = []
    for nid, data in node_items:
        nt = data.get("node_type")
        typ = getattr(nt, "value", str(nt))
        meta = dict(data.get("metadata") or {})
        nodes.append(
            GraphNodeDTO(
                id=nid,
                type=typ,
                label=_node_label(nid, typ, meta),
                metadata=_trim_metadata(meta, metadata_max_chars),
            )
        )

    allowed = {n.id for n in nodes}
    edges_out: list[GraphEdgeDTO] = []
    for u, v, key, ed in g.edges(keys=True, data=True):
        if u not in allowed or v not in allowed:
            continue
        et = ed.get("edge_type")
        et_s = getattr(et, "value", str(et))
        attrs = {k: _json_safe(val) for k, val in ed.items() if k != "edge_type"}
        edges_out.append(
            GraphEdgeDTO(source=u, target=v, type=et_s, key=key, attributes=attrs)
        )
        if len(edges_out) >= max_edges:
            break

    if len(edges_out) >= max_edges and total_e > max_edges:
        truncated = True

    stats = {
        "node_count_returned": len(nodes),
        "edge_count_returned": len(edges_out),
        "node_count_total": total_n,
        "edge_count_total": total_e,
        "truncated": truncated,
    }
    return GraphResponse(nodes=nodes, edges=edges_out, stats=stats, truncated=truncated, warnings=warnings)
