from pathlib import Path
from types import SimpleNamespace

from backend.app.services import object_storage
import pytest


class FakeS3:
    def __init__(self):
        self.uploads = []
        self.deleted = []

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.uploads.append((Path(path).read_bytes(), bucket, key, ExtraArgs))

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return SimpleNamespace(paginate=lambda **_: [{"Contents": [{"Key": "aligned/sessions/sid/reports/a.xlsx"}]}])

    def delete_objects(self, **kwargs):
        self.deleted.append(kwargs)

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign = (operation, Params, ExpiresIn)
        return "https://private-bucket.example/signed-report"


def test_s3_artifact_persistence_and_session_cleanup(tmp_path, monkeypatch):
    artifact = tmp_path / "report.xlsx"
    artifact.write_bytes(b"PK report")
    fake = FakeS3()
    settings = object_storage.S3Settings("private-bucket", "ap-south-1", "aligned", None, "AES256")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(object_storage, "s3_settings", lambda: settings)
    monkeypatch.setattr(object_storage, "s3_client", lambda: fake)

    reference = object_storage.persist_artifact(
        "sid", artifact, category="reports", filename="report.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert reference.startswith("s3://private-bucket/aligned/sessions/sid/reports/")
    assert fake.uploads[0][0] == b"PK report"
    assert fake.uploads[0][3]["ServerSideEncryption"] == "AES256"
    assert fake.uploads[0][3]["ContentDisposition"].startswith("attachment;")
    object_storage.delete_session_artifacts("sid")
    assert fake.deleted[0]["Delete"]["Objects"] == [{"Key": "aligned/sessions/sid/reports/a.xlsx"}]


def test_local_artifacts_remain_plain_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    path = tmp_path / "report.json"
    path.write_text("{}", encoding="utf-8")
    reference = object_storage.persist_artifact(
        "sid", path, category="reports", filename="report.json", content_type="application/json"
    )
    assert reference == str(path)
    assert object_storage.artifact_available(reference)
    assert object_storage.artifact_size(reference) == 2


def test_cloud_only_mode_rejects_local_object_storage(monkeypatch):
    monkeypatch.setenv("REQUIRE_S3_OBJECT_STORAGE", "true")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")

    with pytest.raises(object_storage.ObjectStorageConfigurationError, match="requires OBJECT_STORAGE_BACKEND=s3"):
        object_storage.validate_object_storage_configuration()


def test_cloud_only_mode_accepts_s3_configuration(monkeypatch):
    monkeypatch.setenv("REQUIRE_S3_OBJECT_STORAGE", "true")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "private-bucket")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("ARTIFACT_DELIVERY_PROVIDER", "s3")
    object_storage.s3_settings.cache_clear()
    object_storage.artifact_delivery_settings.cache_clear()

    object_storage.validate_object_storage_configuration()


def test_s3_delivery_redirects_to_short_lived_presigned_url(monkeypatch):
    fake = FakeS3()
    settings = object_storage.S3Settings("private-bucket", "ap-south-1", "aligned", None, "AES256")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("ARTIFACT_DELIVERY_PROVIDER", "s3")
    monkeypatch.setenv("ARTIFACT_DOWNLOAD_URL_TTL_SECONDS", "180")
    monkeypatch.setattr(object_storage, "s3_settings", lambda: settings)
    monkeypatch.setattr(object_storage, "s3_client", lambda: fake)
    object_storage.artifact_delivery_settings.cache_clear()

    response = object_storage.artifact_response(
        "s3://private-bucket/aligned/sessions/sid/reports/id/report.xlsx",
        filename="report.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert response.status_code == 307
    assert response.headers["location"] == "https://private-bucket.example/signed-report"
    assert response.headers["cache-control"] == "no-store, private"
    assert fake.presign[0] == "get_object"
    assert fake.presign[1]["Bucket"] == "private-bucket"
    assert fake.presign[1]["Key"].endswith("report.xlsx")
    assert fake.presign[2] == 180


def test_delivery_configuration_rejects_cloudfront_without_signer(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "private-bucket")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("ARTIFACT_DELIVERY_PROVIDER", "cloudfront")
    monkeypatch.setenv("CLOUDFRONT_BASE_URL", "https://cdn.example.com")
    monkeypatch.delenv("CLOUDFRONT_KEY_PAIR_ID", raising=False)
    monkeypatch.delenv("CLOUDFRONT_PRIVATE_KEY_PATH", raising=False)
    object_storage.artifact_delivery_settings.cache_clear()

    try:
        object_storage.artifact_delivery_settings()
    except object_storage.ObjectStorageConfigurationError as exc:
        assert "CLOUDFRONT_KEY_PAIR_ID" in str(exc)
    else:
        raise AssertionError("CloudFront delivery accepted missing signing credentials")


def test_cloudfront_delivery_generates_signed_https_url(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "cloudfront-private.pem"
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "private-bucket")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("ARTIFACT_DELIVERY_PROVIDER", "cloudfront")
    monkeypatch.setenv("CLOUDFRONT_BASE_URL", "https://files.example.com/")
    monkeypatch.setenv("CLOUDFRONT_KEY_PAIR_ID", "KEXAMPLE")
    monkeypatch.setenv("CLOUDFRONT_PRIVATE_KEY_PATH", str(key_path))
    object_storage.artifact_delivery_settings.cache_clear()
    object_storage.s3_settings.cache_clear()

    url = object_storage.artifact_download_url(
        "s3://private-bucket/aligned/sessions/sid/reports/id/report.xlsx",
        filename="report.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert url.startswith("https://files.example.com/aligned/sessions/sid/reports/id/report.xlsx?")
    assert "Key-Pair-Id=KEXAMPLE" in url
    assert "Signature=" in url and "Expires=" in url
