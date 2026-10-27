"""
Column Detector for GST Reconciliation Tool.

Once a header row has been detected, this module maps each semantic field
to the actual column header found in the sheet.

It produces ColumnMapping objects with confidence scores.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend.app.models.records import ColumnMapping
from backend.app.services.header_detector import KNOWN_COLUMN_ALIASES, _normalize_header_text


def detect_columns(
    headers: List[Any],
) -> List[ColumnMapping]:
    """
    Map detected header values to semantic field names.
    
    Args:
        headers: List of cell values from the detected header row.
    
    Returns:
        List of ColumnMapping objects for every semantic field that
        was successfully matched.
    """
    mappings: List[ColumnMapping] = []
    used_indices: set = set()

    # Build normalized header list
    normalized_headers = [_normalize_header_text(h) for h in headers]

    for semantic_name, aliases in KNOWN_COLUMN_ALIASES.items():
        best_match: Optional[ColumnMapping] = None
        best_confidence = 0.0
        best_col_idx = -1
        is_ambiguous = False

        for col_idx, norm_header in enumerate(normalized_headers):
            if col_idx in used_indices:
                continue
            if not norm_header:
                continue

            col_best_confidence = 0.0

            for alias in aliases:
                alias_norm = _normalize_header_text(alias)

                # Exact match → highest confidence
                if norm_header == alias_norm:
                    confidence = 1.0
                # Contains match → medium confidence
                elif semantic_name == "supplier_name" and any(
                    token in norm_header for token in ("invoice", "voucher", "bill", "document")
                ):
                    continue
                elif alias_norm == "type":
                    continue
                elif alias_norm in norm_header or norm_header in alias_norm:
                    confidence = 0.7
                else:
                    continue

                if confidence > col_best_confidence:
                    col_best_confidence = confidence

            if col_best_confidence > 0:
                if col_best_confidence > best_confidence:
                    best_confidence = col_best_confidence
                    best_col_idx = col_idx
                    best_match = ColumnMapping(
                        semantic_name=semantic_name,
                        detected_header=str(headers[col_idx]).strip() if headers[col_idx] else "",
                        confidence=col_best_confidence,
                    )
                    is_ambiguous = False
                elif col_best_confidence == best_confidence:
                    is_ambiguous = True

        if is_ambiguous:
            # Genuinely ambiguous columns detected; leave it unmapped
            continue

        if best_match is not None and best_col_idx >= 0:
            mappings.append(best_match)
            used_indices.add(best_col_idx)

    return mappings


def get_column_index(
    headers: List[Any],
    semantic_name: str,
    mappings: List[ColumnMapping],
) -> Optional[int]:
    """
    Get the column index for a given semantic field name.
    
    Returns the 0-based index into the headers list, or None if not found.
    """
    # Find the mapping
    target_mapping = None
    for m in mappings:
        if m.semantic_name == semantic_name:
            target_mapping = m
            break

    if target_mapping is None:
        return None

    # Find the header that matches
    target_header = target_mapping.detected_header
    for idx, h in enumerate(headers):
        header_text = str(h).strip() if h else ""
        if header_text == target_header:
            return idx

    return None


def build_column_index_map(
    headers: List[Any],
    mappings: List[ColumnMapping],
) -> Dict[str, int]:
    """
    Build a dictionary mapping semantic field names to column indices.
    
    Returns:
        {semantic_name: column_index, ...}
    """
    result: Dict[str, int] = {}
    for m in mappings:
        idx = get_column_index(headers, m.semantic_name, mappings)
        if idx is not None:
            result[m.semantic_name] = idx
    return result
