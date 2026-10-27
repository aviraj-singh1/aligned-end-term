import shutil
import tempfile
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from backend.app.services.session_store import SessionStore, configured_store
from backend.app.services.object_storage import artifact_available, delete_session_artifacts


SESSION_TIMEOUT_MINUTES = 60
BASE_TEMP_DIR = Path(tempfile.gettempdir()) / "gst_reconciliation_sessions"


class SessionManager:
    def __init__(self, store: SessionStore, base_temp_dir: Path = BASE_TEMP_DIR, timeout_minutes: int = SESSION_TIMEOUT_MINUTES):
        self.store = store
        self.base_temp_dir = Path(base_temp_dir)
        self.timeout = timedelta(minutes=timeout_minutes)

    @staticmethod
    def _valid_session_id(session_id: str) -> bool:
        try:
            return str(uuid.UUID(str(session_id))) == str(session_id)
        except (TypeError, ValueError, AttributeError):
            return False

    def _directory(self, session_id: str) -> Path:
        if not self._valid_session_id(session_id):
            raise ValueError("Invalid session identifier")
        directory = self.base_temp_dir / session_id
        directory.mkdir(parents=True, exist_ok=True)
        directory.touch(exist_ok=True)
        return directory

    def create(self) -> str:
        self.cleanup_expired()
        session_id = str(uuid.uuid4())
        now = datetime.now()
        self.store.save(session_id, {
            "id": session_id, "created_at": now, "last_accessed": now,
            "directory": self._directory(session_id), "files": {},
            "inspection_results": None, "reconciliation_summary": None,
            "notes_summary": None, "reports": {},
        })
        return session_id

    def get(self, session_id: str, touch: bool = True) -> Optional[Dict[str, Any]]:
        if not self._valid_session_id(session_id):
            return None
        expired = False

        def load(session):
            nonlocal expired
            if datetime.now() - session["last_accessed"] > self.timeout:
                expired = True
                return None
            session["directory"] = self._directory(session_id)
            if any(not artifact_available(path) for path in session.get("reports", {}).values()):
                session["reports"] = {}
            if touch:
                session["last_accessed"] = datetime.now()
            return session

        session = self.store.mutate(session_id, load)
        if expired:
            self.delete(session_id)
            return None
        return session

    def update(self, session_id: str, key: str, value: Any) -> bool:
        if not self._valid_session_id(session_id):
            return False
        def apply(session):
            session[key] = value
            session["last_accessed"] = datetime.now()
            return True
        return bool(self.store.mutate(session_id, apply))

    def mutate(self, session_id: str, callback: Callable[[Dict[str, Any]], Any]) -> Any:
        if not self._valid_session_id(session_id):
            return None
        def apply(session):
            if datetime.now() - session["last_accessed"] > self.timeout:
                return None
            session["directory"] = self._directory(session_id)
            result = callback(session)
            session["last_accessed"] = datetime.now()
            return result
        return self.store.mutate(session_id, apply)

    def delete(self, session_id: str) -> None:
        if not self._valid_session_id(session_id):
            return
        self.store.delete(session_id)
        shutil.rmtree(self.base_temp_dir / session_id, ignore_errors=True)
        delete_session_artifacts(session_id)

    def cleanup_expired(self) -> None:
        now = datetime.now()
        for session_id in self.store.ids():
            session = self.store.get(session_id)
            if session and now - session["last_accessed"] > self.timeout:
                self.delete(session_id)
        if self.base_temp_dir.is_dir():
            for directory in self.base_temp_dir.iterdir():
                if not directory.is_dir() or not self._valid_session_id(directory.name):
                    continue
                modified = datetime.fromtimestamp(directory.stat().st_mtime)
                if now - modified > self.timeout:
                    shutil.rmtree(directory, ignore_errors=True)


_MANAGER = SessionManager(configured_store())
_STAGED_MANAGER = ContextVar('staged_session_manager', default=None)


@contextmanager
def staged_manager(manager):
    """Request-local scratch state; never swap the process-wide store."""
    token = _STAGED_MANAGER.set(manager)
    try:
        yield manager
    finally:
        _STAGED_MANAGER.reset(token)


def active_manager():
    return _STAGED_MANAGER.get() or _MANAGER


def create_session() -> str: return active_manager().create()
def get_session(session_id: str) -> Optional[Dict[str, Any]]: return active_manager().get(session_id)
def update_session(session_id: str, key: str, value: Any): return active_manager().update(session_id, key, value)
def mutate_session(session_id: str, callback): return active_manager().mutate(session_id, callback)
def delete_session(session_id: str): return active_manager().delete(session_id)
def cleanup_expired_sessions(): return _MANAGER.cleanup_expired()
