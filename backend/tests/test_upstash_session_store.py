import json
import threading
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.reconciliation import MatchMethod
from backend.app.services import session_manager
from backend.app.services.session_manager import SessionManager
from backend.app.services.session_store import (
    SessionConfigurationError,
    UpstashRedisSessionStore,
    configured_store,
)
from backend.tests.test_session_persistence import durable_reconciliation


class FakeUpstashRedis:
    """Thread-safe command-level fake; no credentials or network required."""

    def __init__(self):
        self._values = {}
        self._expires = {}
        self._now = 0
        self._lock = threading.RLock()

    def advance(self, seconds):
        with self._lock:
            self._now += seconds

    def _expire(self, key):
        deadline = self._expires.get(key)
        if deadline is not None and deadline <= self._now:
            self._values.pop(key, None)
            self._expires.pop(key, None)

    def get(self, key):
        with self._lock:
            self._expire(key)
            return self._values.get(key)

    def set(self, key, value, ex=None, nx=False):
        with self._lock:
            self._expire(key)
            if nx and key in self._values:
                return False
            self._values[key] = value
            self._expires[key] = self._now + int(ex) if ex else None
            return True

    def delete(self, key):
        with self._lock:
            existed = key in self._values
            self._values.pop(key, None)
            self._expires.pop(key, None)
            return int(existed)

    def eval(self, script, *, keys, args):
        assert "redis.call('GET', KEYS[1])" in script
        with self._lock:
            key = keys[0]
            self._expire(key)
            if self._values.get(key) != args[0]:
                return 0
            self._values[key] = args[1]
            self._expires[key] = self._now + int(args[2])
            return 1


def redis_store(client, ttl=3600):
    return UpstashRedisSessionStore(
        "https://example.upstash.io", "test-token", ttl_seconds=ttl, client=client
    )


def redis_managers(tmp_path):
    client = FakeUpstashRedis()
    return (
        client,
        SessionManager(redis_store(client), tmp_path / "instance-a"),
        SessionManager(redis_store(client), tmp_path / "instance-b"),
        SessionManager(redis_store(client), tmp_path / "instance-c"),
    )


def test_save_get_delete_ttl_isolation_and_exact_types():
    client = FakeUpstashRedis()
    first = redis_store(client, ttl=60)
    second = redis_store(client, ttl=60)
    first.save("a", {"amount": Decimal("10940.94"), "day": date(2026, 8, 16)})
    first.save("b", {"marker": "isolated"})
    assert second.get("a") == {"amount": Decimal("10940.94"), "day": date(2026, 8, 16)}
    assert client.get("gst-reconciliation:session:a").startswith("z1:")
    second.delete("a")
    assert first.get("a") is None
    assert first.get("b") == {"marker": "isolated"}
    client.advance(61)
    assert second.get("b") is None


def test_successful_mutation_refreshes_ttl():
    client = FakeUpstashRedis()
    store = redis_store(client, ttl=60)
    store.save("session", {"value": 1})
    client.advance(50)
    store.mutate("session", lambda session: session.update(value=2))
    client.advance(50)
    assert store.get("session")["value"] == 2
    client.advance(11)
    assert store.get("session") is None


