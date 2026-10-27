from collections import defaultdict
from typing import Any, Dict, List

from backend.app.services.gstr_merge.gst_schema import normalize_label


class WorkbookValidator:
    def __init__(self, metadata: Dict[str, Any], mode: str = "quarterly", *,
                 expected_gstin: str = None, expected_financial_year: str = None,
                 expected_periods: Dict[str, str] = None):
        self.metadata = metadata
        self.mode = mode
        self.expected_gstin = expected_gstin.strip().upper() if expected_gstin else None
        self.expected_financial_year = expected_financial_year.strip() if expected_financial_year else None
        self.expected_periods = {
            source_id: str(code).strip().upper()
            for source_id, code in (expected_periods or {}).items()
        }
        self.warnings: List[str] = []
        self.informational_notes: List[str] = []
        self.period_warnings: List[str] = []
        self.verification: Dict[str, Dict[str, Any]] = {}

    def _validate_static_sheets(self) -> List[str]:
        errors = []
        grouped = defaultdict(list)
        for source_id, item in self.metadata.items():
            for info in item.get("sheets", {}).values():
                if info.get("category"):
                    continue
                grouped[normalize_label(info["sheet_name"])].append((source_id, item, info))

        for normalized, instances in grouped.items():
            display = instances[0][2]["sheet_name"]
            policies = {instance[2].get("static_policy", "unknown") for instance in instances}
            signatures = {instance[2].get("content_signature") for instance in instances}
            policy = next(iter(policies)) if len(policies) == 1 else "unknown"
            if policy == "ignored_summary":
                self.informational_notes.append(
                    f"Period summary sheet '{display}' was omitted; annual totals are verified from merged detail rows."
                )
                continue
            if policy == "first_source_static":
                if len(signatures) > 1:
                    self.informational_notes.append(
                        f"Source instruction sheet '{display}' differs by period metadata; the first source version was retained for reference."
                    )
                continue
            if policy == "metadata":
                if len(signatures) > 1:
                    names = ", ".join(instance[1]["filename"] for instance in instances)
                    errors.append(
                        f"Approved metadata sheet '{display}' differs across {names} outside recognized metadata fields."
                    )
                continue
            if policy == "approved_static":
                if len(signatures) > 1:
                    names = ", ".join(instance[1]["filename"] for instance in instances)
                    errors.append(f"Approved static sheet '{display}' differs across uploaded files: {names}.")
                continue
            if any(instance[2].get("is_table_like") for instance in instances):
                names = ", ".join(instance[1]["filename"] for instance in instances)
                errors.append(
                    f"Unsupported populated table sheet '{display}' was found in {names}; its category is not registered."
                )
            elif len(instances) != len(self.metadata):
                names = ", ".join(instance[1]["filename"] for instance in instances)
                errors.append(
                    f"Unknown sheet '{display}' is not present in every workbook (found in {names}); "
                    "it cannot be retained safely."
                )
            elif len(signatures) > 1:
                names = ", ".join(instance[1]["filename"] for instance in instances)
                errors.append(
                    f"Sheet '{display}' differs across uploaded files ({names}) and is not a recognized mergeable GST category."
                )
        return errors

    @staticmethod
    def _status(found_count: int, total: int) -> str:
        if found_count == total:
            return "VERIFIED"
        if found_count:
            return "PARTIALLY VERIFIED"
        return "NOT VERIFIABLE"

    def validate(self) -> List[str]:
        self.warnings = []
        self.informational_notes = []
        self.period_warnings = []
        errors: List[str] = []
        expected = 4 if self.mode == "quarterly" else 12 if self.mode == "monthly" else None
        if expected is None:
            return ["Mode must be 'quarterly' or 'monthly'."]
        if len(self.metadata) != expected:
            errors.append(f"{self.mode.title()} mode requires exactly {expected} files; received {len(self.metadata)}.")

        supported_count = 0
        gstins, years, periods = defaultdict(list), defaultdict(list), defaultdict(list)
        mismatched_periods = []
        for item in self.metadata.values():
            source_id = item["source_id"]
            filename = item["filename"]
            errors.extend(f"{filename}: {message}" for message in item.get("errors", []))
            supported_count += len(item.get("categories", {}))
            if not item.get("categories"):
                errors.append(f"{filename}: no supported GST business-data category was found.")
            if item.get("taxpayer_gstin"):
                gstins[item["taxpayer_gstin"]].append(filename)
            else:
                self.warnings.append(f"{filename}: GSTIN validation unavailable for this workbook.")
            if item.get("financial_year"):
                years[item["financial_year"]].append(filename)
            else:
                self.warnings.append(f"{filename}: financial-year validation unavailable.")
            period = item.get("period")
            if period:
                periods[(period["kind"], period["code"])].append(filename)
                expected_period = self.expected_periods.get(source_id)
                if expected_period and period["code"] != expected_period:
                    errors.append(
                        f"{filename}: detected return period {period['code']}, "
                        f"but the supplied slot expects {expected_period}."
                    )
                wanted = "quarter" if self.mode == "quarterly" else "month"
                if period["kind"] != wanted:
                    mismatched_periods.append((filename, period))
            else:
                warning = f"{filename}: return period could not be determined confidently."
                self.period_warnings.append(warning)
                self.warnings.append(warning)

        if mismatched_periods:
            if self.mode == "quarterly" and all(period["kind"] == "month" for _, period in mismatched_periods):
                month_names = {
                    "M01": "January", "M02": "February", "M03": "March", "M04": "April",
                    "M05": "May", "M06": "June", "M07": "July", "M08": "August",
                    "M09": "September", "M10": "October", "M11": "November", "M12": "December",
                }
                detected = ", ".join(month_names.get(period["code"], period["code"])
                                     for _, period in mismatched_periods)
                kind = "monthly GSTR-2B statements" if all(
                    self.metadata[source_id].get("is_gstr2b_workbook") for source_id in self.metadata
                ) else "monthly statements"
                errors.append(
                    f"Quarterly mode received {kind} ({detected}). Use Monthly mode and upload all 12 "
                    "statements for the financial year, from April through March."
                )
            else:
                wanted = "quarter" if self.mode == "quarterly" else "month"
                errors.extend(
                    f"{filename}: detected {period['kind']} {period['code']}, but {self.mode} mode requires {wanted} periods."
                    for filename, period in mismatched_periods
                )

        if supported_count == 0:
            errors.append("No supported GST business-data category was found in the uploaded workbooks.")
        if len(gstins) > 1:
            errors.append("Conflicting taxpayer GSTIN values: " + "; ".join(
                f"{value}: {', '.join(names)}" for value, names in gstins.items()))
        if self.expected_gstin:
            for value, names in gstins.items():
                if value != self.expected_gstin:
                    errors.append(
                        f"Taxpayer GSTIN {value} in {', '.join(names)} does not match "
                        f"the expected GSTIN {self.expected_gstin}."
                    )
        if len(years) > 1:
            errors.append("Inconsistent financial years: " + "; ".join(
                f"{value}: {', '.join(names)}" for value, names in years.items()))
        if self.expected_financial_year:
            for value, names in years.items():
                if value != self.expected_financial_year:
                    errors.append(
                        f"Financial year {value} in {', '.join(names)} does not match "
                        f"the expected financial year {self.expected_financial_year}."
                    )
        for (_, code), names in periods.items():
            if len(names) > 1:
                errors.append(f"Duplicate return period {code}: {', '.join(names)}")
        detected_periods = [item.get("period") for item in self.metadata.values() if item.get("period")]
        if self.mode == "monthly" and len(detected_periods) != len(self.metadata):
            errors.append(
                "Monthly mode requires a verifiable tax period in every workbook so all 12 financial-year months "
                "(April through March) can be confirmed."
            )
        wanted_kind = "quarter" if self.mode == "quarterly" else "month"
        if (len(detected_periods) == len(self.metadata)
                and all(period["kind"] == wanted_kind for period in detected_periods)):
            expected_codes = {"Q1", "Q2", "Q3", "Q4"} if self.mode == "quarterly" else {
                f"M{month:02d}" for month in range(1, 13)
            }
            actual_codes = {period["code"] for period in detected_periods}
            if actual_codes != expected_codes:
                fiscal_order = [f"M{month:02d}" for month in range(4, 13)] + [f"M{month:02d}" for month in range(1, 4)]
                ordering = fiscal_order if self.mode == "monthly" else ["Q1", "Q2", "Q3", "Q4"]
                missing = ", ".join(code for code in ordering if code in expected_codes - actual_codes) or "none"
                unexpected = ", ".join(sorted(actual_codes - expected_codes)) or "none"
                errors.append(f"Return-period set is incomplete; missing: {missing}; unexpected: {unexpected}.")

        errors.extend(self._validate_static_sheets())
        total = len(self.metadata)
        gstin_count = sum(bool(item.get("taxpayer_gstin")) for item in self.metadata.values())
        year_count = sum(bool(item.get("financial_year")) for item in self.metadata.values())
        period_count = len(detected_periods)
        period_label = "Quarter" if self.mode == "quarterly" else "Month"
        self.verification = {
            "gstin": {
                "status": self._status(gstin_count, total),
                "message": "Taxpayer GSTIN verified across all files." if gstin_count == total else (
                    f"Taxpayer GSTIN partially verified — metadata unavailable for {total - gstin_count} file(s)."
                    if gstin_count else "Taxpayer consistency not verifiable — no reliable GSTIN metadata found."
                ),
            },
            "financial_year": {
                "status": self._status(year_count, total),
                "message": "Financial year verified across all files." if year_count == total else (
                    f"Financial year partially verified — metadata unavailable for {total - year_count} file(s)."
                    if year_count else "Financial year not verifiable — no reliable metadata found."
                ),
            },
            "period": {
                "status": self._status(period_count, total),
                "message": f"{period_label} validation verified across all files." if period_count == total else (
                    f"{period_label} validation incomplete — period metadata unavailable for {total - period_count} file(s)."
                    if period_count else f"{period_label} validation not verifiable — no reliable period metadata found."
                ),
            },
        }
        return errors

    def gstin_result(self) -> Dict[str, Any]:
        found = {item.get("taxpayer_gstin") for item in self.metadata.values() if item.get("taxpayer_gstin")}
        unavailable = [item["filename"] for item in self.metadata.values() if not item.get("taxpayer_gstin")]
        return {"status": "validated" if len(found) == 1 and not unavailable else "partial" if found else "unavailable",
                "gstin": next(iter(found)) if len(found) == 1 else None,
                "unavailable_files": unavailable}

    def verification_summary(self) -> Dict[str, Dict[str, Any]]:
        return self.verification
