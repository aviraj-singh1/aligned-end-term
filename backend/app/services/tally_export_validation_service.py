"""Deterministic pre-export validation for Tally Export V1.

The validator is pure: callers provide a canonical candidate plus persisted
configuration snapshots. The database-facing helper only resolves tenant-
scoped snapshots and previously exported identities.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence
from uuid import UUID, uuid5

from sqlmodel import Session, select

from backend.app.db.models import (
    TallyExportItem,
    TallyExportProfile,
    TallyPartyLedgerMapping,
    TallyPurchaseLedgerMapping,
)
from backend.app.db.session import get_database_engine
from backend.app.models.tally_export import (
    ReverseChargeStatus,
    TallyExportCandidate,
    TallyExportTransactionType,
    TallyTaxStructure,
)
from backend.app.models.tally_export_validation import (
    ResolvedTallyConfiguration,
    TallyBlockerCode,
    TallyCandidateState,
    TallyCandidateValidation,
    TallyPartyMappingSnapshot,
    TallyPriorExportSnapshot,
    TallyProfileSnapshot,
    TallyPurchaseMappingSnapshot,
    TallyValidationIssue,
    TallyWarningCode,
)
from backend.app.services.normalizer import normalize_gstin, normalize_invoice_number
from backend.app.services.tally_export_configuration_service import (
    SUPPORTED_TALLY_VERSIONS,
    _authorize_target,
)


IDENTITY_NAMESPACE = UUID("8c6dd559-56c4-4a0a-bd80-d7db499f5258")
MONEY = Decimal("0.01")
RATE = Decimal("0.001")

_STATE_CODES = {
    "jammu and kashmir": "01", "himachal pradesh": "02", "punjab": "03",
    "chandigarh": "04", "uttarakhand": "05", "haryana": "06", "delhi": "07",
    "rajasthan": "08", "uttar pradesh": "09", "bihar": "10", "sikkim": "11",
    "arunachal pradesh": "12", "nagaland": "13", "manipur": "14", "mizoram": "15",
    "tripura": "16", "meghalaya": "17", "assam": "18", "west bengal": "19",
    "jharkhand": "20", "odisha": "21", "chhattisgarh": "22", "madhya pradesh": "23",
    "gujarat": "24", "dadra and nagar haveli and daman and diu": "26",
    "maharashtra": "27", "andhra pradesh": "37", "karnataka": "29", "goa": "30",
    "lakshadweep": "31", "kerala": "32", "tamil nadu": "33", "puducherry": "34",
    "andaman and nicobar islands": "35", "telangana": "36", "ladakh": "38",
    "other territory": "97", "centre jurisdiction": "99",
}


def _money(value: Decimal) -> str:
    return format(Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP), "f")


def _rate(value: Decimal) -> str:
    return format(Decimal(value).quantize(RATE, rounding=ROUND_HALF_UP), "f")


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_identity(candidate: TallyExportCandidate, client_gstin: str) -> dict[str, str]:
    _, normalized_client, _ = normalize_gstin(client_gstin)
    _, normalized_supplier, _ = normalize_gstin(candidate.supplier_gstin)
    invoice = normalize_invoice_number(candidate.document_number).normalized
    return {
        "client_gstin": normalized_client,
        "document_kind": candidate.transaction_type.value,
        "supplier_gstin": normalized_supplier,
        "normalized_document_number": invoice,
        "document_date": candidate.document_date.isoformat() if candidate.document_date else "",
    }


def deterministic_remote_id(candidate: TallyExportCandidate, client_gstin: str) -> UUID:
    return uuid5(IDENTITY_NAMESPACE, _canonical(canonical_identity(candidate, client_gstin)))


def transaction_fingerprint(candidate: TallyExportCandidate, client_gstin: str) -> str:
    payload = {
        "identity": canonical_identity(candidate, client_gstin),
        "taxable_value": _money(candidate.taxable_value),
        "igst": _money(candidate.igst),
        "cgst": _money(candidate.cgst),
        "sgst": _money(candidate.sgst),
        "cess": _money(candidate.cess),
        "rates": [
            {
                "rate": _rate(item.gst_rate),
                "taxable_value": _money(item.taxable_value),
                "igst": _money(item.igst),
                "cgst": _money(item.cgst),
                "sgst": _money(item.sgst),
                "cess": _money(item.cess),
            }
            for item in sorted(candidate.rate_allocations, key=lambda row: row.gst_rate)
        ],
    }
    return hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()


def _place_state_code(value: str) -> str | None:
    normalized = " ".join(str(value or "").strip().lower().replace("&", "and").split())
    match = re.match(r"^(\d{2})(?:\D|$)", normalized)
    if match and (match.group(1) in set(_STATE_CODES.values())):
        return match.group(1)
    return _STATE_CODES.get(normalized)


def _profile_snapshot(row: TallyExportProfile | None) -> TallyProfileSnapshot | None:
    if row is None:
        return None
    return TallyProfileSnapshot(
        id=row.id, version=row.version, status=row.status, company_name=row.company_name,
        tally_version=row.tally_version, purchase_voucher_type=row.purchase_voucher_type,
        igst_ledger_name=row.igst_ledger_name, cgst_ledger_name=row.cgst_ledger_name,
        sgst_ledger_name=row.sgst_ledger_name, numbering_strategy=row.numbering_strategy,
    )


def _party_snapshot(row: TallyPartyLedgerMapping | None) -> TallyPartyMappingSnapshot | None:
    if row is None:
        return None
    return TallyPartyMappingSnapshot(
        status=row.status, supplier_gstin=row.supplier_gstin,
        last_source_supplier_name=row.last_source_supplier_name,
        tally_party_ledger_name=row.tally_party_ledger_name,
    )


def _purchase_snapshots(rows: Iterable[TallyPurchaseLedgerMapping]) -> tuple[TallyPurchaseMappingSnapshot, ...]:
    return tuple(TallyPurchaseMappingSnapshot(
        status=row.status, transaction_kind=row.transaction_kind, gst_rate=row.gst_rate,
        tally_purchase_ledger_name=row.tally_purchase_ledger_name,
    ) for row in rows)


def validate_tally_candidate(
    candidate: TallyExportCandidate,
    *,
    client_gstin: str,
    profile: TallyProfileSnapshot | None,
    party_mapping: TallyPartyMappingSnapshot | None,
    purchase_mappings: Sequence[TallyPurchaseMappingSnapshot] = (),
    prior_exports: Sequence[TallyPriorExportSnapshot] = (),
    candidate_state: TallyCandidateState = TallyCandidateState.CURRENT,
) -> TallyCandidateValidation:
    """Validate without I/O or mutation and return stable machine-readable issues."""
    blockers: list[TallyValidationIssue] = []
    warnings: list[TallyValidationIssue] = []

    def block(code: TallyBlockerCode, message: str):
        if code.value not in {issue.code for issue in blockers}:
            blockers.append(TallyValidationIssue(code.value, message))

    def warn(code: TallyWarningCode, message: str):
        if code.value not in {issue.code for issue in warnings}:
            warnings.append(TallyValidationIssue(code.value, message))

    remote_id = deterministic_remote_id(candidate, client_gstin)
    fingerprint = transaction_fingerprint(candidate, client_gstin)
    candidate_id = f"{candidate.transaction_type.value}:{remote_id}"

    if candidate.transaction_type != TallyExportTransactionType.PURCHASE_INVOICE:
        block(TallyBlockerCode.UNSUPPORTED_TRANSACTION_TYPE, "Only purchase invoices are supported in Tally Export V1.")
    if not candidate.supplier_gstin_valid or not normalize_gstin(candidate.supplier_gstin)[2]:
        block(TallyBlockerCode.INVALID_SUPPLIER_GSTIN, "Supplier GSTIN is missing or invalid.")
    if not normalize_invoice_number(candidate.document_number).normalized:
        block(TallyBlockerCode.MISSING_DOCUMENT_NUMBER, "Supplier invoice number is required.")
    if candidate.document_date is None:
        block(TallyBlockerCode.MISSING_DOCUMENT_DATE, "Supplier invoice date is required.")
    if candidate.financial_parse_issues:
        block(TallyBlockerCode.UNRESOLVED_FINANCIAL_PARSE_ISSUES, "Source financial values contain parsing errors.")

    if profile is None:
        block(TallyBlockerCode.MISSING_TALLY_PROFILE, "An active Tally export profile is required.")
    else:
        if profile.status != "ACTIVE":
            block(TallyBlockerCode.INACTIVE_TALLY_PROFILE, "The Tally export profile is inactive.")
        if profile.tally_version not in SUPPORTED_TALLY_VERSIONS:
            block(
                TallyBlockerCode.UNSUPPORTED_TALLY_VERSION,
                "Tally Export V1 supports TallyPrime 7.1 only.",
            )
        if profile.numbering_strategy != "TALLY_AUTO":
            block(TallyBlockerCode.UNSUPPORTED_NUMBERING_STRATEGY, "Tally Export V1 requires Tally auto-numbering.")

    if party_mapping is None:
        block(TallyBlockerCode.MISSING_PARTY_LEDGER_MAPPING, "A confirmed exact-GSTIN party ledger mapping is required.")
    elif party_mapping.status != "ACTIVE":
        block(TallyBlockerCode.INACTIVE_PARTY_LEDGER_MAPPING, "The supplier party ledger mapping is inactive.")
    else:
        warn(TallyWarningCode.SAVED_PARTY_MAPPING_REUSED, "The saved exact-GSTIN party mapping will be reused.")
        source_name = " ".join(candidate.supplier_name.casefold().split())
        saved_source_name = " ".join(party_mapping.last_source_supplier_name.casefold().split())
        mapped_name = " ".join(party_mapping.tally_party_ledger_name.casefold().split())
        if source_name != saved_source_name:
            warn(TallyWarningCode.SUPPLIER_NAME_CHANGED, "The source supplier name changed for this GSTIN.")
        if source_name != mapped_name:
            warn(TallyWarningCode.DISPLAY_NAME_DIFFERS_FROM_MAPPING, "The source supplier name differs from the saved Tally ledger name.")

    mapping_by_rate = {
        Decimal(row.gst_rate).quantize(RATE): row for row in purchase_mappings
        if row.transaction_kind == TallyExportTransactionType.PURCHASE_INVOICE.value
    }
    resolved_purchase: list[tuple[Decimal, str]] = []
    if candidate.gst_rate_missing or not candidate.gst_rates:
        block(TallyBlockerCode.UNRESOLVED_GST_RATE, "Every purchase allocation requires a resolved GST rate.")
    for gst_rate in sorted(candidate.gst_rates):
        rate_key = Decimal(gst_rate).quantize(RATE)
        mapping = mapping_by_rate.get(rate_key)
        if mapping is None:
            block(TallyBlockerCode.MISSING_PURCHASE_LEDGER_MAPPING, f"No purchase ledger mapping exists for GST rate {_rate(gst_rate)}%.")
        elif mapping.status != "ACTIVE":
            block(TallyBlockerCode.INACTIVE_PURCHASE_LEDGER_MAPPING, f"The purchase ledger mapping for GST rate {_rate(gst_rate)}% is inactive.")
        else:
            resolved_purchase.append((gst_rate, mapping.tally_purchase_ledger_name))

    amounts = (candidate.igst, candidate.cgst, candidate.sgst, candidate.cess)
    if candidate.taxable_value <= 0:
        block(TallyBlockerCode.NON_POSITIVE_TAXABLE_VALUE, "Purchase taxable value must be greater than zero.")
    if any(amount < 0 for amount in amounts):
        block(TallyBlockerCode.NEGATIVE_TAX_AMOUNT, "Purchase tax amounts cannot be negative.")
    if candidate.cess != 0:
        block(TallyBlockerCode.CESS_UNSUPPORTED, "Cess-bearing purchases are not supported in Tally Export V1.")
    if candidate.is_amendment:
        block(TallyBlockerCode.AMENDMENT_UNSUPPORTED, "Amended documents are not supported in Tally Export V1.")
    if candidate.is_cancelled:
        block(TallyBlockerCode.CANCELLATION_UNSUPPORTED, "Cancelled documents are not supported in Tally Export V1.")

    if candidate.reverse_charge_status == ReverseChargeStatus.YES:
        block(TallyBlockerCode.REVERSE_CHARGE_UNSUPPORTED, "Reverse-charge purchases are not supported in Tally Export V1.")
    elif candidate.reverse_charge_status != ReverseChargeStatus.NO:
        block(TallyBlockerCode.REVERSE_CHARGE_UNRESOLVED, "Reverse-charge status is missing, unknown, or conflicting.")

    if candidate.place_of_supply_conflicting:
        block(TallyBlockerCode.CONFLICTING_PLACE_OF_SUPPLY, "Source rows contain conflicting places of supply.")
    elif candidate.place_of_supply_missing or not candidate.place_of_supply:
        block(TallyBlockerCode.MISSING_PLACE_OF_SUPPLY, "Place of supply is required for safe tax-ledger selection.")
    else:
        place_code = _place_state_code(candidate.place_of_supply)
        supplier_code = normalize_gstin(candidate.supplier_gstin)[1][:2]
        if place_code is None:
            block(TallyBlockerCode.INVALID_PLACE_OF_SUPPLY, "Place of supply could not be mapped to a GST state code.")
        elif candidate.tax_structure == TallyTaxStructure.IGST_ONLY and place_code == supplier_code:
            block(TallyBlockerCode.PLACE_OF_SUPPLY_TAX_CONFLICT, "IGST cannot be safely used for an intra-state place of supply.")
        elif candidate.tax_structure == TallyTaxStructure.CGST_SGST and place_code != supplier_code:
            block(TallyBlockerCode.PLACE_OF_SUPPLY_TAX_CONFLICT, "CGST/SGST cannot be safely used for an inter-state place of supply.")

    has_igst = candidate.igst != 0
    has_cgst = candidate.cgst != 0
    has_sgst = candidate.sgst != 0
    if has_igst and (has_cgst or has_sgst):
        block(TallyBlockerCode.MIXED_IGST_AND_CGST_SGST, "IGST and CGST/SGST cannot coexist on one supported purchase candidate.")
    if has_cgst != has_sgst:
        block(TallyBlockerCode.INCOMPLETE_CGST_SGST, "Both CGST and SGST are required for an intra-state purchase.")
    if has_cgst and has_sgst and candidate.cgst != candidate.sgst:
        block(TallyBlockerCode.UNEQUAL_CGST_SGST, "CGST and SGST must be equal.")
    if candidate.tax_structure == TallyTaxStructure.MIXED_OR_INCONSISTENT:
        block(TallyBlockerCode.INCONSISTENT_TAX_STRUCTURE, "The aggregate tax structure is inconsistent.")
    for allocation in candidate.rate_allocations:
        allocation_has_igst = allocation.igst != 0
        allocation_has_cgst = allocation.cgst != 0
        allocation_has_sgst = allocation.sgst != 0
        if allocation.taxable_value <= 0:
            block(TallyBlockerCode.NON_POSITIVE_TAXABLE_VALUE, "Every GST-rate allocation requires a positive taxable value.")
        if any(amount < 0 for amount in (
            allocation.igst, allocation.cgst, allocation.sgst, allocation.cess,
        )):
            block(TallyBlockerCode.NEGATIVE_TAX_AMOUNT, "GST-rate allocation tax amounts cannot be negative.")
        if allocation_has_igst and (allocation_has_cgst or allocation_has_sgst):
            block(TallyBlockerCode.MIXED_IGST_AND_CGST_SGST, "A GST-rate allocation mixes IGST with CGST/SGST.")
        if allocation_has_cgst != allocation_has_sgst:
            block(TallyBlockerCode.INCOMPLETE_CGST_SGST, "A GST-rate allocation has incomplete CGST/SGST.")
        if allocation_has_cgst and allocation_has_sgst and allocation.cgst != allocation.sgst:
            block(TallyBlockerCode.UNEQUAL_CGST_SGST, "A GST-rate allocation has unequal CGST and SGST.")

    if profile is not None:
        required_tax_ledgers = []
        if has_igst:
            required_tax_ledgers.append(profile.igst_ledger_name)
        if has_cgst:
            required_tax_ledgers.append(profile.cgst_ledger_name)
        if has_sgst:
            required_tax_ledgers.append(profile.sgst_ledger_name)
        if any(not str(name or "").strip() for name in required_tax_ledgers):
            block(TallyBlockerCode.MISSING_TAX_LEDGER, "Every used tax head requires a configured Tally ledger.")

    allocation_totals = (
        sum((row.taxable_value for row in candidate.rate_allocations), Decimal("0")),
        sum((row.igst for row in candidate.rate_allocations), Decimal("0")),
        sum((row.cgst for row in candidate.rate_allocations), Decimal("0")),
        sum((row.sgst for row in candidate.rate_allocations), Decimal("0")),
        sum((row.cess for row in candidate.rate_allocations), Decimal("0")),
    )
    candidate_totals = (candidate.taxable_value, candidate.igst, candidate.cgst, candidate.sgst, candidate.cess)
    allocations_balance = all(
        Decimal(actual).quantize(MONEY) == Decimal(expected).quantize(MONEY)
        for actual, expected in zip(allocation_totals, candidate_totals)
    ) and len(candidate.rate_allocations) == len(candidate.gst_rates)
    if not allocations_balance:
        block(TallyBlockerCode.DEBIT_CREDIT_IMBALANCE, "Per-rate purchase allocations do not balance to the candidate totals.")

    calculated_total = sum(candidate_totals)
    if candidate.invoice_total is not None:
        if candidate.invoice_total.quantize(MONEY) != calculated_total.quantize(MONEY):
            block(TallyBlockerCode.INVOICE_TOTAL_MISMATCH, "Invoice total differs from taxable value plus tax components.")
    elif allocations_balance:
        warn(TallyWarningCode.INVOICE_TOTAL_MISSING_COMPONENTS_BALANCED, "Invoice total is absent, but the source components balance exactly.")

    state_codes = {
        TallyCandidateState.STALE: (TallyBlockerCode.STALE_CANDIDATE, "The Not-in-Tally candidate is stale."),
        TallyCandidateState.CONSUMED: (TallyBlockerCode.CONSUMED_CANDIDATE, "The Not-in-Tally candidate was already consumed."),
        TallyCandidateState.NON_CURRENT: (TallyBlockerCode.NON_CURRENT_CANDIDATE, "The candidate is not part of the current reconciliation result."),
    }
    if candidate_state in state_codes:
        block(*state_codes[candidate_state])

    for previous in prior_exports:
        if previous.remote_id != remote_id:
            continue
        if previous.transaction_fingerprint == fingerprint:
            warn(TallyWarningCode.PREVIOUSLY_EXPORTED, "This exact transaction was previously exported; explicit re-export confirmation is required.")
        else:
            block(TallyBlockerCode.REMOTE_ID_FINGERPRINT_CONFLICT, "The stable voucher identity was previously exported with different financial data.")

    resolved = ResolvedTallyConfiguration(
        profile_id=profile.id if profile else None,
        profile_version=profile.version if profile else None,
        tally_version=profile.tally_version if profile else "",
        company_name=profile.company_name if profile else "",
        purchase_voucher_type=profile.purchase_voucher_type if profile else "",
        party_ledger_name=party_mapping.tally_party_ledger_name if party_mapping and party_mapping.status == "ACTIVE" else "",
        purchase_ledgers=tuple(resolved_purchase),
        igst_ledger_name=profile.igst_ledger_name if profile else "",
        cgst_ledger_name=profile.cgst_ledger_name if profile else "",
        sgst_ledger_name=profile.sgst_ledger_name if profile else "",
    )
    return TallyCandidateValidation(
        candidate_id=candidate_id, remote_id=remote_id, fingerprint=fingerprint,
        eligible=not blockers, blockers=tuple(blockers), warnings=tuple(warnings), resolved=resolved,
    )


def validate_persisted_tally_candidate(
    user,
    workspace_id: UUID,
    client_id: UUID,
    client_gstin_id: UUID,
    candidate: TallyExportCandidate,
    *,
    candidate_state: TallyCandidateState = TallyCandidateState.CURRENT,
) -> TallyCandidateValidation:
    """Resolve current tenant-scoped configuration and invoke the pure validator."""
    with Session(get_database_engine()) as db:
        _, gstin = _authorize_target(db, user, workspace_id, client_id, client_gstin_id)
        profile = db.exec(select(TallyExportProfile).where(
            TallyExportProfile.workspace_id == workspace_id,
            TallyExportProfile.client_id == client_id,
            TallyExportProfile.client_gstin_id == client_gstin_id,
        )).first()
        party = db.exec(select(TallyPartyLedgerMapping).where(
            TallyPartyLedgerMapping.workspace_id == workspace_id,
            TallyPartyLedgerMapping.client_id == client_id,
            TallyPartyLedgerMapping.client_gstin_id == client_gstin_id,
            TallyPartyLedgerMapping.supplier_gstin == candidate.supplier_gstin.strip().upper(),
        )).first()
        purchases = db.exec(select(TallyPurchaseLedgerMapping).where(
            TallyPurchaseLedgerMapping.workspace_id == workspace_id,
            TallyPurchaseLedgerMapping.client_id == client_id,
            TallyPurchaseLedgerMapping.client_gstin_id == client_gstin_id,
        )).all()
        identity = deterministic_remote_id(candidate, gstin.gstin)
        previous = db.exec(select(TallyExportItem).where(
            TallyExportItem.workspace_id == workspace_id,
            TallyExportItem.client_id == client_id,
            TallyExportItem.client_gstin_id == client_gstin_id,
            TallyExportItem.remote_id == identity,
            TallyExportItem.outcome == "EXPORTED",
        )).all()
        return validate_tally_candidate(
            candidate, client_gstin=gstin.gstin, profile=_profile_snapshot(profile),
            party_mapping=_party_snapshot(party), purchase_mappings=_purchase_snapshots(purchases),
            prior_exports=tuple(TallyPriorExportSnapshot(row.remote_id, row.transaction_fingerprint) for row in previous),
            candidate_state=candidate_state,
        )
