from fastapi import APIRouter, HTTPException, Request, Response
from backend.app.services.session_manager import get_session, delete_session
from backend.app.api.schemas import SessionStatusResponse

router = APIRouter()

@router.get("/{session_id}", response_model=SessionStatusResponse)
def get_session_status(session_id: str, http_request: Request, response: Response):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        session = owned.read(http_request.state.owned_context[0], session_id)
        owned.version_header(response, session)
    else:
        session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
        
    return SessionStatusResponse(
        session_id=session["id"],
        created_at=session["created_at"].isoformat(),
        last_accessed=session["last_accessed"].isoformat(),
        has_inspection=session.get("inspection_results") is not None,
        has_reconciliation=session.get("reconciliation_summary") is not None
    )

@router.delete("/{session_id}")
def delete_session_endpoint(session_id: str, http_request: Request, response: Response):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return owned.mutate(http_request, response, session_id, 'DELETE', {})
    # delete_session is safe if not exists
    delete_session(session_id)
    return {"status": "success", "detail": "Session deleted successfully"}
