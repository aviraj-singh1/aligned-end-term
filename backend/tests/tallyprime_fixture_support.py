"""Test-only support for immutable real Tally fixtures.

Raw fixtures are evidence and must never be rewritten. Sanitized derivatives
exist only because XML 1.0 parsers reject Tally's invalid character references
and literal control-character separators.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import re
from xml.etree import ElementTree as ET


_NUMERIC_REFERENCE = re.compile(r"&#(?:(?P<hex>x[0-9a-fA-F]+)|(?P<decimal>[0-9]+));")


@dataclass(frozen=True)
class SanitizationReport:
    source_encoding: str
    numeric_references_removed: tuple[tuple[int, int], ...]
    literal_controls_replaced: tuple[tuple[int, int], ...]
    literal_control_replacement: str


@dataclass(frozen=True)
class SemanticLedgerEntry:
    name: str
    amount: Decimal
    is_deemed_positive: str
    is_party_ledger: str
    bill_reference: str
    bill_type: str
    gst_rate_details: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SemanticVoucher:
    envelope_style: str
    company_name: str
    attributes: dict[str, str]
    date: str
    reference_date: str
    voucher_type: str
    reference: str
    party_gstin: str
    place_of_supply: str
    party_ledger: str
    persisted_view: str
    is_invoice: str
    narration: str
    ledger_container: str
    ledgers: tuple[SemanticLedgerEntry, ...]


def is_xml_10_character(codepoint: int) -> bool:
    return (
        codepoint in (0x09, 0x0A, 0x0D)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
    )


def sanitize_real_tally_xml(raw_bytes: bytes) -> tuple[bytes, SanitizationReport]:
    """Return a UTF-16 parser-safe copy without mutating the raw evidence.

    Rules:
    1. Decode the UTF-16 fixture, retaining its original line endings.
    2. Remove only numeric references whose code point is invalid in XML 1.0.
       The surrounding Tally text (for example ``Not Applicable``) remains.
    3. Replace each literal XML 1.0-invalid control with U+FFFD so the loss is
       visible rather than silently deleting Tally's metadata separator.
    4. Re-encode as UTF-16 with a BOM. No accounting value is normalized.
    """
    if not raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("Expected a BOM-marked UTF-16 Tally fixture")
    text = raw_bytes.decode("utf-16")
    removed: Counter[int] = Counter()

    def replace_reference(match: re.Match[str]) -> str:
        token = match.group("hex") or match.group("decimal")
        codepoint = int(token[1:], 16) if token.startswith("x") else int(token)
        if is_xml_10_character(codepoint):
            return match.group(0)
        removed[codepoint] += 1
        return ""

    text = _NUMERIC_REFERENCE.sub(replace_reference, text)
    replaced: Counter[int] = Counter()
    safe_characters: list[str] = []
    for character in text:
        codepoint = ord(character)
        if is_xml_10_character(codepoint):
            safe_characters.append(character)
        else:
            replaced[codepoint] += 1
            safe_characters.append("\ufffd")
    sanitized = "".join(safe_characters).encode("utf-16")
    return sanitized, SanitizationReport(
        source_encoding="UTF-16 with BOM",
        numeric_references_removed=tuple(sorted(removed.items())),
        literal_controls_replaced=tuple(sorted(replaced.items())),
        literal_control_replacement="U+FFFD REPLACEMENT CHARACTER",
    )


def semantic_voucher(xml_bytes: bytes) -> SemanticVoucher:
    root = ET.fromstring(xml_bytes)
    voucher = root.find(".//VOUCHER")
    if voucher is None:
        raise ValueError("Fixture contains no VOUCHER")
    real_entries = voucher.findall("LEDGERENTRIES.LIST")
    aligned_entries = voucher.findall("ALLLEDGERENTRIES.LIST")
    if real_entries and aligned_entries:
        raise ValueError("Voucher mixes real-export and Aligned ledger containers")
    entries = real_entries or aligned_entries
    container = "LEDGERENTRIES.LIST" if real_entries else "ALLLEDGERENTRIES.LIST"

    ledgers = []
    for entry in entries:
        rate_details = tuple(
            (rate.findtext("GSTRATEDUTYHEAD", "").strip(), rate.findtext("GSTRATE", "").strip())
            for rate in entry.findall("RATEDETAILS.LIST")
            if rate.findtext("GSTRATEDUTYHEAD", "").strip()
        )
        bill = entry.find("BILLALLOCATIONS.LIST")
        ledgers.append(SemanticLedgerEntry(
            name=entry.findtext("LEDGERNAME", "").strip(),
            amount=Decimal(entry.findtext("AMOUNT", "0").strip()),
            is_deemed_positive=entry.findtext("ISDEEMEDPOSITIVE", "").strip(),
            is_party_ledger=entry.findtext("ISPARTYLEDGER", "").strip(),
            bill_reference=bill.findtext("NAME", "").strip() if bill is not None else "",
            bill_type=bill.findtext("BILLTYPE", "").strip() if bill is not None else "",
            gst_rate_details=rate_details,
        ))

    company = root.findtext(".//SVCURRENTCOMPANY", "").strip()
    style = "IMPORTDATA/REQUESTDATA" if root.find("./BODY/IMPORTDATA") is not None else "DESC/DATA"
    return SemanticVoucher(
        envelope_style=style,
        company_name=company,
        attributes=dict(voucher.attrib),
        date=voucher.findtext("DATE", "").strip(),
        reference_date=voucher.findtext("REFERENCEDATE", "").strip(),
        voucher_type=voucher.findtext("VOUCHERTYPENAME", "").strip(),
        reference=voucher.findtext("REFERENCE", "").strip(),
        party_gstin=voucher.findtext("PARTYGSTIN", "").strip(),
        place_of_supply=voucher.findtext("PLACEOFSUPPLY", "").strip(),
        party_ledger=voucher.findtext("PARTYLEDGERNAME", "").strip(),
        persisted_view=voucher.findtext("PERSISTEDVIEW", "").strip(),
        is_invoice=voucher.findtext("ISINVOICE", "").strip(),
        narration=voucher.findtext("NARRATION", "").strip(),
        ledger_container=container,
        ledgers=tuple(ledgers),
    )
