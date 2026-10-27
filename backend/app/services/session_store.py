from __future__ import annotations

import base64
import inspect
import json
import os
import threading
import zlib
import copy
from abc import ABC, abstractmethod
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from backend.app.models import records as records_models
from backend.app.models import reconciliation as reconciliation_models
from backend.app.services import notes_reconciliation_engine as notes_models


class SessionConfigurationError(RuntimeError):
    pass


class SessionMutationConflict(RuntimeError):
    """Raised when a distributed session update cannot win bounded CAS retries."""


def _allowed_types() -> Dict[str, type]:
    registry: Dict[str, type] = {}
    for module in (records_models, reconciliation_models, notes_models):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and (is_dataclass(cls) or issubclass(cls, Enum)):
                registry[f"{cls.__module__}.{cls.__name__}"] = cls
    return registry


_TYPE_REGISTRY = _allowed_types()


def encode_session_value(value: Any) -> Any:
    if isinstance(value, Decimal): return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, datetime): return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date): return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, Path): return {"__type__": "path", "value": str(value)}
    if isinstance(value, Enum):
        return {"__type__": "enum", "class": f"{value.__class__.__module__}.{value.__class__.__name__}", "value": value.value}
    if is_dataclass(value):
        encoded_fields = {}
        declared = {item.name: item for item in fields(value)}
        for name, item_value in vars(value).items():
            item = declared.get(name)
            if item is not None:
                if item.default is not MISSING:
                    default = item.default
                elif item.default_factory is not MISSING:
                    default = item.default_factory()
                else:
                    default = MISSING
                if default is not MISSING and item_value == default:
                    continue
            encoded_fields[name] = encode_session_value(item_value)
        return {
            "__type__": "dataclass",
            "class": f"{value.__class__.__module__}.{value.__class__.__name__}",
            "fields": encoded_fields,
        }
    if isinstance(value, dict): return {str(k): encode_session_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [encode_session_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)): return value
    raise TypeError(f"Unsupported session value: {type(value).__name__}")


def decode_session_value(value: Any) -> Any:
    if isinstance(value, list): return [decode_session_value(v) for v in value]
    if not isinstance(value, dict): return value
    marker = value.get("__type__")
    if marker == "decimal": return Decimal(value["value"])
    if marker == "datetime": return datetime.fromisoformat(value["value"])
    if marker == "date": return date.fromisoformat(value["value"])
    if marker == "path": return Path(value["value"])
    if marker in ("enum", "dataclass"):
        cls = _TYPE_REGISTRY.get(value.get("class", ""))
        if cls is None: raise ValueError("Session payload contains an unsupported type")
        if marker == "enum": return cls(value["value"])
        decoded = {k: decode_session_value(v) for k, v in value["fields"].items()}
        declared = {f.name for f in fields(cls)}
        obj = cls(**{k: v for k, v in decoded.items() if k in declared})
        for key, item in decoded.items():
            if key not in declared: setattr(obj, key, item)
        return obj
    return {k: decode_session_value(v) for k, v in value.items()}


class SessionStore(ABC):
    @abstractmethod
    def get(self, session_id: str) -> Optional[Dict[str, Any]]: ...
    @abstractmethod
    def save(self, session_id: str, session: Dict[str, Any]) -> None: ...
    @abstractmethod
    def delete(self, session_id: str) -> None: ...
    @abstractmethod
    def ids(self) -> list[str]: ...
    @abstractmethod
    def mutate(self, session_id: str, callback: Callable[[Dict[str, Any]], Any]) -> Any: ...
    @abstractmethod
    def create_if_absent(self, session_id: str, session: Dict[str, Any]) -> bool: ...


class InMemorySessionStore(SessionStore):
    def __init__(self):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
    def get(self, session_id):
        with self._lock: return self._sessions.get(session_id)
    def save(self, session_id, session):
        with self._lock: self._sessions[session_id] = session
    def create_if_absent(self, session_id, session):
        with self._lock:
            if session_id in self._sessions: return False
            self._sessions[session_id] = copy.deepcopy(session)
            return True
    def delete(self, session_id):
        with self._lock: self._sessions.pop(session_id, None)
    def ids(self):
        with self._lock: return list(self._sessions)
    def mutate(self, session_id, callback):
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None: return None
            result = callback(session)
            self._sessions[session_id] = session
            return result


