from fastapi import APIRouter, HTTPException, Body, Query, Request, Response
from typing import Optional, Dict

from backend.app.services.reconciliation_orchestrator import run_reconciliation, build_reconciliation_summary_response
from backend.app.services.result_pagination import paginate_results, MAX_PAGE_SIZE
from backend.app.services.manual_match_service import execute_manual_match
from backend.app.services.session_store import SessionMutationConflict
from backend.app.api.schemas import (
    ReconciliationRequest, ReconciliationResponse, ManualMatchRequest, ResultsPageResponse
)
from backend.app.services.session_manager import get_session

router = APIRouter()

@router.post("/reconcile", response_model=ReconciliationResponse)
def reconcile_session(
    http_request: Request,
    response: Response,
    session_id: str,
    request: ReconciliationRequest = Body(default_factory=ReconciliationRequest)
):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return owned.mutate(http_request, response, session_id, 'RECONCILE', request.mapping_overrides)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Reconciliation session has expired or not found. Please upload the files again.")
        
    try:
        results = run_reconciliation(session_id, request.mapping_overrides)
        return results
    except SessionMutationConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to run reconciliation")

@router.post("/manual-match", response_model=ReconciliationResponse)
def manual_match(
    http_request: Request,
    response: Response,
    request: ManualMatchRequest,
    session_id: str
):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return owned.mutate(http_request, response, session_id, 'MANUAL_MATCH', request.model_dump())
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Reconciliation session has expired. Please upload the files again.")
        
    try:
        execute_manual_match(session_id, request.result_id, request.candidate_id)
        updated = get_session(session_id)
        return build_reconciliation_summary_response(
            session_id,
            updated["reconciliation_summary"],
            updated["notes_summary"],
        )
    except SessionMutationConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        msg = str(e)
        if "already consumed" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to apply manual match")


@router.get("/session/{session_id}/results", response_model=ResultsPageResponse)
def get_result_page(
    http_request: Request,
    response: Response,
    session_id: str,
    category: str = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
    search: str = "",
    sort_by: Optional[str] = None,
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        session = owned.read(http_request.state.owned_context[0], session_id)
        owned.version_header(response, session)
    else:
        session = get_session(session_id)
    if not session or not session.get("reconciliation_summary"):
        raise HTTPException(status_code=404, detail="Reconciliation session has expired or is not ready.")
    try:
        return paginate_results(
            session["reconciliation_summary"], session["notes_summary"],
            category=category, page=page, page_size=page_size, search=search,
            sort_by=sort_by, sort_order=sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
