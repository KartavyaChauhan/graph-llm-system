"""
Canonical Pydantic models for SAP Order-to-Cash extract (JSONL).

Design notes (keys and relationships)
------------------------------------
- Source payloads use camelCase; Python attributes are snake_case with Field(alias=...).
- Document identifiers (sales orders, deliveries, billing docs, accounting docs) are kept as
  strings to preserve leading zeros and avoid mixing semantic types with ints.
- SD item numbers are normalized at ingestion (see ``normalize_sd_item_number``) because the
  same logical line appears as "10", "000010", etc. across APIs.
- ``material`` on SD/billing lines is the logistics/material number. ``ProductMaster.product``
  uses the same identifier space for manufactured/traded goods in this extract, but some line
  materials may not exist in ``products`` (partial master data); that is expected in real SAP dumps.
- ``JournalEntryItemAR.reference_document`` points at originating SD billing documents for RV-type
  rows in this dataset; ``BillingDocumentHeader.accounting_document`` is the FI document posted from billing.
- ``PaymentARLine`` rows are open-item / clearing lines keyed by the accounting document line; links to
  billing can be indirect via clearing document chains and customer, or via invoice_reference when populated.

This module intentionally ignores unknown JSON keys (``model_config.extra = "ignore"``) so new extract
fields do not break ingestion; add fields here when you need them in the graph or API.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def normalize_sd_item_number(value: str | None) -> str:
    """Normalize SD item numbers across sales, delivery, and billing references."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s.isdigit():
        return str(int(s, 10))
    return s


def _blank_to_none(v: Any) -> Any:
    if v == "":
        return None
    return v


def _opt_decimal(v: Any) -> Any:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _decimal_any(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v))


OptionalStr = Annotated[Optional[str], BeforeValidator(_blank_to_none)]
OptDecimal = Annotated[Optional[Decimal], BeforeValidator(_opt_decimal)]
StrictDecimal = Annotated[Decimal, BeforeValidator(_decimal_any)]


class SapClockTime(BaseModel):
    """Wall-clock fragment used in several SAP OData payloads (not a full datetime)."""

    model_config = ConfigDict(extra="ignore")

    hours: int = 0
    minutes: int = 0
    seconds: int = 0


class SalesOrderHeader(BaseModel):
    """VBAK-equivalent header. PK: ``sales_order``."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    sales_order: str = Field(alias="salesOrder")
    sales_order_type: str = Field(alias="salesOrderType")
    sales_organization: str = Field(alias="salesOrganization")
    distribution_channel: str = Field(alias="distributionChannel")
    organization_division: str = Field(alias="organizationDivision")
    sales_group: OptionalStr = Field(default=None, alias="salesGroup")
    sales_office: OptionalStr = Field(default=None, alias="salesOffice")
    sold_to_party: str = Field(alias="soldToParty")
    creation_date: OptionalStr = Field(default=None, alias="creationDate")
    created_by_user: OptionalStr = Field(default=None, alias="createdByUser")
    last_change_date_time: OptionalStr = Field(default=None, alias="lastChangeDateTime")
    total_net_amount: OptDecimal = Field(default=None, alias="totalNetAmount")
    overall_delivery_status: OptionalStr = Field(default=None, alias="overallDeliveryStatus")
    overall_ord_reltd_billg_status: OptionalStr = Field(default=None, alias="overallOrdReltdBillgStatus")
    overall_sd_doc_reference_status: OptionalStr = Field(default=None, alias="overallSdDocReferenceStatus")
    transaction_currency: OptionalStr = Field(default=None, alias="transactionCurrency")
    pricing_date: OptionalStr = Field(default=None, alias="pricingDate")
    requested_delivery_date: OptionalStr = Field(default=None, alias="requestedDeliveryDate")
    header_billing_block_reason: OptionalStr = Field(default=None, alias="headerBillingBlockReason")
    delivery_block_reason: OptionalStr = Field(default=None, alias="deliveryBlockReason")
    incoterms_classification: OptionalStr = Field(default=None, alias="incotermsClassification")
    incoterms_location1: OptionalStr = Field(default=None, alias="incotermsLocation1")
    customer_payment_terms: OptionalStr = Field(default=None, alias="customerPaymentTerms")
    total_credit_check_status: OptionalStr = Field(default=None, alias="totalCreditCheckStatus")


class SalesOrderItem(BaseModel):
    """VBAP-equivalent item. PK: (``sales_order``, normalized ``sales_order_item``)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    sales_order: str = Field(alias="salesOrder")
    sales_order_item: str = Field(alias="salesOrderItem")
    sales_order_item_category: OptionalStr = Field(default=None, alias="salesOrderItemCategory")
    material: str = Field(alias="material")
    requested_quantity: OptDecimal = Field(default=None, alias="requestedQuantity")
    requested_quantity_unit: OptionalStr = Field(default=None, alias="requestedQuantityUnit")
    transaction_currency: OptionalStr = Field(default=None, alias="transactionCurrency")
    net_amount: OptDecimal = Field(default=None, alias="netAmount")
    material_group: OptionalStr = Field(default=None, alias="materialGroup")
    production_plant: OptionalStr = Field(default=None, alias="productionPlant")
    storage_location: OptionalStr = Field(default=None, alias="storageLocation")
    sales_document_rjcn_reason: OptionalStr = Field(default=None, alias="salesDocumentRjcnReason")
    item_billing_block_reason: OptionalStr = Field(default=None, alias="itemBillingBlockReason")

    @property
    def item_key(self) -> str:
        return normalize_sd_item_number(self.sales_order_item)


