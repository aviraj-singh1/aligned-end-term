from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
import copy
import json
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from backend.tests.test_database_schema import schema_engine
from backend.tests.test_auth_api import auth_api
from backend.tests.test_workspace_api import actor, login, mutate as workspace_mutate
from backend.tests.test_api_inspection import create_multi_sheet_excel
from backend.tests.test_session_persistence import durable_reconciliation
from backend.tests.test_upstash_session_store import FakeUpstashRedis, redis_store
from backend.app.services import owned_reconciliation as owned, session_manager
from backend.app.db.models import ReconciliationRun, RunOperation, Client, ClientGSTIN, WorkspaceMembership, AuditEvent
from backend.app.auth.security import utcnow

pytestmark = pytest.mark.postgres


@pytest.fixture
def owned_api(auth_api, monkeypatch, tmp_path, request):
    client, mail, engine = auth_api
    monkeypatch.setenv('AUTH_RECONCILIATION_ENABLED', 'true')
    monkeypatch.setattr(owned, 'get_database_engine', lambda: engine)
    fake = FakeUpstashRedis()
    from backend.app.services.session_store import InMemorySessionStore
    store = InMemorySessionStore() if getattr(request, 'param', 'redis') == 'memory' else redis_store(fake)
    manager = session_manager.SessionManager(store, tmp_path/'sessions')
    monkeypatch.setattr(session_manager, '_MANAGER', manager)
    from backend.app.api.routes.inspection import router as inspection
    from backend.app.api.routes.reconciliation import router as reconciliation
    from backend.app.api.routes.sessions import router as sessions
    from backend.app.api.routes.export import router as exports
    client.app.include_router(inspection, prefix='/api')
    client.app.include_router(reconciliation, prefix='/api')
    client.app.include_router(sessions, prefix='/api/session')
    client.app.include_router(exports, prefix='/api/export')
    who = actor(engine)
    login(client, who)
    with Session(engine, expire_on_commit=False) as db:
        business = Client(workspace_id=who[1].id, created_by=who[0].id, name='Owned client')
        db.add(business)
        db.flush()
        gstin = ClientGSTIN(workspace_id=who[1].id, client_id=business.id, gstin='27ABCDE1234F1Z5')
        db.add(gstin)
        db.commit()
    return client, engine, who, business, gstin, manager, fake


def headers(client, key=None, version=None):
    csrf = client.get('/api/auth/csrf')
    assert csrf.status_code == 200
    return {'X-CSRF-Token':csrf.json()['csrf_token'], 'Idempotency-Key':str(key or uuid4()),
        **({'X-Run-Version':str(version)} if version is not None else {})}


