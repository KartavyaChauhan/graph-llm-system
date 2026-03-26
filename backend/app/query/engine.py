"""
Structured query engine (mini analytics layer) over :class:`~app.db.loader.O2CDataBundle` and
:class:`~app.graph.manager.GraphManager`.

How it works internally
----------------------
1. **Planner** — Validates intent identifiers and parameters, emits an :class:`~app.query.types.ExecutionPlan`
   (human-readable interpretation + machine ``steps``). No database reads: plans are pure logic, so you can
   log exactly what the system *would* do before execution.
2. **Executor** — Runs each plan against real structures: bundle dicts for set scans / aggregations, graph
   store for typed traversals and lifecycle predicates. Results are plain Python structures (dicts/lists),
   never template text.
3. **Formatter** — Converts results to JSON-safe dicts with an explicit ``interpretation`` block so an LLM
   or UI can cite *data-backed* sections separately from narrative it might generate.

Why this scales
---------------
- **Intent registry** — New analytics are a new planner branch + executor op; callers keep using
  ``execute(intent, parameters)`` without touching graph construction.
- **Separation of concerns** — Aggregation-heavy queries can read the bundle in O(n) over relevant maps;
  path queries use the graph’s index (NetworkX) without re-joining raw tables in application code.
- **Observability** — Standard logging records intent, normalized parameters, and the plan string; raw
  payloads stay out of logs by default (can be large).
- **Upgrade path** — The executor can swap in columnar stores, SQL, or precomputed cubes behind the same
  ``ExecutionPlan`` ops when data volume grows; the planner/formatter API stays stable.

Usage::

    from app.db.loader import load_o2c_bundle
    from app.graph.graph_builder import build_graph_from_bundle
    from app.query.engine import QueryEngine

    bundle = load_o2c_bundle()
    graph, _ = build_graph_from_bundle(bundle)
    engine = QueryEngine(bundle, graph)
    out = engine.execute("find_top_products_by_billing", {"limit": 5})
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.db.loader import O2CDataBundle
from app.graph.manager import GraphManager
from app.query.executor import QueryExecutor
from app.query.formatter import ResultFormatter
from app.query.planner import QueryPlanner
from app.query.types import ExecutionPlan, QueryError, SUPPORTED_INTENTS

logger = logging.getLogger("app.query.engine")


class QueryEngine:
    """
    Facade: plan → execute → format, with structured logging.

    Thread-safe if the underlying ``bundle`` and ``graph`` are not mutated concurrently.
    """

    __slots__ = ("_bundle", "_graph", "_planner", "_executor", "_formatter")

    def __init__(self, bundle: O2CDataBundle, graph: GraphManager) -> None:
        self._bundle = bundle
        self._graph = graph
        self._planner = QueryPlanner()
        self._executor = QueryExecutor(bundle, graph)
        self._formatter = ResultFormatter()

    @property
    def bundle(self) -> O2CDataBundle:
        return self._bundle

    @property
    def graph(self) -> GraphManager:
        return self._graph

    def supported_intents(self) -> tuple[str, ...]:
        return SUPPORTED_INTENTS

    def plan_only(self, intent: str, parameters: Optional[dict[str, Any]] = None) -> ExecutionPlan | QueryError:
        """Return a plan without executing (for debugging / LLM tool dry-runs)."""
        params = dict(parameters or {})
        return self._planner.plan(intent, params)

    def execute(self, intent: str, parameters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """
        Run a structured intent. Always returns a JSON-serializable dict (check ``success``).
        """
        params = dict(parameters or {})
        logger.info("query.request intent=%r parameters=%s", intent, params)

        planned = self._planner.plan(intent, params)
        if isinstance(planned, QueryError):
            logger.info(
                "query.plan_failed intent=%r code=%s message=%s",
                intent,
                planned.code,
                planned.message,
            )
            return self._formatter.format_error(intent=intent, parameters=params, err=planned, plan=None)

        plan: ExecutionPlan = planned
        logger.info("query.interpreted intent=%r plan=%s", intent, plan.human_readable)
        logger.debug("query.plan_steps intent=%r steps=%s", intent, plan.steps)

        raw = self._executor.run(plan)
        if isinstance(raw, QueryError):
            logger.warning(
                "query.execute_failed intent=%r code=%s message=%s",
                intent,
                raw.code,
                raw.message,
            )
            return self._formatter.format_error(intent=intent, parameters=params, err=raw, plan=plan)

        logger.info("query.executed intent=%r result_type=dict_keys=%s", intent, list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__)
        return self._formatter.format_success(intent=intent, parameters=plan.parameters, plan=plan, raw=raw)

    def execute_json(self, intent: str, parameters: Optional[dict[str, Any]] = None, *, indent: int | None = 2) -> str:
        """Convenience: same as :meth:`execute` but JSON string."""
        return self._formatter.dumps(self.execute(intent, parameters), indent=indent)


def _smoke() -> None:
    logging.basicConfig(level=logging.INFO)
    from app.graph.graph_builder import build_graph_from_bundle
    from app.db.loader import load_o2c_bundle
    from app.query.types import INTENT_FIND_TOP_PRODUCTS_BY_BILLING, INTENT_TRACE_ORDER_FLOW, INTENT_FIND_INCOMPLETE_ORDERS

    b = load_o2c_bundle()
    g, _ = build_graph_from_bundle(b)
    eng = QueryEngine(b, g)
    print(eng.execute_json(INTENT_FIND_TOP_PRODUCTS_BY_BILLING, {"limit": 3})[:800])
    print("---")
    print(eng.execute_json(INTENT_TRACE_ORDER_FLOW, {"order_id": next(iter(b.sales_orders.keys()))})[:1200])
    print("---")
    print(eng.execute_json(INTENT_FIND_INCOMPLETE_ORDERS, {"limit": 3, "criteria": ["missing_invoice"]})[:900])


if __name__ == "__main__":
    _smoke()
