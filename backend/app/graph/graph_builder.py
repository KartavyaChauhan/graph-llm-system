"""
Construct the O2C graph from :class:`app.db.loader.O2CDataBundle`.

Relationship modeling (why these edges)
--------------------------------------
We collapse SAP’s many physical tables into **seven node types** and **five directed edges** that
match how business users describe the flow:

* **Customer → Order** — ``sold_to_party`` on the sales order header.
* **Order → Delivery** — inferred from delivery items’ ``reference_sd_document`` (sales order).
* **Delivery → Invoice** — billing items reference the originating delivery document in
  ``reference_sd_document`` (SD object category delivery).
* **Invoice → Payment** — there is usually **no single FK** from FI payment extract to SD billing.
  We resolve links in this order: (1) ``invoice_reference`` on the payment line when populated;
  (2) match the payment line’s ``accounting_document`` to the billing header’s
  ``accounting_document`` (company code + fiscal year + FI document); (3) fall back to journal
  AR lines where ``reference_document`` is a known billing document number and the FI document
  matches the payment line’s accounting document. Unresolved lines are **skipped** with a warning
  rather than failing the build.
* **Order → Product** — sales order items carry ``material``.

**Address nodes** are created for partner addresses, but the assignment’s edge taxonomy does not
include a customer–address relation. We therefore store ``address_node_ids`` on the customer node’s
metadata and ``business_partner`` on each address node so :class:`app.graph.manager.GraphManager`
can resolve addresses without inventing a sixth edge type.

Tradeoffs vs relational SQL
----------------------------
* **Graph:** Local navigation (neighbors, lifecycle, “what touches this id?”) is O(k) in degree
  instead of hand-written joins across ten tables; incomplete links surface as missing edges plus
  structured warnings.
* **SQL:** Aggregations, window functions, and full-table scans stay easier in the database; this
  layer is intentionally a **semantic projection** for exploration and NL-to-path, not a warehouse
  replacement. Keeping NetworkX behind :class:`app.graph.store.IGraphStore` lets you add SQL-backed
  traversals later without changing manager call sites.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Optional, Tuple

from app.db.loader import O2CDataBundle
from app.graph.manager import GraphManager
from app.graph.store import NetworkXGraphStore
from app.graph.types import (
    EdgeType,
    GraphBuildReport,
    NodeType,
    address_node_id,
    customer_node_id,
    delivery_node_id,
    invoice_node_id,
    order_node_id,
    payment_node_id,
    product_node_id,
)


def _dump(obj: Any) -> dict[str, Any]:
    return obj.model_dump(mode="json")


def _bump(report: GraphBuildReport, key: str, *, nodes: bool = False, edges: bool = False) -> None:
    bucket = report.nodes_created if nodes else report.edges_created
    bucket[key] = bucket.get(key, 0) + 1


def _merge_fi_to_billing(bundle: O2CDataBundle) -> DefaultDict[Tuple[str, str, str], set[str]]:
    """
    Map (company_code, fiscal_year, accounting_document) -> billing document number(s).

    Populated from billing headers and reinforced by journal AR lines that reference a billing doc.
    """
    m: DefaultDict[Tuple[str, str, str], set[str]] = defaultdict(set)
    for bdoc, hdr in bundle.billing_documents.items():
        if hdr.accounting_document and hdr.company_code and hdr.fiscal_year:
            m[(hdr.company_code, hdr.fiscal_year, hdr.accounting_document)].add(bdoc)
    for je in bundle.journal_entry_items.values():
        ref = je.reference_document
        if ref and ref in bundle.billing_documents and je.accounting_document:
            m[(je.company_code, je.fiscal_year, je.accounting_document)].add(ref)
    return m


def build_graph_from_bundle(bundle: O2CDataBundle) -> tuple[GraphManager, GraphBuildReport]:
    """
    Build a :class:`GraphManager` over a fresh :class:`NetworkXGraphStore`.

    Idempotent within a single call: duplicate logical edges are merged where noted below.
    """
    store = NetworkXGraphStore()
    report = GraphBuildReport()

    # --- Customers ---
    for cust_id, bp in bundle.customers.items():
        nid = customer_node_id(cust_id)
        store.add_node(
            nid,
            node_type=NodeType.CUSTOMER,
            metadata={**_dump(bp), "address_node_ids": []},
        )
        _bump(report, NodeType.CUSTOMER.value, nodes=True)

    # --- Addresses (metadata link to customer; no typed edge) ---
    for (bp, aid), addr in bundle.partner_addresses.items():
        anid = address_node_id(bp, aid)
        meta = _dump(addr)
        meta["business_partner"] = bp
        store.add_node(anid, node_type=NodeType.ADDRESS, metadata=meta)
        _bump(report, NodeType.ADDRESS.value, nodes=True)
        cnid = customer_node_id(bp)
        if not store.has_node(cnid):
            report.warnings.append(f"Address {anid} references business partner {bp} with no customer node.")

    for cust_id in bundle.customers.keys():
        cnid = customer_node_id(cust_id)
        if not store.has_node(cnid):
            continue
        base = store.get_node(cnid)
        meta = dict(base["metadata"]) if base else {}
        ids = [
            address_node_id(cust_id, aid)
            for (bp, aid) in bundle.partner_addresses.keys()
            if bp == cust_id
        ]
        meta["address_node_ids"] = [i for i in ids if store.has_node(i)]
        store.replace_node_metadata(cnid, meta)

    # --- Orders ---
    for so, hdr in bundle.sales_orders.items():
        oid = order_node_id(so)
        store.add_node(oid, node_type=NodeType.ORDER, metadata=_dump(hdr))
        _bump(report, NodeType.ORDER.value, nodes=True)

        sold = hdr.sold_to_party
        cnid = customer_node_id(sold)
        if store.has_node(cnid):
            store.add_edge(
                cnid,
                oid,
                edge_type=EdgeType.CUSTOMER_PLACED_ORDER,
                attributes={"sold_to_party": sold},
            )
            _bump(report, EdgeType.CUSTOMER_PLACED_ORDER.value, edges=True)
        else:
            report.warnings.append(
                f"Order {so} sold_to_party={sold!r} has no customer node; skipped CUSTOMER_PLACED_ORDER."
            )

    # --- Products + ORDER_CONTAINS_PRODUCT ---
    seen_op: set[tuple[str, str]] = set()
    for (so, _item), line in bundle.sales_order_items.items():
        oid = order_node_id(so)
        if not store.has_node(oid):
            report.warnings.append(f"Sales order item references missing order node {oid}; skipped.")
            continue
        mat = (line.material or "").strip()
        if not mat:
            report.warnings.append(f"Sales order {so} item has empty material; skipped product edge.")
            continue
        pnid = product_node_id(mat)
        if not store.has_node(pnid):
            pm = bundle.products.get(mat)
            meta = _dump(pm) if pm else {"product": mat, "master_missing": True}
            store.add_node(pnid, node_type=NodeType.PRODUCT, metadata=meta)
            _bump(report, NodeType.PRODUCT.value, nodes=True)
        key = (oid, pnid)
        if key in seen_op:
            continue
        store.add_edge(
            oid,
            pnid,
            edge_type=EdgeType.ORDER_CONTAINS_PRODUCT,
            attributes={"material": mat},
        )
        seen_op.add(key)
        _bump(report, EdgeType.ORDER_CONTAINS_PRODUCT.value, edges=True)

    # --- Deliveries ---
    for dd, hdr in bundle.delivery_headers.items():
        dnid = delivery_node_id(dd)
        store.add_node(dnid, node_type=NodeType.DELIVERY, metadata=_dump(hdr))
        _bump(report, NodeType.DELIVERY.value, nodes=True)

    seen_od: set[tuple[str, str]] = set()
    for (_dd, _ln), dit in bundle.delivery_items.items():
        oid = order_node_id(dit.reference_sd_document)
        dnid = delivery_node_id(dit.delivery_document)
        if not store.has_node(oid):
            report.warnings.append(
                f"Delivery item references sales order {dit.reference_sd_document!r} without order node; "
                f"skipped ORDER_HAS_DELIVERY for {dit.delivery_document}."
            )
            continue
        if not store.has_node(dnid):
            report.warnings.append(f"Delivery item references missing delivery header {dnid}; skipped.")
            continue
        key = (oid, dnid)
        if key in seen_od:
            continue
        store.add_edge(
            oid,
            dnid,
            edge_type=EdgeType.ORDER_HAS_DELIVERY,
            attributes={
                "reference_sd_document": dit.reference_sd_document,
                "delivery_document": dit.delivery_document,
            },
        )
        seen_od.add(key)
        _bump(report, EdgeType.ORDER_HAS_DELIVERY.value, edges=True)

    # --- Invoices ---
    for bid, hdr in bundle.billing_documents.items():
        iid = invoice_node_id(bid)
        store.add_node(iid, node_type=NodeType.INVOICE, metadata=_dump(hdr))
        _bump(report, NodeType.INVOICE.value, nodes=True)

    seen_di: set[tuple[str, str]] = set()
    for (_b, _it), bit in bundle.billing_items.items():
        dnid = delivery_node_id(bit.reference_sd_document)
        iid = invoice_node_id(bit.billing_document)
        if not store.has_node(dnid):
            report.warnings.append(
                f"Billing item {bit.billing_document}/{bit.billing_document_item} references delivery "
                f"{bit.reference_sd_document!r} with no delivery node; skipped DELIVERY_HAS_INVOICE."
            )
            continue
        if not store.has_node(iid):
            report.warnings.append(f"Billing item references missing invoice node {iid}; skipped.")
            continue
        key = (dnid, iid)
        if key in seen_di:
            continue
        store.add_edge(
            dnid,
            iid,
            edge_type=EdgeType.DELIVERY_HAS_INVOICE,
            attributes={
                "billing_document": bit.billing_document,
                "reference_sd_document": bit.reference_sd_document,
                "material": bit.material,
            },
        )
        seen_di.add(key)
        _bump(report, EdgeType.DELIVERY_HAS_INVOICE.value, edges=True)

    # --- Payments (Invoice → Payment) ---
    fi_to_billing = _merge_fi_to_billing(bundle)
    seen_ip: set[tuple[str, str]] = set()

    for _pk, pl in bundle.payment_lines.items():
        clearing = (pl.clearing_accounting_document or "").strip()
        if not clearing:
            report.skipped_edges.append("payment line without clearing_accounting_document; no Payment node edge.")
            continue
        fy_clear = (pl.clearing_doc_fiscal_year or pl.fiscal_year or "").strip()
        cc = (pl.company_code or "").strip()
        pay_id = payment_node_id(cc, fy_clear, clearing)
        if not store.has_node(pay_id):
            store.add_node(pay_id, node_type=NodeType.PAYMENT, metadata=_dump(pl))
            _bump(report, NodeType.PAYMENT.value, nodes=True)
        inv: Optional[str] = None
        inv_ref = pl.invoice_reference
        if inv_ref and str(inv_ref).strip():
            cand = str(inv_ref).strip()
            if cand in bundle.billing_documents:
                inv = cand
        if inv is None:
            k = (cc, (pl.fiscal_year or "").strip(), (pl.accounting_document or "").strip())
            if k[2]:
                cands = fi_to_billing.get(k, set())
                if len(cands) == 1:
                    inv = next(iter(cands))
                elif len(cands) > 1:
                    report.warnings.append(
                        f"Payment line {pl.accounting_document} maps to multiple billing docs {cands!r}; skipped."
                    )
                    continue
        if inv is None:
            report.warnings.append(
                f"Could not resolve invoice for payment/clearing {pay_id} "
                f"(accounting_document={pl.accounting_document!r})."
            )
            continue
        iid = invoice_node_id(inv)
        if not store.has_node(iid):
            report.warnings.append(f"Resolved billing {inv} but invoice node missing; skipped INVOICE_HAS_PAYMENT.")
            continue
        key = (iid, pay_id)
        if key in seen_ip:
            continue
        store.add_edge(
            iid,
            pay_id,
            edge_type=EdgeType.INVOICE_HAS_PAYMENT,
            attributes={
                "clearing_accounting_document": clearing,
                "company_code": cc,
                "payment_accounting_document": pl.accounting_document,
            },
        )
        seen_ip.add(key)
        _bump(report, EdgeType.INVOICE_HAS_PAYMENT.value, edges=True)

    manager = GraphManager(store, build_report=report)
    return manager, report


def _smoke() -> None:
    from app.db.loader import load_o2c_bundle

    bundle = load_o2c_bundle()
    mgr, rep = build_graph_from_bundle(bundle)
    print("Nodes:", mgr.store.number_of_nodes(), "Edges:", mgr.store.number_of_edges())
    print("Build warnings (first 5):", rep.warnings[:5])
    print("Broken flow count:", len(mgr.detect_broken_flows()))
    if bundle.sales_orders:
        first_so = next(iter(bundle.sales_orders.keys()))
        life = mgr.order_lifecycle(first_so)
        print("Sample lifecycle for", first_so, ":", life)


if __name__ == "__main__":
    _smoke()
