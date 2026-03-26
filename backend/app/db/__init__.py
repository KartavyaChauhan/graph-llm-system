"""Data layer: canonical O2C schema and ingestion."""

from typing import TYPE_CHECKING, Any

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
    SapClockTime,
    accounting_line_key,
    normalize_sd_item_number,
)

__all__ = [
    "BillingDocumentHeader",
    "BillingDocumentItem",
    "Customer",
    "CustomerCompanyAssignment",
    "CustomerSalesAreaAssignment",
    "JournalEntryItemAR",
    "LoadReport",
    "O2CDataBundle",
    "OutboundDeliveryHeader",
    "OutboundDeliveryItem",
    "PartnerAddress",
    "PaymentARLine",
    "Plant",
    "ProductDescription",
    "ProductMaster",
    "ProductPlant",
    "ProductStorageLocation",
    "SalesOrderHeader",
    "SalesOrderItem",
    "SalesOrderScheduleLine",
    "SapClockTime",
    "accounting_line_key",
    "load_o2c_bundle",
    "normalize_sd_item_number",
]

if TYPE_CHECKING:
    from app.db.loader import LoadReport as LoadReport
    from app.db.loader import O2CDataBundle as O2CDataBundle


def __getattr__(name: str) -> Any:
    if name in ("LoadReport", "O2CDataBundle", "load_o2c_bundle"):
        from app.db import loader as _loader

        return getattr(_loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
