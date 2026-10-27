"""Pure validation DTOs for Tally Export V1 purchase candidates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Tuple
from uuid import UUID


class TallyCandidateState(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    CONSUMED = "consumed"
    NON_CURRENT = "non_current"


class TallyBlockerCode(str, Enum):
    UNSUPPORTED_TRANSACTION_TYPE = "UNSUPPORTED_TRANSACTION_TYPE"
    INVALID_SUPPLIER_GSTIN = "INVALID_SUPPLIER_GSTIN"
    MISSING_DOCUMENT_NUMBER = "MISSING_DOCUMENT_NUMBER"
    MISSING_DOCUMENT_DATE = "MISSING_DOCUMENT_DATE"
    UNRESOLVED_FINANCIAL_PARSE_ISSUES = "UNRESOLVED_FINANCIAL_PARSE_ISSUES"
    MISSING_TALLY_PROFILE = "MISSING_TALLY_PROFILE"
    INACTIVE_TALLY_PROFILE = "INACTIVE_TALLY_PROFILE"
    UNSUPPORTED_TALLY_VERSION = "UNSUPPORTED_TALLY_VERSION"
    UNSUPPORTED_NUMBERING_STRATEGY = "UNSUPPORTED_NUMBERING_STRATEGY"
    MISSING_PARTY_LEDGER_MAPPING = "MISSING_PARTY_LEDGER_MAPPING"
    INACTIVE_PARTY_LEDGER_MAPPING = "INACTIVE_PARTY_LEDGER_MAPPING"
    MISSING_PURCHASE_LEDGER_MAPPING = "MISSING_PURCHASE_LEDGER_MAPPING"
    INACTIVE_PURCHASE_LEDGER_MAPPING = "INACTIVE_PURCHASE_LEDGER_MAPPING"
    MISSING_TAX_LEDGER = "MISSING_TAX_LEDGER"
    MISSING_PLACE_OF_SUPPLY = "MISSING_PLACE_OF_SUPPLY"
    CONFLICTING_PLACE_OF_SUPPLY = "CONFLICTING_PLACE_OF_SUPPLY"
    INVALID_PLACE_OF_SUPPLY = "INVALID_PLACE_OF_SUPPLY"
    PLACE_OF_SUPPLY_TAX_CONFLICT = "PLACE_OF_SUPPLY_TAX_CONFLICT"
    INCONSISTENT_TAX_STRUCTURE = "INCONSISTENT_TAX_STRUCTURE"
    MIXED_IGST_AND_CGST_SGST = "MIXED_IGST_AND_CGST_SGST"
    INCOMPLETE_CGST_SGST = "INCOMPLETE_CGST_SGST"
    UNEQUAL_CGST_SGST = "UNEQUAL_CGST_SGST"
    REVERSE_CHARGE_UNSUPPORTED = "REVERSE_CHARGE_UNSUPPORTED"
    REVERSE_CHARGE_UNRESOLVED = "REVERSE_CHARGE_UNRESOLVED"
    CESS_UNSUPPORTED = "CESS_UNSUPPORTED"
    AMENDMENT_UNSUPPORTED = "AMENDMENT_UNSUPPORTED"
    CANCELLATION_UNSUPPORTED = "CANCELLATION_UNSUPPORTED"
    NON_POSITIVE_TAXABLE_VALUE = "NON_POSITIVE_TAXABLE_VALUE"
    NEGATIVE_TAX_AMOUNT = "NEGATIVE_TAX_AMOUNT"
    UNRESOLVED_GST_RATE = "UNRESOLVED_GST_RATE"
    INVOICE_TOTAL_MISMATCH = "INVOICE_TOTAL_MISMATCH"
    DEBIT_CREDIT_IMBALANCE = "DEBIT_CREDIT_IMBALANCE"
    STALE_CANDIDATE = "STALE_CANDIDATE"
    CONSUMED_CANDIDATE = "CONSUMED_CANDIDATE"
    NON_CURRENT_CANDIDATE = "NON_CURRENT_CANDIDATE"
    REMOTE_ID_FINGERPRINT_CONFLICT = "REMOTE_ID_FINGERPRINT_CONFLICT"


class TallyWarningCode(str, Enum):
    DISPLAY_NAME_DIFFERS_FROM_MAPPING = "DISPLAY_NAME_DIFFERS_FROM_MAPPING"
    SAVED_PARTY_MAPPING_REUSED = "SAVED_PARTY_MAPPING_REUSED"
    SUPPLIER_NAME_CHANGED = "SUPPLIER_NAME_CHANGED"
    INVOICE_TOTAL_MISSING_COMPONENTS_BALANCED = "INVOICE_TOTAL_MISSING_COMPONENTS_BALANCED"
    PREVIOUSLY_EXPORTED = "PREVIOUSLY_EXPORTED"


@dataclass(frozen=True)
class TallyValidationIssue:
    code: str
    message: str


@dataclass(frozen=True)
class TallyProfileSnapshot:
    id: UUID
    version: int
    status: str
    company_name: str
    tally_version: str
    purchase_voucher_type: str
    igst_ledger_name: str
    cgst_ledger_name: str
    sgst_ledger_name: str
    numbering_strategy: str


@dataclass(frozen=True)
class TallyPartyMappingSnapshot:
    status: str
    supplier_gstin: str
    last_source_supplier_name: str
    tally_party_ledger_name: str


@dataclass(frozen=True)
class TallyPurchaseMappingSnapshot:
    status: str
    transaction_kind: str
    gst_rate: Decimal
    tally_purchase_ledger_name: str


@dataclass(frozen=True)
class TallyPriorExportSnapshot:
    remote_id: UUID
    transaction_fingerprint: str


@dataclass(frozen=True)
class ResolvedTallyConfiguration:
    profile_id: UUID | None
    profile_version: int | None
    tally_version: str
    company_name: str
    purchase_voucher_type: str
    party_ledger_name: str
    purchase_ledgers: Tuple[Tuple[Decimal, str], ...]
    igst_ledger_name: str
    cgst_ledger_name: str
    sgst_ledger_name: str


@dataclass(frozen=True)
class TallyCandidateValidation:
    candidate_id: str
    remote_id: UUID
    fingerprint: str
    eligible: bool
    blockers: Tuple[TallyValidationIssue, ...]
    warnings: Tuple[TallyValidationIssue, ...]
    resolved: ResolvedTallyConfiguration

