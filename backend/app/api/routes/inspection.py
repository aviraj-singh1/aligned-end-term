import os
import json
import zipfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Response
from typing import Optional

from backend.app.services.session_manager import create_session, delete_session, get_session, update_session
from backend.app.services.reconciliation_orchestrator import inspect_session_files
from backend.app.api.schemas import InspectionResponse

router = APIRouter()

from backend.app.transport_limits import MAX_COMBINED_UPLOAD_BYTES
UPLOAD_CHUNK_SIZE = 1024 * 1024
ALLOWED_EXTENSIONS = (".xls", ".xlsx", ".json")
XLS_COMPOUND_FILE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

def _validate_file(file: UploadFile):
    if not file:
        raise HTTPException(status_code=400, detail="File is required")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Supported formats: .xls, .xlsx and .json",
        )

def _content_extension(path: Path) -> str:
    """Identify extensionless inputs from validated container/content markers."""
    excel_extension = _excel_content_extension(path)
    if excel_extension:
        return excel_extension
    try:
        if not zipfile.is_zipfile(path):
            with path.open("r", encoding="utf-8") as source:
                data = json.load(source)
            if isinstance(data, (dict, list)):
                return ".json"
    except (ValueError, UnicodeError, RecursionError, zipfile.BadZipFile):
        pass
    raise HTTPException(
        status_code=400,
        detail="This file could not be recognized as a valid Excel (.xls or .xlsx) or JSON file.",
    )


def _excel_content_extension(path: Path) -> Optional[str]:
    """Return the real Excel container type, independent of the uploaded suffix."""
    try:
        with path.open("rb") as source:
            signature = source.read(len(XLS_COMPOUND_FILE_SIGNATURE))
        if signature == XLS_COMPOUND_FILE_SIGNATURE:
            return ".xls"
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            if {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                return ".xlsx"
    except (OSError, zipfile.BadZipFile):
        return None
    return None


async def _save_file(file: UploadFile, session_dir: str, prefix: str, uploaded_bytes=None) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower()
    safe_name = f"{prefix}{ext}"
    path = Path(session_dir) / safe_name
    size = 0
    uploaded_bytes = uploaded_bytes if uploaded_bytes is not None else [0]
    try:
        with path.open("wb") as destination:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                uploaded_bytes[0] += len(chunk)
                if uploaded_bytes[0] > MAX_COMBINED_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Combined upload exceeds the 25 MB combined upload limit.",
                    )
                destination.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        detected_extension = None
        if not ext:
            detected_extension = _content_extension(path)
        elif ext in (".xls", ".xlsx"):
            # Some accounting products emit an OOXML workbook with a legacy
            # .xls filename (and vice versa). Select the reader by container.
            detected_extension = _excel_content_extension(path)
        if detected_extension and detected_extension != ext:
            path = path.rename(path.with_suffix(detected_extension))
        return str(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _display_filename(file: UploadFile) -> str:
    """Return metadata only; uploaded names never become filesystem paths."""
    return Path((file.filename or "").replace("\\", "/")).name

@router.post("/inspect", response_model=InspectionResponse)
async def inspect_files(
    http_request: Request,
    response: Response,
    accounts_file: UploadFile = File(...),
    gstr_file: UploadFile = File(...),
    debit_notes_file: Optional[UploadFile] = File(None)
):
    if hasattr(http_request.state, 'owned_context'):
        from backend.app.services import owned_reconciliation as owned
        return await owned.inspect(http_request, response, [accounts_file, gstr_file, debit_notes_file], _inspect_files)
    return await _inspect_files(accounts_file, gstr_file, debit_notes_file)


async def _inspect_files(accounts_file, gstr_file, debit_notes_file=None):
    _validate_file(accounts_file)
    _validate_file(gstr_file)
    if debit_notes_file:
        _validate_file(debit_notes_file)
        
    session_id = create_session()
    session = get_session(session_id)
    session_dir = session["directory"]
    
    try:
        uploaded_bytes = [0]
        accounts_path = await _save_file(accounts_file, session_dir, "accounts", uploaded_bytes)
        gstr_path = await _save_file(gstr_file, session_dir, "gstr", uploaded_bytes)
        dn_path = await _save_file(debit_notes_file, session_dir, "debit_notes", uploaded_bytes) if debit_notes_file else None
        
        update_session(session_id, "files", {
            "accounts": accounts_path,
            "gstr": gstr_path,
            "debit_notes": dn_path
        })
        
        update_session(session_id, "original_filenames", {
            "accounts": _display_filename(accounts_file),
            "gstr": _display_filename(gstr_file),
            "debit_notes": _display_filename(debit_notes_file) if debit_notes_file else None
        })

        results = inspect_session_files(session_id)
        return results
        
    except ValueError as e:
        delete_session(session_id)
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        delete_session(session_id)
        raise
    except Exception:
        delete_session(session_id)
        raise HTTPException(status_code=500, detail="Failed to inspect files")
