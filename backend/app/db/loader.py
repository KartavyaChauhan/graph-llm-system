"""
JSONL ingestion for SAP O2C extracts.

Loads all shards under ``data/raw/sap-o2c-data/<entity>/`` into validated Pydantic models and
aggregates them into :class:`O2CDataBundle`.

Run (from ``backend``): ``python -m app.db.loader``

Design decisions
----------------
- **Sharding / duplicates:** Extracts are split across multiple ``part-*.jsonl`` files. The same
  primary key can theoretically appear in more than one shard (re-export, overlap). Default policy
  is **last write wins** with counts recorded in :class:`LoadReport` so you can detect data quality
  issues without failing the whole load.
- **Billing headers vs cancellations:** Both folders share the same JSON shape; cancellations are
  billing documents in cancelled state. They are stored in a single ``billing_documents`` map keyed
  by ``billing_document`` so the graph has one node type for invoice/billing.
- **Item number normalization:** Applied when computing dictionary keys (and available via
  ``normalize_sd_item_number`` on raw strings) so sales, delivery, and billing references join
  reliably despite differing zero-padding.
- **String IDs:** Numeric-looking identifiers remain strings to avoid silent corruption (e.g. fiscal
  year ``"2025"``, document numbers with leading zeros if introduced later).
- **Strict validation:** Bad lines are skipped; counts are surfaced in :class:`LoadReport` for
  observability instead of halting ingestion (typical for large, slightly dirty extracts). Tighten
  by wrapping ``load_o2c_bundle`` in your own guard if you prefer fail-fast.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from pydantic import ValidationError

from app.db.schema import (
    BillingDocumentHeader,
    BillingDocumentItem,
    Customer,
    CustomerCompanyAssignment,
    CustomerSalesAreaAssignment,
    JournalEntryItemAR,
    OutboundDeliveryHeader,
    OutboundDeliveryItem,
    PartnerAddress,
    PaymentARLine,
    Plant,
    ProductDescription,
    ProductMaster,
    ProductPlant,
    ProductStorageLocation,
    SalesOrderHeader,
    SalesOrderItem,
    SalesOrderScheduleLine,
    accounting_line_key,
    normalize_sd_item_number,
)

T = TypeVar("T")


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_ROOT = BACKEND_ROOT / "data" / "raw" / "sap-o2c-data"


@dataclass(frozen=True, slots=True)
class LoadReport:
    """
    Aggregate statistics for an ingestion run.

    ``entity_counts`` counts successfully parsed rows per source folder (including duplicates that
    were merged into the same key). ``unique_record_counts`` reflects final dict sizes in
    :class:`O2CDataBundle`.
    """

    source_root: Path
    files_processed: int
    lines_attempted: int
    lines_loaded: int
    json_decode_errors: int
    validation_errors: int
    duplicate_overwrites: dict[str, int]
    entity_counts: dict[str, int]
    unique_record_counts: dict[str, int]


@dataclass
class O2CDataBundle:
    """
    Unified in-memory O2C dataset.

    All mappings use business-primary keys (see module docstring in ``schema.py``). Values are
    immutable snapshots of the latest ingested row for that key (see duplicate policy).
    """

    sales_orders: dict[str, SalesOrderHeader] = field(default_factory=dict)
    sales_order_items: dict[tuple[str, str], SalesOrderItem] = field(default_factory=dict)
    sales_order_schedule_lines: dict[tuple[str, str, str], SalesOrderScheduleLine] = field(default_factory=dict)

    delivery_headers: dict[str, OutboundDeliveryHeader] = field(default_factory=dict)
    delivery_items: dict[tuple[str, str], OutboundDeliveryItem] = field(default_factory=dict)

    billing_documents: dict[str, BillingDocumentHeader] = field(default_factory=dict)
    billing_items: dict[tuple[str, str], BillingDocumentItem] = field(default_factory=dict)

    journal_entry_items: dict[str, JournalEntryItemAR] = field(default_factory=dict)
    payment_lines: dict[str, PaymentARLine] = field(default_factory=dict)

    customers: dict[str, Customer] = field(default_factory=dict)
    partner_addresses: dict[tuple[str, str], PartnerAddress] = field(default_factory=dict)
    customer_sales_areas: dict[tuple[str, str, str, str], CustomerSalesAreaAssignment] = field(
        default_factory=dict
    )
    customer_company_data: dict[tuple[str, str], CustomerCompanyAssignment] = field(default_factory=dict)

    products: dict[str, ProductMaster] = field(default_factory=dict)
    product_descriptions: dict[tuple[str, str], ProductDescription] = field(default_factory=dict)
    product_plants: dict[tuple[str, str], ProductPlant] = field(default_factory=dict)
    product_storage_locations: dict[tuple[str, str, str], ProductStorageLocation] = field(default_factory=dict)
    plants: dict[str, Plant] = field(default_factory=dict)

    report: LoadReport | None = None


def resolve_raw_root(explicit: Path | None = None) -> Path:
    """Resolve dataset root: explicit arg > ``O2C_RAW_ROOT`` env > default under ``backend/data``."""
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("O2C_RAW_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_RAW_ROOT.resolve()


def _iter_jsonl_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.jsonl"))
    return files


def _iter_lines(paths: Iterable[Path]) -> Iterable[tuple[Path, int, str]]:
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                yield path, line_no, line


def _put(
    store: dict,
    key,
    value: T,
    *,
    table: str,
    dup_counter: dict[str, int],
) -> None:
    if key in store:
        dup_counter[table] = dup_counter.get(table, 0) + 1
    store[key] = value


def load_o2c_bundle(raw_root: Path | None = None, *, fail_fast: bool = False) -> O2CDataBundle:
    """
    Load every JSONL shard under ``raw_root`` (default ``backend/data/raw/sap-o2c-data``).

    :param fail_fast: if True, raise on the first JSON or validation error.
    """
    root = resolve_raw_root(raw_root)
    bundle = O2CDataBundle()

    dup_counter: dict[str, int] = {}
    entity_counts: dict[str, int] = {}

    lines_attempted = 0
    lines_loaded = 0
    json_errors = 0
    validation_errors = 0
    files_processed = 0

    def bump(table: str, n: int = 1) -> None:
        entity_counts[table] = entity_counts.get(table, 0) + n

    def process_file_list(
        *,
        table: str,
        paths: list[Path],
        model_cls: type[T],
        upsert: Callable[[T], None],
    ) -> None:
        nonlocal lines_attempted, lines_loaded, json_errors, validation_errors, files_processed
        if not paths:
            return
        for path in paths:
            files_processed += 1
        for path, _line_no, line in _iter_lines(paths):
            lines_attempted += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                json_errors += 1
                if fail_fast:
                    raise
                continue
            try:
                obj = model_cls.model_validate(raw)
            except ValidationError:
                validation_errors += 1
                if fail_fast:
                    raise
                continue
            upsert(obj)
            lines_loaded += 1
            bump(table)

    # --- Sales ---
    so_paths = _iter_jsonl_files(root / "sales_order_headers")

    def upsert_so_header(obj: SalesOrderHeader) -> None:
        _put(bundle.sales_orders, obj.sales_order, obj, table="sales_order_headers", dup_counter=dup_counter)

    process_file_list(table="sales_order_headers", paths=so_paths, model_cls=SalesOrderHeader, upsert=upsert_so_header)

    soi_paths = _iter_jsonl_files(root / "sales_order_items")

    def upsert_so_item(obj: SalesOrderItem) -> None:
        key = (obj.sales_order, obj.item_key)
        _put(bundle.sales_order_items, key, obj, table="sales_order_items", dup_counter=dup_counter)

    process_file_list(table="sales_order_items", paths=soi_paths, model_cls=SalesOrderItem, upsert=upsert_so_item)

    sched_paths = _iter_jsonl_files(root / "sales_order_schedule_lines")

    def upsert_sched(obj: SalesOrderScheduleLine) -> None:
        key = (obj.sales_order, obj.item_key, obj.schedule_line.strip())
        _put(bundle.sales_order_schedule_lines, key, obj, table="sales_order_schedule_lines", dup_counter=dup_counter)

    process_file_list(
        table="sales_order_schedule_lines",
        paths=sched_paths,
        model_cls=SalesOrderScheduleLine,
        upsert=upsert_sched,
    )

    # --- Deliveries ---
    odh_paths = _iter_jsonl_files(root / "outbound_delivery_headers")

    def upsert_odh(obj: OutboundDeliveryHeader) -> None:
        _put(bundle.delivery_headers, obj.delivery_document, obj, table="outbound_delivery_headers", dup_counter=dup_counter)

    process_file_list(
        table="outbound_delivery_headers",
        paths=odh_paths,
        model_cls=OutboundDeliveryHeader,
        upsert=upsert_odh,
    )

    odi_paths = _iter_jsonl_files(root / "outbound_delivery_items")

    def upsert_odi(obj: OutboundDeliveryItem) -> None:
        key = (obj.delivery_document, obj.item_key)
        _put(bundle.delivery_items, key, obj, table="outbound_delivery_items", dup_counter=dup_counter)

    process_file_list(
        table="outbound_delivery_items",
        paths=odi_paths,
        model_cls=OutboundDeliveryItem,
        upsert=upsert_odi,
    )

    # --- Billing (headers + cancellations share model/map) ---
    bill_header_paths = sorted(
        _iter_jsonl_files(root / "billing_document_headers") + _iter_jsonl_files(root / "billing_document_cancellations")
    )

    def upsert_bill_hdr(obj: BillingDocumentHeader) -> None:
        _put(bundle.billing_documents, obj.billing_document, obj, table="billing_documents", dup_counter=dup_counter)

    process_file_list(
        table="billing_documents",
        paths=bill_header_paths,
        model_cls=BillingDocumentHeader,
        upsert=upsert_bill_hdr,
    )

    bdi_paths = _iter_jsonl_files(root / "billing_document_items")

    def upsert_bdi(obj: BillingDocumentItem) -> None:
        key = (obj.billing_document, obj.item_key)
        _put(bundle.billing_items, key, obj, table="billing_document_items", dup_counter=dup_counter)

    process_file_list(
        table="billing_document_items",
        paths=bdi_paths,
        model_cls=BillingDocumentItem,
        upsert=upsert_bdi,
    )

    # --- FI-AR ---
    je_paths = _iter_jsonl_files(root / "journal_entry_items_accounts_receivable")

    def upsert_je(obj: JournalEntryItemAR) -> None:
        key = accounting_line_key(
            obj.company_code,
            obj.fiscal_year,
            obj.accounting_document,
            obj.accounting_document_item,
        )
        _put(bundle.journal_entry_items, key, obj, table="journal_entry_items_accounts_receivable", dup_counter=dup_counter)

    process_file_list(
        table="journal_entry_items_accounts_receivable",
        paths=je_paths,
        model_cls=JournalEntryItemAR,
        upsert=upsert_je,
    )

    pay_paths = _iter_jsonl_files(root / "payments_accounts_receivable")

    def upsert_pay(obj: PaymentARLine) -> None:
        key = accounting_line_key(
            obj.company_code,
            obj.fiscal_year,
            obj.accounting_document,
            obj.accounting_document_item,
        )
        _put(bundle.payment_lines, key, obj, table="payments_accounts_receivable", dup_counter=dup_counter)

    process_file_list(
        table="payments_accounts_receivable",
        paths=pay_paths,
        model_cls=PaymentARLine,
        upsert=upsert_pay,
    )

    # --- Business partners ---
    bp_paths = _iter_jsonl_files(root / "business_partners")

    def upsert_bp(obj: Customer) -> None:
        _put(bundle.customers, obj.business_partner, obj, table="business_partners", dup_counter=dup_counter)

    process_file_list(table="business_partners", paths=bp_paths, model_cls=Customer, upsert=upsert_bp)

    addr_paths = _iter_jsonl_files(root / "business_partner_addresses")

    def upsert_addr(obj: PartnerAddress) -> None:
        key = (obj.business_partner, obj.address_id)
        _put(bundle.partner_addresses, key, obj, table="business_partner_addresses", dup_counter=dup_counter)

    process_file_list(
        table="business_partner_addresses",
        paths=addr_paths,
        model_cls=PartnerAddress,
        upsert=upsert_addr,
    )

    csa_paths = _iter_jsonl_files(root / "customer_sales_area_assignments")

    def upsert_csa(obj: CustomerSalesAreaAssignment) -> None:
        key = (obj.customer, obj.sales_organization, obj.distribution_channel, obj.division)
        _put(bundle.customer_sales_areas, key, obj, table="customer_sales_area_assignments", dup_counter=dup_counter)

    process_file_list(
        table="customer_sales_area_assignments",
        paths=csa_paths,
        model_cls=CustomerSalesAreaAssignment,
        upsert=upsert_csa,
    )

    cca_paths = _iter_jsonl_files(root / "customer_company_assignments")

    def upsert_cca(obj: CustomerCompanyAssignment) -> None:
        key = (obj.customer, obj.company_code)
        _put(bundle.customer_company_data, key, obj, table="customer_company_assignments", dup_counter=dup_counter)

    process_file_list(
        table="customer_company_assignments",
        paths=cca_paths,
        model_cls=CustomerCompanyAssignment,
        upsert=upsert_cca,
    )

    # --- Materials / plants ---
    prod_paths = _iter_jsonl_files(root / "products")

    def upsert_prod(obj: ProductMaster) -> None:
        _put(bundle.products, obj.product, obj, table="products", dup_counter=dup_counter)

    process_file_list(table="products", paths=prod_paths, model_cls=ProductMaster, upsert=upsert_prod)

    pd_paths = _iter_jsonl_files(root / "product_descriptions")

    def upsert_pd(obj: ProductDescription) -> None:
        key = (obj.product, obj.language)
        _put(bundle.product_descriptions, key, obj, table="product_descriptions", dup_counter=dup_counter)

    process_file_list(
        table="product_descriptions",
        paths=pd_paths,
        model_cls=ProductDescription,
        upsert=upsert_pd,
    )

    pp_paths = _iter_jsonl_files(root / "product_plants")

    def upsert_pp(obj: ProductPlant) -> None:
        key = (obj.product, obj.plant)
        _put(bundle.product_plants, key, obj, table="product_plants", dup_counter=dup_counter)

    process_file_list(table="product_plants", paths=pp_paths, model_cls=ProductPlant, upsert=upsert_pp)

    psl_paths = _iter_jsonl_files(root / "product_storage_locations")

    def upsert_psl(obj: ProductStorageLocation) -> None:
        key = (obj.product, obj.plant, obj.storage_location)
        _put(
            bundle.product_storage_locations,
            key,
            obj,
            table="product_storage_locations",
            dup_counter=dup_counter,
        )

    process_file_list(
        table="product_storage_locations",
        paths=psl_paths,
        model_cls=ProductStorageLocation,
        upsert=upsert_psl,
    )

    plant_paths = _iter_jsonl_files(root / "plants")

    def upsert_plant(obj: Plant) -> None:
        _put(bundle.plants, obj.plant, obj, table="plants", dup_counter=dup_counter)

    process_file_list(table="plants", paths=plant_paths, model_cls=Plant, upsert=upsert_plant)

    unique_record_counts = {
        "sales_orders": len(bundle.sales_orders),
        "sales_order_items": len(bundle.sales_order_items),
        "sales_order_schedule_lines": len(bundle.sales_order_schedule_lines),
        "delivery_headers": len(bundle.delivery_headers),
        "delivery_items": len(bundle.delivery_items),
        "billing_documents": len(bundle.billing_documents),
        "billing_items": len(bundle.billing_items),
        "journal_entry_items": len(bundle.journal_entry_items),
        "payment_lines": len(bundle.payment_lines),
        "customers": len(bundle.customers),
        "partner_addresses": len(bundle.partner_addresses),
        "customer_sales_areas": len(bundle.customer_sales_areas),
        "customer_company_data": len(bundle.customer_company_data),
        "products": len(bundle.products),
        "product_descriptions": len(bundle.product_descriptions),
        "product_plants": len(bundle.product_plants),
        "product_storage_locations": len(bundle.product_storage_locations),
        "plants": len(bundle.plants),
    }

    bundle.report = LoadReport(
        source_root=root,
        files_processed=files_processed,
        lines_attempted=lines_attempted,
        lines_loaded=lines_loaded,
        json_decode_errors=json_errors,
        validation_errors=validation_errors,
        duplicate_overwrites=dict(sorted(dup_counter.items())),
        entity_counts=dict(sorted(entity_counts.items())),
        unique_record_counts=dict(sorted(unique_record_counts.items())),
    )
    return bundle


def _main() -> None:
    bundle = load_o2c_bundle()
    r = bundle.report
    assert r is not None
    print(f"Source: {r.source_root}")
    print(f"Files: {r.files_processed}  Lines OK: {r.lines_loaded} / attempted {r.lines_attempted}")
    print(f"JSON errors: {r.json_decode_errors}  Validation errors: {r.validation_errors}")
    print(f"Duplicate PK overwrites: {r.duplicate_overwrites}")
    print("Rows ingested per file group:")
    for k, v in r.entity_counts.items():
        print(f"  {k}: {v}")
    print("Unique records (maps):")
    for k, v in r.unique_record_counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    _main()
