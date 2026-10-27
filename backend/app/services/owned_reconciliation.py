"""Authorized run ownership and staged, receipt-backed session publication.

SQL intent -> compute against a private snapshot -> one store CAS with receipt
-> SQL completion/audit. No SQL transaction spans parsing, matching or exports.
Uncertain store/SQL failures leave PENDING intent for recovery, never blind replay.
"""
import copy
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlmodel import Session, select
from backend.app.auth.dependencies import current_user
from backend.app.auth.security import utcnow, validate_csrf
from backend.app.db.models import ReconciliationRun, RunOperation, ClientGSTIN, AuditEvent
from backend.app.db.session import get_database_engine
from backend.app.services import session_manager as sessions
from backend.app.services.session_store import InMemorySessionStore
from backend.app.services.workspace_service import scope, client_scope, missing
from backend.app.transport_limits import MAX_COMBINED_UPLOAD_BYTES


def enabled():
    return os.getenv('AUTH_RECONCILIATION_ENABLED', 'false').lower() == 'true'


def identifier(value, message='Resource not found'):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, message) from None


def client_access(db, user, wid, cid, gid=None):
    _, member = scope(db, user, wid)
    client = client_scope(db, member, cid)
    if client.status != 'ACTIVE':
        missing()
    if gid and not db.exec(select(ClientGSTIN).where(ClientGSTIN.id == gid,
        ClientGSTIN.workspace_id == wid, ClientGSTIN.client_id == cid, ClientGSTIN.status == 'ACTIVE')).first():
        missing()


def load_run(db, user, sid):
    run = db.exec(select(ReconciliationRun).where(ReconciliationRun.redis_session_id == identifier(sid))).first()
    if not run:
        missing()  # Never adopt an old anonymous session.
    client_access(db, user, run.workspace_id, run.client_id, run.client_gstin_id)
    return db.exec(select(ReconciliationRun).where(ReconciliationRun.id == run.id).with_for_update()
                   .execution_options(populate_existing=True)).one()


def authorize_request(request):
    """Middleware runs this BEFORE multipart parsing, storage reads or file handling."""
    user = current_user(request)
    if request.method in {'POST', 'DELETE'}:
        validate_csrf(request)
    if request.url.path == '/api/inspect':
        wid = identifier(request.headers.get('x-workspace-id'))
        cid = identifier(request.headers.get('x-client-id'))
        raw_gid = request.headers.get('x-client-gstin-id')
        gid = identifier(raw_gid) if raw_gid else None
        with Session(get_database_engine()) as db:
            client_access(db, user, wid, cid, gid)
        return user, {'workspace_id': wid, 'client_id': cid, 'gstin_id': gid}
    sid = request.query_params.get('session_id') if request.url.path in {'/api/reconcile', '/api/manual-match'} else request.url.path.split('/')[3]
    with Session(get_database_engine()) as db:
        load_run(db, user, sid)
    return user, {'session_id': sid}


def operation_key(request):
    raw = request.headers.get('idempotency-key')
    try:
        return UUID(raw or '')
    except ValueError:
        raise HTTPException(422, 'A UUID Idempotency-Key header is required') from None


def requested_version(request):
    try:
        version = int(request.headers.get('x-run-version', ''))
        if version < 0:
            raise ValueError()
        return version
    except ValueError:
        raise HTTPException(422, 'A nonnegative X-Run-Version header is required') from None


