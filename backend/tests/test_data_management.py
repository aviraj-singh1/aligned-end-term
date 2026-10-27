import csv
import io
import json
import zipfile
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from backend.app.auth.security import utcnow
from backend.app.db.models import (AuditEvent, AuthIdentity, Client, ClientAssignment, ClientGSTIN,
    PasswordCredential, ReconciliationRun, User, Workspace, WorkspaceInvite, WorkspaceMembership)
from backend.tests.test_auth_api import PASSWORD, auth_api, post
from backend.tests.test_database_schema import schema_engine
from backend.tests.test_workspace_api import actor, login, mutate

pytestmark = pytest.mark.postgres


def test_workspace_and_member_exports_are_portable_and_exclude_credentials(auth_api):
    client, _, engine = auth_api
    owner = actor(engine)
    member = actor(engine, workspace=owner[1], role='ACCOUNTANT')
    with Session(engine) as db:
        customer = Client(workspace_id=owner[1].id, name='Export client', created_by=owner[0].id)
        db.add(customer); db.flush()
        db.add(ClientAssignment(workspace_id=owner[1].id, client_id=customer.id,
            membership_id=member[2].id, assigned_by=owner[0].id))
        db.add(AuditEvent(workspace_id=owner[1].id, actor_user_id=member[0].id,
            action='EXPORT_TEST', entity_type='workspace_memberships', entity_id=member[2].id))
        db.commit()
    login(client, owner)
    exported = mutate(client, f'workspaces/{owner[1].id}/data-export')
    assert exported.status_code == 200 and exported.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert {'manifest.json','workspace.json','members.csv','clients.csv','run_operations.csv',
            'invitations.csv','ownership_transfers.csv','audit_log.csv'} <= set(archive.namelist())
        manifest = json.loads(archive.read('manifest.json'))
        assert 'password hashes' in manifest['excludes']
        assert b'Export client' in archive.read('clients.csv')
        assert PASSWORD.encode() not in exported.content
    one = mutate(client, f'workspaces/{owner[1].id}/members/{member[2].id}/data-export')
    assert one.status_code == 200
    with zipfile.ZipFile(io.BytesIO(one.content)) as archive:
        assert json.loads(archive.read('member.json'))['membership']['email'] == member[0].email
        assert b'Export client' in archive.read('client_assignments.csv')


def test_member_exports_follow_workspace_role_hierarchy(auth_api):
    client, _, engine = auth_api
    owner = actor(engine)
    admin = actor(engine, workspace=owner[1], role='ADMIN')
    peer_admin = actor(engine, workspace=owner[1], role='ADMIN')
    accountant = actor(engine, workspace=owner[1], role='ACCOUNTANT')
    login(client, admin)
    root = f'workspaces/{owner[1].id}'
    assert mutate(client, root + '/data-export').status_code == 403
    assert mutate(client, root + f'/members/{owner[2].id}/data-export').status_code == 403
    assert mutate(client, root + f'/members/{peer_admin[2].id}/data-export').status_code == 403
    assert mutate(client, root + f'/members/{admin[2].id}/data-export').status_code == 200
    assert mutate(client, root + f'/members/{accountant[2].id}/data-export').status_code == 200


def test_logs_are_scoped_filterable_and_exportable(auth_api):
    client, _, engine = auth_api
    owner = actor(engine)
    accountant = actor(engine, workspace=owner[1], role='ACCOUNTANT')
    with Session(engine) as db:
        db.add_all([
            AuditEvent(workspace_id=owner[1].id, actor_user_id=owner[0].id, action='OWNER_EVENT', entity_type='users'),
            AuditEvent(workspace_id=owner[1].id, actor_user_id=accountant[0].id, action='ACCOUNTANT_EVENT', entity_type='users'),
        ]); db.commit()
    login(client, owner)
    filtered = client.get(f'/api/workspaces/{owner[1].id}/logs?actor_user_id={accountant[0].id}')
    assert filtered.status_code == 200
    assert {row['action'] for row in filtered.json()['items']} == {'ACCOUNTANT_EVENT'}
    csv_response = mutate(client, f'workspaces/{owner[1].id}/logs/export?actor_user_id={accountant[0].id}')
    assert csv_response.status_code == 200
    assert csv_response.headers['content-type'].startswith('text/csv')
    assert csv_response.headers['content-disposition'].endswith('.csv"')
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert [row['action'] for row in rows] == ['ACCOUNTANT_EVENT']
    login(client, accountant)
    own_view = client.get(f'/api/workspaces/{owner[1].id}/logs').json()
    assert own_view['can_filter_people'] is False
    assert {row['action'] for row in own_view['items']} == {'ACCOUNTANT_EVENT'}


