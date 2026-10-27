"""Sanitized, nonzero worker startup failures."""

import pytest

from backend.app.db.revision import DatabaseRevisionError, GSTR_DATABASE_REVISION
from backend.app.workers import gstr_merge_worker


def test_worker_entrypoint_exits_cleanly_on_wrong_revision(monkeypatch, capsys):
    monkeypatch.setattr(gstr_merge_worker.signal, "signal", lambda *_args: None)

    def refuse(_stop):
        raise DatabaseRevisionError(
            f"GSTR processing requires database revision {GSTR_DATABASE_REVISION}."
        )

    monkeypatch.setattr(gstr_merge_worker, "run_worker_loop", refuse)
    with pytest.raises(SystemExit) as exc:
        gstr_merge_worker.main()

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "GSTR worker startup refused: GSTR processing requires database revision "
        f"{GSTR_DATABASE_REVISION}.\n"
    )
    assert "Traceback" not in captured.err
    assert "postgresql://" not in captured.err
