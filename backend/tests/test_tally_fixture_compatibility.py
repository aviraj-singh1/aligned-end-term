"""Anonymized structural-fixture tests; passing does not prove Tally compatibility."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from backend.tests.tallyprime_fixture_support import (
    is_xml_10_character,
    sanitize_real_tally_xml,
    semantic_voucher,
)
from backend.tests.tallyprime_sample_generator import (
    build_sample_xml,
    comparison_sample,
    generate_manual_validation_artifacts,
    validation_samples,
)


ROOT = Path(__file__).parent / "fixtures" / "tallyprime"
RAW = ROOT / "synthetic" / "unclassified" / "SYNTH-CGST-001.raw.xml"
SANITIZED = ROOT / "synthetic" / "unclassified" / "SYNTH-CGST-001.sanitized.xml"
ALIGNED = ROOT / "synthetic" / "unclassified" / "SYNTH-CGST-001.aligned-shared.xml"
MANIFEST = ROOT / "manual-validation.synthetic.json"
RAW_SHA256 = "ffa0fbec155bad6067c5538d7edffd0547918b68914a3b85f301636661a5f379"


def test_anonymized_raw_fixture_is_byte_stable_and_intentionally_not_strict_xml():
    raw = RAW.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RAW_SHA256
    assert raw.startswith(b"\xff\xfe")
    with pytest.raises(ET.ParseError):
        ET.fromstring(raw)


def test_sanitized_fixture_is_exactly_reproducible_and_parser_safe():
    sanitized, report = sanitize_real_tally_xml(RAW.read_bytes())
    assert sanitized == SANITIZED.read_bytes()
    assert report.source_encoding == "UTF-16 with BOM"
    assert report.numeric_references_removed == ((4, 16),)
    assert report.literal_controls_replaced == ((5, 4),)
    root = ET.fromstring(sanitized)
    assert root.tag == "ENVELOPE"
    decoded = sanitized.decode("utf-16")
    assert "&#4;" not in decoded
    assert "\x05" not in decoded
    assert decoded.count("\ufffd") == 4


def test_anonymized_fixture_preserves_purchase_accounting_polarity_and_bill_reference():
    real = semantic_voucher(SANITIZED.read_bytes())
    assert real.company_name == "Synthetic Tally Test"
    assert real.date == real.reference_date == "20260115"
    assert real.voucher_type == "Purchase"
    assert real.reference == "SYNTH-CGST-001"
    assert real.party_gstin == "06AAAAA0000A1Z5"
    assert real.place_of_supply == "Haryana"
    assert real.party_ledger == "Synthetic Haryana Supplier"
    assert real.attributes["ACTION"] == "Create"
    assert real.attributes["OBJVIEW"] == "Invoice Voucher View"
    assert real.persisted_view == "Invoice Voucher View"
    assert real.is_invoice == "Yes"
    assert real.ledger_container == "LEDGERENTRIES.LIST"
    assert [(row.name, str(row.amount), row.is_deemed_positive) for row in real.ledgers] == [
        ("Synthetic Haryana Supplier", "236.00", "No"),
        ("Purchase 18%", "-200.00", "Yes"),
        ("Input CGST", "-18.00", "Yes"),
        ("Input SGST", "-18.00", "Yes"),
    ]
    assert real.ledgers[0].is_party_ledger == "Yes"
    assert real.ledgers[0].bill_reference == "SYNTH-CGST-001"
    assert real.ledgers[0].bill_type == "New Ref"
    assert real.ledgers[1].gst_rate_details[:3] == (
        ("CGST", "9"), ("SGST/UTGST", "9"), ("IGST", "18"),
    )
    assert sum((row.amount for row in real.ledgers), start=real.ledgers[0].amount * 0) == 0


def test_aligned_equivalent_matches_invoice_view_ledger_semantics():
    assert build_sample_xml(comparison_sample()) == ALIGNED.read_bytes()
    real = semantic_voucher(SANITIZED.read_bytes())
    aligned = semantic_voucher(ALIGNED.read_bytes())
    for field in (
        "company_name", "date", "reference_date", "voucher_type", "reference",
        "party_gstin", "place_of_supply", "party_ledger", "is_invoice",
    ):
        assert getattr(aligned, field) == getattr(real, field)
    assert [(row.name, row.amount, row.is_deemed_positive) for row in aligned.ledgers] == [
        ("Synthetic Haryana Supplier", real.ledgers[0].amount, "No"),
        ("Purchase 18%", real.ledgers[1].amount, "Yes"),
        ("Input CGST", real.ledgers[2].amount, "Yes"),
        ("Input SGST", real.ledgers[3].amount, "Yes"),
    ]
    assert aligned.envelope_style == "DESC/DATA"
    assert real.envelope_style == "IMPORTDATA/REQUESTDATA"
    assert aligned.ledger_container == real.ledger_container == "LEDGERENTRIES.LIST"
    assert aligned.persisted_view == real.persisted_view == "Invoice Voucher View"
    assert aligned.attributes["OBJVIEW"] == real.attributes["OBJVIEW"]
    assert aligned.ledgers[0].is_party_ledger == "Yes"
    assert aligned.ledgers[0].bill_reference == aligned.reference
    assert aligned.ledgers[0].bill_type == "New Ref"
    assert aligned.narration.startswith("Aligned reconciliation ")


def test_aligned_output_contains_no_invalid_xml_10_characters():
    output = ALIGNED.read_bytes()
    ET.fromstring(output)
    decoded = output.decode("utf-8")
    assert all(is_xml_10_character(ord(character)) for character in decoded)


def test_manual_validation_artifacts_are_deterministic(tmp_path):
    target = tmp_path / "tallyprime"
    raw_target = target / "synthetic" / "unclassified" / RAW.name
    raw_target.parent.mkdir(parents=True)
    raw_target.write_bytes(RAW.read_bytes())
    generate_manual_validation_artifacts(target)
    expected = [
        ROOT / sample.version / "generated" / sample.filename
        for sample in validation_samples()
    ] + [SANITIZED, ALIGNED]
    for committed in expected:
        relative = committed.relative_to(ROOT)
        assert (target / relative).read_bytes() == committed.read_bytes()


def test_synthetic_validation_matrix_records_71_support_and_70_out_of_scope():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["compatibility_claim"] == (
        "TALLYPRIME_7_1_PURCHASE_SCENARIOS_MANUALLY_VALIDATED"
    )
    assert manifest["raw_fixture"]["sha256"] == RAW_SHA256
    out_of_scope = [row for row in manifest["results"] if row["import_result"] == "OUT_OF_SCOPE"]
    passed = [row for row in manifest["results"] if row["import_result"] == "SUCCESS"]
    assert len(out_of_scope) == len(passed) == 3
    assert {(row["tally_version"], row["scenario"]) for row in out_of_scope} == {
        ("7.0", scenario)
        for scenario in ("IGST Purchase", "CGST + SGST Purchase", "Multi-rate Purchase")
    }
    assert {(row["tally_version"], row["scenario"]) for row in passed} == {
        ("7.1", scenario)
        for scenario in ("IGST Purchase", "CGST + SGST Purchase", "Multi-rate Purchase")
    }
    expected_behaviors = {
        "party_account", "purchase_ledgers", "tax_ledgers", "totals",
        "supplier_reference", "day_book_party", "day_book_credit_total",
    }
    assert all(not row["exact_version_verified"] for row in out_of_scope)
    assert all(row["support_status"] == "NOT_SUPPORTED_V1" for row in out_of_scope)
    assert all(row["exact_version_verified"] for row in passed)
    assert all(row["semantic_result"] == "PASS" for row in passed)
    assert all(row["evidence_scope"] == "PRIVATE_EQUIVALENT_MANUAL_IMPORT" for row in passed)
    assert all(set(row["verified_behaviors"]) == expected_behaviors for row in passed)
