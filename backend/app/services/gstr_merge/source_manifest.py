import datetime
import decimal
import hashlib
import os
import pickle
import tempfile
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

import openpyxl
from openpyxl.utils.datetime import from_excel

from backend.app.services.gstr_merge.gst_schema import (
    CATEGORY_BY_KEY,
    CATEGORY_SPECS,
    FINANCIAL_FIELDS,
    canonical_header,
    header_mapping,
    normalize_label,
)
from backend.app.services.gstr_merge.workbook_inspector import GSTIN_RE


EXCEL_MAX_ROWS = 1_048_576
FOOTER_MARKERS = {
    "total",
    "total amount",
    "grand total",
    "subtotal",
    "summary",
    "net total",
}
GSTIN_REQUIRED_CATEGORIES = {
    spec.key for spec in CATEGORY_SPECS if "gstin" in spec.identity
}

FIELD_LABELS = {
    "gstin": "GSTIN",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "original_invoice_number": "Original Invoice Number",
    "original_invoice_date": "Original Invoice Date",
    "revised_invoice_number": "Revised Invoice Number",
    "revised_invoice_date": "Revised Invoice Date",
    "note_number": "Note Number",
    "note_date": "Note Date",
    "original_note_number": "Original Note Number",
    "original_note_date": "Original Note Date",
    "revised_note_number": "Revised Note Number",
    "revised_note_date": "Revised Note Date",
    "document_number": "Document Number",
    "document_date": "Document Date",
    "original_document_number": "Original Document Number",
    "original_document_date": "Original Document Date",
    "revised_document_number": "Revised Document Number",
    "revised_document_date": "Revised Document Date",
    "bill_of_entry_number": "Bill of Entry Number",
    "bill_of_entry_date": "Bill of Entry Date",
    "taxable_value": "Taxable Value",
    "igst": "IGST",
    "cgst": "CGST",
    "sgst": "SGST",
    "cess": "Cess",
    "itc_reduction_igst": "ITC Reduction — Integrated Tax",
    "itc_reduction_cgst": "ITC Reduction — Central Tax",
    "itc_reduction_sgst": "ITC Reduction — State/UT Tax",
    "itc_reduction_cess": "ITC Reduction — Cess",
    "hsn": "HSN/SAC",
    "period": "Period",
}


class SourceValidationError(ValueError):
    pass