def test_atomic_mutation_prevents_lost_updates():
    client = FakeUpstashRedis()
    first, second = redis_store(client), redis_store(client)
    first.save("session", {"count": 0})
    barrier = threading.Barrier(2)
    errors = []

    def increment(store):
        try:
            def apply(session):
                if session["count"] == 0:
                    barrier.wait(timeout=2)
                session["count"] += 1
            store.mutate("session", apply)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=increment, args=(store,)) for store in (first, second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert errors == []
    assert first.get("session")["count"] == 2


def test_two_competing_consumptions_exactly_one_succeeds():
    client = FakeUpstashRedis()
    first, second = redis_store(client), redis_store(client)
    first.save("session", {"consumed": False, "count": 0})
    barrier = threading.Barrier(2)
    outcomes = []

    def consume(store):
        try:
            def apply(session):
                if session["consumed"]:
                    raise ValueError("Candidate is already consumed elsewhere")
                barrier.wait(timeout=2)
                session["consumed"] = True
                session["count"] += 1
            store.mutate("session", apply)
            outcomes.append("success")
        except ValueError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=consume, args=(store,)) for store in (first, second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert sorted(outcomes) == ["conflict", "success"]
    assert first.get("session") == {"consumed": True, "count": 1}


def test_failed_mutation_does_not_write_partial_state():
    client = FakeUpstashRedis()
    store = redis_store(client)
    store.save("session", {"value": "original"})

    def fail(session):
        session["value"] = "corrupt"
        raise ValueError("rejected")

    with pytest.raises(ValueError, match="rejected"):
        store.mutate("session", fail)
    assert store.get("session") == {"value": "original"}


def test_cross_instance_manual_match_and_domain_state(tmp_path, monkeypatch):
    _, first, second, third = redis_managers(tmp_path)
    session_id = durable_reconciliation(first)
    monkeypatch.setattr(session_manager, "_MANAGER", second)
    response = TestClient(app).post(
        f"/api/manual-match?session_id={session_id}",
        json={"result_id": "result-1", "candidate_id": "candidate-1"},
    )
    assert response.status_code == 200
    loaded = third.get(session_id)
    result = loaded["reconciliation_summary"].results[0]
    assert result.match_method == MatchMethod.MANUAL_MATCH
    assert result.date_mismatch is True
    assert loaded["reconciliation_summary"].needs_review == 0
    assert loaded["reconciliation_summary"].not_in_tally == 0
    assert loaded["manual_match_map"]["result-1"]["candidate-1"].consumed is True


def test_invoice_and_date_mismatch_metadata_round_trip(tmp_path):
    _, first, second, _ = redis_managers(tmp_path)
    session_id = durable_reconciliation(first)

    def mark_mismatch(session):
        result = session["reconciliation_summary"].results[0]
        result.invoice_mismatch = True
        result.date_mismatch = True
        result.date_difference_days = 3

    first.mutate(session_id, mark_mismatch)
    loaded = second.get(session_id)["reconciliation_summary"].results[0]
    assert loaded.invoice_mismatch is True
    assert loaded.date_mismatch is True
    assert loaded.date_difference_days == 3


def test_cross_instance_exports_regenerate_without_original_files(tmp_path, monkeypatch):
    _, first, second, third = redis_managers(tmp_path)
    session_id = durable_reconciliation(first)
    monkeypatch.setattr(session_manager, "_MANAGER", second)
    excel = TestClient(app).get(f"/api/export/{session_id}/excel")
    assert excel.status_code == 200 and excel.content.startswith(b"PK")
    monkeypatch.setattr(session_manager, "_MANAGER", third)
    exported_json = TestClient(app).get(f"/api/export/{session_id}/json")
    assert exported_json.status_code == 200
    assert exported_json.json()["counts"]["needs_review_ambiguous"] == 1


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_URL is required"),
        ("UPSTASH_REDIS_REST_TOKEN", "UPSTASH_REDIS_REST_TOKEN is required"),
    ],
)
def test_production_missing_credentials_fails_clearly(monkeypatch, missing, message):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_STORE_BACKEND", "upstash")
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "secret")
    monkeypatch.delenv(missing)
    with pytest.raises(SessionConfigurationError, match=message):
        configured_store()


def test_production_selects_upstash_and_rejects_memory(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_STORE_BACKEND", "memory")
    with pytest.raises(SessionConfigurationError, match="SESSION_STORE_BACKEND=upstash"):
        configured_store()
    monkeypatch.setenv("SESSION_STORE_BACKEND", "upstash")
    monkeypatch.setenv("UPSTASH_REDIS_REST_URL", "https://example.upstash.io")
    monkeypatch.setenv("UPSTASH_REDIS_REST_TOKEN", "secret")
    assert isinstance(configured_store(), UpstashRedisSessionStore)
