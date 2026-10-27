"""
Sheet Detector for GST Reconciliation Tool.

Classifies Excel sheets by examining both their name and their header
structure. Does not rely exclusively on sheet names — header content
takes precedence for category assignment.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.models.records import (
    ColumnMapping,
    SheetCategory,
    SheetDetectionResult,
)
from backend.app.services.header_detector import (
    KNOWN_COLUMN_ALIASES,
    detect_header_row,
    score_row_as_header,
)
from backend.app.services.column_detector import detect_columns


def _propagate_parent_headers(row: List[Any]) -> List[str]:
    propagated: List[str] = []
    current = ""
    for value in row:
        text = str(value).strip() if value not in (None, "") else ""
        if text:
            current = text
        propagated.append(current)
    return propagated


def _flatten_header_rows(header_rows: List[List[Any]]) -> List[str]:
    width = max((len(row) for row in header_rows), default=0)
    padded = [list(row) + [None] * (width - len(row)) for row in header_rows]
    levels = [
        _propagate_parent_headers(row) if index < len(padded) - 1
        else [str(value).strip() if value not in (None, "") else "" for value in row]
        for index, row in enumerate(padded)
    ]
    leaves: List[str] = []
    paths: List[List[str]] = []
    for col in range(width):
        path: List[str] = []
        for level in levels:
            text = level[col]
            if text and (not path or text != path[-1]):
                path.append(text)
        paths.append(path)
        leaves.append(path[-1] if path else "")
    duplicates = {leaf for leaf in leaves if leaf and leaves.count(leaf) > 1}
    return [" | ".join(paths[i]) if leaves[i] in duplicates else leaves[i] for i in range(width)]


def _multirow_gst_header(sheet_name: str, rows: List[List[Any]], max_scan_depth: int):
    name = sheet_name.strip().lower()
    if name not in {"b2b", "b2ba", "cdnr", "cdnra", "notes", "note", "b2b-cdnr", "b2b-cdnra"}:
        return None
    limit = min(len(rows) - 1, max_scan_depth)
    best = None
    for idx in range(limit):
        left_score, _ = score_row_as_header(rows[idx])
        right_score, _ = score_row_as_header(rows[idx + 1])
        if left_score and right_score:
            score = left_score + right_score
            if best is None or score > best[0]:
                best = (score, idx, idx + 1)
    if best is None:
        return None
    _, start, end = best
    if name in {"b2ba", "b2b-cdnra"} and start > 0:
        previous = " ".join(str(value or "") for value in rows[start - 1]).lower()
        if "original details" in previous or "revised details" in previous:
            start -= 1
    return start, end, _flatten_header_rows(rows[start:end + 1])


def _blank_header_label(index: int) -> str:
    number = index + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"Column {letters} (blank header)"


def _apply_daybook_blank_roles(
    result: SheetDetectionResult,
    rows: List[List[Any]],
    data_start_idx: int,
) -> None:
    normalized = {header.strip().lower() for header in result.headers}
    signature = {"date", "supplier invoice no.", "gstin/uin", "value", "cgst", "sgst", "i-gst"}
    if result.sheet_name.strip().lower() != "purchase register" or not signature.issubset(normalized):
        return
    blank_indices = [
        index for index, header in enumerate(result.headers)
        if header.startswith("Column ") and header.endswith("(blank header)")
    ]
    samples = {
        index: [
            str(row[index]).strip() for row in rows[data_start_idx:]
            if index < len(row) and row[index] not in (None, "")
        ]
        for index in blank_indices
    }
    voucher_values = {"purchase", "sales", "debit note", "credit note"}
    voucher_candidates = [
        index for index, values in samples.items()
        if values and {value.lower() for value in values}.issubset(voucher_values)
    ]
    supplier_candidates = [
        index for index, values in samples.items()
        if len(set(values)) > 1
        and not {value.lower() for value in values}.issubset(voucher_values)
        and all(not value.replace(".", "", 1).isdigit() for value in values[:30])
    ]
    existing = {mapping.semantic_name for mapping in result.column_mappings}
    if len(supplier_candidates) == 1 and "supplier_name" not in existing:
        result.column_mappings.append(ColumnMapping("supplier_name", result.headers[supplier_candidates[0]], 1.0))
    if len(voucher_candidates) == 1 and "voucher_type" not in existing:
        result.column_mappings.append(ColumnMapping("voucher_type", result.headers[voucher_candidates[0]], 1.0))


def _apply_b2ba_revised_roles(result: SheetDetectionResult) -> None:
    if result.sheet_name.strip().lower() != "b2ba":
        return
    targets = {
        "invoice_number": "invoice number",
        "invoice_date": "invoice date",
        "taxable_value": "taxable value (₹)",
        "total_invoice_value": "invoice value(₹)",
    }
    existing = {mapping.semantic_name: mapping for mapping in result.column_mappings}
    for semantic, leaf in targets.items():
        matches = [
            header for header in result.headers
            if "revised details" in header.lower() and header.lower().endswith(leaf)
        ]
        if len(matches) != 1:
            continue
        if semantic in existing:
            existing[semantic].detected_header = matches[0]
            existing[semantic].confidence = 1.0
        else:
            mapping = ColumnMapping(semantic, matches[0], 1.0)
            result.column_mappings.append(mapping)
            existing[semantic] = mapping
    tax_targets = {
        "igst": "integrated tax(₹)",
        "cgst": "central tax(₹)",
        "sgst": "state/ut tax(₹)",
    }
    for semantic, leaf in tax_targets.items():
        matches = [
            header for header in result.headers
            if "revised details | tax amount |" in header.lower()
            and header.lower().endswith(leaf)
        ]
        if len(matches) == 1:
            mapping = existing.get(semantic)
            if mapping:
                mapping.detected_header = matches[0]
                mapping.confidence = 1.0
            else:
                result.column_mappings.append(ColumnMapping(semantic, matches[0], 1.0))


# ---------------------------------------------------------------------------
# Sheet name hints → category
# ---------------------------------------------------------------------------

_SHEET_NAME_HINTS: Dict[str, SheetCategory] = {
    "purchase register": SheetCategory.PURCHASE_REGISTER,
    "purchase": SheetCategory.PURCHASE_REGISTER,
    "purchases": SheetCategory.PURCHASE_REGISTER,
    "daybook": SheetCategory.PURCHASE_REGISTER,
    "day book": SheetCategory.PURCHASE_REGISTER,
    "debit note register": SheetCategory.DEBIT_NOTE_REGISTER,
    "debit note": SheetCategory.DEBIT_NOTE_REGISTER,
    "debit notes": SheetCategory.DEBIT_NOTE_REGISTER,
    "b2b": SheetCategory.B2B,
    "b2ba": SheetCategory.B2BA,
    "cdnr": SheetCategory.CDNR,
    "notes": SheetCategory.CDNR,
    "note": SheetCategory.CDNR,
    "cdnra": SheetCategory.CDNRA,
    "party": SheetCategory.PARTY,
}


# ---------------------------------------------------------------------------
# Header-based category detection
# ---------------------------------------------------------------------------

# Signature column sets that distinguish sheet types.
# If a sheet's headers contain a certain combination, we can classify it.

_CATEGORY_SIGNATURES: List[Tuple[SheetCategory, List[str], int]] = [
    # (category, required_semantic_fields, min_required_count)
    #
    # CDNR: must have note_number or note_type AND gstin
    (SheetCategory.CDNR, ["note_number", "note_type", "gstin", "taxable_value"], 3),
    (SheetCategory.CDNRA, ["note_number", "note_type", "gstin"], 2),
    #
    # Debit Note Register: voucher_type present, and "debit note" columns
    (SheetCategory.DEBIT_NOTE_REGISTER, ["voucher_type", "gstin", "invoice_number"], 3),
    #
    # B2B: gstin + invoice_number + taxable_value + (no note fields)
    (SheetCategory.B2B, ["gstin", "invoice_number", "invoice_date", "taxable_value"], 3),
    (SheetCategory.B2BA, ["gstin", "invoice_number", "invoice_date", "taxable_value"], 3),
    #
    # Purchase Register: voucher_type + gstin + taxable_value
    (SheetCategory.PURCHASE_REGISTER, ["supplier_name", "gstin", "invoice_number", "taxable_value"], 3),
    #
    # Party
    (SheetCategory.PARTY, ["gstin", "supplier_name"], 2),
]


def _classify_by_headers(
    matched_semantics: List[str],
    sheet_name: str,
) -> SheetCategory:
    """
    Classify a sheet by the semantic fields found in its headers.
    
    Also uses the sheet name as a tie-breaker.
    """
    matched_set = set(matched_semantics)
    name_lower = sheet_name.strip().lower()

    # Check name-based hint first for GST sheets that have clear names
    if name_lower in _SHEET_NAME_HINTS:
        hint = _SHEET_NAME_HINTS[name_lower]
        # Verify the headers are at least somewhat consistent
        # (don't override if headers say otherwise)
        if hint == SheetCategory.CDNR and "note_number" in matched_set:
            return SheetCategory.CDNR
        if hint == SheetCategory.CDNRA and "note_number" in matched_set:
            return SheetCategory.CDNRA
        if hint == SheetCategory.B2B and "invoice_number" in matched_set:
            return SheetCategory.B2B
        if hint == SheetCategory.B2BA and "invoice_number" in matched_set:
            return SheetCategory.B2BA
        if hint == SheetCategory.PARTY:
            return SheetCategory.PARTY
        if hint in (SheetCategory.PURCHASE_REGISTER, SheetCategory.DEBIT_NOTE_REGISTER):
            return hint

    # Some Tally exports use a generic sheet name and label the supplier
    # column "Account".  Require the complete purchase identity/value shape
    # before the broader voucher-type debit-note signature is considered.
    purchase_fields = {
        "supplier_name", "gstin", "invoice_number", "taxable_value",
    }
    if purchase_fields.issubset(matched_set) and "voucher_type" in matched_set:
        return SheetCategory.PURCHASE_REGISTER

    # Fall back to signature matching
    for category, required_fields, min_count in _CATEGORY_SIGNATURES:
        hits = sum(1 for f in required_fields if f in matched_set)
        if hits >= min_count:
            # Extra checks to distinguish B2B from Purchase Register
            if category == SheetCategory.B2B:
                # B2B sheets typically don't have voucher_type
                if "voucher_type" in matched_set:
                    continue
            if category == SheetCategory.PURCHASE_REGISTER:
                # Purchase Register should have voucher_type or be named accordingly
                if "voucher_type" not in matched_set and name_lower not in _SHEET_NAME_HINTS:
                    continue
            return category

    return SheetCategory.UNKNOWN


def detect_sheet(
    sheet_name: str,
    rows: List[List[Any]],
    max_scan_depth: int = 30,
) -> SheetDetectionResult:
    """
    Detect the header row, classify the sheet, and map columns.
    
    Args:
        sheet_name: Name of the Excel sheet.
        rows: All rows from the sheet (each row is a list of cell values).
        max_scan_depth: Maximum rows to scan for the header.
    
    Returns:
        SheetDetectionResult with category, header info, and column mappings.
    """
    result = SheetDetectionResult(sheet_name=sheet_name)

    if not rows:
        return result

    # Step 1: Detect the header row
    best_idx, best_values, confidence, candidates = detect_header_row(
        rows, max_scan_depth
    )

    multirow = _multirow_gst_header(sheet_name, rows, max_scan_depth)
    header_end_idx = best_idx
    if multirow:
        best_idx, header_end_idx, best_values = multirow
        confidence = len(detect_columns(best_values)) / max(1, len(KNOWN_COLUMN_ALIASES))

    result.header_row = best_idx + 1  # Convert to 1-indexed
    result.data_start_row = header_end_idx + 2  # Data starts after all header rows
    raw_headers = [str(v).strip() if v else "" for v in best_values]
    result.headers = [
        header if header else (
            _blank_header_label(index)
            if any(
                index < len(row) and row[index] not in (None, "")
                for row in rows[header_end_idx + 1:]
            ) else ""
        )
        for index, header in enumerate(raw_headers)
    ]
    result.confidence = confidence
    result.candidate_header_rows = [c + 1 for c in candidates]  # 1-indexed

    # Step 2: Detect column mappings
    result.column_mappings = detect_columns(result.headers)
    _apply_daybook_blank_roles(result, rows, header_end_idx + 1)
    _apply_b2ba_revised_roles(result)

    # Step 3: Count data rows (non-empty rows after header)
    data_count = 0
    for i in range(header_end_idx + 1, len(rows)):
        row = rows[i]
        non_empty = sum(1 for v in row if v is not None and str(v).strip())
        if non_empty > 0:
            data_count += 1
    result.total_data_rows = data_count

    # Step 4: Classify the sheet
    matched_semantics = [m.semantic_name for m in result.column_mappings]
    result.category = _classify_by_headers(matched_semantics, sheet_name)

    # Special case: Debit Note Register with no data rows
    # and Purchase Register with the same header structure
    # Distinguish by sheet name if both have voucher_type
    name_lower = sheet_name.strip().lower()
    if name_lower == "debit note register":
        result.category = SheetCategory.DEBIT_NOTE_REGISTER
    elif name_lower == "purchase register":
        result.category = SheetCategory.PURCHASE_REGISTER

    return result
