"""
Header Detector for GST Reconciliation Tool.

Automatically finds the header row in an Excel sheet by scoring each row
against known semantic column names from Tally and GST data formats.

The detector does NOT assume headers are always on row 1. It scans up to
a configurable depth and picks the row with the highest concentration of
recognized column names.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Known semantic column aliases
# ---------------------------------------------------------------------------

# Each key is a semantic field name.
# Each value is a list of known aliases (case-insensitive matching).
# The aliases cover Tally, GST portal, and common variations.

KNOWN_COLUMN_ALIASES: Dict[str, List[str]] = {
    # --- Common / shared ---
    "supplier_name": [
        "particulars", "party name", "party", "supplier name",
        "supplier", "vendor name", "vendor", "name", "account",
        "trade/legal name",
    ],
    "gstin": [
        "gstin/uin", "gstin", "gstin/uin of supplier", "gstin number",
        "gst no", "gst number", "gstin of supplier",
    ],
    "invoice_number": [
        "voucher no.", "voucher no", "voucher number", "invoice no.",
        "invoice no", "invoice number", "inv no", "inv no.",
        "bill no", "bill no.", "bill number", "document no",
        "document number", "supplier invoice no.", "supplier invoice no",
    ],
    "invoice_date": [
        "date", "invoice date", "inv date", "bill date",
        "voucher date", "document date",
    ],
    "taxable_value": [
        "value", "taxable value", "taxable amount", "taxable amt",
        "total taxable value", "net value", "assessable value",
        "taxable value (₹)", "taxable value(₹)",
    ],
    "igst": [
        "igst", "igst amount", "igst amt", "integrated tax",
        "integrated tax(₹)", "integrated tax (₹)",
    ],
    "cgst": [
        "cgst", "cgst amount", "cgst amt", "central tax",
        "central tax(₹)", "central tax (₹)",
    ],
    "sgst": [
        "sgst", "sgst amount", "sgst amt", "state tax", "sgst/utgst",
        "state/ut tax(₹)", "state/ut tax (₹)",
    ],
    "total_invoice_value": [
        "total invoice value", "total value",
        "invoice value", "total amount", "invoice value(₹)", "invoice value (₹)",
    ],
    "gross_total": [
        "gross total", "gross amount", "grand total", "voucher total",
        "bill total", "bill amount",
    ],

    # --- Tally-specific ---
    "voucher_type": [
        "voucher type", "vch type", "type",
    ],
    "additional_cost": [
        "addl. cost", "addl cost", "additional cost",
    ],
    "round_off": [
        "round off", "rounding off", "rounding",
    ],

    # --- GST-specific ---
    "gst_rate": [
        "rate", "tax rate", "gst rate",
    ],
    "place_of_supply": [
        "place of supply", "pos",
    ],
    "reverse_charge": [
        "reverse charge",
    ],
    "invoice_type": [
        "invoice type",
    ],

    # --- CDNR / Notes ---
    "note_type": [
        "note type", "document type",
    ],
    "note_number": [
        "note/refund number", "note number", "credit note no",
        "debit note no", "note no", "note/refund no",
    ],
    "note_date": [
        "note/refund date", "note date", "credit note date",
        "debit note date",
    ],
    "note_value": [
        "note/refund value", "note value",
    ],
    "original_invoice_number": [
        "invoice number", "original invoice number",
        "against invoice number",
    ],
    "original_invoice_date": [
        "invoice date", "original invoice date",
    ],
    "cess": [
        "cess amount", "cess",
    ],
    # --- Amendment identity / provenance ---
    "amendment_original_number": [
        "original invoice no.", "original invoice no", "original invoice number",
        "original note no", "original note no.", "original note number",
    ],
    "amendment_original_date": [
        "original invoice date", "original note date",
    ],
    "revised_invoice_number": [
        "revised invoice no.", "revised invoice no", "revised invoice number",
    ],
    "revised_invoice_date": ["revised invoice date"],
    "revised_note_number": [
        "revised note no.", "revised note no", "revised note number",
    ],
    "revised_note_date": ["revised note date"],
    "amendment_date": ["amendment, if any", "amendment date"],
    "amendment_period": ["amendment period"],
}

# Build a reverse lookup: normalized alias → semantic name
_ALIAS_LOOKUP: Dict[str, str] = {}
for semantic, aliases in KNOWN_COLUMN_ALIASES.items():
    for alias in aliases:
        # We manually apply the same normalization we use for headers
        alias_lower = str(alias).strip().lower()
        alias_norm = re.sub(r"[\s\-_]+", "", alias_lower)
        _ALIAS_LOOKUP[alias_norm] = semantic


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _normalize_header_text(text: Any) -> str:
    """Normalize a cell value for alias matching by removing spaces, hyphens, and underscores."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    # Remove all spaces, hyphens, and underscores
    s = re.sub(r"[\s\-_]+", "", s)
    return s


def score_row_as_header(row_values: List[Any]) -> Tuple[int, List[str]]:
    """
    Score a row of cell values as a potential header row.
    
    Returns:
        (match_count, list_of_matched_semantic_names)
    
    A higher match_count means more recognized column names were found.
    """
    matched = []
    for cell in row_values:
        normalized = _normalize_header_text(cell)
        if normalized in _ALIAS_LOOKUP:
            matched.append(_ALIAS_LOOKUP[normalized])
    return (len(matched), matched)


def detect_header_row(
    rows: List[List[Any]],
    max_scan_depth: int = 30,
) -> Tuple[int, List[Any], float, List[int]]:
    """
    Detect the most likely header row from a list of row data.
    
    Args:
        rows: List of rows (each row is a list of cell values).
              rows[0] corresponds to Excel row 1.
        max_scan_depth: How many rows to scan from the top.
    
    Returns:
        (best_row_index_0based, best_row_values, confidence, candidate_row_indices)
        
    Confidence is (match_count / total_known_fields).
    candidate_row_indices includes all rows that scored > 0.
    """
    if not rows:
        return (0, [], 0.0, [])

    best_idx = 0
    best_score = 0
    best_values: List[Any] = []
    candidates: List[int] = []

    scan_limit = min(len(rows), max_scan_depth)

    for idx in range(scan_limit):
        row = rows[idx]
        score, matched = score_row_as_header(row)
        if score > 0:
            candidates.append(idx)
        if score > best_score:
            best_score = score
            best_idx = idx
            best_values = row

    # Confidence: fraction of the total semantic field count
    total_possible = len(KNOWN_COLUMN_ALIASES)
    confidence = best_score / total_possible if total_possible > 0 else 0.0

    return (best_idx, best_values, confidence, candidates)
