from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
import logging
import os
import re

from .config import auth_enabled, auth_settings

logger = logging.getLogger(__name__)


def is_financial_request(method: str, path: str) -> bool:
    return ((method == 'POST' and path in {'/api/inspect', '/api/reconcile', '/api/manual-match'})
        or (method in {'GET', 'DELETE'} and re.fullmatch(r'/api/session/[^/]+', path) is not None)
        or (method == 'GET' and re.fullmatch(r'/api/session/[^/]+/results', path) is not None)
        or (method == 'GET' and re.fullmatch(r'/api/export/[^/]+/(?:excel|json|notes/excel)', path) is not None))


async def authentication_boundary(request: Request, call_next):
    path = request.url.path
    is_auth = path.startswith(("/api/auth/", "/api/workspaces/")) or path == "/api/me"
    from backend.app.services import owned_reconciliation as owned
    financial = is_financial_request(request.method, path)
    demo = (os.getenv('APP_ENV', '').lower() == 'development'
        and os.getenv('ALLOW_DEMO_MODE', 'false').lower() == 'true'
        and request.headers.get('x-aligned-demo', '').lower() == 'true')
    if request.method == "OPTIONS":
        return await call_next(request)
    if demo and financial:
        return await call_next(request)
    if is_auth and not auth_enabled():
        return JSONResponse({"detail": "Authentication is not enabled on this server"}, status_code=503)
    if auth_enabled():
        try:
            auth_settings()
        except RuntimeError:
            return JSONResponse({"detail": "Authentication configuration is unavailable"}, status_code=503)
        if path.startswith("/api/") and not is_auth and path != "/api/health":
            if not owned.enabled() or not financial:
                return JSONResponse({"detail": "Authenticated reconciliation is unavailable until workspace/client authorization is complete"}, status_code=503)
            try:
                request.state.owned_context = await run_in_threadpool(owned.authorize_request, request)
            except HTTPException as error:
                return JSONResponse({'detail': error.detail}, status_code=error.status_code)
            except SQLAlchemyError:
                logger.exception("Workspace authorization database failure on %s", path)
                return JSONResponse({'detail': 'Account service temporarily unavailable'}, status_code=503)
        if is_auth and request.method in {"POST", "PATCH"}:
            try:
                size = int(request.headers.get("content-length", "-1"))
            except ValueError:
                size = -1
            limit = 8192
            if size < 0 or size > limit:
                return JSONResponse({"detail": "Authentication request must be at most 8 KB with a Content-Length header"}, status_code=413)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "Use application/json for authentication requests"}, status_code=415)
    try:
        return await call_next(request)
    except SQLAlchemyError:
        logger.exception("Account database failure on %s %s", request.method, path)
        if is_auth or financial:
            return JSONResponse({"detail": "Account service temporarily unavailable. Please try again."}, status_code=503)
        raise