class SalesOrderScheduleLine(BaseModel):
    """Schedule line (VBEP-style). PK: (order, item, schedule_line)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    sales_order: str = Field(alias="salesOrder")
    sales_order_item: str = Field(alias="salesOrderItem")
    schedule_line: str = Field(alias="scheduleLine")
    confirmed_delivery_date: OptionalStr = Field(default=None, alias="confirmedDeliveryDate")
    order_quantity_unit: OptionalStr = Field(default=None, alias="orderQuantityUnit")
    confd_order_qty_by_matl_avail_check: OptDecimal = Field(default=None, alias="confdOrderQtyByMatlAvailCheck")

    @property
    def item_key(self) -> str:
        return normalize_sd_item_number(self.sales_order_item)


class OutboundDeliveryHeader(BaseModel):
    """LIKP-equivalent. PK: ``delivery_document``."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    delivery_document: str = Field(alias="deliveryDocument")
    shipping_point: OptionalStr = Field(default=None, alias="shippingPoint")
    creation_date: OptionalStr = Field(default=None, alias="creationDate")
    creation_time: Optional[SapClockTime] = Field(default=None, alias="creationTime")
    delivery_block_reason: OptionalStr = Field(default=None, alias="deliveryBlockReason")
    hdr_general_incompletion_status: OptionalStr = Field(default=None, alias="hdrGeneralIncompletionStatus")
    header_billing_block_reason: OptionalStr = Field(default=None, alias="headerBillingBlockReason")
    last_change_date: OptionalStr = Field(default=None, alias="lastChangeDate")
    overall_goods_movement_status: OptionalStr = Field(default=None, alias="overallGoodsMovementStatus")
    overall_picking_status: OptionalStr = Field(default=None, alias="overallPickingStatus")
    overall_proof_of_delivery_status: OptionalStr = Field(default=None, alias="overallProofOfDeliveryStatus")
    actual_goods_movement_date: OptionalStr = Field(default=None, alias="actualGoodsMovementDate")
    actual_goods_movement_time: Optional[SapClockTime] = Field(default=None, alias="actualGoodsMovementTime")