def is_meaningful(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def typed_row_token(values: Tuple[Any, ...]) -> bytes:
    # Serialize each primitive independently. Pickling the whole tuple is not
    # deterministic for equal rows because pickle memoizes repeated object
    # identities (for example, shared-string instances returned by openpyxl).
    # Length delimiters retain cell boundaries and scalar pickle retains exact
    # types such as None, numeric zero, and text "0".
    digest = hashlib.sha256()
    for value in values:
        encoded = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def validate_excel_row_limits(manifest: Dict[str, Any], maximum: int = EXCEL_MAX_ROWS) -> List[str]:
    errors = []
    for category, item in manifest.get("categories", {}).items():
        final_row = item["header_row_idx"] + item["row_count"]
        if final_row > maximum:
            label = CATEGORY_BY_KEY[category].output_name
            errors.append(
                f"Cannot create consolidated {label} sheet: the merged result would contain "
                f"{final_row:,} rows, exceeding Excel's worksheet limit of {maximum:,}."
            )
    return errors


class SourceManifestBuilder:
    """Validate source rows and record exactly what is approved for output."""

    def __init__(self, temp_paths: Dict[str, str], metadata: Dict[str, Any]):
        self.temp_paths = temp_paths
        self.metadata = metadata
        self.duplicate_warnings: List[Dict[str, Any]] = []

    @staticmethod
    def _is_repeated_header(values, spec) -> bool:
        # Canonical header aliases are textual. Typed dates and numeric tax
        # cells therefore prove that a normal row cannot contain all required
        # header fields, avoiding regex-heavy normalization on the hot path.
        # Rows with enough text still use the original full semantic check.
        if sum(1 for value in values if isinstance(value, str) and value.strip()) < len(spec.required):
            return False
        mapping, _ = header_mapping(values, spec)
        keys = set(mapping)
        known = {key for key in keys if not key.startswith("raw:")}
        return set(spec.required).issubset(keys) and len(known) >= spec.minimum_known

    @staticmethod
    def _decimal(value, filename: str, sheet: str, row: int, field: str) -> decimal.Decimal:
        label = FIELD_LABELS.get(field, field)
        if not is_meaningful(value):
            return decimal.Decimal("0")
        if (field.startswith("itc_reduction_") and isinstance(value, str)
                and normalize_label(value) in {"na", "n a", "not applicable"}):
            return decimal.Decimal("0")
        if isinstance(value, bool):
            raise SourceValidationError(
                f"{filename} → {sheet} → row {row} → {label}: expected numeric value, received boolean."
            )
        try:
            return decimal.Decimal(str(value))
        except (decimal.InvalidOperation, ValueError) as exc:
            raise SourceValidationError(
                f"{filename} → {sheet} → row {row} → {label}: "
                f"expected numeric value, received {value!r}."
            ) from exc

    @staticmethod
    def _valid_date(value) -> bool:
        if isinstance(value, datetime.datetime):
            return True
        if isinstance(value, datetime.date):
            return True
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            try:
                converted = from_excel(value)
                return isinstance(converted, (datetime.date, datetime.datetime))
            except (TypeError, ValueError, OverflowError):
                return False
        if isinstance(value, str):
            text = value.strip()
            for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"):
                try:
                    datetime.datetime.strptime(text, pattern)
                    return True
                except ValueError:
                    continue
        return False

    @staticmethod
    def _is_structural_footer(values, source_map, spec) -> bool:
        meaningful = [(key, values[index]) for key, index in source_map.items()
                      if index < len(values) and is_meaningful(values[index])]
        markers = [(key, value) for key, value in meaningful
                   if isinstance(value, str) and normalize_label(value).rstrip(":") in FOOTER_MARKERS]
        if len(markers) != 1:
            return False
        financial = [(key, value) for key, value in meaningful if key in FINANCIAL_FIELDS]
        if not financial:
            return False
        for _, value in financial:
            if isinstance(value, bool):
                return False
            try:
                decimal.Decimal(str(value))
            except decimal.InvalidOperation:
                return False
        marker = markers[0]
        nonfinancial = [(key, value) for key, value in meaningful if key not in FINANCIAL_FIELDS]
        return len(nonfinancial) == 1 and nonfinancial[0] == marker

    def _validate_row(self, values, source_map, spec, filename: str, sheet: str, row_number: int):
        required_fields = [] if spec.key == "b2csa" else [field for field in spec.required if field != "period"]
        if spec.key in GSTIN_REQUIRED_CATEGORIES and "gstin" not in required_fields:
            required_fields.insert(0, "gstin")
        for field in required_fields:
            if field not in source_map:
                label = FIELD_LABELS.get(field, field)
                raise SourceValidationError(
                    f"{filename} → {sheet} → row {row_number} → {label}: required column is missing."
                )
            index = source_map[field]
            value = values[index] if index < len(values) else None
            label = FIELD_LABELS.get(field, field)
            if not is_meaningful(value):
                raise SourceValidationError(f"{filename} → {sheet} → row {row_number} → {label}: value is required.")
            if field == "gstin" and not GSTIN_RE.fullmatch(str(value).strip().upper()):
                raise SourceValidationError(f"{filename} → {sheet} → row {row_number} → {label}: invalid GSTIN format.")
            if field.endswith("_date") and not self._valid_date(value):
                raise SourceValidationError(f"{filename} → {sheet} → row {row_number} → {label}: invalid or ambiguous date.")
            if field in FINANCIAL_FIELDS:
                self._decimal(value, filename, sheet, row_number, field)

        for field in FINANCIAL_FIELDS:
            if field in source_map:
                value = values[source_map[field]] if source_map[field] < len(values) else None
                if is_meaningful(value):
                    self._decimal(value, filename, sheet, row_number, field)

    @staticmethod
    def _identity_token(value):
        if isinstance(value, datetime.datetime):
            return value.date().isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        if isinstance(value, str):
            return value.strip().upper()
        return str(value) if value is not None else ""

    def build(self) -> Dict[str, Any]:
        manifest: Dict[str, Any] = {"categories": {}, "excluded_footers": []}
        source_ids = list(self.temp_paths)
        duplicate_index = defaultdict(dict)
        spool_handles = {}

        # Establish canonical category layouts once. Row payloads go to a local
        # binary spool; only multiplicities and financial totals remain in RAM.
        for spec in CATEGORY_SPECS:
            instances = [(source_id, self.metadata[source_id]["categories"][spec.key])
                         for source_id in source_ids if spec.key in self.metadata[source_id].get("categories", {})]
            if not instances:
                continue
            _, template_info = instances[0]
            template_map = template_info["column_map"]
            output_width = max(len(template_info["headers"]), max(template_map.values(), default=-1) + 1)
            spool = tempfile.NamedTemporaryFile(
                mode="wb", prefix=f"gstr-{spec.key}-", suffix=".rows",
                dir=os.path.dirname(next(iter(self.temp_paths.values()))), delete=False,
            )
            spool_handles[spec.key] = spool
            manifest["categories"][spec.key] = {
                "sheet_name": spec.output_name,
                "header_row_idx": template_info["header_row_idx"],
                "column_map": dict(template_map),
                "headers": tuple(template_info["headers"]),
                "output_width": output_width,
                "row_count": 0,
                "row_hash_counts": Counter(),
                "totals": {field: decimal.Decimal("0") for field in FINANCIAL_FIELDS if field in template_map},
                "template_source_id": instances[0][0],
                "template_sheet_name": template_info["sheet_name"],
                "spool_path": spool.name,
                "source_counts": Counter(),
            }

        try:
            # File-first traversal guarantees one read-only open per source,
            # regardless of how many GST categories it contains.
            for source_id in source_ids:
                filename = self.metadata[source_id]["filename"]
                wb = openpyxl.load_workbook(
                    self.temp_paths[source_id], data_only=False, read_only=True, keep_links=False
                )
                try:
                    for spec in CATEGORY_SPECS:
                        if spec.key not in self.metadata[source_id].get("categories", {}):
                            continue
                        category_manifest = manifest["categories"][spec.key]
                        info = self.metadata[source_id]["categories"][spec.key]
                        ws = wb[info["sheet_name"]]
                        source_map = info["column_map"]
                        headers = info["headers"]
                        stacked_header_rows = {
                            tuple(row) for row in info.get("header_rows", ())
                        } if info.get("header_depth", 1) > 1 else set()
                        header_keys = tuple(info.get(
                            "canonical_headers_by_column",
                            tuple(canonical_header(header) for header in headers),
                        ))
                        template_map = category_manifest["column_map"]
                        output_width = category_manifest["output_width"]
                        totals = category_manifest["totals"]
                        for row_number, row in enumerate(
                            ws.iter_rows(min_row=info["header_row_idx"] + 1), info["header_row_idx"] + 1
                        ):
                            values = [cell.value for cell in row]
                            if not any(is_meaningful(value) for value in values):
                                continue
                            if tuple(values) in stacked_header_rows:
                                continue
                            if self._is_repeated_header(values, spec):
                                continue
                            if self._is_structural_footer(values, source_map, spec):
                                manifest["excluded_footers"].append(
                                    {"source_id": source_id, "sheet": ws.title, "row_number": row_number}
                                )
                                continue
                            if any(cell.data_type == "f" for cell in row):
                                raise SourceValidationError(
                                    f"{filename} → {ws.title} → row {row_number}: "
                                    "formulas in business data are not supported."
                                )
                            self._validate_row(values, source_map, spec, filename, ws.title, row_number)
                            for index, value in enumerate(values):
                                if not is_meaningful(value):
                                    continue
                                header = headers[index] if index < len(headers) else None
                                key = header_keys[index] if index < len(header_keys) else ""
                                if not key:
                                    raise SourceValidationError(
                                        f"{filename} → {ws.title} → row {row_number}: "
                                        f"populated column {index + 1} has no header."
                                    )
                                if key not in template_map:
                                    raise SourceValidationError(
                                        f"{filename} → {ws.title} → row {row_number}: "
                                        f"populated column {header!r} cannot be mapped to the output template."
                                    )
                            output_row = [None] * output_width
                            for key, source_index in source_map.items():
                                if key in template_map and source_index < len(values):
                                    value = values[source_index]
                                    # Excel/openpyxl serializes zero-length and
                                    # whitespace-only strings back as blank
                                    # cells. Canonicalize them before hashing
                                    # so the trusted manifest reflects the
                                    # representation the XLSX can persist.
                                    if isinstance(value, str) and not value.strip():
                                        value = None
                                    output_row[template_map[key]] = value
                            output_tuple = tuple(output_row)
                            pickle.dump(
                                (source_id, filename, ws.title, row_number, output_tuple),
                                spool_handles[spec.key], protocol=pickle.HIGHEST_PROTOCOL,
                            )
                            category_manifest["row_count"] += 1
                            category_manifest["source_counts"][source_id] += 1
                            category_manifest["row_hash_counts"][typed_row_token(output_tuple)] += 1
                            for field in totals:
                                totals[field] += self._decimal(
                                    output_row[template_map[field]], filename, ws.title, row_number, field
                                )
                            if spec.identity and all(field in source_map for field in spec.identity):
                                identity = tuple(self._identity_token(values[source_map[field]]) for field in spec.identity)
                                if all(identity):
                                    identity_hash = hashlib.sha256(pickle.dumps(
                                        identity, protocol=pickle.HIGHEST_PROTOCOL
                                    )).digest()
                                    category_index = duplicate_index[spec.key]
                                    entry = category_index.get(identity_hash)
                                    if entry is None:
                                        category_index[identity_hash] = [filename, 1, None]
                                    else:
                                        entry[1] += 1
                                        if filename != entry[0]:
                                            if entry[2] is None:
                                                entry[2] = {entry[0]}
                                            entry[2].add(filename)
                finally:
                    wb.close()
        except Exception:
            # A row-level failure can happen after one or more disk-backed
            # spools have been created. Close and remove every internal spool
            # before propagating the validation error.
            for handle in spool_handles.values():
                if not handle.closed:
                    handle.close()
            for category_manifest in manifest["categories"].values():
                spool_path = category_manifest.get("spool_path")
                if spool_path and os.path.exists(spool_path):
                    os.remove(spool_path)
                category_manifest["spool_path"] = None
            raise
        finally:
            for handle in spool_handles.values():
                if not handle.closed:
                    handle.close()

        category_duplicates = defaultdict(lambda: {"sources": set(), "count": 0})
        for category, entries in duplicate_index.items():
            for _, count, sources in entries.values():
                if sources:
                    category_duplicates[category]["sources"].update(sources)
                    category_duplicates[category]["count"] += count
        for category, entry in category_duplicates.items():
            self.duplicate_warnings.append({
                "category": CATEGORY_BY_KEY[category].output_name,
                "duplicate_count": entry["count"],
                "source_files": sorted(entry["sources"]),
            })
        return manifest


def iter_spooled_rows(category_manifest: Dict[str, Any]):
    """Yield canonical source records from the trusted, internally-created spool."""
    with open(category_manifest["spool_path"], "rb") as handle:
        while True:
            try:
                yield pickle.load(handle)
            except EOFError:
                return
