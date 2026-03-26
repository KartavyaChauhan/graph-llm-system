"""
Graph storage abstraction over NetworkX.

``IGraphStore`` is the narrow interface the rest of the app should depend on so we can swap
implementations (e.g. Neo4j, in-memory index) without rewriting traversal logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator, Optional

import networkx as nx

from app.graph.types import EdgeType, NodeType


class IGraphStore(ABC):
    """Minimal directed multigraph contract (O2C queries, traversal, export)."""

    @abstractmethod
    def add_node(self, node_id: str, *, node_type: NodeType, metadata: dict[str, Any]) -> None: ...

    @abstractmethod
    def add_edge(
        self,
        u: str,
        v: str,
        *,
        edge_type: EdgeType,
        attributes: Optional[dict[str, Any]] = None,
    ) -> str | int:
        """Return NetworkX edge key where applicable."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool: ...

    @abstractmethod
    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        """Return ``{"type": NodeType, "metadata": dict}`` or None."""

    @abstractmethod
    def iter_nodes(self) -> Iterator[tuple[str, dict[str, Any]]]: ...

    @abstractmethod
    def successors(self, node_id: str, *, edge_type: Optional[EdgeType] = None) -> list[str]: ...

    @abstractmethod
    def predecessors(self, node_id: str, *, edge_type: Optional[EdgeType] = None) -> list[str]: ...

    @abstractmethod
    def out_edges(
        self, node_id: str, *, edge_type: Optional[EdgeType] = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """``(u, v, data)`` for edges leaving ``node_id`` (``u == node_id``)."""

    @abstractmethod
    def in_edges(
        self, node_id: str, *, edge_type: Optional[EdgeType] = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """``(u, v, data)`` for edges entering ``node_id`` (``v == node_id``)."""

    @abstractmethod
    def number_of_nodes(self) -> int: ...

    @abstractmethod
    def number_of_edges(self) -> int: ...

    @abstractmethod
    def to_networkx(self) -> nx.MultiDiGraph:
        """Escape hatch for advanced analytics; prefer IGraphStore for app code."""

    @abstractmethod
    def replace_node_metadata(self, node_id: str, metadata: dict[str, Any]) -> None:
        """Replace the entire metadata dict for an existing node (no-op if missing)."""


class NetworkXGraphStore(IGraphStore):
    """
    NetworkX ``MultiDiGraph`` backend.

    Node attributes stored as: ``node_type`` (:class:`NodeType`), ``metadata`` (dict).

    Edge attributes stored as: ``edge_type`` (:class:`EdgeType`), plus optional free-form keys.
    """

    __slots__ = ("_g",)

    def __init__(self, graph: Optional[nx.MultiDiGraph] = None) -> None:
        self._g = graph if graph is not None else nx.MultiDiGraph()

    def add_node(self, node_id: str, *, node_type: NodeType, metadata: dict[str, Any]) -> None:
        self._g.add_node(node_id, node_type=node_type, metadata=dict(metadata))

    def add_edge(
        self,
        u: str,
        v: str,
        *,
        edge_type: EdgeType,
        attributes: Optional[dict[str, Any]] = None,
    ) -> str | int:
        data: dict[str, Any] = {"edge_type": edge_type}
        if attributes:
            for k, val in attributes.items():
                if k == "edge_type":
                    continue
                data[k] = val
        key = self._g.add_edge(u, v, **data)
        return key

    def has_node(self, node_id: str) -> bool:
        return self._g.has_node(node_id)

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        if not self._g.has_node(node_id):
            return None
        raw = self._g.nodes[node_id]
        return {"type": raw.get("node_type"), "metadata": dict(raw.get("metadata") or {})}

    def iter_nodes(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for nid, data in self._g.nodes(data=True):
            yield nid, {"type": data.get("node_type"), "metadata": dict(data.get("metadata") or {})}

    def successors(self, node_id: str, *, edge_type: Optional[EdgeType] = None) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for _, v, data in self._g.out_edges(node_id, data=True):
            et = data.get("edge_type")
            if edge_type is not None and et != edge_type:
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def predecessors(self, node_id: str, *, edge_type: Optional[EdgeType] = None) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for u, _, data in self._g.in_edges(node_id, data=True):
            et = data.get("edge_type")
            if edge_type is not None and et != edge_type:
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    def out_edges(
        self, node_id: str, *, edge_type: Optional[EdgeType] = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for u, v, key, data in self._g.out_edges(node_id, keys=True, data=True):
            et = data.get("edge_type")
            if edge_type is not None and et != edge_type:
                continue
            rows.append((u, v, dict(data)))
        return rows

    def in_edges(
        self, node_id: str, *, edge_type: Optional[EdgeType] = None
    ) -> list[tuple[str, str, dict[str, Any]]]:
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for u, v, key, data in self._g.in_edges(node_id, keys=True, data=True):
            et = data.get("edge_type")
            if edge_type is not None and et != edge_type:
                continue
            rows.append((u, v, dict(data)))
        return rows

    def number_of_nodes(self) -> int:
        return int(self._g.number_of_nodes())

    def number_of_edges(self) -> int:
        return int(self._g.number_of_edges())

    def to_networkx(self) -> nx.MultiDiGraph:
        return self._g

    def replace_node_metadata(self, node_id: str, metadata: dict[str, Any]) -> None:
        if not self._g.has_node(node_id):
            return
        self._g.nodes[node_id]["metadata"] = dict(metadata)