def workbook_files():
    row = ['DEMO-1','2025-04-01','Synthetic Supplier','06ABCDE1234F1Z5',1000,0,90,90]
    acc = create_multi_sheet_excel({'Purchase Register': [['Voucher No.','Date','Particulars','GSTIN/UIN','Value','Igst','CGST','SGST'],row]})
    gst = create_multi_sheet_excel({'B2B': [['Invoice No.','Invoice Date','Party Name','GSTIN/UIN','Taxable Value','IGST Amount','CGST Amount','SGST Amount'],row]})
    return {'accounts_file':('accounts.xlsx',acc,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'gstr_file':('gstr.xlsx',gst,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}


def upload(fixture, key=None, files=None):
    client, _, who, business, gstin, _, _ = fixture
    return client.post('/api/inspect', files=files or workbook_files(), headers={**headers(client,key),
        'X-Workspace-ID':str(who[1].id), 'X-Client-ID':str(business.id), 'X-Client-GSTIN-ID':str(gstin.id)})


def seed_review(fixture):
    _, engine, who, business, gstin, manager, _ = fixture
    sid = durable_reconciliation(manager)
    with Session(engine, expire_on_commit=False) as db:
        run = ReconciliationRun(workspace_id=who[1].id, client_id=business.id, client_gstin_id=gstin.id,
            created_by=who[0].id, redis_session_id=sid, status='COMPLETED', completed_at=utcnow(),
            result_version=1, storage_version=1, session_expires_at=utcnow()+timedelta(hours=1), artifacts_available=True)
        db.add(run)
        db.commit()
    snapshot = manager.store.get(sid)
    snapshot.update(owned_run_id=str(run.id), owned_version=1, owned_phase='COMPLETED', owned_result_version=1)
    manager.store.save(sid, snapshot)
    return sid, run


@pytest.mark.parametrize('owned_api', ['memory', 'redis'], indirect=True)
def test_upload_reconcile_resume_exports_and_delete(owned_api):
    client, engine, who, business, _, manager, _ = owned_api
    inspection = upload(owned_api)
    assert inspection.status_code == 200, inspection.text
    sid = inspection.json()['session_id']
    assert inspection.headers['x-run-version'] == '1'
    assert set(inspection.json()) == {'session_id','files','mappings','available_columns','preview','warnings'}
    route = f'/api/workspaces/{who[1].id}/clients/{business.id}/runs'
    assert client.get(route).json()['items'][0]['availability'] == 'AVAILABLE'
    assert client.get(route+'/'+sid+'/open').json()['phase'] == 'READY'
    result = client.post('/api/reconcile?session_id='+sid,json={'mapping_overrides':{}},headers=headers(client,version=1))
    assert result.status_code == 200, result.text
    assert result.json()['counts']['perfectly_matched'] == 1
    assert result.headers['x-run-version'] == '2'
    assert client.get('/api/session/'+sid+'/results').json()['total'] == 1
    assert client.get(route+'/'+sid+'/open').json()['phase'] == 'COMPLETED'
    before = client.get('/api/export/'+sid+'/json')
    assert before.status_code == 200
    assert 'owned_receipt' not in before.text and 'workspace_id' not in before.text
    assert client.get('/api/export/'+sid+'/excel').content[:2] == b'PK'
    snapshot = manager.store.get(sid)
    for path in snapshot['reports'].values():
        from pathlib import Path
        Path(path).unlink()
    assert client.get('/api/export/'+sid+'/json').json().keys() == before.json().keys()
    key = uuid4()
    deleted = client.delete('/api/session/'+sid,headers=headers(client,key,2))
    assert deleted.status_code == 200
    assert client.delete('/api/session/'+sid,headers=headers(client,key,2)).json() == deleted.json()
    assert client.get('/api/export/'+sid+'/json').status_code == 410
    assert client.get(route).json()['items'][0]['availability'] == 'DELETED'
    tombstone = manager.store.get(sid)
    assert 'reconciliation_summary' not in tombstone
    with Session(engine) as db:
        assert len(db.exec(select(AuditEvent).where(AuditEvent.client_id == business.id, AuditEvent.action.like('RUN_%'))).all()) == 3


def test_upload_retry_is_idempotent_and_payload_bound(owned_api):
    client, _, _, _, _, manager, _ = owned_api
    key = uuid4()
    files = workbook_files()
    first = upload(owned_api,key,files)
    second = upload(owned_api,key,files)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    changed = {**files, 'accounts_file':('different.xlsx',files['accounts_file'][1],files['accounts_file'][2])}
    assert upload(owned_api,key,changed).status_code == 409


def test_client_level_reconciliation_completes_without_tally_candidate_snapshot(owned_api):
    client, engine, who, business, _, _, _ = owned_api
    inspection = client.post('/api/inspect', files=workbook_files(), headers={
        **headers(client), 'X-Workspace-ID':str(who[1].id), 'X-Client-ID':str(business.id),
    })
    assert inspection.status_code == 200, inspection.text
    sid = inspection.json()['session_id']
    result = client.post('/api/reconcile?session_id='+sid, json={'mapping_overrides':{}},
        headers=headers(client, version=1))
    assert result.status_code == 200, result.text
    with Session(engine) as db:
        run = db.exec(select(ReconciliationRun).where(ReconciliationRun.redis_session_id == sid)).one()
        assert run.status == 'COMPLETED'
        assert run.client_gstin_id is None
        assert run.tally_candidate_snapshot_version is None


@pytest.mark.parametrize('path,method', [('session/{sid}','GET'),('session/{sid}/results','GET'),('export/{sid}/json','GET'),('export/{sid}/excel','GET'),('export/{sid}/notes/excel','GET'),('session/{sid}','DELETE'),('manual-match?session_id={sid}','POST'),('reconcile?session_id={sid}','POST')])
def test_foreign_and_unassigned_users_cannot_touch_storage(owned_api, monkeypatch, path, method):
    client, engine, who, _, _, manager, _ = owned_api
    sid, _ = seed_review(owned_api)
    outsider = actor(engine)
    unassigned = actor(engine, workspace=who[1], role='ACCOUNTANT')
    reads = []
    monkeypatch.setattr(manager.store, 'get', lambda *args: reads.append(args) or None)
    for denied in [outsider, unassigned]:
        login(client, denied)
        response = client.request(method, '/api/'+path.format(sid=sid),json={},headers=headers(client,version=1))
        assert response.status_code == 404
    assert reads == []


def test_upload_authorized_before_body_parsing_and_anonymous_sessions_rejected(owned_api, monkeypatch):
    client, engine, who, business, _, manager, _ = owned_api
    legacy = manager.create()
    assert client.get('/api/session/'+legacy).status_code == 404
    outsider = actor(engine)
    login(client, outsider)
    response = client.post('/api/inspect', content=b'broken multipart data',headers={**headers(client),
        'X-Workspace-ID':str(who[1].id),'X-Client-ID':str(business.id),'Content-Type':'multipart/form-data'})
    assert response.status_code == 404  # parser would otherwise reject missing boundary with 400


def test_manual_match_receipt_recovers_sql_failure_without_double_consumption(owned_api, monkeypatch):
    client, engine, _, _, _, manager, _ = owned_api
    sid, run = seed_review(owned_api)
    key = uuid4()
    original = owned.finalize
    monkeypatch.setattr(owned, 'finalize', lambda *args: (_ for _ in ()).throw(SQLAlchemyError('simulated SQL outage')))
    payload = {'result_id':'result-1','candidate_id':'candidate-1'}
    failed = client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,key,1))
    assert failed.status_code == 503
    snapshot = manager.store.get(sid)
    assert snapshot['owned_version'] == 2
    monkeypatch.setattr(owned, 'finalize', original)
    retry = client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,key,1))
    assert retry.status_code == 200, retry.text
    assert manager.store.get(sid)['owned_version'] == 2
    with Session(engine) as db:
        assert db.get(ReconciliationRun,run.id).result_version == 2
        assert len(db.exec(select(AuditEvent).where(AuditEvent.reconciliation_run_id == run.id,AuditEvent.action == 'RUN_MANUAL_MATCH')).all()) == 1