class OutboundDeliveryItem(BaseModel):
    """LIPS-equivalent. PK: (delivery, normalized delivery item). FK: reference_sd_document + item -> sales order line."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    delivery_document: str = Field(alias="deliveryDocument")
    delivery_document_item: str = Field(alias="deliveryDocumentItem")
    reference_sd_document: str = Field(alias="referenceSdDocument")
    reference_sd_document_item: str = Field(alias="referenceSdDocumentItem")
    plant: OptionalStr = Field(default=None, alias="plant")
    storage_location: OptionalStr = Field(default=None, alias="storageLocation")
    actual_delivery_quantity: OptDecimal = Field(default=None, alias="actualDeliveryQuantity")
    delivery_quantity_unit: OptionalStr = Field(default=None, alias="deliveryQuantityUnit")
    batch: OptionalStr = Field(default=None, alias="batch")
    item_billing_block_reason: OptionalStr = Field(default=None, alias="itemBillingBlockReason")
    last_change_date: OptionalStr = Field(default=None, alias="lastChangeDate")

    @property
    def item_key(self) -> str:
        return normalize_sd_item_number(self.delivery_document_item)

    @property
    def reference_item_key(self) -> str:
        return normalize_sd_item_number(self.reference_sd_document_item)


class BillingDocumentHeader(BaseModel):
    """VBRK-equivalent. PK: ``billing_document``. Includes rows from cancellations export (same shape)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    billing_document: str = Field(alias="billingDocument")
    billing_document_type: OptionalStr = Field(default=None, alias="billingDocumentType")
    creation_date: OptionalStr = Field(default=None, alias="creationDate")
    creation_time: Optional[SapClockTime] = Field(default=None, alias="creationTime")
    last_change_date_time: OptionalStr = Field(default=None, alias="lastChangeDateTime")
    billing_document_date: OptionalStr = Field(default=None, alias="billingDocumentDate")
    billing_document_is_cancelled: bool = Field(default=False, alias="billingDocumentIsCancelled")
    cancelled_billing_document: OptionalStr = Field(default=None, alias="cancelledBillingDocument")
    total_net_amount: OptDecimal = Field(default=None, alias="totalNetAmount")
    transaction_currency: OptionalStr = Field(default=None, alias="transactionCurrency")
    company_code: OptionalStr = Field(default=None, alias="companyCode")
    fiscal_year: OptionalStr = Field(default=None, alias="fiscalYear")
    accounting_document: OptionalStr = Field(default=None, alias="accountingDocument")
    sold_to_party: OptionalStr = Field(default=None, alias="soldToParty")


class BillingDocumentItem(BaseModel):
    """VBRP-equivalent line. PK: (billing_document, item). FK: reference_sd_document -> delivery (typically)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    billing_document: str = Field(alias="billingDocument")
    billing_document_item: str = Field(alias="billingDocumentItem")
    material: str = Field(alias="material")
    billing_quantity: OptDecimal = Field(default=None, alias="billingQuantity")
    billing_quantity_unit: OptionalStr = Field(default=None, alias="billingQuantityUnit")
    net_amount: OptDecimal = Field(default=None, alias="netAmount")
    transaction_currency: OptionalStr = Field(default=None, alias="transactionCurrency")
    reference_sd_document: str = Field(alias="referenceSdDocument")
    reference_sd_document_item: str = Field(alias="referenceSdDocumentItem")

    @property
    def item_key(self) -> str:
        return normalize_sd_item_number(self.billing_document_item)

    @property
    def reference_item_key(self) -> str:
        return normalize_sd_item_number(self.reference_sd_document_item)


class JournalEntryItemAR(BaseModel):
    """FI-AR line item (subset). PK: (company_code, fiscal_year, accounting_document, accounting_document_item)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    company_code: str = Field(alias="companyCode")
    fiscal_year: str = Field(alias="fiscalYear")
    accounting_document: str = Field(alias="accountingDocument")
    accounting_document_item: str = Field(alias="accountingDocumentItem")
    gl_account: str = Field(alias="glAccount")
    reference_document: OptionalStr = Field(default=None, alias="referenceDocument")
    accounting_document_type: OptionalStr = Field(default=None, alias="accountingDocumentType")
    cost_center: OptionalStr = Field(default=None, alias="costCenter")
    profit_center: OptionalStr = Field(default=None, alias="profitCenter")
    transaction_currency: OptionalStr = Field(default=None, alias="transactionCurrency")
    amount_in_transaction_currency: OptDecimal = Field(default=None, alias="amountInTransactionCurrency")
    company_code_currency: OptionalStr = Field(default=None, alias="companyCodeCurrency")
    amount_in_company_code_currency: OptDecimal = Field(default=None, alias="amountInCompanyCodeCurrency")
    posting_date: OptionalStr = Field(default=None, alias="postingDate")
    document_date: OptionalStr = Field(default=None, alias="documentDate")
    assignment_reference: OptionalStr = Field(default=None, alias="assignmentReference")
    last_change_date_time: OptionalStr = Field(default=None, alias="lastChangeDateTime")
    customer: OptionalStr = Field(default=None, alias="customer")
    financial_account_type: OptionalStr = Field(default=None, alias="financialAccountType")
    clearing_date: OptionalStr = Field(default=None, alias="clearingDate")
    clearing_accounting_document: OptionalStr = Field(default=None, alias="clearingAccountingDocument")
    clearing_doc_fiscal_year: OptionalStr = Field(default=None, alias="clearingDocFiscalYear")