def fingerprint(kind, data):
    return hashlib.sha256(json.dumps([kind, data], sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def store_snapshot(sid):
    try:
        return copy.deepcopy(sessions._MANAGER.store.get(str(sid)))
    except Exception:
        raise HTTPException(503, 'Temporary result storage is unavailable. Retry the same operation later.') from None


def valid_receipt(snapshot, op):
    receipt = (snapshot or {}).get('owned_receipt', {})
    return (receipt.get('id') == str(op.id) and receipt.get('fingerprint') == op.fingerprint
            and snapshot.get('owned_run_id') == str(op.run_id)
            and snapshot.get('owned_version') == op.expected_version + 1)


def finalize(user, sid, op_id, snapshot):
    with Session(get_database_engine()) as db:
        run = load_run(db, user, sid)
        op = db.get(RunOperation, op_id)
        if not op or op.run_id != run.id or not valid_receipt(snapshot, op):
            raise HTTPException(409, 'Operation receipt does not match this run')
        if op.state == 'FINALIZED':
            return
        if op.state not in {'PENDING', 'UNRESOLVED', 'FAILED'} or run.storage_version != op.expected_version:
            raise HTTPException(409, 'Run changed; refresh before continuing')
        receipt = snapshot['owned_receipt']
        run.storage_version = snapshot['owned_version']
        run.result_version = snapshot.get('owned_result_version', 0)
        run.session_expires_at = datetime.fromisoformat(receipt['expires_at'])
        run.status = snapshot.get('owned_phase', 'READY')
        run.artifacts_available = not snapshot.get('owned_deleted', False)
        if snapshot.get('owned_deleted'):
            run.deleted_at = datetime.fromisoformat(receipt['applied_at'])
        if run.status == 'COMPLETED' and not snapshot.get('owned_deleted'):
            run.completed_at = datetime.fromisoformat(receipt['applied_at'])
            # Tally exports are GSTIN-scoped. Client-level reconciliations remain
            # valid, but intentionally have no durable Tally candidate snapshot.
            if run.client_gstin_id is not None:
                from .tally_export_candidate_service import persist_authoritative_purchase_candidates
                summary = snapshot.get('reconciliation_summary')
                if summary is None:
                    raise HTTPException(409, 'Completed reconciliation state has no authoritative result summary')
                persist_authoritative_purchase_candidates(db, run, summary)
        op.state = 'FINALIZED'
        db.add_all([run, op])
        db.add(AuditEvent(workspace_id=run.workspace_id, client_id=run.client_id, reconciliation_run_id=run.id,
            actor_user_id=op.actor_user_id, operation_id=op.id, action='RUN_'+op.kind,
            entity_type='reconciliation_runs', entity_id=run.id))
        db.commit()


def recover(user, sid, cancel_unpublished=False):
    with Session(get_database_engine(), expire_on_commit=False) as db:
        run = load_run(db, user, sid)
        op = db.exec(select(RunOperation).where(RunOperation.run_id == run.id,
            RunOperation.state.in_(['PENDING', 'UNRESOLVED']))).first()
    if not op:
        return
    snapshot = store_snapshot(sid)
    if valid_receipt(snapshot, op):
        finalize(user, sid, op.id, snapshot)
        return
    # Only a never-published fresh INSPECT can be retried without an existing snapshot.
    lost = (snapshot is None and (op.kind != 'INSPECT' or op.created_at + timedelta(hours=1) <= utcnow()))
    if lost or (snapshot and snapshot.get('owned_version') != op.expected_version):
        with Session(get_database_engine()) as db:
            load_run(db, user, sid)
            current = db.get(RunOperation, op.id)
            if current.state == 'PENDING':
                current.state = 'UNRESOLVED'
                db.add(current)
                db.commit()
    elif cancel_unpublished:
        fail_computation(user, run, op)


def reserve(user, key, kind, digest, version, *, sid=None, context=None):
    if sid:
        recover(user, sid)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        if sid:
            run = load_run(db, user, sid)
            wid, cid = run.workspace_id, run.client_id
        else:
            wid, cid = context['workspace_id'], context['client_id']
            client_access(db, user, wid, cid, context['gstin_id'])
        previous = db.exec(select(RunOperation).where(RunOperation.workspace_id == wid, RunOperation.operation_key == key)).first()
        if previous:
            if (previous.fingerprint != digest or previous.kind != kind or previous.client_id != cid
                or previous.actor_user_id != user.id or (sid and previous.run_id != run.id)):
                raise HTTPException(409, 'Idempotency key was already used for a different request')
            run = db.get(ReconciliationRun, previous.run_id)
            if previous.state in {'FAILED', 'UNRESOLVED'}:
                raise HTTPException(409, 'This operation failed or has an unresolved outcome. Do not replay it; inspect run history.')
            if previous.kind == 'INSPECT' and run.session_expires_at and run.session_expires_at <= utcnow():
                raise HTTPException(410, 'This upload has expired. Start a new upload.')
            return run, previous
        if not sid:
            run = ReconciliationRun(workspace_id=wid, client_id=cid, client_gstin_id=context['gstin_id'],
                created_by=user.id, redis_session_id=uuid4(), started_at=utcnow(), engine_version='existing-engine',
                session_expires_at=utcnow()+timedelta(hours=1))
            db.add(run)
            db.flush()
        if run.deleted_at:
            raise HTTPException(410, 'This run was deleted')
        if run.session_expires_at and run.session_expires_at <= utcnow():
            raise HTTPException(410, 'This run has expired. Upload the files again.')
        if db.exec(select(RunOperation).where(RunOperation.run_id == run.id, RunOperation.state.in_(['PENDING','UNRESOLVED']))).first():
            raise HTTPException(409, 'Another operation is pending or unresolved. Retry that operation or recover the run first.')
        if run.storage_version != version:
            raise HTTPException(409, 'Run changed. Reopen the run to load the latest version.')
        op = RunOperation(workspace_id=wid, client_id=cid, run_id=run.id, actor_user_id=user.id,
            operation_key=key, kind=kind, fingerprint=digest, expected_version=version)
        db.add(op)
        db.commit()
        return run, op


def fail_computation(user, run, op):
    sid = str(run.redis_session_id)
    snapshot = store_snapshot(sid)
    if valid_receipt(snapshot, op):
        finalize(user, sid, op.id, snapshot)
        return
    store = sessions._MANAGER.store
    if snapshot is None and op.kind == 'INSPECT':
        # A minimal cancellation fence prevents a still-running upload attempt
        # from winning SET NX after this intent is canceled.
        store.create_if_absent(sid, {'id': sid, 'owned_run_id': str(run.id), 'owned_version': 0,
            'owned_active_operation': 'canceled:'+str(op.id), 'last_accessed': datetime.now(), 'created_at': datetime.now()})
    elif snapshot is not None:
        def fence(stored):
            if valid_receipt(stored, op):
                return
            if stored.get('owned_version') != op.expected_version:
                raise HTTPException(409, 'Cannot cancel an operation with a different stored version')
            if stored.get('owned_active_operation') not in {None, str(op.id), 'canceled:'+str(op.id)}:
                raise HTTPException(409, 'Another operation owns this run version')
            stored['owned_active_operation'] = 'canceled:'+str(op.id)
        store.mutate(sid, fence)
    else:
        recover(user, sid)
        raise HTTPException(409, 'Stored outcome is missing; this operation remains unresolved')
    current_snapshot = store_snapshot(sid)
    if valid_receipt(current_snapshot, op):
        finalize(user, sid, op.id, current_snapshot)
        return
    with Session(get_database_engine()) as db:
        current = load_run(db, user, run.redis_session_id)
        operation = db.get(RunOperation, op.id)
        if operation.state == 'PENDING':
            operation.state = 'FAILED'
            db.add(operation)
            if op.kind == 'INSPECT':
                current.status, current.failed_at = 'FAILED', utcnow()
                db.add(current)
            db.commit()


class ScratchManager(sessions.SessionManager):
    def __init__(self, sid, snapshot=None):
        self.sid = str(sid)
        # Each attempt owns its export paths: a losing CAS cannot overwrite a winner's files.
        root = sessions._MANAGER.base_temp_dir / self.sid / 'attempts' / str(uuid4())
        super().__init__(InMemorySessionStore(), root)
        if snapshot is not None:
            self.store.save(self.sid, copy.deepcopy(snapshot))

    def create(self):
        now = datetime.now()
        self.store.save(self.sid, {'id': self.sid, 'created_at': now, 'last_accessed': now,
            'directory': self._directory(self.sid), 'files': {}, 'inspection_results': None,
            'reconciliation_summary': None, 'notes_summary': None, 'reports': {}})
        return self.sid


def publish(user, run, op, snapshot):
    sid = str(run.redis_session_id)
    with Session(get_database_engine()) as db:
        current = load_run(db, user, sid)  # Recheck permissions after potentially long computation.
        operation = db.get(RunOperation, op.id)
        if operation.state not in {'PENDING', 'FINALIZED'}:
            raise HTTPException(409, 'This operation is no longer publishable')
        if current.deleted_at and op.kind != 'DELETE':
            raise HTTPException(410, 'This run was deleted')
        if current.session_expires_at and current.session_expires_at <= utcnow():
            raise HTTPException(410, 'This run expired before publication')
    now = utcnow()
    snapshot['owned_run_id'] = str(run.id)
    snapshot.pop('owned_active_operation', None)
    snapshot['owned_version'] = op.expected_version + 1
    snapshot['owned_receipt'] = {'id': str(op.id), 'fingerprint': op.fingerprint,
        'applied_at': now.isoformat(), 'expires_at': (now+timedelta(hours=1)).isoformat()}
    snapshot['last_accessed'] = datetime.now()
    store = sessions._MANAGER.store
    try:
        if op.kind == 'INSPECT':
            store.create_if_absent(sid, snapshot)
        else:
            def apply(stored):
                if valid_receipt(stored, op):
                    return True
                if (stored.get('owned_run_id') != str(run.id) or stored.get('owned_version') != op.expected_version
                    or stored.get('owned_active_operation') != str(op.id)):
                    raise HTTPException(409, 'Run changed before publication; recover the pending operation')
                stored.clear()
                stored.update(snapshot)
                return True
            if store.mutate(sid, apply) is None:
                raise HTTPException(410, 'Results expired before publication. The operation outcome requires recovery.')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'Publication outcome is uncertain. Retry with the same operation key to recover it.') from None
    applied = store_snapshot(sid)
    if not valid_receipt(applied, op):
        raise HTTPException(409, 'Publication receipt is unavailable; recover this run before continuing')
    finalize(user, sid, op.id, applied)
    return applied


def operation_snapshot(user, run, op):
    snapshot = store_snapshot(run.redis_session_id)
    if valid_receipt(snapshot, op):
        finalize(user, run.redis_session_id, op.id, snapshot)
        return snapshot, True
    if op.state == 'FINALIZED':
        raise HTTPException(409, 'This operation was already applied. Reopen the run for its current results.')
    if op.kind != 'INSPECT' and (not snapshot or snapshot.get('owned_run_id') != str(run.id)):
        recover(user, run.redis_session_id)
        raise HTTPException(410, 'Results are unavailable; pending operation outcome may be unresolved')
    if snapshot and snapshot.get('owned_version') != op.expected_version:
        raise HTTPException(409, 'Run version changed; recover before continuing')
    if snapshot is not None:
        with Session(get_database_engine()) as db:
            load_run(db, user, run.redis_session_id)
            current = db.get(RunOperation, op.id)
            if current.state != 'PENDING':
                raise HTTPException(409, 'Operation is no longer pending')
            failed_ids = {str(row.id) for row in db.exec(select(RunOperation).where(RunOperation.run_id == run.id,
                RunOperation.expected_version == op.expected_version, RunOperation.state == 'FAILED')).all()}
            failed_ids |= {'canceled:'+value for value in list(failed_ids)}
        def claim(stored):
            if valid_receipt(stored, op):
                return True
            if stored.get('owned_version') != op.expected_version or stored.get('owned_active_operation') not in {None, str(op.id), *failed_ids}:
                raise HTTPException(409, 'Another operation owns this run version')
            stored['owned_active_operation'] = str(op.id)
            return True
        # New claims fence workers belonging to a failed operation at this version.
        if sessions._MANAGER.store.mutate(str(run.redis_session_id), claim) is None:
            raise HTTPException(410, 'Temporary results expired before the operation started')
        snapshot = store_snapshot(run.redis_session_id)
        if valid_receipt(snapshot, op):
            finalize(user, run.redis_session_id, op.id, snapshot)
            return snapshot, True
    return snapshot, False


def response_for(snapshot, kind):
    if kind == 'INSPECT':
        return snapshot['inspection_results']
    if kind == 'DELETE':
        return {'status': 'success', 'detail': 'Session deleted successfully'}
    from .reconciliation_orchestrator import build_reconciliation_summary_response
    return build_reconciliation_summary_response(snapshot['id'], snapshot['reconciliation_summary'], snapshot['notes_summary'])


def version_header(response, snapshot):
    response.headers['X-Run-Version'] = str(snapshot['owned_version'])


async def inspect(request, response, files, inspect_function):
    user, context = request.state.owned_context
    hashes, size = [], 0
    for file in files:
        if file is None:
            hashes.append(None)
            continue
        digest = hashlib.sha256()
        while chunk := await file.read(1024*1024):
            size += len(chunk)
            if size > MAX_COMBINED_UPLOAD_BYTES:
                raise HTTPException(413, 'Combined upload exceeds the 25 MB combined upload limit.')
            digest.update(chunk)
        await file.seek(0)
        hashes.append([file.filename, digest.hexdigest()])
    key = operation_key(request)
    digest = fingerprint('INSPECT', [context, hashes])
    run, op = reserve(user, key, 'INSPECT', digest, 0, context=context)
    snapshot, complete = operation_snapshot(user, run, op)
    if not complete:
        manager = ScratchManager(run.redis_session_id)
        try:
            with sessions.staged_manager(manager):
                await inspect_function(*files)
            snapshot = manager.store.get(str(run.redis_session_id))
            snapshot['owned_phase'], snapshot['owned_result_version'] = 'READY', 0
        except Exception:
            fail_computation(user, run, op)
            raise
        snapshot = publish(user, run, op, snapshot)
    version_header(response, snapshot)
    return response_for(snapshot, 'INSPECT')


def mutate(request, response, sid, kind, payload):
    user, _ = request.state.owned_context
    version = requested_version(request)
    run, op = reserve(user, operation_key(request), kind, fingerprint(kind, [sid, version, payload]), version, sid=sid)
    snapshot, complete = operation_snapshot(user, run, op)
    if not complete:
        manager = ScratchManager(sid, snapshot)
        try:
            with sessions.staged_manager(manager):
                if kind == 'RECONCILE':
                    from .reconciliation_orchestrator import run_reconciliation
                    run_reconciliation(sid, payload)
                elif kind == 'MANUAL_MATCH':
                    from .manual_match_service import execute_manual_match
                    execute_manual_match(sid, payload['result_id'], payload['candidate_id'])
                elif kind == 'DELETE':
                    manager.store.save(sid, {'id': sid, 'created_at': snapshot['created_at'],
                        'owned_deleted': True, 'owned_phase': snapshot['owned_phase'],
                        'owned_result_version': snapshot.get('owned_result_version', 0)})
            snapshot = manager.store.get(sid)
            if kind in {'RECONCILE', 'MANUAL_MATCH'}:
                snapshot['owned_phase'] = 'COMPLETED'
                snapshot['owned_result_version'] = run.result_version + 1
        except (ValueError, HTTPException) as error:
            fail_computation(user, run, op)
            if isinstance(error, ValueError):
                raise HTTPException(409 if 'already consumed' in str(error) else 400, str(error)) from None
            raise
        except Exception:
            fail_computation(user, run, op)
            raise HTTPException(500, 'Reconciliation could not be computed; no new state was published') from None
        snapshot = publish(user, run, op, snapshot)
    if kind == 'DELETE':
        # Also runs on an idempotent retry if publication succeeded but the
        # first S3 cleanup request was interrupted.
        from .object_storage import delete_session_artifacts
        delete_session_artifacts(sid)
    version_header(response, snapshot)
    return response_for(snapshot, kind)


def read(user, sid):
    recover(user, sid)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        run = load_run(db, user, sid)
        if run.deleted_at or (run.session_expires_at and run.session_expires_at <= utcnow()):
            raise HTTPException(410, 'This run was deleted or expired. Upload the files again.')
        if db.exec(select(RunOperation).where(RunOperation.run_id == run.id, RunOperation.state.in_(['PENDING','UNRESOLVED']))).first():
            raise HTTPException(409, 'This run has a pending or unresolved operation. Retry the original request or use Recover in run history.')
    snapshot = store_snapshot(sid)
    if not snapshot:
        with Session(get_database_engine()) as db:
            current = load_run(db, user, sid)
            current.artifacts_available = False
            current.session_expires_at = utcnow()
            db.add(current)
            db.commit()
        raise HTTPException(410, 'Temporary results are no longer available. Upload the files again.')
    if snapshot.get('owned_run_id') != str(run.id) or snapshot.get('owned_version') != run.storage_version or snapshot.get('owned_deleted'):
        raise HTTPException(409, 'Run metadata and temporary state disagree; recover before continuing')
    return snapshot


def export(user, sid, format):
    from .reconciliation_orchestrator import _generate_exports
    from .object_storage import artifact_available, artifact_response, artifact_size
    from backend.app.transport_limits import MAX_JSON_EXPORT_BYTES
    snapshot = read(user, sid)
    if not snapshot.get('reconciliation_summary'):
        raise HTTPException(400, 'Reconciliation not yet complete for this session.')
    notes_summary = snapshot.get('notes_summary')
    if format == 'notes_excel' and (not notes_summary or not notes_summary.results):
        raise HTTPException(409, 'No Notes reconciliation results are available for this run.')
    path = snapshot.get('reports', {}).get(format)
    if not artifact_available(path, verify_remote=True):
        # Rebuild immutable local artifacts from the authorized snapshot; no shared state mutation.
        manager = ScratchManager(sid, snapshot)
        with sessions.staged_manager(manager):
            _generate_exports(sid)
        path = manager.store.get(sid)['reports'][format]
    if format == 'json' and artifact_size(path) > MAX_JSON_EXPORT_BYTES:
        raise HTTPException(413, 'JSON export is too large for the current pilot deployment. Download the Excel report instead.')
    is_excel = format in ('excel', 'notes_excel')
    extension = 'xlsx' if is_excel else 'json'
    filename = f'GSTR2B_{"Notes_" if format == "notes_excel" else ""}Reconciliation.{extension}'
    return artifact_response(path, filename=filename,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if is_excel else 'application/json',
    )


def history(user, wid, cid, page):
    with Session(get_database_engine()) as db:
        client_access(db, user, wid, cid)
        rows = db.exec(select(ReconciliationRun).where(ReconciliationRun.workspace_id == wid, ReconciliationRun.client_id == cid)
            .order_by(ReconciliationRun.created_at.desc(), ReconciliationRun.id).offset((page-1)*25).limit(26)).all()
        operations = db.exec(select(RunOperation).where(RunOperation.run_id.in_([r.id for r in rows]),
            RunOperation.state.in_(['PENDING','UNRESOLVED']))).all() if rows else []
        pending = {o.run_id: o.state for o in operations}
        return {'items': [dict(id=r.id, session_id=r.redis_session_id, client_gstin_id=r.client_gstin_id, created_at=r.created_at,
            status=r.status, version=r.storage_version, expires_at=r.session_expires_at,
            availability='DELETED' if r.deleted_at else pending.get(r.id) or ('EXPIRED' if not r.session_expires_at or r.session_expires_at <= utcnow() else 'AVAILABLE' if r.artifacts_available else 'UNAVAILABLE')) for r in rows[:25]],
            'has_more': len(rows)>25}
