from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import asyncio

from fastapi import HTTPException
import pytest

from backend.app.services import gstr_merge_lifecycle_service as lifecycle
from backend.app.services import object_storage


class FakeStorageS3:
    def __init__(self):
        self.presigned = None
        self.head = None
        self.deleted = []
        self.error = None

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        if self.error:
            raise self.error
        self.presigned = (operation, Params, ExpiresIn)
        return "https://private.example/upload?signature=short-lived"

    def head_object(self, **kwargs):
        if self.error:
            raise self.error
        return self.head

    def delete_object(self, **kwargs):
        if self.error:
            raise self.error
        self.deleted.append(kwargs)


@pytest.fixture
def s3_storage(monkeypatch):
    fake = FakeStorageS3()
    settings = object_storage.S3Settings(
        "private-bucket", "ap-south-1", "aligned", None, "AES256"
    )
    limits = object_storage.GstrStorageSettings(25, 300, 600)
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(object_storage, "s3_settings", lambda: settings)
    monkeypatch.setattr(object_storage, "s3_client", lambda: fake)
    monkeypatch.setattr(object_storage, "gstr_storage_settings", lambda: limits)
    return fake


def test_server_generated_input_key_is_opaque_and_tenant_run_prefixed(s3_storage):
    workspace_id, client_id, run_id = uuid4(), uuid4(), uuid4()
    reference = object_storage.gstr_input_reference(
        workspace_id, client_id, run_id, "M04"
    )
    expected = (
        f"s3://private-bucket/aligned/workspaces/{workspace_id}/clients/{client_id}/"
        f"gstr-merges/{run_id}/inputs/M04/"
    )
    assert reference.startswith(expected)
    assert reference.endswith(".xlsx")
    assert "April" not in reference and ".." not in reference
    assert object_storage.gstr_reference_belongs_to_run(
        reference, workspace_id, client_id, run_id,
        category="inputs", period_code="M04",
    )


def test_cross_tenant_client_and_run_references_are_rejected(s3_storage):
    workspace_id, client_id, run_id = uuid4(), uuid4(), uuid4()
    reference = object_storage.gstr_input_reference(
        workspace_id, client_id, run_id, "Q1"
    )
    for wrong_workspace, wrong_client, wrong_run in (
        (uuid4(), client_id, run_id),
        (workspace_id, uuid4(), run_id),
        (workspace_id, client_id, uuid4()),
    ):
        assert not object_storage.gstr_reference_belongs_to_run(
            reference, wrong_workspace, wrong_client, wrong_run,
            category="inputs", period_code="Q1",
        )
    assert not object_storage.gstr_reference_belongs_to_run(
        reference.replace("/inputs/Q1/", "/inputs/Q1/../Q2/"),
        workspace_id, client_id, run_id, category="inputs", period_code="Q1",
    )


def test_private_put_target_is_exact_short_lived_and_contains_no_credentials(s3_storage):
    workspace_id, client_id, run_id = uuid4(), uuid4(), uuid4()
    reference = object_storage.gstr_input_reference(
        workspace_id, client_id, run_id, "Q1"
    )
    before = datetime.now(timezone.utc)
    target = object_storage.create_private_upload_target(
        reference, run_id=run_id, period_code="Q1", expected_size=1234,
        local_url="/unused",
    )
    assert target["provider"] == "s3" and target["method"] == "PUT"
    assert target["url"].startswith("https://private.example/upload?")
    assert 590 <= (target["expires_at"] - before).total_seconds() <= 610
    operation, params, ttl = s3_storage.presigned
    assert operation == "put_object" and ttl == 600
    assert params["Key"] == reference.split("private-bucket/", 1)[1]
    assert params["ContentType"] == object_storage.XLSX_MEDIA_TYPE
    assert params["Metadata"] == {
        "aligned-run-id": str(run_id),
        "aligned-period": "Q1",
        "aligned-expected-size": "1234",
    }
    assert "credential" not in str(target).lower()


def test_private_head_delete_and_storage_errors_are_sanitized(s3_storage):
    reference = object_storage.gstr_input_reference(uuid4(), uuid4(), uuid4(), "Q1")
    s3_storage.head = {
        "ContentLength": 42,
        "ContentType": object_storage.XLSX_MEDIA_TYPE,
        "Metadata": {"Aligned-Period": "Q1"},
    }
    stat = object_storage.private_object_stat(reference)
    assert stat == object_storage.PrivateObjectStat(
        42, object_storage.XLSX_MEDIA_TYPE, {"aligned-period": "Q1"}
    )
    object_storage.delete_private_object(reference)
    assert s3_storage.deleted[0]["Key"].endswith(".xlsx")
    s3_storage.error = RuntimeError("raw provider credential detail")
    with pytest.raises(object_storage.ObjectStorageUnavailableError) as caught:
        object_storage.private_object_stat(reference)
    assert "credential" not in str(caught.value).lower()
    with pytest.raises(object_storage.ObjectStorageUnavailableError):
        object_storage.create_private_upload_target(
            reference, run_id=uuid4(), period_code="Q1", expected_size=1,
            local_url="/unused",
        )