def test_lost_receipt_stays_unresolved_and_never_replays(owned_api, monkeypatch):
    client, engine, _, _, _, manager, fake = owned_api
    sid, run = seed_review(owned_api)
    original = owned.finalize
    monkeypatch.setattr(owned,'finalize',lambda *args: (_ for _ in ()).throw(SQLAlchemyError('SQL outage')))
    key = uuid4()
    payload = {'result_id':'result-1','candidate_id':'candidate-1'}
    assert client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,key,1)).status_code == 503
    fake.advance(3601)
    monkeypatch.setattr(owned,'finalize',original)
    assert client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,key,1)).status_code == 409
    with Session(engine) as db:
        assert db.exec(select(RunOperation).where(RunOperation.run_id == run.id)).one().state == 'UNRESOLVED'
    assert manager.store.get(sid) is None


def test_revoked_membership_and_inactive_gstin_deny_existing_run(owned_api):
    client, engine, who, _, gstin, _, _ = owned_api
    sid, _ = seed_review(owned_api)
    with Session(engine) as db:
        row = db.get(ClientGSTIN,gstin.id)
        row.status = 'INACTIVE'
        db.add(row)
        db.commit()
    assert client.get('/api/session/'+sid).status_code == 404
    with Session(engine) as db:
        row = db.get(ClientGSTIN,gstin.id)
        row.status = 'ACTIVE'
        member = db.get(WorkspaceMembership,who[2].id)
        # This test exercises revoked non-owner access; Owner immutability is covered separately.
        member.role = 'ADMIN'
        member.status = 'REMOVED'
        db.add_all([row,member])
        db.commit()
    assert client.get('/api/session/'+sid).status_code == 404


