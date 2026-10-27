from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.app.models.records import (
    FinancialParseIssue, ParsedInvoiceRecord, RecordSource, SheetCategory, VoucherType,
)
from backend.app.models.tally_export import ReverseChargeStatus, TallyExportTransactionType
from backend.app.models.tally_export_validation import (
    TallyBlockerCode,
    TallyCandidateState,
    TallyPartyMappingSnapshot,
    TallyPriorExportSnapshot,
    TallyProfileSnapshot,
    TallyPurchaseMappingSnapshot,
    TallyWarningCode,
)
from backend.app.services.normalizer import normalize_invoice_number
from backend.app.services.reconciliation_engine import aggregate_gst_invoices
from backend.app.services.tally_export_contract import purchase_candidate, supplier_credit_note_candidate
from backend.app.services.tally_export_configuration_service import _tally_version
from backend.app.services.tally_export_validation_service import (
    deterministic_remote_id,
    transaction_fingerprint,
    validate_tally_candidate,
)
from backend.app.services.notes_reconciliation_engine import aggregate_gst_credit_notes


D = Decimal
CLIENT_GSTIN = "06AAAAA0000A1Z5"
SUPPLIER_GSTIN = "06DEMOX1234A1Z5"
INTERSTATE_SUPPLIER_GSTIN = "27AAAAA0000A1Z5"


def row(*, number="INV-1", row_number=2, rate="18", taxable="100", igst="0",
        cgst="9", sgst="9", cess="0", place="Haryana", reverse_charge="No",
        supplier_gstin=SUPPLIER_GSTIN):
    return ParsedInvoiceRecord(
        source=RecordSource.GST, source_sheet="b2b", source_row=row_number,
        sheet_category=SheetCategory.B2B, supplier_name="Supplier Private Limited",
        gstin_original=supplier_gstin, gstin_normalized=supplier_gstin, gstin_valid=True,
        invoice_number=normalize_invoice_number(number), invoice_date=date(2026, 4, 1),
        voucher_type=VoucherType.PURCHASE, taxable_value=D(taxable), igst=D(igst),
        cgst=D(cgst), sgst=D(sgst), cess=D(cess), cess_present=True,
        gst_rate=D(rate) if rate is not None else None, place_of_supply=place,
        reverse_charge=reverse_charge,
    )


def candidate(*rows):
    rows = rows or (row(),)
    return purchase_candidate(aggregate_gst_invoices(list(rows))[0])


def profile(**changes):
    values = dict(
        id=uuid4(), version=1, status="ACTIVE", company_name="Client Books",
        tally_version="7.1", purchase_voucher_type="Purchase",
        igst_ledger_name="Input IGST", cgst_ledger_name="Input CGST",
        sgst_ledger_name="Input SGST", numbering_strategy="TALLY_AUTO",
    )
    values.update(changes)
    return TallyProfileSnapshot(**values)


def party(**changes):
    values = dict(
        status="ACTIVE", supplier_gstin=SUPPLIER_GSTIN,
        last_source_supplier_name="Supplier Private Limited",
        tally_party_ledger_name="Supplier Pvt Ltd",
    )
    values.update(changes)
    return TallyPartyMappingSnapshot(**values)


def mapping(rate="18", **changes):
    values = dict(
        status="ACTIVE", transaction_kind="purchase_invoice", gst_rate=D(rate),
        tally_purchase_ledger_name=f"Purchases {rate}%",
    )
    values.update(changes)
    return TallyPurchaseMappingSnapshot(**values)


def validate(value, **changes):
    options = dict(
        client_gstin=CLIENT_GSTIN, profile=profile(), party_mapping=party(),
        purchase_mappings=(mapping(),),
    )
    options.update(changes)
    return validate_tally_candidate(value, **options)


def codes(issues):
    return {issue.code for issue in issues}


def test_v1_configuration_accepts_only_tallyprime_71():
    assert _tally_version("7.1") == "7.1"
    with pytest.raises(HTTPException) as unsupported:
        _tally_version("7.0")
    assert unsupported.value.status_code == 422
    assert unsupported.value.detail == "Tally Export V1 supports TallyPrime 7.1 only"


def test_valid_candidate_is_eligible_and_resolves_exact_configuration():
    result = validate(candidate())

    assert result.eligible
    assert not result.blockers
    assert result.candidate_id == f"purchase_invoice:{result.remote_id}"
    assert result.resolved.party_ledger_name == "Supplier Pvt Ltd"
    assert result.resolved.purchase_ledgers == ((D("18"), "Purchases 18%"),)
    assert TallyWarningCode.SAVED_PARTY_MAPPING_REUSED.value in codes(result.warnings)
    assert TallyWarningCode.INVOICE_TOTAL_MISSING_COMPONENTS_BALANCED.value in codes(result.warnings)


