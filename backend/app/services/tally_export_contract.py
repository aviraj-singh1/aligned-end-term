"""Build canonical Tally-export candidates from reconciliation outputs.

Phase 1 deliberately stops at a validation-ready DTO. It performs no ledger
mapping, persistence, XML generation, version compatibility, or export I/O.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence

from backend.app.models.records import ParsedInvoiceRecord, SheetCategory
from backend.app.models.reconciliation import AggregatedGSTInvoice
from backend.app.models.tally_export import (
    ReverseChargeStatus,
    TallyExportCandidate,
    TallyRateAllocation,
    TallyExportSourceIdentity,
    TallyExportTransactionType,
)
from backend.app.services.cancellation_detector import cancellation_evidence
from backend.app.services.notes_reconciliation_engine import AggregatedGSTCreditNote


_REVERSE_CHARGE_YES = {"y", "yes", "true", "1"}
_REVERSE_CHARGE_NO = {"n", "no", "false", "0"}


def _unique_text(values: Iterable[str]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for raw in values:
        value = " ".join(str(raw or "").strip().split())
        if value:
            unique.setdefault(value.casefold(), value)
    return tuple(unique.values())


def _reverse_charge_status(rows: Sequence[ParsedInvoiceRecord]) -> tuple[ReverseChargeStatus, tuple[str, ...]]:
    raw_values = tuple(" ".join(str(row.reverse_charge or "").strip().split()) for row in rows)
    values = _unique_text(raw_values)
    missing = any(not value for value in raw_values)
    normalized = {value.casefold() for value in values}
    unknown = normalized - _REVERSE_CHARGE_YES - _REVERSE_CHARGE_NO
    has_yes = bool(normalized & _REVERSE_CHARGE_YES)
    has_no = bool(normalized & _REVERSE_CHARGE_NO)
    if unknown:
        return ReverseChargeStatus.UNKNOWN, values
    if (has_yes and has_no) or (missing and (has_yes or has_no)):
        return ReverseChargeStatus.CONFLICTING, values
    if has_yes:
        return ReverseChargeStatus.YES, values
    if has_no:
        return ReverseChargeStatus.NO, values
    return ReverseChargeStatus.MISSING, values


def _source_identity(rows: Sequence[ParsedInvoiceRecord]) -> tuple[TallyExportSourceIdentity, ...]:
    return tuple(
        TallyExportSourceIdentity(
            source=row.source,
            source_sheet=row.source_sheet,
            source_row=row.source_row,
            sheet_category=row.sheet_category,
        )
        for row in rows
    )


def _rate_allocations(rows: Sequence[ParsedInvoiceRecord]) -> tuple[TallyRateAllocation, ...]:
    totals: dict[Decimal, list[Decimal]] = {}
    for row in rows:
        if row.gst_rate is None:
            continue
        values = totals.setdefault(row.gst_rate, [Decimal("0")] * 5)
        values[0] += row.taxable_value
        values[1] += row.igst
        values[2] += row.cgst
        values[3] += row.sgst
        values[4] += row.cess
    return tuple(
        TallyRateAllocation(rate, *totals[rate])
        for rate in sorted(totals)
    )


def _candidate(
    *,
    transaction_type: TallyExportTransactionType,
    gstin: str,
    supplier_name: str,
    document_number: str,
    document_date,
    original_invoice_number: str,
    taxable_value,
    igst,
    cgst,
    sgst,
    cess,
    cess_present: bool,
    invoice_total,
    rows: Sequence[ParsedInvoiceRecord],
    financial_parse_issues,
) -> TallyExportCandidate:
    gst_rates = tuple(sorted({row.gst_rate for row in rows if row.gst_rate is not None}))
    place_values = _unique_text(row.place_of_supply for row in rows)
    place_missing = any(not str(row.place_of_supply or "").strip() for row in rows)
    reverse_status, reverse_values = _reverse_charge_status(rows)
    cancelled = []
    for row in rows:
        evidence = cancellation_evidence(row)
        if evidence:
            cancelled.append(f"{evidence[0]}: {evidence[1]}")
    amendment_rows = [
        row for row in rows
        if row.sheet_category in (SheetCategory.B2BA, SheetCategory.CDNRA)
        or row.amendment_original_number
        or row.amendment_original_date is not None
    ]
    amendment_original_number = next(
        (row.amendment_original_number for row in amendment_rows if row.amendment_original_number), ""
    )
    amendment_original_date = next(
        (row.amendment_original_date for row in amendment_rows if row.amendment_original_date is not None), None
    )
    gstin_valid = bool(rows) and all(row.gstin_valid for row in rows)
    return TallyExportCandidate(
        transaction_type=transaction_type,
        supplier_gstin=gstin,
        supplier_gstin_valid=gstin_valid,
        supplier_name=supplier_name,
        document_number=document_number,
        document_date=document_date,
        original_invoice_number=original_invoice_number,
        taxable_value=taxable_value,
        igst=igst,
        cgst=cgst,
        sgst=sgst,
        cess=cess,
        cess_present=cess_present,
        invoice_total=invoice_total,
        gst_rates=gst_rates,
        rate_allocations=_rate_allocations(rows),
        gst_rate_missing=any(row.gst_rate is None for row in rows),
        place_of_supply=place_values[0] if len(place_values) == 1 and not place_missing else "",
        place_of_supply_values=place_values,
        place_of_supply_missing=place_missing,
        place_of_supply_conflicting=len(place_values) > 1,
        reverse_charge_status=reverse_status,
        reverse_charge_values=reverse_values,
        is_amendment=bool(amendment_rows),
        amendment_original_number=amendment_original_number,
        amendment_original_date=amendment_original_date,
        is_cancelled=bool(cancelled),
        cancellation_evidence=tuple(cancelled),
        source_rows=_source_identity(rows),
        financial_parse_issues=tuple(financial_parse_issues),
    )


def purchase_candidate(record: AggregatedGSTInvoice) -> TallyExportCandidate:
    """Convert one authoritative Not-in-Tally purchase aggregate."""
    return _candidate(
        transaction_type=TallyExportTransactionType.PURCHASE_INVOICE,
        gstin=record.gstin,
        supplier_name=record.supplier_name,
        document_number=record.invoice_number_original,
        document_date=record.invoice_date,
        original_invoice_number="",
        taxable_value=record.taxable_value,
        igst=record.igst,
        cgst=record.cgst,
        sgst=record.sgst,
        cess=record.cess,
        cess_present=record.cess_present,
        invoice_total=record.total_invoice_value if record.total_invoice_value_present else None,
        rows=record.source_rows,
        financial_parse_issues=record.financial_parse_issues,
    )


def supplier_credit_note_candidate(record: AggregatedGSTCreditNote) -> TallyExportCandidate:
    """Convert one unmatched supplier credit-note aggregate."""
    return _candidate(
        transaction_type=TallyExportTransactionType.SUPPLIER_CREDIT_NOTE,
        gstin=record.gstin,
        supplier_name=record.supplier_name,
        document_number=record.note_number_original,
        document_date=record.note_date,
        original_invoice_number=record.original_invoice_number,
        taxable_value=record.taxable_value,
        igst=record.igst,
        cgst=record.cgst,
        sgst=record.sgst,
        cess=record.cess,
        cess_present=record.cess_present,
        invoice_total=record.total_invoice_value if record.total_invoice_value_present else None,
        rows=record.source_rows,
        financial_parse_issues=record.financial_parse_issues,
    )
