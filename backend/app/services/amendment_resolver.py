"""Deterministic B2BA/CDNRA supersession before GST aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from backend.app.models.records import ParsedInvoiceRecord, SheetCategory
from backend.app.services.normalizer import normalize_invoice_number


@dataclass(frozen=True)
class AmendmentResolutionStats:
    raw_regular_rows: int
    raw_amendment_rows: int
    superseded_regular_documents: int
    authoritative_rows: int


def _current_key(record: ParsedInvoiceRecord, *, notes: bool) -> Tuple:
    number = record.note_number if notes else record.invoice_number.original
    when = record.note_date if notes else record.invoice_date
    return (record.gstin_normalized, normalize_invoice_number(number).normalized, when)


def _lineage_key(record: ParsedInvoiceRecord) -> Optional[Tuple]:
    number = normalize_invoice_number(record.amendment_original_number).normalized
    if not record.gstin_normalized or not number or record.amendment_original_date is None:
        return None
    return (record.gstin_normalized, number, record.amendment_original_date)


def resolve_amendments(
    records: List[ParsedInvoiceRecord],
    regular_category: SheetCategory,
    amendment_category: SheetCategory,
) -> Tuple[List[ParsedInvoiceRecord], AmendmentResolutionStats]:
    """Return the authoritative rows for one regular/amendment sheet pair.

    A lineage may have many tax-rate rows, but all rows must name the same
    revised document identity.  Source row order is not revision evidence, so
    conflicting revised identities are rejected instead of choosing a winner.
    Complete orphan amendments remain authoritative.
    """
    notes = amendment_category == SheetCategory.CDNRA
    regular = [r for r in records if r.sheet_category == regular_category]
    amended = [r for r in records if r.sheet_category == amendment_category]

    by_lineage: Dict[Tuple, List[ParsedInvoiceRecord]] = {}
    for row in amended:
        lineage = _lineage_key(row)
        if lineage is None:
            raise ValueError(
                f"{amendment_category.value.upper()} row {row.source_row} lacks a complete "
                "GSTIN/original document number/original document date linkage key."
            )
        by_lineage.setdefault(lineage, []).append(row)

    for lineage, rows in by_lineage.items():
        revised = {_current_key(row, notes=notes) for row in rows}
        if any(not key[1] or key[2] is None for key in revised):
            raise ValueError(f"Incomplete revised document identity for amendment lineage {lineage}.")
        if len(revised) != 1:
            raise ValueError(
                f"Multiple revised document identities for amendment lineage {lineage}; "
                "the workbook has no deterministic latest-version evidence."
            )

    superseded_keys = set(by_lineage)
    retained_regular = [r for r in regular if _current_key(r, notes=notes) not in superseded_keys]
    superseded_documents = len({_current_key(r, notes=notes) for r in regular} & superseded_keys)
    authoritative = retained_regular + amended
    return authoritative, AmendmentResolutionStats(
        raw_regular_rows=len(regular),
        raw_amendment_rows=len(amended),
        superseded_regular_documents=superseded_documents,
        authoritative_rows=len(authoritative),
    )