def test_removed_member_can_consent_to_rejoin(auth_api):
    client, mail, engine = auth_api
    owner = actor(engine)
    accountant = actor(engine, workspace=owner[1], role='ACCOUNTANT')
    login(client, owner)
    assert mutate(client, f'workspaces/{owner[1].id}/members/{accountant[2].id}', method='DELETE').status_code == 200
    assert mutate(client, f'workspaces/{owner[1].id}/invites', {'email':accountant[0].email,'role':'ADMIN'}).status_code == 201
    token = mail[-1][2]
    login(client, accountant)
    assert post(client, 'invitations/accept', {'token':token}).status_code == 200
    with Session(engine) as db:
        restored = db.get(WorkspaceMembership, accountant[2].id)
        assert restored.status == 'ACTIVE' and restored.role == 'ADMIN'


def test_explicit_member_removal_clears_assignments_and_protects_owner(auth_api):
    client, _, engine = auth_api
    owner = actor(engine)
    accountant = actor(engine, workspace=owner[1], role='ACCOUNTANT')
    with Session(engine) as db:
        customer = Client(workspace_id=owner[1].id, name='Assigned', created_by=owner[0].id)
        db.add(customer); db.flush()
        db.add(ClientAssignment(workspace_id=owner[1].id, client_id=customer.id,
            membership_id=accountant[2].id, assigned_by=owner[0].id)); db.commit()
    login(client, owner)
    assert mutate(client, f'workspaces/{owner[1].id}/members/{owner[2].id}', method='DELETE').status_code == 403
    removed = mutate(client, f'workspaces/{owner[1].id}/members/{accountant[2].id}', method='DELETE')
    assert removed.status_code == 200
    with Session(engine) as db:
        assert db.get(WorkspaceMembership, accountant[2].id).status == 'REMOVED'
        assert not db.exec(select(ClientAssignment).where(ClientAssignment.membership_id == accountant[2].id)).all()
    assert all(row['id'] != str(accountant[2].id) for row in client.get(f'/api/workspaces/{owner[1].id}/team').json()['members'])


def test_owner_reset_creates_a_fresh_boundary_and_retains_audit(auth_api):
    client, _, engine = auth_api
    owner = actor(engine)
    member = actor(engine, workspace=owner[1], role='ACCOUNTANT')
    with Session(engine) as db:
        customer = Client(workspace_id=owner[1].id, name='Old client', created_by=owner[0].id)
        db.add(customer); db.flush()
        db.add(ClientGSTIN(workspace_id=owner[1].id, client_id=customer.id, gstin='27ABCDE1234F1Z5'))
        db.add(ReconciliationRun(workspace_id=owner[1].id, client_id=customer.id,
            created_by=owner[0].id, status='DRAFT'))
        db.commit()
    login(client, owner)
    wrong = mutate(client, f'workspaces/{owner[1].id}/reset', {'confirmation':'wrong'})
    assert wrong.status_code == 422
    done = mutate(client, f'workspaces/{owner[1].id}/reset', {'confirmation':owner[1].name})
    assert done.status_code == 200
    assert client.get(f'/api/workspaces/{owner[1].id}/clients').json()['items'] == []
    team = client.get(f'/api/workspaces/{owner[1].id}/team').json()['members']
    assert [row['role'] for row in team] == ['OWNER']
    with Session(engine) as db:
        assert db.get(Workspace, owner[1].id).reset_at is not None
        assert db.get(WorkspaceMembership, member[2].id).status == 'REMOVED'
        assert db.exec(select(AuditEvent).where(AuditEvent.workspace_id == owner[1].id,
            AuditEvent.action == 'WORKSPACE_RESET')).one()
    recreated = mutate(client, f'workspaces/{owner[1].id}/clients', {'name':'Fresh client'})
    assert recreated.status_code == 201
    new_id = recreated.json()['id']
    assert mutate(client, f'workspaces/{owner[1].id}/clients/{new_id}/gstins', {'gstin':'27ABCDE1234F1Z5'}).status_code == 201
    # Leave the migration fixture downgradeable after proving an inactive historical GSTIN can be reused.
    with Session(engine) as db:
        fresh = db.exec(select(ClientGSTIN).where(ClientGSTIN.client_id == new_id)).one()
        db.delete(fresh); db.commit()


