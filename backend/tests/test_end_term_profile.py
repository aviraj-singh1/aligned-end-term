from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.main import app, validate_end_term_runtime
from backend.app.services.object_storage import artifact_response


def test_excluded_feature_routes_are_not_registered():
    paths = set(app.openapi()["paths"])
    assert not any("gstr-merges" in path for path in paths)
    assert not any("tally-exports" in path for path in paths)
    assert not any("invites" in path or "invitations" in path for path in paths)
    assert not any("ownership" in path or path.endswith("/team") for path in paths)


def test_end_term_runtime_requires_local_artifacts(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("REQUIRE_S3_OBJECT_STORAGE", "false")
    validate_end_term_runtime()

    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    with pytest.raises(RuntimeError, match="OBJECT_STORAGE_BACKEND=local"):
        validate_end_term_runtime()


def test_local_download_is_deleted_after_response(tmp_path):
    report = Path(tmp_path) / "report.json"
    report.write_text('{"status":"ok"}', encoding="utf-8")
    test_app = FastAPI()

    @test_app.get("/report")
    def download():
        return artifact_response(report, filename="report.json", media_type="application/json")

    with TestClient(test_app) as client:
        response = client.get("/report")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert not report.exists()