def test_local_streaming_upload_is_bounded_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setattr(object_storage, "local_storage_root", lambda: tmp_path)
    reference = object_storage.gstr_input_reference(uuid4(), uuid4(), uuid4(), "Q1")

    async def chunks():
        yield b"PK"
        yield b" workbook"

    size = asyncio.run(object_storage.write_local_private_upload(
        reference, chunks(), maximum_bytes=20
    ))
    assert size == 11
    assert object_storage.private_object_stat(reference).size_bytes == 11
    assert str(tmp_path) in reference

    async def too_large():
        yield b"123456"

    with pytest.raises(object_storage.ObjectStorageUploadError) as caught:
        asyncio.run(object_storage.write_local_private_upload(
            reference, too_large(), maximum_bytes=5
        ))
    assert caught.value.limit_exceeded


def test_local_upload_targets_are_disabled_outside_development(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(object_storage, "local_storage_root", lambda: tmp_path)
    reference = object_storage.gstr_input_reference(uuid4(), uuid4(), uuid4(), "Q1")
    with pytest.raises(object_storage.ObjectStorageConfigurationError):
        object_storage.create_private_upload_target(
            reference, run_id=uuid4(), period_code="Q1", expected_size=1,
            local_url="/api/development-only",
        )


def descriptors(periods, *, suffix=".xlsx", size=1, filename_prefix="file"):
    return [
        {"period_code": period, "filename": f"{filename_prefix}-{period}{suffix}", "size_bytes": size}
        for period in periods
    ]


@pytest.mark.parametrize(
    "mode,periods",
    [("QUARTERLY", lifecycle.QUARTERLY_PERIODS), ("MONTHLY", lifecycle.MONTHLY_PERIODS)],
)
def test_exact_period_slots_and_uppercase_xlsx(mode, periods):
    values = descriptors(periods, suffix=".XLSX")
    normalized = lifecycle._normalized_files(mode, values)
    assert [item["period_code"] for item in normalized] == list(periods)


@pytest.mark.parametrize(
    "mode,values",
    [
        ("QUARTERLY", descriptors(("Q1", "Q2", "Q3"))),
        ("QUARTERLY", descriptors(("Q1", "Q2", "Q3", "Q4", "Q1"))),
        ("MONTHLY", descriptors(lifecycle.MONTHLY_PERIODS[:-1])),
        ("MONTHLY", descriptors((*lifecycle.MONTHLY_PERIODS, "M04"))),
        ("QUARTERLY", descriptors(("Q1", "Q2", "Q3", "Q5"))),
    ],
)
def test_missing_extra_duplicate_and_unsupported_slots_are_rejected(mode, values):
    with pytest.raises(HTTPException) as caught:
        lifecycle._normalized_files(mode, values)
    assert caught.value.status_code == 400


@pytest.mark.parametrize("suffix", [".xls", ".csv", ""])
def test_legacy_and_wrong_extensions_are_rejected(suffix):
    with pytest.raises(HTTPException) as caught:
        lifecycle._normalized_files(
            "QUARTERLY", descriptors(lifecycle.QUARTERLY_PERIODS, suffix=suffix)
        )
    assert caught.value.status_code == 415


def test_filename_traversal_is_reduced_to_display_name_and_never_used_as_key():
    values = descriptors(lifecycle.QUARTERLY_PERIODS)
    values[0]["filename"] = "../../private/April.xlsx"
    normalized = lifecycle._normalized_files("QUARTERLY", values)
    assert normalized[0]["filename"] == "April.xlsx"


@pytest.mark.parametrize("size", [0, -1, True])
def test_nonpositive_or_noninteger_declared_sizes_are_rejected(size):
    values = descriptors(lifecycle.QUARTERLY_PERIODS)
    values[0]["size_bytes"] = size
    with pytest.raises(HTTPException) as caught:
        lifecycle._normalized_files("QUARTERLY", values)
    assert caught.value.status_code == 400


def test_blank_control_and_overlong_filenames_are_rejected():
    for filename in ("   ", "bad\x00.xlsx", "a" * 251 + ".xlsx"):
        values = descriptors(lifecycle.QUARTERLY_PERIODS)
        values[0]["filename"] = filename
        with pytest.raises(HTTPException) as caught:
            lifecycle._normalized_files("QUARTERLY", values)
        assert caught.value.status_code == 400


def test_declared_individual_and_aggregate_limits(monkeypatch):
    monkeypatch.setattr(
        object_storage,
        "gstr_storage_settings",
        lambda: object_storage.GstrStorageSettings(10, 25, 600),
    )
    over_one = descriptors(lifecycle.QUARTERLY_PERIODS, size=1)
    over_one[0]["size_bytes"] = 11
    with pytest.raises(HTTPException) as individual:
        lifecycle._normalized_files("QUARTERLY", over_one)
    assert individual.value.status_code == 413
    with pytest.raises(HTTPException) as aggregate:
        lifecycle._normalized_files(
            "QUARTERLY", descriptors(lifecycle.QUARTERLY_PERIODS, size=7)
        )
    assert aggregate.value.status_code == 413
