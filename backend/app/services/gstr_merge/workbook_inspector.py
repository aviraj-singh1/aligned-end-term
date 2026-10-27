import datetime
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from backend.app.services.gstr_merge.gst_schema import (
    CategorySpec, category_candidates, header_mapping, header_mapping_from_existing, normalize_label,
)


GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
METADATA_SHEETS = {"main", "summary", "profile", "taxpayer details", "annual summary"}
APPROVED_STATIC_SHEETS = {"party", "sheet1", "read me"}
# These are period-level GSTR-2B summaries, not row-level ledgers. Adding four
# quarterly summaries would produce invalid annual figures, so detail sheets
# are merged while these derived views are deliberately omitted.
IGNORED_GSTR2B_SUMMARY_SHEETS = {
    "itc available", "itc not available", "itc reversal", "itc rejected",
}
METADATA_LABELS = {
    "gstin", "gstin of the tax payer", "gstin of taxpayer", "taxpayer gstin",
    "financial year", "fy", "month", "months", "tax period", "return period", "period", "quarter",
    "legal name", "trade name", "trade name if any", "trade name (if any)", "date of generation",
}
DANGEROUS_FORMULA_FUNCTIONS = ("WEBSERVICE(", "HYPERLINK(", "RTD(", "DDE(", "FILTERXML(", "IMAGE(")