_FILE_LOCKS: Dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


class JsonFileSessionStore(SessionStore):
    """Explicit JSON persistence for local/restart tests; not serverless durability."""
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        key = str(self.root.resolve())
        with _FILE_LOCKS_GUARD: self._lock = _FILE_LOCKS.setdefault(key, threading.RLock())
    def _path(self, session_id): return self.root / f"{session_id}.json"
    def get(self, session_id):
        with self._lock:
            path = self._path(session_id)
            return None if not path.exists() else decode_session_value(json.loads(path.read_text(encoding="utf-8")))
    def save(self, session_id, session):
        with self._lock:
            path = self._path(session_id)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(encode_session_value(session)), encoding="utf-8")
            os.replace(temporary, path)
    def delete(self, session_id):
        with self._lock: self._path(session_id).unlink(missing_ok=True)
    def create_if_absent(self, session_id, session):
        with self._lock:
            if self._path(session_id).exists(): return False
            self.save(session_id, session)
            return True
    def ids(self):
        with self._lock: return [path.stem for path in self.root.glob("*.json")]
    def mutate(self, session_id, callback):
        with self._lock:
            session = self.get(session_id)
            if session is None: return None
            result = callback(session)
            self.save(session_id, session)
            return result


class UpstashRedisSessionStore(SessionStore):
    """Shared serverless session storage using Upstash Redis REST and Lua CAS."""

    KEY_PREFIX = "gst-reconciliation:session:"
    DEFAULT_TTL_SECONDS = 60 * 60
    DEFAULT_MUTATION_RETRIES = 5
    _CAS_SCRIPT = """#!lua flags=allow-key-locking
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        mutation_retries: int = DEFAULT_MUTATION_RETRIES,
        client: Any = None,
    ):
        if not url:
            raise SessionConfigurationError("UPSTASH_REDIS_REST_URL is required for upstash sessions")
        if not token:
            raise SessionConfigurationError("UPSTASH_REDIS_REST_TOKEN is required for upstash sessions")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if mutation_retries <= 0:
            raise ValueError("mutation_retries must be positive")
        if client is None:
            try:
                from upstash_redis import Redis
            except ImportError as exc:
                raise SessionConfigurationError(
                    "upstash-redis is required when SESSION_STORE_BACKEND=upstash"
                ) from exc
            client = Redis(url=url, token=token, allow_telemetry=False)
        self._client = client
        self.ttl_seconds = ttl_seconds
        self.mutation_retries = mutation_retries

    def _key(self, session_id: str) -> str:
        return f"{self.KEY_PREFIX}{session_id}"

    @staticmethod
    def _serialize(session: Dict[str, Any]) -> str:
        payload = json.dumps(encode_session_value(session), separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(payload, level=6)
        return "z1:" + base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def _deserialize(payload: str) -> Dict[str, Any]:
        if payload.startswith("z1:"):
            payload = zlib.decompress(base64.b64decode(payload[3:])).decode("utf-8")
        return decode_session_value(json.loads(payload))

    def get(self, session_id):
        payload = self._client.get(self._key(session_id))
        return None if payload is None else self._deserialize(payload)

    def save(self, session_id, session):
        self._client.set(self._key(session_id), self._serialize(session), ex=self.ttl_seconds)

    def create_if_absent(self, session_id, session):
        return bool(self._client.set(self._key(session_id), self._serialize(session), ex=self.ttl_seconds, nx=True))

    def delete(self, session_id):
        self._client.delete(self._key(session_id))

    def ids(self):
        # Redis owns expiry through per-key TTL. Avoid broad scans in request paths.
        return []

    def mutate(self, session_id, callback):
        key = self._key(session_id)
        for _ in range(self.mutation_retries):
            original = self._client.get(key)
            if original is None:
                return None
            session = self._deserialize(original)
            result = callback(session)
            replacement = self._serialize(session)
            swapped = self._client.eval(
                self._CAS_SCRIPT,
                keys=[key],
                args=[original, replacement, str(self.ttl_seconds)],
            )
            if int(swapped) == 1:
                return result
        raise SessionMutationConflict("Session changed concurrently; retry the request")


class PostgresSessionStore(SessionStore):
    """Shared session state stored in PostgreSQL (including Supabase Postgres)."""

    def __init__(self, ttl_seconds: int = 60 * 60, engine=None):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if engine is None:
            from backend.app.db.session import get_database_engine
            engine = get_database_engine()
        self.engine = engine
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _serialize(session):
        return UpstashRedisSessionStore._serialize(session)

    @staticmethod
    def _deserialize(payload):
        return UpstashRedisSessionStore._deserialize(payload)

    def get(self, session_id):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            payload = connection.scalar(text(
                "SELECT payload FROM reconciliation_session_state "
                "WHERE session_id=:session_id"
            ), {"session_id": session_id})
        return None if payload is None else self._deserialize(payload)

    def save(self, session_id, session):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO reconciliation_session_state(session_id,payload,expires_at) "
                "VALUES (:session_id,:payload,now() + make_interval(secs => :ttl)) "
                "ON CONFLICT (session_id) DO UPDATE SET payload=excluded.payload, "
                "expires_at=excluded.expires_at, updated_at=now()"
            ), {"session_id": session_id, "payload": self._serialize(session), "ttl": self.ttl_seconds})

    def create_if_absent(self, session_id, session):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            created = connection.scalar(text(
                "INSERT INTO reconciliation_session_state(session_id,payload,expires_at) "
                "VALUES (:session_id,:payload,now() + make_interval(secs => :ttl)) "
                "ON CONFLICT (session_id) DO UPDATE SET payload=excluded.payload, "
                "expires_at=excluded.expires_at, updated_at=now() "
                "WHERE reconciliation_session_state.expires_at <= now() RETURNING session_id"
            ), {"session_id": session_id, "payload": self._serialize(session), "ttl": self.ttl_seconds})
        return created is not None

    def delete(self, session_id):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM reconciliation_session_state WHERE session_id=:session_id"
            ), {"session_id": session_id})

    def ids(self):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            return list(connection.scalars(text("SELECT session_id FROM reconciliation_session_state")))

    def mutate(self, session_id, callback):
        from sqlalchemy import text
        with self.engine.begin() as connection:
            payload = connection.scalar(text(
                "SELECT payload FROM reconciliation_session_state "
                "WHERE session_id=:session_id AND expires_at > now() FOR UPDATE"
            ), {"session_id": session_id})
            if payload is None:
                return None
            session = self._deserialize(payload)
            result = callback(session)
            connection.execute(text(
                "UPDATE reconciliation_session_state SET payload=:payload, "
                "expires_at=now() + make_interval(secs => :ttl), updated_at=now() "
                "WHERE session_id=:session_id"
            ), {"session_id": session_id, "payload": self._serialize(session), "ttl": self.ttl_seconds})
            return result


def configured_store() -> SessionStore:
    backend = os.getenv("SESSION_STORE_BACKEND", "memory").strip().lower()
    production = bool(os.getenv("VERCEL")) or os.getenv("APP_ENV", "").lower() == "production"
    if production and backend != "postgres":
        raise SessionConfigurationError(
            "SESSION_STORE_BACKEND=postgres is required in production"
        )
    if backend == "memory": return InMemorySessionStore()
    if backend == "json_file":
        path = os.getenv("SESSION_STORE_PATH")
        if not path: raise SessionConfigurationError("SESSION_STORE_PATH is required for json_file sessions")
        return JsonFileSessionStore(Path(path))
    if backend == "upstash":
        return UpstashRedisSessionStore(
            os.getenv("UPSTASH_REDIS_REST_URL", "").strip(),
            os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip(),
        )
    if backend == "postgres":
        try:
            ttl = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
        except ValueError:
            raise SessionConfigurationError("SESSION_TTL_SECONDS must be an integer") from None
        return PostgresSessionStore(ttl_seconds=ttl)
    raise SessionConfigurationError(f"Unsupported SESSION_STORE_BACKEND: {backend}")
