from fastapi import APIRouter, HTTPException, Request

from backend.app.services.session_manager import get_session
from backend.app.services.reconciliation_orchestrator import _generate_exports
from backend.app.services.object_storage import artifact_available, artifact_response, artifact_size
from backend.app.transport_limits import MAX_JSON_EXPORT_BYTES

router = APIRouter()

@router.get("/{session_id}/notes/excel")
def export_notes_excel(session_id: str, http_request: Request):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return owned.export(http_request.state.owned_context[0], session_id, 'notes_excel')
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Reconciliation session has expired or not found.")
    notes_summary = session.get("notes_summary")
    if not notes_summary or not notes_summary.results:
        raise HTTPException(status_code=409, detail="No Notes reconciliation results are available for this run.")
    path = session.get("reports", {}).get("notes_excel")
    if not artifact_available(path, verify_remote=True):
        try:
            _generate_exports(session_id)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to generate Notes Excel report.")
        session = get_session(session_id)
        path = session.get("reports", {}).get("notes_excel") if session else None
    if not artifact_available(path, verify_remote=True):
        raise HTTPException(status_code=500, detail="Failed to generate Notes Excel report.")
    return artifact_response(
        path,
        filename="GSTR2B_Notes_Reconciliation.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@router.get("/{session_id}/excel")
def export_excel(session_id: str, http_request: Request):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return owned.export(http_request.state.owned_context[0], session_id, 'excel')
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Reconciliation session has expired or not found.")
        
    if not session.get("reconciliation_summary"):
        raise HTTPException(status_code=400, detail="Reconciliation not yet complete for this session.")
        
    reports = session.get("reports", {})
    excel_path = reports.get("excel")
    
    if not artifact_available(excel_path, verify_remote=True):
        try:
            _generate_exports(session_id)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to generate Excel report.")
        session = get_session(session_id)
        reports = session.get("reports", {}) if session else {}
        excel_path = reports.get("excel")
        
    if not artifact_available(excel_path, verify_remote=True):
        raise HTTPException(status_code=500, detail="Failed to generate Excel report.")
        
    filename = "GSTR2B_Reconciliation.xlsx"
    return artifact_response(
        excel_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@router.get("/{session_id}/json")
def export_json(session_id: str, http_request: Request):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return owned.export(http_request.state.owned_context[0], session_id, 'json')
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Reconciliation session has expired or not found.")
        
    if not session.get("reconciliation_summary"):
        raise HTTPException(status_code=400, detail="Reconciliation not yet complete for this session.")
        
    reports = session.get("reports", {})
    json_path = reports.get("json")
    
    if not artifact_available(json_path, verify_remote=True):
        try:
            _generate_exports(session_id)
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to generate JSON report.")
        session = get_session(session_id)
        reports = session.get("reports", {}) if session else {}
        json_path = reports.get("json")
        
    if not artifact_available(json_path, verify_remote=True):
        raise HTTPException(status_code=500, detail="Failed to generate JSON report.")
        
    filename = "GSTR2B_Reconciliation.json"
    if artifact_size(json_path) > MAX_JSON_EXPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail="JSON export is too large for the current pilot deployment. Download the Excel report instead.",
        )
    return artifact_response(
        json_path,
        filename=filename,
        media_type="application/json",
    )
