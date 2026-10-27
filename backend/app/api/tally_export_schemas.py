"""Strict public schemas for Tally Export V1 configuration APIs."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from backend.app.api.auth_schemas import StrictRequest


Status = Literal["ACTIVE", "INACTIVE"]
PurchaseTransactionKind = Literal["purchase_invoice"]


def _trim(value):
    return value.strip() if isinstance(value, str) else value


class TallyProfileCreateRequest(StrictRequest):
    company_name: str = Field(min_length=1, max_length=255)
    tally_version: Literal["7.1"]
    purchase_voucher_type: str = Field(min_length=1, max_length=200)
    debit_note_voucher_type: str | None = Field(default=None, min_length=1, max_length=200)
    igst_ledger_name: str = Field(min_length=1, max_length=255)
    cgst_ledger_name: str = Field(min_length=1, max_length=255)
    sgst_ledger_name: str = Field(min_length=1, max_length=255)
    numbering_strategy: Literal["TALLY_AUTO"] = "TALLY_AUTO"

    _trim_names = field_validator(
        "company_name", "tally_version", "purchase_voucher_type",
        "debit_note_voucher_type", "igst_ledger_name", "cgst_ledger_name",
        "sgst_ledger_name", "numbering_strategy", mode="before",
    )(_trim)


class TallyProfileUpdateRequest(TallyProfileCreateRequest):
    expected_version: int = Field(ge=1)
    status: Status

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class TallyProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    company_name: str
    tally_version: Literal["7.1"]
    purchase_voucher_type: str
    debit_note_voucher_type: str | None
    igst_ledger_name: str
    cgst_ledger_name: str
    sgst_ledger_name: str
    numbering_strategy: Literal["TALLY_AUTO"]
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class PartyMappingCreateRequest(StrictRequest):
    supplier_gstin: str = Field(
        min_length=15, max_length=15,
        pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$",
    )
    source_supplier_name: str = Field(min_length=1, max_length=255)
    tally_party_ledger_name: str = Field(min_length=1, max_length=255)

    @field_validator("supplier_gstin", mode="before")
    @classmethod
    def normalize_gstin(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    _trim_names = field_validator(
        "source_supplier_name", "tally_party_ledger_name", mode="before"
    )(_trim)


class PartyMappingUpdateRequest(StrictRequest):
    expected_version: int = Field(ge=1)
    source_supplier_name: str = Field(min_length=1, max_length=255)
    tally_party_ledger_name: str | None = Field(default=None, min_length=1, max_length=255)

    _trim_names = field_validator(
        "source_supplier_name", "tally_party_ledger_name", mode="before"
    )(_trim)


class PartyMappingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    supplier_gstin: str
    last_source_supplier_name: str
    tally_party_ledger_name: str
    status: Status
    confirmed_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("confirmed_at", "created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class PartyMappingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PartyMappingResponse]


class PurchaseLedgerMappingCreateRequest(StrictRequest):
    transaction_kind: PurchaseTransactionKind
    gst_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("100"), max_digits=6, decimal_places=3)
    tally_purchase_ledger_name: str = Field(min_length=1, max_length=255)

    @field_validator("transaction_kind", "tally_purchase_ledger_name", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class PurchaseLedgerMappingUpdateRequest(StrictRequest):
    expected_version: int = Field(ge=1)
    tally_purchase_ledger_name: str = Field(min_length=1, max_length=255)
    status: Status = "ACTIVE"

    @field_validator("tally_purchase_ledger_name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return _trim(value)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class PurchaseLedgerMappingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID
    client_gstin_id: UUID
    profile_id: UUID
    transaction_kind: PurchaseTransactionKind
    gst_rate: Decimal
    tally_purchase_ledger_name: str
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("gst_rate")
    def serialize_gst_rate(self, value: Decimal) -> str:
        return format(value, ".3f")

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class PurchaseLedgerMappingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PurchaseLedgerMappingResponse]


class TallyFinancialParseIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str


class TallyCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    supplier_gstin: str
    supplier_name: str
    document_number: str
    document_date: date | None
    taxable_value: str
    igst: str
    cgst: str
    sgst: str
    cess: str
    invoice_total: str | None
    gst_rates: list[str]
    tax_structure: Literal["no_tax", "igst_only", "cgst_sgst", "mixed_or_inconsistent"]
    party_mapping_status: Literal["MAPPED", "UNMAPPED"]
    required_purchase_rates: list[str]
    previously_exported: bool
    financial_parse_issues: list[TallyFinancialParseIssueResponse]


class TallyUnresolvedSupplierResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_gstin: str
    supplier_name: str
    candidate_count: int = Field(ge=1)


class TallyUnresolvedPurchaseRateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_kind: PurchaseTransactionKind
    gst_rate: str
    candidate_count: int = Field(ge=1)


class TallyCandidateRequirementsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unresolved_suppliers: list[TallyUnresolvedSupplierResponse]
    unresolved_purchase_rates: list[TallyUnresolvedPurchaseRateResponse]


class TallyCandidateListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    reconciliation_run_id: UUID
    run_version: int = Field(ge=1)
    items: list[TallyCandidateResponse]
    requirements: TallyCandidateRequirementsResponse
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class TallyBulkValidationRequest(StrictRequest):
    candidate_ids: list[str] = Field(min_length=1, max_length=100)
    expected_run_version: int = Field(ge=1)

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(cls, values):
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 255 for value in normalized):
            raise ValueError("Candidate IDs must be non-empty and at most 255 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Candidate IDs must be unique")
        return normalized


class TallyValidationIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class TallyResolvedPurchaseLedgerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gst_rate: str
    ledger_name: str


class TallyResolvedConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID | None
    profile_version: int | None
    tally_version: str
    party_ledger_name: str
    purchase_ledgers: list[TallyResolvedPurchaseLedgerResponse]


class TallyCandidateValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    eligible: bool
    blockers: list[TallyValidationIssueResponse]
    warnings: list[TallyValidationIssueResponse]
    resolved: TallyResolvedConfigurationResponse


class TallyBulkValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exportable_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    items: list[TallyCandidateValidationResponse]


class TallyBatchCreateRequest(TallyBulkValidationRequest):
    allow_reexport: bool = False


class TallyBatchSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    status: Literal["PENDING", "GENERATED", "FAILED"]
    created_at: datetime
    reconciliation_run_id: UUID
    session_id: UUID | None
    candidate_count: int = Field(ge=0)
    generated_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    checksum_sha256: str | None
    artifact_available: bool
    download_url: str | None

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class TallyBatchCreateResponse(TallyBatchSummaryResponse):
    reused: bool


class TallyBatchListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TallyBatchSummaryResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class TallyBatchItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    supplier_gstin: str | None = None
    supplier_name: str | None = None
    document_number: str | None = None
    document_date: date | None = None
    taxable_value: str | None = None
    total_tax: str | None = None
    invoice_total: str | None = None
    gst_rates: list[str] = Field(default_factory=list)
    outcome: Literal["SELECTED", "EXPORTED", "BLOCKED", "SKIPPED"]
    warning_codes: list[str]


class TallyBatchDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch: TallyBatchSummaryResponse
    failure_code: str | None
    failure_message: str | None
    items: list[TallyBatchItemResponse]
