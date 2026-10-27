"""Structural tests only; they do not prove acceptance by TallyPrime."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

from backend.app.models.records import ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType
from backend.app.models.tally_export_validation import (
    ResolvedTallyConfiguration,
    TallyCandidateValidation,
    TallyPartyMappingSnapshot,
    TallyProfileSnapshot,
    TallyPurchaseMappingSnapshot,
    TallyValidationIssue,
)
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.reconciliation_engine import aggregate_gst_invoices
from backend.app.services.tally_export_contract import purchase_candidate
from backend.app.services.tally_export_validation_service import validate_tally_candidate
from backend.app.services.tally_voucher_exporter import (
    TallyVoucherExportError,
    build_common_purchase_voucher,
    generate_tally_xml,
    generate_tally_xml_artifact,
    voucher_signed_balance,
)


D = Decimal
CLIENT_GSTIN = "06AAAAA0000A1Z5"
SUPPLIER_GSTIN = "06DEMOX1234A1Z5"
INTERSTATE_SUPPLIER_GSTIN = "03DEMOX1234A1Z5"


def source_row(*, number="INV-1", row_number=2, day=1, rate="18", taxable="100",
               igst="0", cgst="9", sgst="9", place="Haryana",
               supplier_gstin=SUPPLIER_GSTIN):
    return ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="b2b", source_row=row_number,
        sheet_category=SheetCategory.B2B, supplier_name="Supplier Private Limited",
        gstin_original=supplier_gstin, gstin_normalized=supplier_gstin, gstin_valid=True,
        invoice_number=normalize_invoice_number(number), invoice_date=date(2026, 4, day),
        voucher_type=VoucherType.PURCHASE, taxable_value=D(taxable), igst=D(igst),
        cgst=D(cgst), sgst=D(sgst), gst_rate=D(rate), place_of_supply=place,
        reverse_charge="No",
    )


def canonical_candidate(*rows):
    return purchase_candidate(aggregate_gst_invoices(list(rows or (source_row(),)))[0])


def validated(candidate, *, version="7.1", company="Client & Company",
              party_ledger="Supplier Ledger", mappings=None):
    profile = TallyProfileSnapshot(
        id=uuid4(), version=1, status="ACTIVE", company_name=company,
        tally_version=version, purchase_voucher_type="Purchase",
        igst_ledger_name="Input IGST", cgst_ledger_name="Input CGST",
        sgst_ledger_name="Input SGST", numbering_strategy="TALLY_AUTO",
    )
    party = TallyPartyMappingSnapshot(
        status="ACTIVE", supplier_gstin=candidate.supplier_gstin,
        last_source_supplier_name="Supplier Private Limited",
        tally_party_ledger_name=party_ledger,
    )
    if mappings is None:
        mappings = tuple(
            TallyPurchaseMappingSnapshot(
                status="ACTIVE", transaction_kind="purchase_invoice", gst_rate=rate,
                tally_purchase_ledger_name=f"Purchases {rate}%",
            )
            for rate in candidate.gst_rates
        )
    result = validate_tally_candidate(
        candidate, client_gstin=CLIENT_GSTIN, profile=profile,
        party_mapping=party, purchase_mappings=mappings,
    )
    assert result.eligible, [issue.code for issue in result.blockers]
    return result


def voucher(candidate, validation, run_id=None):
    return build_common_purchase_voucher(
        candidate, validation, client_gstin=CLIENT_GSTIN,
        reconciliation_run_id=run_id or uuid4(),
    )


def parsed_vouchers(xml_bytes):
    root = ET.fromstring(xml_bytes)
    return root, root.findall("./BODY/DATA/TALLYMESSAGE/VOUCHER")


def ledger_values(element):
    return {
        entry.findtext("LEDGERNAME"): D(entry.findtext("AMOUNT"))
        for entry in element.findall("LEDGERENTRIES.LIST")
    }


def test_single_cgst_sgst_purchase_xml_has_resolved_identity_ledgers_and_balance():
    candidate = canonical_candidate()
    validation = validated(candidate)
    common = voucher(candidate, validation)
    xml_bytes = generate_tally_xml(
        [common], tally_version="7.1", company_name=common.company_name,
    )
    root, vouchers = parsed_vouchers(xml_bytes)
    rendered = vouchers[0]

    assert root.findtext("./HEADER/TALLYREQUEST") == "Import"
    assert root.findtext("./HEADER/TYPE") == "Data"
    assert root.findtext("./HEADER/ID") == "Vouchers"
    assert root.findtext("./BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY") == "Client & Company"
    assert rendered.attrib == {
        "REMOTEID": str(validation.remote_id), "VCHTYPE": "Purchase", "ACTION": "Create",
        "OBJVIEW": "Invoice Voucher View",
    }
    assert rendered.findtext("DATE") == "20260401"
    assert rendered.findtext("REFERENCEDATE") == "20260401"
    assert rendered.findtext("REFERENCE") == "INV-1"
    assert rendered.findtext("PARTYLEDGERNAME") == "Supplier Ledger"
    assert rendered.findtext("PARTYGSTIN") == SUPPLIER_GSTIN
    assert rendered.findtext("PLACEOFSUPPLY") == "Haryana"
    assert rendered.findtext("PERSISTEDVIEW") == "Invoice Voucher View"
    assert rendered.findtext("ISINVOICE") == "Yes"
    entries = rendered.findall("LEDGERENTRIES.LIST")
    assert not rendered.findall("ALLLEDGERENTRIES.LIST")
    assert [entry.findtext("LEDGERNAME") for entry in entries] == [
        "Supplier Ledger", "Purchases 18%", "Input CGST", "Input SGST",
    ]
    party = entries[0]
    assert party.findtext("ISPARTYLEDGER") == "Yes"
    assert party.findtext("ISDEEMEDPOSITIVE") == "No"
    assert party.findtext("BILLALLOCATIONS.LIST/NAME") == "INV-1"
    assert party.findtext("BILLALLOCATIONS.LIST/BILLTYPE") == "New Ref"
    assert party.findtext("BILLALLOCATIONS.LIST/AMOUNT") == "118.00"
    assert entries[1].findtext("ISPARTYLEDGER") == "No"
    assert ledger_values(rendered) == {
        "Purchases 18%": D("-100.00"),
        "Input CGST": D("-9.00"),
        "Input SGST": D("-9.00"),
        "Supplier Ledger": D("118.00"),
    }
    assert voucher_signed_balance(common) == D("0.00")
    assert sum(ledger_values(rendered).values()) == D("0.00")
    assert rendered.find("VCHKEY") is None


def test_single_igst_purchase_uses_only_igst_tax_ledger():
    candidate = canonical_candidate(source_row(
        igst="18", cgst="0", sgst="0", place="Haryana",
        supplier_gstin=INTERSTATE_SUPPLIER_GSTIN,
    ))
    validation = validated(candidate)
    common = voucher(candidate, validation)
    _, rendered = parsed_vouchers(generate_tally_xml(
        [common], tally_version="7.1", company_name=common.company_name,
    ))
    values = ledger_values(rendered[0])
    assert values == {
        "Purchases 18%": D("-100.00"), "Input IGST": D("-18.00"),
        "Supplier Ledger": D("118.00"),
    }


def test_multi_rate_purchase_has_ordered_allocations_and_aggregated_tax_ledgers():
    candidate = canonical_candidate(
        source_row(rate="12", taxable="200", cgst="12", sgst="12"),
        source_row(row_number=3, rate="5", taxable="100", cgst="2.5", sgst="2.5"),
    )
    validation = validated(candidate, version="7.1")
    common = voucher(candidate, validation)
    _, rendered = parsed_vouchers(generate_tally_xml(
        [common], tally_version="7.1", company_name=common.company_name,
    ))
    entries = rendered[0].findall("LEDGERENTRIES.LIST")
    assert [entry.findtext("LEDGERNAME") for entry in entries] == [
        "Supplier Ledger", "Purchases 5%", "Purchases 12%", "Input CGST", "Input SGST",
    ]
    assert [entry.findtext("GSTOVRDNRATE") for entry in entries[1:3]] == ["5.000", "12.000"]
    assert [entry.findtext("AMOUNT") for entry in entries[1:3]] == ["-100.00", "-200.00"]
    assert ledger_values(rendered[0])["Supplier Ledger"] == D("329.00")
    assert sum(D(entry.findtext("AMOUNT")) for entry in entries) == D("0.00")


@pytest.mark.parametrize("version", ["7.0", "7.1"])
def test_low_level_renderer_keeps_generic_70_and_supported_71_paths(version):
    candidate = canonical_candidate()
    validation = validated(candidate, version="7.1")
    common = replace(voucher(candidate, validation), tally_version=version)
    root, rendered = parsed_vouchers(generate_tally_xml(
        [common], tally_version=version, company_name=common.company_name,
    ))
    assert root.findtext("./HEADER/VERSION") == "1"
    assert len(rendered) == 1


def test_multiple_vouchers_and_manifest_are_deterministically_ordered_and_hashed():
    run_id = uuid4()
    first_candidate = canonical_candidate(source_row(number="INV-2", day=2))
    second_candidate = canonical_candidate(source_row(number="INV-1", day=1))
    first_validation = validated(first_candidate)
    second_validation = replace(
        validated(second_candidate), resolved=first_validation.resolved,
    )
    vouchers = [
        voucher(first_candidate, first_validation, run_id),
        voucher(second_candidate, second_validation, run_id),
    ]
    batch_one, batch_two = uuid4(), uuid4()
    artifact_one = generate_tally_xml_artifact(
        list(reversed(vouchers)), batch_id=batch_one,
        profile_id=first_validation.resolved.profile_id,
        profile_version=1, reconciliation_run_id=run_id,
    )
    artifact_two = generate_tally_xml_artifact(
        vouchers, batch_id=batch_two,
        profile_id=first_validation.resolved.profile_id,
        profile_version=1, reconciliation_run_id=run_id,
    )
    _, rendered = parsed_vouchers(artifact_one.xml_bytes)
    expected = sorted(str(item.remote_id) for item in vouchers)
    assert [item.attrib["REMOTEID"] for item in rendered] == expected
    assert artifact_one.xml_bytes == artifact_two.xml_bytes
    assert artifact_one.content_sha256 == artifact_two.content_sha256
    assert artifact_one.content_sha256 == hashlib.sha256(artifact_one.xml_bytes).hexdigest()
    assert artifact_one.manifest.batch_id == batch_one
    assert artifact_one.manifest.voucher_count == 2


def test_xml_library_escapes_text_and_preserves_unicode():
    candidate = canonical_candidate()
    validation = validated(
        candidate, company="A & B <Books>", party_ledger='आपूर्तिकर्ता & "Ledger"',
    )
    common = voucher(candidate, validation)
    xml_bytes = generate_tally_xml(
        [common], tally_version="7.1", company_name=common.company_name,
    )
    decoded = xml_bytes.decode("utf-8")
    root, rendered = parsed_vouchers(xml_bytes)
    assert "A &amp; B &lt;Books&gt;" in decoded
    assert root.findtext("./BODY/DESC/STATICVARIABLES/SVCURRENTCOMPANY") == "A & B <Books>"
    assert rendered[0].findtext("PARTYLEDGERNAME") == 'आपूर्तिकर्ता & "Ledger"'


def test_invalid_control_characters_fail_instead_of_emitting_malformed_xml():
    candidate = canonical_candidate()
    validation = validated(candidate, party_ledger="Bad\x01Ledger")
    with pytest.raises(TallyVoucherExportError, match="invalid XML character"):
        voucher(candidate, validation)


def test_blocked_or_unresolved_candidate_cannot_reach_generation():
    candidate = canonical_candidate()
    validation = validated(candidate)
    blocked = replace(
        validation, eligible=False,
        blockers=(TallyValidationIssue("TEST_BLOCK", "blocked"),),
    )
    with pytest.raises(TallyVoucherExportError, match="Blocked candidates"):
        voucher(candidate, blocked)

    unresolved = replace(
        validation,
        resolved=replace(validation.resolved, purchase_ledgers=()),
    )
    with pytest.raises(TallyVoucherExportError, match="missing for GST rate"):
        voucher(candidate, unresolved)


def test_common_voucher_builder_rejects_fabricated_70_eligibility():
    candidate = canonical_candidate()
    validation = validated(candidate)
    fabricated = replace(
        validation,
        resolved=replace(validation.resolved, tally_version="7.0"),
    )
    with pytest.raises(TallyVoucherExportError, match="supports TallyPrime 7.1 only"):
        voucher(candidate, fabricated)


def test_unsupported_version_and_batch_limit_are_rejected():
    candidate = canonical_candidate()
    validation = validated(candidate)
    common = voucher(candidate, validation)
    with pytest.raises(TallyVoucherExportError, match="not recognized by the XML renderer"):
        generate_tally_xml([common], tally_version="6.6", company_name=common.company_name)
    with pytest.raises(TallyVoucherExportError, match="1-voucher limit"):
        generate_tally_xml(
            [common, common], tally_version="7.1", company_name=common.company_name,
            maximum_vouchers=1,
        )


def test_parser_safe_anonymized_tally_fixture_is_available():
    fixture_root = Path(__file__).parent / "fixtures" / "tallyprime"
    parseable = sorted((fixture_root / "synthetic").rglob("*.sanitized.xml"))
    if not parseable:
        pytest.skip("No parser-safe anonymized Tally structural fixture is available")
    for fixture in parseable:
        ET.parse(fixture)