def test_csrf_versions_conflicts_and_expiry(owned_api):
    client, engine, _, _, _, manager, _ = owned_api
    sid, run = seed_review(owned_api)
    assert client.delete('/api/session/'+sid).status_code == 403
    payload = {'result_id':'result-1','candidate_id':'candidate-1'}
    assert client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,version=0)).status_code == 409
    assert manager.store.get(sid)['owned_version'] == 1
    with Session(engine) as db:
        row = db.get(ReconciliationRun,run.id)
        row.session_expires_at = utcnow()-timedelta(seconds=1)
        db.add(row)
        db.commit()
    assert client.get('/api/session/'+sid).status_code == 410


def test_recovery_cancels_unpublished_worker_with_fence(owned_api):
    client, _, who, business, _, manager, _ = owned_api
    sid, _ = seed_review(owned_api)
    run, op = owned.reserve(who[0],uuid4(),'MANUAL_MATCH',owned.fingerprint('test',{}),1,sid=sid)
    snapshot, _ = owned.operation_snapshot(who[0],run,op)
    url = f'/api/workspaces/{who[1].id}/clients/{business.id}/runs/{sid}/recover'
    assert client.post(url,json={},headers=headers(client)).status_code == 200
    with pytest.raises(Exception):
        owned.publish(who[0],run,op,snapshot)
    assert manager.store.get(sid)['owned_version'] == 1
    assert client.get('/api/session/'+sid).status_code == 200


@pytest.mark.parametrize('owned_api', ['memory', 'redis'], indirect=True)
def test_simultaneous_same_key_manual_confirmation_applies_once(owned_api):
    client, engine, _, _, _, manager, _ = owned_api
    sid, run = seed_review(owned_api)
    request_headers = headers(client, uuid4(), 1)
    def submit(_):
        return client.post('/api/manual-match?session_id='+sid,
            json={'result_id':'result-1','candidate_id':'candidate-1'},headers=request_headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert [r.status_code for r in responses] == [200,200]
    assert responses[0].json() == responses[1].json()
    assert manager.store.get(sid)['owned_version'] == 2
    with Session(engine) as db:
        assert len(db.exec(select(AuditEvent).where(AuditEvent.reconciliation_run_id == run.id, AuditEvent.action == 'RUN_MANUAL_MATCH')).all()) == 1


def test_real_upstash_receipt_recovery_and_expiry(owned_api, monkeypatch):
    """Optional release gate: only supplied TEST credentials; unique prefix, no scans."""
    import os
    import time
    from backend.app.services.session_store import UpstashRedisSessionStore
    url, token = os.getenv('TEST_UPSTASH_REDIS_REST_URL'), os.getenv('TEST_UPSTASH_REDIS_REST_TOKEN')
    if not url or not token:
        pytest.skip('Real Redis gate not verified: isolated TEST_UPSTASH_REDIS_REST_URL/TOKEN required')
    client, engine, _, _, _, manager, _ = owned_api
    store = UpstashRedisSessionStore(url, token, ttl_seconds=3)
    store.KEY_PREFIX = 'aligned-owned-integration:'+str(uuid4())+':'
    manager.store = store
    sid, run = seed_review(owned_api)
    original = owned.finalize
    try:
        monkeypatch.setattr(owned,'finalize',lambda *args: (_ for _ in ()).throw(SQLAlchemyError('injected finalize outage')))
        key = uuid4()
        payload = {'result_id':'result-1','candidate_id':'candidate-1'}
        assert client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,key,1)).status_code == 503
        assert store.get(sid)['owned_version'] == 2
        monkeypatch.setattr(owned,'finalize',original)
        assert client.post('/api/manual-match?session_id='+sid,json=payload,headers=headers(client,key,1)).status_code == 200
        time.sleep(4)
        assert store.get(sid) is None
        assert client.get('/api/session/'+sid).status_code == 410
    finally:
        store.delete(sid)  # Only this test-owned prefixed key.
