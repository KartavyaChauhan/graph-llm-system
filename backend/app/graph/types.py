"""
Graph domain types: node kinds, edge kinds, lifecycle and quality issue records.

Only the five required O2C edge types exist as first-class :class:`EdgeType` values. Address nodes
are associated to customers via node metadata (see ``graph_builder`` module docstring) because the
assignment schema does not include a dedicated customer–address edge type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeType(str, Enum):
    ORDER = "Order"
    DELIVERY = "Delivery"
    INVOICE = "Invoice"
    PAYMENT = "Payment"
    JOURNAL_ENTRY = "JournalEntry"
    CUSTOMER = "Customer"
    PRODUCT = "Product"
    ADDRESS = "Address"


class EdgeType(str, Enum):
    CUSTOMER_PLACED_ORDER = "CUSTOMER_PLACED_ORDER"
    ORDER_HAS_DELIVERY = "ORDER_HAS_DELIVERY"
    DELIVERY_HAS_INVOICE = "DELIVERY_HAS_INVOICE"
    INVOICE_HAS_PAYMENT = "INVOICE_HAS_PAYMENT"
    INVOICE_HAS_JOURNAL_ENTRY = "INVOICE_HAS_JOURNAL_ENTRY"
    ORDER_CONTAINS_PRODUCT = "ORDER_CONTAINS_PRODUCT"


class FlowIssueKind(str, Enum):
    """Categories used by :meth:`GraphManager.detect_broken_flows`."""

    MISSING_CUSTOMER = "missing_customer"
    ORDER_NO_DELIVERY = "order_no_delivery"
    ORDER_NO_PRODUCT = "order_no_product"
    DELIVERY_NO_ORDER = "delivery_no_order"
    DELIVERY_NO_INVOICE = "delivery_no_invoice"
    INVOICE_NO_DELIVERY = "invoice_no_delivery"
    INVOICE_NO_PAYMENT = "invoice_no_payment"
    PAYMENT_NO_INVOICE = "payment_no_invoice"
    ORPHAN_ADDRESS = "orphan_address"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class FlowIssue:
    """A single data-quality or incomplete-flow observation."""

    kind: FlowIssueKind
    severity: Severity
    message: str
    node_id: Optional[str] = None
    related_node_ids: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderLifecycle:
    """Structured view of everything reachable from an order along the O2C chain."""

    order_id: str
    customer_id: Optional[str]
    delivery_ids: list[str]
    invoice_ids: list[str]
    payment_ids: list[str]
    product_ids: list[str]
    address_ids: list[str]
    """Gaps detected while traversing required edge types only."""
    missing_links: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GraphBuildReport:
    """Warnings and counters from graph construction."""

    warnings: list[str] = field(default_factory=list)
    nodes_created: dict[str, int] = field(default_factory=dict)
    edges_created: dict[str, int] = field(default_factory=dict)
    skipped_edges: list[str] = field(default_factory=list)


# --- Stable node id helpers (single place for API + storage) ---

def customer_node_id(customer_number: str) -> str:
    return f"customer:{customer_number.strip()}"


def order_node_id(sales_order: str) -> str:
    return f"order:{sales_order.strip()}"


def delivery_node_id(delivery_document: str) -> str:
    return f"delivery:{delivery_document.strip()}"


def invoice_node_id(billing_document: str) -> str:
    return f"invoice:{billing_document.strip()}"


def payment_node_id(company_code: str, fiscal_year: str, clearing_accounting_document: str) -> str:
    return f"payment:{company_code.strip()}|{fiscal_year.strip()}|{clearing_accounting_document.strip()}"


def journal_entry_node_id(company_code: str, fiscal_year: str, accounting_document: str) -> str:
    return f"je:{company_code.strip()}|{fiscal_year.strip()}|{accounting_document.strip()}"


def product_node_id(material: str) -> str:
    return f"product:{material.strip()}"


def address_node_id(business_partner: str, address_id: str) -> str:
    return f"address:{business_partner.strip()}|{address_id.strip()}"


def parse_stripped_id(node_id: str, prefix: str) -> Optional[str]:
    if not node_id.startswith(prefix):
        return None
    return node_id[len(prefix) :].strip() or None