def test_account_deletion_requires_password_and_transfer_when_firm_has_people(auth_api):
    client, _, engine = auth_api
    owner = actor(engine)
    # actor() bypasses registration, so add the password credential required by account deletion.
    from backend.app.auth.security import PASSWORD_HASHER
    with Session(engine) as db:
        db.add(PasswordCredential(user_id=owner[0].id, password_hash=PASSWORD_HASHER.hash(PASSWORD))); db.commit()
    login(client, owner)
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    wrong = client.request('DELETE','/api/auth/account',json={'password':'wrong','confirmation':'DELETE MY ACCOUNT'},headers={'X-CSRF-Token':csrf})
    assert wrong.status_code == 403
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    deleted = client.request('DELETE','/api/auth/account',json={'password':PASSWORD,'confirmation':'DELETE MY ACCOUNT'},headers={'X-CSRF-Token':csrf})
    assert deleted.status_code == 200
    assert client.get('/api/me').status_code == 401
    with Session(engine) as db:
        person = db.get(User, owner[0].id)
        assert person.status == 'DISABLED' and person.email.endswith('@deleted.invalid')
        assert db.get(Workspace, owner[1].id).status == 'ARCHIVED'


def test_supabase_account_deletion_removes_provider_before_anonymizing_postgres(auth_api, monkeypatch):
    client, _, engine = auth_api
    owner = actor(engine)
    with Session(engine) as db:
        person = db.get(User, owner[0].id)
        person.phone_number, person.phone_verified_at = '+919876543210', utcnow()
        db.add(person)
        db.add(AuthIdentity(user_id=person.id, issuer='https://project.supabase.co/auth/v1',
            subject='supabase-delete-owner', provider='SUPABASE_GOOGLE'))
        db.commit()
    from backend.app.services import auth_service
    monkeypatch.setattr(auth_service, 'supabase_reauthenticate',
        lambda user, token: 'supabase-delete-owner')
    deleted_subjects = []
    monkeypatch.setattr(auth_service, 'delete_supabase_identity', deleted_subjects.append)
    login(client, owner)
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    response = client.request('DELETE', '/api/auth/account', json={
        'supabase_access_token':'x' * 120, 'confirmation':'DELETE MY ACCOUNT',
    }, headers={'X-CSRF-Token':csrf})
    assert response.status_code == 200
    assert deleted_subjects == ['supabase-delete-owner']
    with Session(engine) as db:
        person = db.get(User, owner[0].id)
        assert (person.status, person.phone_number, person.phone_verified_at) == ('DISABLED', None, None)
        assert db.exec(select(AuthIdentity).where(AuthIdentity.user_id == owner[0].id)).first() is None


def test_supabase_deletion_failure_leaves_aligned_account_active(auth_api, monkeypatch):
    client, _, engine = auth_api
    owner = actor(engine)
    with Session(engine) as db:
        db.add(AuthIdentity(user_id=owner[0].id, issuer='https://project.supabase.co/auth/v1',
            subject='supabase-delete-failure', provider='SUPABASE_GOOGLE'))
        db.commit()
    from backend.app.services import auth_service
    monkeypatch.setattr(auth_service, 'supabase_reauthenticate',
        lambda user, token: 'supabase-delete-failure')
    def unavailable(subject):
        from fastapi import HTTPException
        raise HTTPException(503, 'Supabase account administration is temporarily unavailable')
    monkeypatch.setattr(auth_service, 'delete_supabase_identity', unavailable)
    login(client, owner)
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    response = client.request('DELETE', '/api/auth/account', json={
        'supabase_access_token':'x' * 120, 'confirmation':'DELETE MY ACCOUNT',
    }, headers={'X-CSRF-Token':csrf})
    assert response.status_code == 503
    with Session(engine) as db:
        assert db.get(User, owner[0].id).status == 'ACTIVE'
        assert db.exec(select(AuthIdentity).where(AuthIdentity.user_id == owner[0].id)).first() is not None
