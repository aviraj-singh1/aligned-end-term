"""Disabled-by-default behavior for the authoritative GSTR switch."""

import pytest

from backend.app.services.gstr_merge_feature import gstr_consolidation_enabled


def test_gstr_feature_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("GSTR_CONSOLIDATION_ENABLED", raising=False)
    assert gstr_consolidation_enabled() is False


@pytest.mark.parametrize("value", ["false", "FALSE", " false "])
def test_gstr_feature_accepts_explicit_false(monkeypatch, value):
    monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", value)
    assert gstr_consolidation_enabled() is False


def test_gstr_feature_accepts_explicit_true(monkeypatch):
    monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "true")
    assert gstr_consolidation_enabled() is True


def test_gstr_feature_rejects_ambiguous_values(monkeypatch):
    monkeypatch.setenv("GSTR_CONSOLIDATION_ENABLED", "yes")
    with pytest.raises(RuntimeError, match="must be either"):
        gstr_consolidation_enabled()
