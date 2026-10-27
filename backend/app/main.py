from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
from urllib.parse import urlsplit

from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.inspection import router as inspection_router
from backend.app.api.routes.reconciliation import router as reconciliation_router
from backend.app.api.routes.export import router as export_router
from backend.app.api.routes.sessions import router as session_router
from backend.app.services.session_manager import cleanup_expired_sessions
from backend.app.auth.config import auth_enabled, auth_settings
from backend.app.auth.middleware import authentication_boundary
from backend.app.api.routes.auth import router as auth_router
from backend.app.api.routes.workspaces import core_router as workspace_router
from backend.app.db.revision import (
    TALLY_EXPORT_DATABASE_REVISION,
    validate_web_database_revision,
)
from backend.app.db.session import get_database_engine
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from backend.app.services.object_storage import storage_backend

REQUIRED_DATABASE_REVISION = TALLY_EXPORT_DATABASE_REVISION


def validate_application_database_revision() -> str:
    """Validate the bounded web rollout contract used during startup."""

    return validate_web_database_revision(
        get_database_engine(),
        gstr_consolidation_enabled=False,
    )


def validate_end_term_runtime() -> None:
    """Reject deployment settings that re-enable excluded infrastructure."""

    production = bool(os.getenv("VERCEL")) or os.getenv("APP_ENV", "").lower() == "production"
    if storage_backend() != "local":
        raise RuntimeError("The end-term application requires OBJECT_STORAGE_BACKEND=local")
    if os.getenv("REQUIRE_S3_OBJECT_STORAGE", "false").strip().lower() == "true":
        raise RuntimeError("The end-term application does not use S3 object storage")
    if production and os.getenv("SESSION_STORE_BACKEND", "postgres").strip().lower() != "postgres":
        raise RuntimeError("Production requires SESSION_STORE_BACKEND=postgres")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_end_term_runtime()
    if auth_enabled():
        auth_settings()
        validate_application_database_revision()
    # Startup: clean any stray temp sessions if needed
    cleanup_expired_sessions()
    yield
    # Shutdown
    cleanup_expired_sessions()

app = FastAPI(
    title="Aligned GST Reconciliation API",
    description="Accounting reconciliation and exception-management API",
    version="2.0.0",
    lifespan=lifespan
)

# CORS Configuration
DEVELOPMENT_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000"
]


def configured_cors_origins() -> list[str]:
    configured = [
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    production = bool(os.getenv("VERCEL")) or os.getenv("APP_ENV", "").lower() == "production"
    if production and not configured:
        raise RuntimeError("CORS_ORIGINS must contain the production frontend origin")

    origins = configured if production else DEVELOPMENT_ORIGINS + configured
    if auth_enabled() and not production:
        origins.append(auth_settings().frontend_url)
    validated = []
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("CORS_ORIGINS contains an invalid or unsafe origin")
        if origin not in validated:
            validated.append(origin)
    return validated

allowed_origins = configured_cors_origins()

app.middleware("http")(authentication_boundary)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=auth_enabled(),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Accept", "Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Run-Version", "X-Workspace-ID", "X-Client-ID", "X-Client-GSTIN-ID", "X-Expected-User-ID", "X-ALIGNED-Demo"],
    expose_headers=["X-Run-Version", "Content-Disposition", "Retry-After"],
)

@app.exception_handler(RequestValidationError)
async def safe_validation_errors(request, exc):
    if request.url.path.startswith(("/api/auth/", "/api/workspaces/")):
        # Pydantic error inputs can contain the password or a reset token.
        return JSONResponse({"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]}, status_code=422)
    from fastapi.exception_handlers import request_validation_exception_handler
    return await request_validation_exception_handler(request, exc)

@app.middleware("http")
async def sensitive_response_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response

# API Routers
app.include_router(health_router, prefix="/api", tags=["Health"])
app.include_router(auth_router, prefix="/api", tags=["Authentication"])
app.include_router(workspace_router, prefix="/api", tags=["Workspaces"])
app.include_router(inspection_router, prefix="/api", tags=["Inspection"])
app.include_router(reconciliation_router, prefix="/api", tags=["Reconciliation"])
app.include_router(export_router, prefix="/api/export", tags=["Export"])
app.include_router(session_router, prefix="/api/session", tags=["Session"])