def test_remote_id_is_normalization_stable_and_fingerprint_tracks_financials():
    first = candidate(row(number=" INV/1 "))
    second = candidate(row(number="inv-1", taxable="100.00", cgst="9.00", sgst="9.0"))
    changed = candidate(row(number="INV_1", taxable="101", cgst="9", sgst="9"))

    assert deterministic_remote_id(first, CLIENT_GSTIN.lower()) == deterministic_remote_id(second, CLIENT_GSTIN)
    assert transaction_fingerprint(first, CLIENT_GSTIN) == transaction_fingerprint(second, CLIENT_GSTIN)
    assert transaction_fingerprint(first, CLIENT_GSTIN) != transaction_fingerprint(changed, CLIENT_GSTIN)


def test_exact_previous_export_warns_but_changed_fingerprint_blocks():
    value = candidate()
    remote = deterministic_remote_id(value, CLIENT_GSTIN)
    fingerprint = transaction_fingerprint(value, CLIENT_GSTIN)

    exact = validate(value, prior_exports=(TallyPriorExportSnapshot(remote, fingerprint),))
    drift = validate(value, prior_exports=(TallyPriorExportSnapshot(remote, "f" * 64),))

    assert exact.eligible
    assert TallyWarningCode.PREVIOUSLY_EXPORTED.value in codes(exact.warnings)
    assert not drift.eligible
    assert TallyBlockerCode.REMOTE_ID_FINGERPRINT_CONFLICT.value in codes(drift.blockers)


def test_multi_rate_allocations_balance_and_each_rate_requires_a_mapping():
    value = candidate(
        row(rate="5", taxable="100", cgst="2.5", sgst="2.5"),
        row(row_number=3, rate="12", taxable="200", cgst="12", sgst="12"),
    )
    assert [(item.gst_rate, item.taxable_value) for item in value.rate_allocations] == [
        (D("5"), D("100")), (D("12"), D("200")),
    ]

    valid = validate(value, purchase_mappings=(mapping("5"), mapping("12")))
    missing = validate(value, purchase_mappings=(mapping("5"),))

    assert valid.eligible
    assert TallyBlockerCode.MISSING_PURCHASE_LEDGER_MAPPING.value in codes(missing.blockers)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"supplier_gstin_valid": False}, TallyBlockerCode.INVALID_SUPPLIER_GSTIN),
        ({"document_number": ""}, TallyBlockerCode.MISSING_DOCUMENT_NUMBER),
        ({"document_date": None}, TallyBlockerCode.MISSING_DOCUMENT_DATE),
        ({"reverse_charge_status": ReverseChargeStatus.YES}, TallyBlockerCode.REVERSE_CHARGE_UNSUPPORTED),
        ({"reverse_charge_status": ReverseChargeStatus.UNKNOWN}, TallyBlockerCode.REVERSE_CHARGE_UNRESOLVED),
        ({"cess": D("1")}, TallyBlockerCode.CESS_UNSUPPORTED),
        ({"is_amendment": True}, TallyBlockerCode.AMENDMENT_UNSUPPORTED),
        ({"is_cancelled": True}, TallyBlockerCode.CANCELLATION_UNSUPPORTED),
        ({"taxable_value": D("0")}, TallyBlockerCode.NON_POSITIVE_TAXABLE_VALUE),
        ({"igst": D("-1")}, TallyBlockerCode.NEGATIVE_TAX_AMOUNT),
        ({"place_of_supply_missing": True, "place_of_supply": ""}, TallyBlockerCode.MISSING_PLACE_OF_SUPPLY),
        ({"place_of_supply_conflicting": True}, TallyBlockerCode.CONFLICTING_PLACE_OF_SUPPLY),
    ],
)
def test_candidate_blocker_codes_are_stable(change, expected):
    result = validate(replace(candidate(), **change))
    assert expected.value in codes(result.blockers)


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"profile": None}, TallyBlockerCode.MISSING_TALLY_PROFILE),
        ({"profile": profile(status="INACTIVE")}, TallyBlockerCode.INACTIVE_TALLY_PROFILE),
        ({"profile": profile(tally_version="7.0")}, TallyBlockerCode.UNSUPPORTED_TALLY_VERSION),
        ({"profile": profile(tally_version="6.6")}, TallyBlockerCode.UNSUPPORTED_TALLY_VERSION),
        ({"profile": profile(numbering_strategy="ALIGNED_GENERATED")}, TallyBlockerCode.UNSUPPORTED_NUMBERING_STRATEGY),
        ({"party_mapping": None}, TallyBlockerCode.MISSING_PARTY_LEDGER_MAPPING),
        ({"party_mapping": party(status="INACTIVE")}, TallyBlockerCode.INACTIVE_PARTY_LEDGER_MAPPING),
        ({"purchase_mappings": ()}, TallyBlockerCode.MISSING_PURCHASE_LEDGER_MAPPING),
        ({"purchase_mappings": (mapping(status="INACTIVE"),)}, TallyBlockerCode.INACTIVE_PURCHASE_LEDGER_MAPPING),
    ],
)
def test_configuration_blockers(options, expected):
    assert expected.value in codes(validate(candidate(), **options).blockers)


