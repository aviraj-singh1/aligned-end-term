import pytest
from fastapi import HTTPException

from backend.app.db.models import GstrMergeMode, GstrMergeRun, GstrMergeStatus
from backend.app.services import gstr_merge_run_service as service


def test_mode_and_status_enums_are_coarse_and_stable():
    assert {item.value for item in GstrMergeMode} == {"QUARTERLY", "MONTHLY"}
    assert {item.value for item in GstrMergeStatus} == {
        "UPLOADING", "QUEUED", "PROCESSING", "COMPLETED", "FAILED", "EXPIRED", "DELETED",
    }


def test_state_machine_has_no_worker_stage_statuses_and_deleted_is_terminal():
    assert "VALIDATING" not in service.VALID_TRANSITIONS
    assert service.VALID_TRANSITIONS["DELETED"] == set()
    assert service.VALID_TRANSITIONS["UPLOADING"] == {"QUEUED", "FAILED", "EXPIRED", "DELETED"}
    assert service.VALID_TRANSITIONS["COMPLETED"] == {"EXPIRED", "DELETED"}


@pytest.mark.parametrize("value,expected", [("quarterly", "QUARTERLY"), (" MONTHLY ", "MONTHLY")])
def test_mode_normalization(value, expected):
    assert service._mode(value) == expected


@pytest.mark.parametrize("value", ["WEEKLY", "", "Q1"])
def test_invalid_modes_are_rejected(value):
    with pytest.raises(HTTPException) as exc:
        service._mode(value)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("value", ["2025/26", "2025-27", "25-26", "2025-2A"])
def test_invalid_financial_years_are_rejected(value):
    with pytest.raises(HTTPException) as exc:
        service._financial_year(value)
    assert exc.value.status_code == 422


def test_financial_year_normalization_accepts_consecutive_years():
    assert service._financial_year(" 2025-26 ") == "2025-26"
    assert service._financial_year("2099-00") == "2099-00"


def test_metadata_guard_accepts_summary_metadata_but_rejects_rows_and_secrets():
    safe = {"categories": {"B2B": 4}, "warning_count": 0}
    assert service._metadata_object(safe, "Summary") == safe
    for unsafe in ({"rows": []}, {"nested": {"signed_url": "https://example.invalid"}}):
        with pytest.raises(HTTPException) as exc:
            service._metadata_object(unsafe, "Summary")
        assert exc.value.status_code == 422


def test_metadata_guard_enforces_encoded_byte_limit_and_json_values():
    with pytest.raises(HTTPException):
        service._metadata_object({"metadata": "x" * 66_000}, "Manifest")
    with pytest.raises(HTTPException):
        service._metadata_object({"value": float("nan")}, "Manifest")


def test_model_registers_expected_constraints_and_indexes():
    table = GstrMergeRun.__table__
    constraint_names = {constraint.name for constraint in table.constraints}
    index_names = {index.name for index in table.indexes}
    assert {
        "ck_gstr_merge_runs_mode",
        "ck_gstr_merge_runs_status",
        "ck_gstr_merge_runs_financial_year",
        "ck_gstr_merge_runs_input_manifest_bounded_object",
        "ck_gstr_merge_runs_result_summary_bounded_object",
        "fk_gstr_merge_runs_workspace_id_client_id_clients",
        "fk_gstr_merge_runs_workspace_id_client_id_client_gstin_id_client_gstins",
    } <= constraint_names
    assert {
        "ix_gstr_merge_runs_workspace_client_created",
        "ix_gstr_merge_runs_queue",
        "ix_gstr_merge_runs_created_by",
    } == index_names