class PaymentARLine(BaseModel):
    """Clearing / payment line on customer account. PK matches accounting line composite."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    company_code: str = Field(alias="companyCode")
    fiscal_year: str = Field(alias="fiscalYear")
    accounting_document: str = Field(alias="accountingDocument")
    accounting_document_item: str = Field(alias="accountingDocumentItem")
    clearing_date: OptionalStr = Field(default=None, alias="clearingDate")
    clearing_accounting_document: OptionalStr = Field(default=None, alias="clearingAccountingDocument")
    clearing_doc_fiscal_year: OptionalStr = Field(default=None, alias="clearingDocFiscalYear")
    amount_in_transaction_currency: OptDecimal = Field(default=None, alias="amountInTransactionCurrency")
    transaction_currency: OptionalStr = Field(default=None, alias="transactionCurrency")
    amount_in_company_code_currency: OptDecimal = Field(default=None, alias="amountInCompanyCodeCurrency")
    company_code_currency: OptionalStr = Field(default=None, alias="companyCodeCurrency")
    customer: OptionalStr = Field(default=None, alias="customer")
    invoice_reference: OptionalStr = Field(default=None, alias="invoiceReference")
    invoice_reference_fiscal_year: OptionalStr = Field(default=None, alias="invoiceReferenceFiscalYear")
    sales_document: OptionalStr = Field(default=None, alias="salesDocument")
    sales_document_item: OptionalStr = Field(default=None, alias="salesDocumentItem")
    posting_date: OptionalStr = Field(default=None, alias="postingDate")
    document_date: OptionalStr = Field(default=None, alias="documentDate")
    assignment_reference: OptionalStr = Field(default=None, alias="assignmentReference")
    gl_account: OptionalStr = Field(default=None, alias="glAccount")
    financial_account_type: OptionalStr = Field(default=None, alias="financialAccountType")
    profit_center: OptionalStr = Field(default=None, alias="profitCenter")
    cost_center: OptionalStr = Field(default=None, alias="costCenter")


class Customer(BaseModel):
    """Business partner marked as customer (KNA1-style subset). PK: ``business_partner`` / ``customer``."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    business_partner: str = Field(alias="businessPartner")
    customer: str = Field(alias="customer")
    business_partner_category: OptionalStr = Field(default=None, alias="businessPartnerCategory")
    business_partner_full_name: OptionalStr = Field(default=None, alias="businessPartnerFullName")
    business_partner_grouping: OptionalStr = Field(default=None, alias="businessPartnerGrouping")
    business_partner_name: OptionalStr = Field(default=None, alias="businessPartnerName")
    correspondence_language: OptionalStr = Field(default=None, alias="correspondenceLanguage")
    created_by_user: OptionalStr = Field(default=None, alias="createdByUser")
    creation_date: OptionalStr = Field(default=None, alias="creationDate")
    creation_time: Optional[SapClockTime] = Field(default=None, alias="creationTime")
    first_name: OptionalStr = Field(default=None, alias="firstName")
    form_of_address: OptionalStr = Field(default=None, alias="formOfAddress")
    industry: OptionalStr = Field(default=None, alias="industry")
    last_change_date: OptionalStr = Field(default=None, alias="lastChangeDate")
    last_name: OptionalStr = Field(default=None, alias="lastName")
    organization_bp_name1: OptionalStr = Field(default=None, alias="organizationBpName1")
    organization_bp_name2: OptionalStr = Field(default=None, alias="organizationBpName2")
    business_partner_is_blocked: bool = Field(default=False, alias="businessPartnerIsBlocked")
    is_marked_for_archiving: bool = Field(default=False, alias="isMarkedForArchiving")


