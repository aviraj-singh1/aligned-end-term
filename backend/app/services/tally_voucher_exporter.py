"""Deterministic structural TallyPrime purchase-voucher XML generation.

The tracked repository contains only anonymized structural fixtures. Genuine
Tally evidence remains local/private. Tally Export V1 supports TallyPrime 7.1.
The low-level renderer retains a 7.0 structural regression path, but that path
is not a selectable or validated product target.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Sequence
from uuid import UUID
from xml.etree import ElementTree as ET

from backend.app.models.tally_export import TallyExportCandidate
from backend.app.models.tally_export_validation import TallyCandidateValidation
from backend.app.models.tally_voucher import (
    CommonTallyPurchaseVoucher,
    TallyExportManifest,
    TallyPurchaseAllocation,
    TallyTaxLedgerEntry,
    TallyXmlArtifact,
)
from backend.app.services.tally_export_configuration_service import SUPPORTED_TALLY_VERSIONS


RENDERABLE_TALLY_XML_VERSIONS = {"7.0", "7.1"}
DEFAULT_MAX_BATCH_VOUCHERS = 500
MONEY = Decimal("0.01")
RATE = Decimal("0.001")


class TallyVoucherExportError(ValueError):
    """Validated source data cannot safely form the supported XML contract."""


def _valid_xml_character(value: str) -> bool:
    return all(
        character in "\t\n\r"
        or 0x20 <= ord(character) <= 0xD7FF
        or 0xE000 <= ord(character) <= 0xFFFD
        or 0x10000 <= ord(character) <= 0x10FFFF
        for character in value
    )


def _required_text(value: object, label: str, *, maximum: int = 4096) -> str:
    text = str(value or "").strip()
    if not text:
        raise TallyVoucherExportError(f"{label} is required")
    if len(text) > maximum:
        raise TallyVoucherExportError(f"{label} exceeds {maximum} characters")
    if not _valid_xml_character(text):
        raise TallyVoucherExportError(f"{label} contains an invalid XML character")
    return text


def _optional_text(value: object, label: str, *, maximum: int = 4096) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise TallyVoucherExportError(f"{label} exceeds {maximum} characters")
    if not _valid_xml_character(text):
        raise TallyVoucherExportError(f"{label} contains an invalid XML character")
    return text


def _exact_money(value: Decimal, label: str) -> Decimal:
    amount = Decimal(value)
    quantized = amount.quantize(MONEY)
    if amount != quantized:
        raise TallyVoucherExportError(f"{label} is not exact to two decimal places")
    return quantized


def _money_text(value: Decimal) -> str:
    return format(_exact_money(value, "Ledger amount"), ".2f")


def _rate_text(value: Decimal) -> str:
    rate = Decimal(value)
    quantized = rate.quantize(RATE)
    if rate != quantized:
        raise TallyVoucherExportError("GST rate is not exact to three decimal places")
    return format(quantized, ".3f")


def _child(parent: ET.Element, tag: str, value: object) -> ET.Element:
    element = ET.SubElement(parent, tag)
    element.text = str(value)
    return element


def build_common_purchase_voucher(
    candidate: TallyExportCandidate,
    validation: TallyCandidateValidation,
    *,
    client_gstin: str,
    reconciliation_run_id: UUID,
) -> CommonTallyPurchaseVoucher:
    """Translate one eligible Phase 3 result without resolving mappings again."""
    if not validation.eligible or validation.blockers:
        raise TallyVoucherExportError("Blocked candidates cannot reach Tally XML generation")
    resolved = validation.resolved
    if resolved.profile_id is None or resolved.profile_version is None:
        raise TallyVoucherExportError("Resolved Tally profile is required")
    if resolved.tally_version not in SUPPORTED_TALLY_VERSIONS:
        raise TallyVoucherExportError("Tally Export V1 supports TallyPrime 7.1 only")
    if candidate.document_date is None:
        raise TallyVoucherExportError("Supplier invoice date is required")

    ledger_by_rate = {
        Decimal(rate).quantize(RATE): _required_text(name, "Purchase ledger", maximum=255)
        for rate, name in resolved.purchase_ledgers
    }
    allocations: list[TallyPurchaseAllocation] = []
    for source in sorted(candidate.rate_allocations, key=lambda item: item.gst_rate):
        rate = Decimal(source.gst_rate).quantize(RATE)
        ledger_name = ledger_by_rate.get(rate)
        if not ledger_name:
            raise TallyVoucherExportError(f"Resolved purchase ledger is missing for GST rate {rate}")
        taxable = _exact_money(source.taxable_value, "Purchase allocation")
        if taxable <= 0:
            raise TallyVoucherExportError("Purchase allocation must be positive")
        allocations.append(TallyPurchaseAllocation(rate, ledger_name, taxable))

    tax_entries: list[TallyTaxLedgerEntry] = []
    for tax_head, amount, ledger in (
        ("IGST", candidate.igst, resolved.igst_ledger_name),
        ("CGST", candidate.cgst, resolved.cgst_ledger_name),
        ("SGST", candidate.sgst, resolved.sgst_ledger_name),
    ):
        exact_amount = _exact_money(amount, tax_head)
        if exact_amount < 0:
            raise TallyVoucherExportError(f"{tax_head} cannot be negative")
        if exact_amount:
            tax_entries.append(TallyTaxLedgerEntry(
                tax_head, _required_text(ledger, f"{tax_head} ledger", maximum=255), exact_amount,
            ))

    total_party_amount = _exact_money(
        sum((item.taxable_value for item in allocations), Decimal("0"))
        + sum((item.amount for item in tax_entries), Decimal("0")),
        "Party amount",
    )
    if candidate.invoice_total is not None:
        invoice_total = _exact_money(candidate.invoice_total, "Invoice total")
        if invoice_total != total_party_amount:
            raise TallyVoucherExportError("Resolved party amount differs from invoice total")

    audit_reference = (
        f"Aligned reconciliation {reconciliation_run_id}; candidate {validation.candidate_id}"
    )
    return CommonTallyPurchaseVoucher(
        candidate_id=_required_text(validation.candidate_id, "Candidate ID", maximum=255),
        remote_id=validation.remote_id,
        fingerprint=_required_text(validation.fingerprint, "Fingerprint", maximum=64),
        client_gstin=_required_text(client_gstin, "Client GSTIN", maximum=15),
        company_name=_required_text(resolved.company_name, "Tally company", maximum=255),
        tally_version=resolved.tally_version,
        voucher_type=_required_text(resolved.purchase_voucher_type, "Purchase voucher type", maximum=200),
        voucher_date=candidate.document_date,
        supplier_invoice_number=_required_text(candidate.document_number, "Supplier invoice number", maximum=255),
        supplier_invoice_date=candidate.document_date,
        party_ledger_name=_required_text(resolved.party_ledger_name, "Party ledger", maximum=255),
        supplier_gstin=_required_text(candidate.supplier_gstin, "Supplier GSTIN", maximum=15),
        place_of_supply=_required_text(candidate.place_of_supply, "Place of supply", maximum=100),
        purchase_allocations=tuple(allocations),
        tax_entries=tuple(tax_entries),
        total_party_amount=total_party_amount,
        narration=_optional_text(audit_reference, "Narration", maximum=1024),
        reconciliation_run_id=reconciliation_run_id,
    )


def voucher_signed_balance(voucher: CommonTallyPurchaseVoucher) -> Decimal:
    debit = sum((entry.taxable_value for entry in voucher.purchase_allocations), Decimal("0"))
    debit += sum((entry.amount for entry in voucher.tax_entries), Decimal("0"))
    return _exact_money(voucher.total_party_amount - debit, "Voucher balance")


def _ledger_entry(
    voucher: ET.Element,
    *,
    ledger_name: str,
    amount: Decimal,
    debit: bool,
    party: bool,
) -> ET.Element:
    entry = ET.SubElement(voucher, "LEDGERENTRIES.LIST")
    _child(entry, "LEDGERNAME", ledger_name)
    _child(entry, "ISDEEMEDPOSITIVE", "Yes" if debit else "No")
    _child(entry, "LEDGERFROMITEM", "No")
    _child(entry, "REMOVEZEROENTRIES", "No")
    _child(entry, "ISPARTYLEDGER", "Yes" if party else "No")
    signed = -amount if debit else amount
    _child(entry, "AMOUNT", _money_text(signed))
    return entry


def _append_party_entry(element: ET.Element, voucher: CommonTallyPurchaseVoucher) -> None:
    entry = _ledger_entry(
        element, ledger_name=voucher.party_ledger_name,
        amount=voucher.total_party_amount, debit=False, party=True,
    )
    bill = ET.SubElement(entry, "BILLALLOCATIONS.LIST")
    _child(bill, "NAME", voucher.supplier_invoice_number)
    _child(bill, "BILLTYPE", "New Ref")
    _child(bill, "AMOUNT", _money_text(voucher.total_party_amount))


def _append_voucher(message: ET.Element, voucher: CommonTallyPurchaseVoucher) -> None:
    if voucher_signed_balance(voucher) != Decimal("0.00"):
        raise TallyVoucherExportError("Voucher does not balance to zero")
    element = ET.SubElement(message, "VOUCHER", {
        "REMOTEID": str(voucher.remote_id),
        "VCHTYPE": voucher.voucher_type,
        "ACTION": "Create",
        "OBJVIEW": "Invoice Voucher View",
    })
    _child(element, "DATE", voucher.voucher_date.strftime("%Y%m%d"))
    _child(element, "REFERENCEDATE", voucher.supplier_invoice_date.strftime("%Y%m%d"))
    _child(element, "VOUCHERTYPENAME", voucher.voucher_type)
    _child(element, "REFERENCE", voucher.supplier_invoice_number)
    _child(element, "PARTYLEDGERNAME", voucher.party_ledger_name)
    _child(element, "PARTYGSTIN", voucher.supplier_gstin)
    _child(element, "PLACEOFSUPPLY", voucher.place_of_supply)
    _child(element, "PERSISTEDVIEW", "Invoice Voucher View")
    _child(element, "ISINVOICE", "Yes")
    _child(element, "NARRATION", voucher.narration)

    _append_party_entry(element, voucher)
    for allocation in voucher.purchase_allocations:
        entry = _ledger_entry(
            element, ledger_name=allocation.ledger_name,
            amount=allocation.taxable_value, debit=True, party=False,
        )
        _child(entry, "GSTOVRDNTAXABILITY", "Taxable")
        _child(entry, "GSTOVRDNRATE", _rate_text(allocation.gst_rate))
        _child(entry, "GSTOVRDNASSESSABLEVALUE", _money_text(allocation.taxable_value))
    for tax in voucher.tax_entries:
        entry = _ledger_entry(
            element, ledger_name=tax.ledger_name, amount=tax.amount,
            debit=True, party=False,
        )
        _child(entry, "GSTDUTYHEAD", tax.tax_head)


def generate_tally_xml(
    vouchers: Sequence[CommonTallyPurchaseVoucher],
    *,
    tally_version: str,
    company_name: str,
    maximum_vouchers: int = DEFAULT_MAX_BATCH_VOUCHERS,
) -> bytes:
    if tally_version not in RENDERABLE_TALLY_XML_VERSIONS:
        raise TallyVoucherExportError("TallyPrime version is not recognized by the XML renderer")
    if not vouchers:
        raise TallyVoucherExportError("At least one eligible voucher is required")
    if maximum_vouchers < 1 or len(vouchers) > maximum_vouchers:
        raise TallyVoucherExportError(f"Tally export batch exceeds the {maximum_vouchers}-voucher limit")
    safe_company = _required_text(company_name, "Tally company", maximum=255)
    ordered = sorted(vouchers, key=lambda item: (str(item.remote_id), item.candidate_id))
    if any(item.tally_version != tally_version for item in ordered):
        raise TallyVoucherExportError("All vouchers must target the same TallyPrime version")
    if any(item.company_name != safe_company for item in ordered):
        raise TallyVoucherExportError("All vouchers must target the same Tally company")

    envelope = ET.Element("ENVELOPE")
    header = ET.SubElement(envelope, "HEADER")
    _child(header, "VERSION", "1")
    _child(header, "TALLYREQUEST", "Import")
    _child(header, "TYPE", "Data")
    _child(header, "ID", "Vouchers")
    body = ET.SubElement(envelope, "BODY")
    desc = ET.SubElement(body, "DESC")
    static = ET.SubElement(desc, "STATICVARIABLES")
    _child(static, "SVCURRENTCOMPANY", safe_company)
    data = ET.SubElement(body, "DATA")
    for item in ordered:
        message = ET.SubElement(data, "TALLYMESSAGE")
        _append_voucher(message, item)
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True, short_empty_elements=False)


def generate_tally_xml_artifact(
    vouchers: Sequence[CommonTallyPurchaseVoucher],
    *,
    batch_id: UUID,
    profile_id: UUID,
    profile_version: int,
    reconciliation_run_id: UUID,
    maximum_vouchers: int = DEFAULT_MAX_BATCH_VOUCHERS,
) -> TallyXmlArtifact:
    if not vouchers:
        raise TallyVoucherExportError("At least one eligible voucher is required")
    ordered = tuple(sorted(vouchers, key=lambda item: (str(item.remote_id), item.candidate_id)))
    first = ordered[0]
    xml_bytes = generate_tally_xml(
        ordered, tally_version=first.tally_version, company_name=first.company_name,
        maximum_vouchers=maximum_vouchers,
    )
    digest = hashlib.sha256(xml_bytes).hexdigest()
    manifest = TallyExportManifest(
        batch_id=batch_id, tally_version=first.tally_version,
        profile_id=profile_id, profile_version=profile_version,
        company_name=first.company_name, reconciliation_run_id=reconciliation_run_id,
        voucher_count=len(ordered),
        candidate_ids=tuple(item.candidate_id for item in ordered),
        remote_ids=tuple(item.remote_id for item in ordered),
        fingerprints=tuple(item.fingerprint for item in ordered),
        content_sha256=digest,
    )
    return TallyXmlArtifact(xml_bytes=xml_bytes, content_sha256=digest, manifest=manifest)