class WorkbookInspector:
    def __init__(self, temp_paths: Dict[str, str], filenames: Optional[Dict[str, str]] = None,
                 max_rows: Optional[int] = None, max_columns: Optional[int] = None):
        self.temp_paths = temp_paths
        self.filenames = filenames or {key: os.path.basename(path) for key, path in temp_paths.items()}
        self.max_rows = max_rows or int(os.getenv("GSTR_MAX_WORKSHEET_ROWS", "500000"))
        self.max_columns = max_columns or int(os.getenv("GSTR_MAX_WORKSHEET_COLUMNS", "512"))

    @staticmethod
    def _meaningful(value) -> bool:
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    @staticmethod
    def _hash_row(row):
        def token(value):
            if value is None:
                return ["none", None]
            if isinstance(value, bool):
                return ["bool", value]
            if isinstance(value, datetime.datetime):
                return ["datetime", value.isoformat()]
            if isinstance(value, datetime.date):
                return ["date", value.isoformat()]
            if isinstance(value, int):
                return ["int", str(value)]
            if isinstance(value, float):
                return ["float", repr(value)]
            if isinstance(value, str):
                return ["str", value]
            return [type(value).__name__, repr(value)]

        encoded = json.dumps([token(value) for value in row], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_mapping(row, spec: Optional[CategorySpec] = None) -> Tuple[Dict[str, int], List[str]]:
        return header_mapping(row, spec)

    @classmethod
    def _looks_like_business_row(cls, row) -> bool:
        return any(
            isinstance(value, (int, float, datetime.date)) and not isinstance(value, bool)
            or isinstance(value, str) and GSTIN_RE.fullmatch(value.strip().upper())
            for value in row if cls._meaningful(value)
        )

    @staticmethod
    def _header_group(value) -> Optional[str]:
        label = normalize_label(value)
        if not label:
            return None
        if "amount declared by taxpayer" in label and "itc reduction" in label:
            return "itc_reduction"
        if "bill of entry" in label and "detail" in label:
            return "bill_of_entry"
        if "original" in label and "detail" in label:
            return "original"
        if ("revised" in label or "amended" in label) and "detail" in label:
            return "revised"
        if "credit note" in label or "debit note" in label:
            return "note"
        if "invoice" in label and "detail" in label:
            return "invoice"
        if "document" in label and "detail" in label:
            return "document"
        return None

    @classmethod
    def _layered_row_mapping(cls, rows, spec: CategorySpec) -> Tuple[Dict[str, int], List[str]]:
        width = max((len(row) for row in rows), default=0)
        carried_groups = []
        for row in rows:
            current = None
            groups = []
            for column in range(width):
                value = row[column] if column < len(row) else None
                group = cls._header_group(value)
                if group:
                    current = group
                elif cls._meaningful(value):
                    current = None
                groups.append(current)
            carried_groups.append(groups)

        keys_by_column = {}
        for column in range(width):
            values = [row[column] if column < len(row) else None for row in rows]
            contexts = {
                groups[column] for groups in carried_groups if groups[column]
            }
            key = ""
            raw_label = ""
            for value in reversed(values):
                candidate = header_mapping((value,))[0]
                if candidate:
                    candidate_key = next(iter(candidate))
                    if not candidate_key.startswith("raw:"):
                        key = candidate_key
                        raw_label = normalize_label(value)
                        break
                    if not key:
                        key = candidate_key
                        raw_label = normalize_label(value)

            if not key:
                continue
            if "itc_reduction" in contexts and key in {"igst", "cgst", "sgst", "cess"}:
                key = f"itc_reduction_{key}"
            elif key.startswith("raw:") and raw_label in {"number", "no", "date"}:
                suffix = "number" if raw_label in {"number", "no"} else "date"
                if "bill_of_entry" in contexts:
                    key = f"bill_of_entry_{suffix}"
                elif "note" in contexts:
                    key = f"note_{suffix}"
                elif "invoice" in contexts:
                    key = f"invoice_{suffix}"
                elif "document" in contexts:
                    key = f"document_{suffix}"

            if "original" in contexts:
                replacements = {
                    "invoice_number": "original_invoice_number", "invoice_date": "original_invoice_date",
                    "note_number": "original_note_number", "note_date": "original_note_date",
                    "document_number": "original_document_number", "document_date": "original_document_date",
                }
                key = replacements.get(key, key)
                if key.startswith("raw:"):
                    key = f"raw:original:{key[4:]}"
            elif "revised" in contexts:
                replacements = {
                    "invoice_number": "revised_invoice_number", "invoice_date": "revised_invoice_date",
                    "note_number": "revised_note_number", "note_date": "revised_note_date",
                    "document_number": "revised_document_number", "document_date": "revised_document_date",
                }
                key = replacements.get(key, key)
                if key.startswith("raw:"):
                    key = f"raw:revised:{key[4:]}"
            keys_by_column[column] = key

        mapping: Dict[str, int] = {}
        duplicates: List[str] = []
        for column, key in keys_by_column.items():
            if key in mapping:
                duplicates.append(key)
            else:
                mapping[key] = column
        return header_mapping_from_existing(mapping, duplicates, spec)

    @staticmethod
    def _sheet_signature(ws, mask_metadata: bool = False) -> Tuple[str, bool, int, List[Tuple[str, str]]]:
        def token(value):
            if isinstance(value, (datetime.date, datetime.datetime)):
                return [type(value).__name__, value.isoformat()]
            return [type(value).__name__, repr(value)]

        digest = hashlib.sha256()
        populated_rows = 0
        table_like_row = False
        meaningful_cells = 0
        formulas = []
        for row_number, row in enumerate(ws.iter_rows(), 1):
            values = [cell.value for cell in row]
            label_columns = set()
            value_columns = set()
            if mask_metadata:
                for index, value in enumerate(values):
                    if normalize_label(value) in METADATA_LABELS:
                        label_columns.add(index)
                        value_columns.add(index + 1)
            row_count = 0
            for index, cell in enumerate(row):
                value = cell.value
                if cell.data_type == "f":
                    formulas.append((cell.coordinate, str(value)))
                if index in label_columns:
                    value = "<METADATA LABEL>"
                elif index in value_columns:
                    value = "<METADATA VALUE>"
                elif not WorkbookInspector._meaningful(value):
                    continue
                digest.update(json.dumps(
                    [row_number, index + 1, token(value)], separators=(",", ":")
                ).encode("utf-8"))
                meaningful_cells += 1
                row_count += 1
            if row_count:
                populated_rows += 1
                table_like_row = table_like_row or row_count >= 2
        return digest.hexdigest(), populated_rows >= 2 and table_like_row, meaningful_cells, formulas

    @staticmethod
    def _unsafe_formula_reason(formula: str, sheet_names: List[str], renamed_sheets=None) -> Optional[str]:
        upper = formula.upper().replace(" ", "")
        if any(marker in formula for marker in ("[", "]", "://", "\\\\")):
            return "external workbook or network reference"
        if any(function in upper for function in DANGEROUS_FORMULA_FUNCTIONS):
            return "external-data formula"
        for quoted, plain in re.findall(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_ ]*))!", formula):
            referenced = quoted or plain.strip()
            if referenced not in sheet_names:
                return f"reference to missing sheet '{referenced}'"
            if renamed_sheets and referenced in renamed_sheets and renamed_sheets[referenced] != referenced:
                return f"reference to category sheet '{referenced}' that is renamed in the output"
        return None

    @classmethod
    def _detect_header(cls, ws, spec: CategorySpec) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        candidates = []
        rows = list(ws.iter_rows(min_row=1, max_row=min(25, ws.max_row or 25), values_only=True))
        for row_index, row in enumerate(rows, 1):
            mapping, duplicates = cls._row_mapping(row, spec)
            known = {key for key in mapping if not key.startswith("raw:")}
            if set(spec.required).issubset(mapping) and len(known) >= spec.minimum_known:
                candidates.append((len(known), len(mapping), -row_index, row_index, mapping, duplicates, list(row), 1))

        # Official GSTR-2B exports use stacked two/three-row headers.  Try this
        # only when no complete single row exists, keeping the legacy path and
        # its ambiguity behavior unchanged.
        if not candidates:
            for end in range(2, len(rows) + 1):
                for depth in (2, 3, 4):
                    if depth > end:
                        continue
                    window = rows[end - depth:end]
                    if cls._looks_like_business_row(window[-1]):
                        continue
                    mapping, duplicates = cls._layered_row_mapping(window, spec)
                    known = {key for key in mapping if not key.startswith("raw:")}
                    if set(spec.required).issubset(mapping) and len(known) >= spec.minimum_known:
                        candidates.append((len(known), len(mapping), -end, end, mapping, duplicates,
                                           list(window[-1]), depth))
                # The first complete stacked header is the table header. Do not
                # slide the same window into business rows, where legitimate
                # values can otherwise mutate unmapped labels and look like a
                # second conflicting header.
                if candidates:
                    break

        # A candidate can already contain every required identity field while
        # a following row still expands a merged tax group. Extend from the
        # candidate's original first row, but never through a row carrying a
        # typed amount/date or GSTIN business value.
        if candidates:
            expanded = []
            for candidate in candidates:
                start = candidate[3] - candidate[7]
                for depth in range(candidate[7] + 1, 5):
                    end = start + depth
                    if end > len(rows) or cls._looks_like_business_row(rows[end - 1]):
                        continue
                    window = rows[start:end]
                    mapping, duplicates = cls._layered_row_mapping(window, spec)
                    known = {key for key in mapping if not key.startswith("raw:")}
                    if (set(spec.required).issubset(mapping)
                            and len(known) > candidate[0]
                            and len(known) >= spec.minimum_known):
                        expanded.append((len(known), len(mapping), -end, end, mapping, duplicates,
                                         list(window[-1]), depth))
            candidates.extend(expanded)

        # The GST portal's empty B2B-DNRA template labels N:Q as "Taxable
        # Value" while the next row contains tax-amount subcolumns, so no
        # standalone taxable-value column exists. Recognize that exact empty
        # template without relaxing validation for any financial data row.
        if not candidates and spec.key == "gstr2b_b2b_dnra":
            for end in range(2, len(rows) + 1):
                if any(cls._meaningful(value) for row in rows[end:] for value in row):
                    continue
                for depth in (2, 3, 4):
                    if depth > end:
                        continue
                    window = rows[end - depth:end]
                    if cls._looks_like_business_row(window[-1]):
                        continue
                    mapping, duplicates = cls._layered_row_mapping(window, spec)
                    known = {key for key in mapping if not key.startswith("raw:")}
                    missing = set(spec.required) - set(mapping)
                    if missing == {"taxable_value"} and len(known) >= spec.minimum_known:
                        candidates.append((len(known), len(mapping), -end, end, mapping, duplicates,
                                           list(window[-1]), depth))
                if candidates:
                    break

        if not candidates:
            required = ", ".join(spec.required) or f"at least {spec.minimum_known} recognized fields"
            return None, f"could not find an unambiguous header containing {required} in the first 25 rows"
        candidates.sort(reverse=True, key=lambda item: item[:3])
        best = candidates[0]
        equals = [item for item in candidates if item[:3] == best[:3]]
        if len({tuple(sorted(item[4].items())) for item in equals}) > 1:
            return None, f"ambiguous header candidates at rows {', '.join(str(item[3]) for item in equals)}"
        if best[5]:
            return None, f"duplicate/ambiguous header fields: {', '.join(sorted(set(best[5])))}"
        canonical_by_column = [""] * len(best[6])
        for key, index in best[4].items():
            canonical_by_column[index] = key
        header_start = best[3] - best[7]
        return {"header_row_idx": best[3], "column_map": best[4], "headers": best[6],
                "canonical_headers": list(best[4]),
                "canonical_headers_by_column": canonical_by_column,
                "header_depth": best[7],
                "header_rows": [list(row) for row in rows[header_start:best[3]]]}, None

    @staticmethod
    def _metadata_pairs(wb, include_gstr2b_read_me: bool = False) -> Dict[str, Any]:
        pairs: Dict[str, Any] = {}
        worksheets = sorted(
            wb.worksheets,
            key=lambda sheet: 0 if include_gstr2b_read_me and normalize_label(sheet.title) == "read me" else 1,
        )
        for ws in worksheets:
            title = normalize_label(ws.title)
            if title not in METADATA_SHEETS and not (include_gstr2b_read_me and title == "read me"):
                continue
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 30, 30), values_only=True):
                for index, value in enumerate(row):
                    label = normalize_label(value)
                    if label not in METADATA_LABELS:
                        continue
                    # Official GSTR-2B Read me sheets merge the label across A:B
                    # and place its value in C. Use the first populated cell to
                    # the right rather than assuming an adjacent label/value pair.
                    right_value = next(
                        (candidate for candidate in row[index + 1:]
                         if WorkbookInspector._meaningful(candidate)),
                        None,
                    )
                    if WorkbookInspector._meaningful(right_value):
                        pairs.setdefault(label, right_value)
        return pairs

    @staticmethod
    def _extract_gstin(pairs: Dict[str, Any]) -> Optional[str]:
        for label, value in pairs.items():
            if label in {"gstin", "gstin of the tax payer", "gstin of taxpayer", "taxpayer gstin"}:
                candidate = str(value).strip().upper()
                if GSTIN_RE.fullmatch(candidate):
                    return candidate
        return None

    @staticmethod
    def _extract_fy(pairs: Dict[str, Any]) -> Optional[str]:
        for label, value in pairs.items():
            if label in {"financial year", "fy"}:
                match = re.search(r"(20\d{2})\s*[-/]\s*(?:20)?(\d{2})", str(value))
                if match:
                    return f"{match.group(1)}-{match.group(2)}"
        return None

    @staticmethod
    def _extract_period(pairs: Dict[str, Any], filename: str) -> Optional[Dict[str, str]]:
        text = ""
        for label, value in pairs.items():
            if label in {"month", "months", "tax period", "return period", "period", "quarter"}:
                text = normalize_label(value)
                break
        source = "workbook"
        if not text:
            text = normalize_label(os.path.splitext(filename)[0])
            if not re.search(r"(?:^| )q[1-4](?: |$)", text):
                return None
            source = "filename"
        quarter_match = re.search(r"(?:^| )(q[1-4])(?: |$)", text)
        if quarter_match:
            return {"kind": "quarter", "code": quarter_match.group(1).upper(), "source": source}
        found = [number for name, number in MONTHS.items() if re.search(rf"\b{name}[a-z]*\b", text)]
        found = list(dict.fromkeys(found))
        if len(found) == 1:
            return {"kind": "month", "code": f"M{found[0]:02d}", "source": source}
        ranges = {(4, 6): "Q1", (7, 9): "Q2", (10, 12): "Q3", (1, 3): "Q4"}
        if len(found) >= 2 and (found[0], found[-1]) in ranges:
            return {"kind": "quarter", "code": ranges[(found[0], found[-1])], "source": source}
        return None

    @staticmethod
    def _extract_metadata_value(pairs: Dict[str, Any], labels) -> Optional[str]:
        for label in labels:
            value = pairs.get(label)
            if WorkbookInspector._meaningful(value):
                return str(value).strip()
        return None

    def inspect_all(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        for source_id, path in self.temp_paths.items():
            item = {"source_id": source_id, "filename": self.filenames[source_id], "path": path,
                    "sheet_names": [], "sheets": {}, "categories": {}, "errors": []}
            try:
                wb = openpyxl.load_workbook(path, data_only=False, read_only=True, keep_links=False)
            except Exception as exc:
                item["errors"].append("cannot open workbook; it is corrupt, malformed, or unsupported")
                metadata[source_id] = item
                continue
            if getattr(wb, "_external_links", None):
                item["errors"].append("external workbook links are not supported")
            item["sheet_names"] = wb.sheetnames[:]
            is_gstr2b_workbook = any(
                normalize_label(title) in IGNORED_GSTR2B_SUMMARY_SHEETS
                for title in wb.sheetnames
            )
            pairs = self._metadata_pairs(wb, include_gstr2b_read_me=is_gstr2b_workbook)
            item["taxpayer_gstin"] = self._extract_gstin(pairs)
            item["financial_year"] = self._extract_fy(pairs)
            item["period"] = self._extract_period(pairs, item["filename"])
            item["legal_name"] = self._extract_metadata_value(pairs, ("legal name",))
            item["trade_name"] = self._extract_metadata_value(
                pairs, ("trade name", "trade name if any", "trade name (if any)")
            )
            item["generation_date"] = self._extract_metadata_value(pairs, ("date of generation",))
            item["is_gstr2b_workbook"] = is_gstr2b_workbook
            static_formulas = []
            for ws in wb.worksheets:
                normalized_title = normalize_label(ws.title)
                policy = (
                    "metadata" if normalized_title in METADATA_SHEETS else
                    "first_source_static" if normalized_title == "read me" and is_gstr2b_workbook else
                    "approved_static" if normalized_title in APPROVED_STATIC_SHEETS else
                    "ignored_summary" if normalized_title in IGNORED_GSTR2B_SUMMARY_SHEETS else
                    "unknown"
                )
                info = {"sheet_name": ws.title, "category": None, "sheet_type": "static",
                        "is_transaction": False, "max_row": ws.max_row or 0, "max_column": ws.max_column or 0,
                        "static_policy": policy, "content_signature": "",
                        "is_table_like": False, "meaningful_cells": 0}
                if (ws.max_row or 0) > self.max_rows or (ws.max_column or 0) > self.max_columns:
                    item["errors"].append(
                        f"sheet '{ws.title}' exceeds safe dimensions ({ws.max_row} rows × {ws.max_column} columns)"
                    )
                specs = category_candidates(ws.title)
                if specs:
                    detections, reasons = [], []
                    for spec in specs:
                        header, reason = self._detect_header(ws, spec)
                        (detections if header else reasons).append((spec, header) if header else reason)
                    if len(detections) != 1:
                        reason = "schema matches multiple categories" if detections else "; ".join(reasons)
                        item["errors"].append(f"sheet '{ws.title}': {reason}")
                    else:
                        spec, header = detections[0]
                        info.update(header)
                        info.update({"category": spec.key, "sheet_type": "business", "is_transaction": True})
                        if spec.key in item["categories"]:
                            item["errors"].append(
                                f"sheets '{item['categories'][spec.key]['sheet_name']}' and '{ws.title}' both map to {spec.output_name}"
                            )
                        else:
                            item["categories"][spec.key] = info
                        for template_row in ws.iter_rows(min_row=1, max_row=header["header_row_idx"]):
                            static_formulas.extend(
                                (ws.title, cell.coordinate, str(cell.value))
                                for cell in template_row if cell.data_type == "f"
                            )
                if not info["is_transaction"] and policy == "ignored_summary":
                    info.update({"content_signature": "<omitted-period-summary>",
                                 "is_table_like": True, "meaningful_cells": 0})
                elif not info["is_transaction"]:
                    signature, table_like, meaningful_cells, formulas = self._sheet_signature(
                        ws, mask_metadata=policy == "metadata"
                    )
                    info.update({"content_signature": signature, "is_table_like": table_like,
                                 "meaningful_cells": meaningful_cells})
                    static_formulas.extend((ws.title, coordinate, formula) for coordinate, formula in formulas)
                item["sheets"][ws.title] = info
            renamed_sheets = {
                info["sheet_name"]: category_candidates(info["sheet_name"])[0].output_name
                for info in item["categories"].values()
                if category_candidates(info["sheet_name"])
            }
            for sheet_name, coordinate, formula in static_formulas:
                reason = self._unsafe_formula_reason(formula, wb.sheetnames, renamed_sheets)
                if reason:
                    item["errors"].append(
                        f"sheet '{sheet_name}', cell {coordinate}: unsafe formula ({reason})"
                    )
            wb.close()
            metadata[source_id] = item
        return metadata

    def generate_audit_snapshots(self) -> Dict[str, Any]:
        metadata = self.inspect_all()
        snapshots: Dict[str, Any] = {}
        for source_id, item in metadata.items():
            snapshots[source_id] = {}
            if item["errors"]:
                continue
            wb = openpyxl.load_workbook(item["path"], data_only=False)
            for _, info in item["categories"].items():
                ws = wb[info["sheet_name"]]
                rows = [tuple(row) for row in ws.iter_rows(min_row=info["header_row_idx"] + 1, values_only=True)
                        if any(self._meaningful(value) for value in row)]
                snapshots[source_id][info["sheet_name"]] = {
                    "rows": rows, "row_hashes": [self._hash_row(row) for row in rows],
                    "non_empty_row_count": len(rows), "data_row_count": len(rows),
                    "totals": {}, "headers": info["headers"]}
            wb.close()
        return snapshots