class PartnerAddress(BaseModel):
    """BP address (validity window). PK: (business_partner, address_id)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    business_partner: str = Field(alias="businessPartner")
    address_id: str = Field(alias="addressId")
    validity_start_date: OptionalStr = Field(default=None, alias="validityStartDate")
    validity_end_date: OptionalStr = Field(default=None, alias="validityEndDate")
    address_uuid: OptionalStr = Field(default=None, alias="addressUuid")
    address_time_zone: OptionalStr = Field(default=None, alias="addressTimeZone")
    city_name: OptionalStr = Field(default=None, alias="cityName")
    country: OptionalStr = Field(default=None, alias="country")
    postal_code: OptionalStr = Field(default=None, alias="postalCode")
    region: OptionalStr = Field(default=None, alias="region")
    street_name: OptionalStr = Field(default=None, alias="streetName")
    po_box: OptionalStr = Field(default=None, alias="poBox")
    po_box_deviating_city_name: OptionalStr = Field(default=None, alias="poBoxDeviatingCityName")
    po_box_deviating_country: OptionalStr = Field(default=None, alias="poBoxDeviatingCountry")
    po_box_deviating_region: OptionalStr = Field(default=None, alias="poBoxDeviatingRegion")
    po_box_is_without_number: Optional[bool] = Field(default=None, alias="poBoxIsWithoutNumber")
    po_box_lobby_name: OptionalStr = Field(default=None, alias="poBoxLobbyName")
    po_box_postal_code: OptionalStr = Field(default=None, alias="poBoxPostalCode")
    tax_jurisdiction: OptionalStr = Field(default=None, alias="taxJurisdiction")
    transport_zone: OptionalStr = Field(default=None, alias="transportZone")


class ProductMaster(BaseModel):
    """Material master header (MARA-style subset). PK: ``product`` (material number in this extract)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    product: str = Field(alias="product")
    product_type: OptionalStr = Field(default=None, alias="productType")
    cross_plant_status: OptionalStr = Field(default=None, alias="crossPlantStatus")
    cross_plant_status_validity_date: OptionalStr = Field(default=None, alias="crossPlantStatusValidityDate")
    creation_date: OptionalStr = Field(default=None, alias="creationDate")
    created_by_user: OptionalStr = Field(default=None, alias="createdByUser")
    last_change_date: OptionalStr = Field(default=None, alias="lastChangeDate")
    last_change_date_time: OptionalStr = Field(default=None, alias="lastChangeDateTime")
    is_marked_for_deletion: bool = Field(default=False, alias="isMarkedForDeletion")
    product_old_id: OptionalStr = Field(default=None, alias="productOldId")
    gross_weight: OptDecimal = Field(default=None, alias="grossWeight")
    weight_unit: OptionalStr = Field(default=None, alias="weightUnit")
    net_weight: OptDecimal = Field(default=None, alias="netWeight")
    product_group: OptionalStr = Field(default=None, alias="productGroup")
    base_unit: OptionalStr = Field(default=None, alias="baseUnit")
    division: OptionalStr = Field(default=None, alias="division")
    industry_sector: OptionalStr = Field(default=None, alias="industrySector")


class ProductDescription(BaseModel):
    """Text for material. PK: (product, language)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    product: str = Field(alias="product")
    language: str = Field(alias="language")
    product_description: OptionalStr = Field(default=None, alias="productDescription")


class ProductPlant(BaseModel):
    """MARC-style plant view. PK: (product, plant)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    product: str = Field(alias="product")
    plant: str = Field(alias="plant")
    country_of_origin: OptionalStr = Field(default=None, alias="countryOfOrigin")
    region_of_origin: OptionalStr = Field(default=None, alias="regionOfOrigin")
    production_invtry_managed_loc: OptionalStr = Field(default=None, alias="productionInvtryManagedLoc")
    availability_check_type: OptionalStr = Field(default=None, alias="availabilityCheckType")
    fiscal_year_variant: OptionalStr = Field(default=None, alias="fiscalYearVariant")
    profit_center: OptionalStr = Field(default=None, alias="profitCenter")
    mrp_type: OptionalStr = Field(default=None, alias="mrpType")


