"""Deterministic manual-validation artifacts using the production Phase 1-4 path.

Run with ``python -m backend.tests.tallyprime_sample_generator``. Generated
files are test evidence/candidates only and are not proof of Tally acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.app.models.records import RecordSource, SheetCategory
from backend.app.models.tally_export import (
    ReverseChargeStatus,
    TallyExportCandidate,
    TallyExportSourceIdentity,
    TallyExportTransactionType,
    TallyRateAllocation,
)
from backend.app.models.tally_export_validation import (
    TallyPartyMappingSnapshot,
    TallyProfileSnapshot,
    TallyPurchaseMappingSnapshot,
)
from backend.app.services.tally_export_validation_service import validate_tally_candidate
from backend.app.services.tally_voucher_exporter import (
    build_common_purchase_voucher,
    generate_tally_xml,
)
from backend.tests.tallyprime_fixture_support import sanitize_real_tally_xml


D = Decimal
CLIENT_GSTIN = "06AAAAA0000A1Z5"
COMPANY = "Synthetic Tally Test"
PARTY_LEDGER = "Synthetic Haryana Supplier"
INTERSTATE_PARTY_LEDGER = "Synthetic Punjab Supplier"
INTRASTATE_SUPPLIER_GSTIN = "06AAAAA0000A1Z5"
INTERSTATE_SUPPLIER_GSTIN = "03DEMOX1234A1Z5"


@dataclass(frozen=True)
class ManualSample:
    version: str
    scenario: str
    filename: str
    invoice_number: str
    supplier_gstin: str
    party_ledger: str
    place_of_supply: str
    allocations: tuple[TallyRateAllocation, ...]


def validation_samples() -> tuple[ManualSample, ...]:
    samples = []
    for version, label in (("7.0", "70"), ("7.1", "71")):
        samples.extend((
            ManualSample(
                version, "IGST Purchase", f"ALIGNED-{label}-IGST-001.xml",
                f"ALIGNED-{label}-IGST-001", INTERSTATE_SUPPLIER_GSTIN,
                INTERSTATE_PARTY_LEDGER, "Haryana",
                (TallyRateAllocation(D("18"), D("240"), D("43.20"), D("0"), D("0"), D("0")),),
            ),
            ManualSample(
                version, "CGST + SGST Purchase", f"ALIGNED-{label}-CGST-001.xml",
                f"ALIGNED-{label}-CGST-001", INTRASTATE_SUPPLIER_GSTIN,
                PARTY_LEDGER, "Haryana",
                (TallyRateAllocation(D("18"), D("200"), D("0"), D("18"), D("18"), D("0")),),
            ),
            ManualSample(
                version, "Multi-rate Purchase", f"ALIGNED-{label}-MULTI-001.xml",
                f"ALIGNED-{label}-MULTI-001", INTRASTATE_SUPPLIER_GSTIN,
                PARTY_LEDGER, "Haryana",
                (
                    TallyRateAllocation(D("5"), D("300"), D("0"), D("7.50"), D("7.50"), D("0")),
                    TallyRateAllocation(D("12"), D("400"), D("0"), D("24"), D("24"), D("0")),
                ),
            ),
        ))
    return tuple(samples)


def comparison_sample() -> ManualSample:
    return ManualSample(
        "7.1", "Synthetic shared-renderer comparison", "SYNTH-CGST-001.aligned-shared.xml",
        "SYNTH-CGST-001", INTRASTATE_SUPPLIER_GSTIN, PARTY_LEDGER, "Haryana",
        (TallyRateAllocation(D("18"), D("200"), D("0"), D("18"), D("18"), D("0")),),
    )


def build_sample_xml(sample: ManualSample) -> bytes:
    allocations = sample.allocations
    taxable = sum((row.taxable_value for row in allocations), D("0"))
    igst = sum((row.igst for row in allocations), D("0"))
    cgst = sum((row.cgst for row in allocations), D("0"))
    sgst = sum((row.sgst for row in allocations), D("0"))
    source_rows = tuple(
        TallyExportSourceIdentity(RecordSource.GST, "manual-validation", index, SheetCategory.B2B)
        for index, _ in enumerate(allocations, start=2)
    )
    candidate = TallyExportCandidate(
        transaction_type=TallyExportTransactionType.PURCHASE_INVOICE,
        supplier_gstin=sample.supplier_gstin,
        supplier_gstin_valid=True,
        supplier_name=sample.party_ledger,
        document_number=sample.invoice_number,
        document_date=date(2026, 1, 15),
        original_invoice_number=sample.invoice_number,
        taxable_value=taxable,
        igst=igst,
        cgst=cgst,
        sgst=sgst,
        cess=D("0"),
        cess_present=False,
        invoice_total=taxable + igst + cgst + sgst,
        gst_rates=tuple(row.gst_rate for row in allocations),
        rate_allocations=allocations,
        gst_rate_missing=False,
        place_of_supply=sample.place_of_supply,
        place_of_supply_values=(sample.place_of_supply,),
        place_of_supply_missing=False,
        place_of_supply_conflicting=False,
        reverse_charge_status=ReverseChargeStatus.NO,
        reverse_charge_values=("No",),
        is_amendment=False,
        amendment_original_number="",
        amendment_original_date=None,
        is_cancelled=False,
        cancellation_evidence=(),
        source_rows=source_rows,
        financial_parse_issues=(),
    )
    profile = TallyProfileSnapshot(
        id=uuid5(NAMESPACE_URL, f"aligned-manual-profile:{sample.version}"),
        version=1,
        status="ACTIVE",
        company_name=COMPANY,
        tally_version="7.1",
        purchase_voucher_type="Purchase",
        igst_ledger_name="Input IGST",
        cgst_ledger_name="Input CGST",
        sgst_ledger_name="Input SGST",
        numbering_strategy="TALLY_AUTO",
    )
    party = TallyPartyMappingSnapshot(
        status="ACTIVE",
        supplier_gstin=sample.supplier_gstin,
        last_source_supplier_name=sample.party_ledger,
        tally_party_ledger_name=sample.party_ledger,
    )
    purchase_mappings = tuple(
        TallyPurchaseMappingSnapshot(
            status="ACTIVE",
            transaction_kind="purchase_invoice",
            gst_rate=row.gst_rate,
            tally_purchase_ledger_name=f"Purchase {format(row.gst_rate, 'f')}%",
        )
        for row in allocations
    )
    validation = validate_tally_candidate(
        candidate,
        client_gstin=CLIENT_GSTIN,
        profile=profile,
        party_mapping=party,
        purchase_mappings=purchase_mappings,
    )
    if not validation.eligible:
        raise ValueError(f"Sample is not Phase 3 eligible: {[item.code for item in validation.blockers]}")
    voucher = build_common_purchase_voucher(
        candidate,
        validation,
        client_gstin=CLIENT_GSTIN,
        reconciliation_run_id=uuid5(NAMESPACE_URL, f"aligned-manual-run:{sample.invoice_number}"),
    )
    # Retain deterministic 7.0 XML-dialect regression artifacts without
    # presenting 7.0 as a Phase 3-eligible or supported V1 product target.
    voucher = replace(voucher, tally_version=sample.version)
    return generate_tally_xml(
        [voucher], tally_version=sample.version, company_name=COMPANY,
    )


def generate_manual_validation_artifacts(root: Path) -> None:
    raw_path = root / "synthetic" / "unclassified" / "SYNTH-CGST-001.raw.xml"
    sanitized_path = raw_path.with_name("SYNTH-CGST-001.sanitized.xml")
    sanitized_path.write_bytes(sanitize_real_tally_xml(raw_path.read_bytes())[0])
    sanitized_path.with_name("SYNTH-CGST-001.aligned-shared.xml").write_bytes(
        build_sample_xml(comparison_sample())
    )
    for sample in validation_samples():
        destination = root / sample.version / "generated" / sample.filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(build_sample_xml(sample))


if __name__ == "__main__":
    generate_manual_validation_artifacts(Path(__file__).parent / "fixtures" / "tallyprime")
