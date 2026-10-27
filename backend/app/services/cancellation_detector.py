"""Conservative explicit cancellation detection for purchase invoices."""

from __future__ import annotations

import re
import uuid
from typing import Optional, Tuple

from backend.app.models.records import ParsedInvoiceRecord, RecordSource
from backend.app.models.reconciliation import (
    AccountingSourceSystem,
    CancelledSourceSide,
    CancelledVoucher,
)


def _normalized_marker(value: object) -> str:
    return re.sub(r"[^A-Z]", "", str(value or "").upper())


def _contains_cancelled(value: object) -> bool:
    normalized = _normalized_marker(value)
    return "CANCELLED" in normalized or "CANCELED" in normalized


def detect_accounts_source_system(record: ParsedInvoiceRecord) -> AccountingSourceSystem:
    stored = getattr(record, "accounting_source_system", "")
    if stored:
        try:
            return AccountingSourceSystem(stored)
        except ValueError:
            pass
    headers = {str(header).strip().lower() for header in record.raw_data}
    busy = {"account", "vch/bill no", "tin/gstin no.", "taxable amt."}
    tally = {"particulars", "voucher no.", "gstin/uin", "value"}
    if busy.issubset(headers):
        return AccountingSourceSystem.BUSY
    if tally.issubset(headers):
        return AccountingSourceSystem.TALLY
    return AccountingSourceSystem.ACCOUNTS


def cancellation_evidence(record: ParsedInvoiceRecord) -> Optional[Tuple[str, str]]:
    if record.source == RecordSource.TALLY:
        appropriate = {"account", "particulars", "supplier", "supplier name", "notes", "remarks", "narration", "status"}
        for header, value in record.raw_data.items():
            if str(header).strip().lower() in appropriate and _contains_cancelled(value):
                return str(header), str(value)
        return None

    for header, value in record.raw_data.items():
        header_text = str(header).strip().lower()
        if "cancellation" in header_text and value not in (None, ""):
            return str(header), str(value)
        if header_text in {"status", "reason", "remarks"} and _contains_cancelled(value):
            return str(header), str(value)
    return None


def build_cancelled_voucher(record: ParsedInvoiceRecord) -> Optional[CancelledVoucher]:
    evidence = cancellation_evidence(record)
    if not evidence:
        return None
    header, value = evidence
    if record.source == RecordSource.GST:
        side = CancelledSourceSide.GSTR
        system = AccountingSourceSystem.GSTR
        status = "Cancelled GSTR Voucher"
    else:
        side = CancelledSourceSide.ACCOUNTS
        system = detect_accounts_source_system(record)
        status = {
            AccountingSourceSystem.BUSY: "Cancelled BUSY Voucher",
            AccountingSourceSystem.TALLY: "Cancelled Tally Voucher",
        }.get(system, "Cancelled Accounts Voucher")
    return CancelledVoucher(
        id=str(uuid.uuid4()), source_side=side, source_system=system,
        supplier=record.supplier_name, gstin=record.gstin_normalized or record.gstin_original,
        invoice_number=record.invoice_number.original, invoice_date=record.invoice_date,
        taxable_value=record.taxable_value, igst=record.igst, cgst=record.cgst, sgst=record.sgst,
        source_sheet=record.source_sheet, source_row=record.source_row, status=status,
        cancellation_reason=f"Explicit cancellation metadata in {header}",
        cancellation_evidence=f"{header}: {value}", source_rows=[record],
    )