class ProductStorageLocation(BaseModel):
    """MARD-style storage location row. PK: (product, plant, storage_location)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    product: str = Field(alias="product")
    plant: str = Field(alias="plant")
    storage_location: str = Field(alias="storageLocation")
    physical_inventory_block_ind: OptionalStr = Field(default=None, alias="physicalInventoryBlockInd")
    date_of_last_posted_cnt_un_rstrcd_stk: OptionalStr = Field(default=None, alias="dateOfLastPostedCntUnRstrcdStk")


class Plant(BaseModel):
    """T001W-style plant master. PK: ``plant``."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    plant: str = Field(alias="plant")
    plant_name: OptionalStr = Field(default=None, alias="plantName")
    valuation_area: OptionalStr = Field(default=None, alias="valuationArea")
    plant_customer: OptionalStr = Field(default=None, alias="plantCustomer")
    plant_supplier: OptionalStr = Field(default=None, alias="plantSupplier")
    factory_calendar: OptionalStr = Field(default=None, alias="factoryCalendar")
    default_purchasing_organization: OptionalStr = Field(default=None, alias="defaultPurchasingOrganization")
    sales_organization: OptionalStr = Field(default=None, alias="salesOrganization")
    address_id: OptionalStr = Field(default=None, alias="addressId")
    plant_category: OptionalStr = Field(default=None, alias="plantCategory")
    distribution_channel: OptionalStr = Field(default=None, alias="distributionChannel")
    division: OptionalStr = Field(default=None, alias="division")
    language: OptionalStr = Field(default=None, alias="language")
    is_marked_for_archiving: bool = Field(default=False, alias="isMarkedForArchiving")


class CustomerSalesAreaAssignment(BaseModel):
    """KNVV-style. PK: (customer, sales_organization, distribution_channel, division)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    customer: str = Field(alias="customer")
    sales_organization: str = Field(alias="salesOrganization")
    distribution_channel: str = Field(alias="distributionChannel")
    division: str = Field(alias="division")
    billing_is_blocked_for_customer: OptionalStr = Field(default=None, alias="billingIsBlockedForCustomer")
    complete_delivery_is_defined: Optional[bool] = Field(default=None, alias="completeDeliveryIsDefined")
    credit_control_area: OptionalStr = Field(default=None, alias="creditControlArea")
    currency: OptionalStr = Field(default=None, alias="currency")
    customer_payment_terms: OptionalStr = Field(default=None, alias="customerPaymentTerms")
    delivery_priority: OptionalStr = Field(default=None, alias="deliveryPriority")
    incoterms_classification: OptionalStr = Field(default=None, alias="incotermsClassification")
    incoterms_location1: OptionalStr = Field(default=None, alias="incotermsLocation1")
    sales_group: OptionalStr = Field(default=None, alias="salesGroup")
    sales_office: OptionalStr = Field(default=None, alias="salesOffice")
    shipping_condition: OptionalStr = Field(default=None, alias="shippingCondition")
    sls_unlmtd_ovrdeliv_is_allwd: Optional[bool] = Field(default=None, alias="slsUnlmtdOvrdelivIsAllwd")
    supplying_plant: OptionalStr = Field(default=None, alias="supplyingPlant")
    sales_district: OptionalStr = Field(default=None, alias="salesDistrict")
    exchange_rate_type: OptionalStr = Field(default=None, alias="exchangeRateType")


class CustomerCompanyAssignment(BaseModel):
    """KNB1-style company code data. PK: (customer, company_code)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    customer: str = Field(alias="customer")
    company_code: str = Field(alias="companyCode")
    accounting_clerk: OptionalStr = Field(default=None, alias="accountingClerk")
    accounting_clerk_fax_number: OptionalStr = Field(default=None, alias="accountingClerkFaxNumber")
    accounting_clerk_internet_address: OptionalStr = Field(default=None, alias="accountingClerkInternetAddress")
    accounting_clerk_phone_number: OptionalStr = Field(default=None, alias="accountingClerkPhoneNumber")
    alternative_payer_account: OptionalStr = Field(default=None, alias="alternativePayerAccount")
    payment_blocking_reason: OptionalStr = Field(default=None, alias="paymentBlockingReason")
    payment_methods_list: OptionalStr = Field(default=None, alias="paymentMethodsList")
    payment_terms: OptionalStr = Field(default=None, alias="paymentTerms")
    reconciliation_account: OptionalStr = Field(default=None, alias="reconciliationAccount")
    deletion_indicator: bool = Field(default=False, alias="deletionIndicator")
    customer_account_group: OptionalStr = Field(default=None, alias="customerAccountGroup")


def accounting_line_key(
    company_code: str,
    fiscal_year: str,
    accounting_document: str,
    accounting_document_item: str,
) -> str:
    """Stable string key for FI document lines (company-local uniqueness)."""
    item = normalize_sd_item_number(accounting_document_item)
    return f"{company_code}|{fiscal_year}|{accounting_document}|{item}"