def test_tax_structure_place_of_supply_and_balancing_blockers():
    mixed = candidate(row(igst="18", cgst="9", sgst="9"))
    incomplete = candidate(row(cgst="9", sgst="0"))
    unequal = candidate(row(cgst="9", sgst="8"))
    interstate_local_tax = candidate(row(place="Punjab"))
    unbalanced = replace(candidate(), taxable_value=D("101"))

    mixed_codes = codes(validate(mixed).blockers)
    assert TallyBlockerCode.MIXED_IGST_AND_CGST_SGST.value in mixed_codes
    assert TallyBlockerCode.INCONSISTENT_TAX_STRUCTURE.value in mixed_codes
    assert TallyBlockerCode.INCOMPLETE_CGST_SGST.value in codes(validate(incomplete).blockers)
    assert TallyBlockerCode.UNEQUAL_CGST_SGST.value in codes(validate(unequal).blockers)
    assert TallyBlockerCode.PLACE_OF_SUPPLY_TAX_CONFLICT.value in codes(validate(interstate_local_tax).blockers)
    assert TallyBlockerCode.DEBIT_CREDIT_IMBALANCE.value in codes(validate(unbalanced).blockers)


def test_remaining_required_source_and_configuration_blockers():
    parsed = candidate()
    parsed = replace(parsed, financial_parse_issues=(FinancialParseIssue("taxable_value", "x", "invalid"),))
    assert TallyBlockerCode.UNRESOLVED_FINANCIAL_PARSE_ISSUES.value in codes(validate(parsed).blockers)

    unresolved_rate = candidate(row(rate=None))
    assert TallyBlockerCode.UNRESOLVED_GST_RATE.value in codes(validate(unresolved_rate).blockers)

    invalid_place = replace(candidate(), place_of_supply="Unknown State")
    assert TallyBlockerCode.INVALID_PLACE_OF_SUPPLY.value in codes(validate(invalid_place).blockers)

    missing_tax_ledger = profile(cgst_ledger_name="")
    assert TallyBlockerCode.MISSING_TAX_LEDGER.value in codes(
        validate(candidate(), profile=missing_tax_ledger).blockers
    )


def test_interstate_igst_purchase_is_supported_when_configuration_balances():
    value = candidate(row(place="Punjab", igst="18", cgst="0", sgst="0"))
    assert validate(value).eligible


def test_supplier_state_determines_interstate_igst_not_client_state():
    value = candidate(row(
        supplier_gstin=INTERSTATE_SUPPLIER_GSTIN,
        place="Haryana",
        igst="18",
        cgst="0",
        sgst="0",
    ))

    result = validate(
        value,
        party_mapping=party(supplier_gstin=INTERSTATE_SUPPLIER_GSTIN),
    )

    assert result.eligible
    assert TallyBlockerCode.PLACE_OF_SUPPLY_TAX_CONFLICT.value not in codes(result.blockers)


def test_per_rate_tax_inconsistency_cannot_be_hidden_by_balanced_aggregate_totals():
    value = candidate(
        row(rate="5", cgst="3", sgst="2"),
        row(row_number=3, rate="12", cgst="2", sgst="3"),
    )
    result = validate(value, purchase_mappings=(mapping("5"), mapping("12")))
    assert TallyBlockerCode.UNEQUAL_CGST_SGST.value in codes(result.blockers)


def test_invoice_total_and_stale_consumed_non_current_states_block():
    source = row()
    source.total_invoice_value = D("999")
    source.total_invoice_value_present = True
    mismatch = validate(candidate(source))
    assert TallyBlockerCode.INVOICE_TOTAL_MISMATCH.value in codes(mismatch.blockers)

    expected = {
        TallyCandidateState.STALE: TallyBlockerCode.STALE_CANDIDATE,
        TallyCandidateState.CONSUMED: TallyBlockerCode.CONSUMED_CANDIDATE,
        TallyCandidateState.NON_CURRENT: TallyBlockerCode.NON_CURRENT_CANDIDATE,
    }
    for state, code in expected.items():
        assert code.value in codes(validate(candidate(), candidate_state=state).blockers)


def test_credit_notes_remain_explicitly_unsupported():
    source = row()
    source.sheet_category = SheetCategory.CDNR
    source.voucher_type = VoucherType.CREDIT_NOTE
    source.note_type = "Credit Note"
    source.note_number = "CN-1"
    source.note_date = date(2026, 4, 2)
    value = supplier_credit_note_candidate(aggregate_gst_credit_notes([source])[0])

    result = validate(value)
    assert value.transaction_type == TallyExportTransactionType.SUPPLIER_CREDIT_NOTE
    assert TallyBlockerCode.UNSUPPORTED_TRANSACTION_TYPE.value in codes(result.blockers)


def test_warning_codes_for_reused_mapping_name_changes_are_stable():
    result = validate(candidate(), party_mapping=party(
        last_source_supplier_name="Old Supplier Name", tally_party_ledger_name="Supplier Ledger"
    ))
    assert {
        TallyWarningCode.SAVED_PARTY_MAPPING_REUSED.value,
        TallyWarningCode.SUPPLIER_NAME_CHANGED.value,
        TallyWarningCode.DISPLAY_NAME_DIFFERS_FROM_MAPPING.value,
    }.issubset(codes(result.warnings))
