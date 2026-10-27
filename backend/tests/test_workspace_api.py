"""Real PostgreSQL tenant, permission and invitation regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlmodel import Session, select
from backend.tests.test_database_schema import schema_engine
from backend.tests.test_auth_api import auth_api, post, PASSWORD
from backend.app.auth.security import digest, utcnow
from backend.app.db.models import User, Workspace, WorkspaceMembership, AuthSession, WorkspaceInvite, AuditEvent, Client, PasswordCredential, AuthActionToken
from backend.app.services import workspace_service as service
from backend.app.api.workspace_schemas import MemberInput
from backend.app.services import ownership_service

pytestmark = pytest.mark.postgres


def actor(engine, kind='FIRM', verified=True, workspace=None, role='OWNER'):
    raw = str(uuid4())
    with Session(engine, expire_on_commit=False) as db:
        user = User(name='Workspace test', email=f'{uuid4()}@example.com', email_normalized=f'{uuid4()}@example.com', email_verified_at=utcnow() if verified else None)
        user.email_normalized = user.email
        db.add(user)
        db.flush()
        if workspace is None:
            workspace = Workspace(name='Test workspace', type=kind, created_by=user.id)
            db.add(workspace)
            db.flush()
        member = WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role=role, joined_at=utcnow())
        db.add(member)
        db.add(AuthSession(user_id=user.id, token_hash=digest(raw), expires_at=utcnow()+timedelta(hours=2), idle_expires_at=utcnow()+timedelta(hours=1), last_seen_at=utcnow()))
        db.commit()
        return user, workspace, member, raw


def login(client, who):
    client.cookies.clear()
    client.cookies.set('aligned_dev_session', who[3])


def test_managed_account_exists_before_activation_and_login_alias_works(auth_api):
    client, mail, engine = auth_api
    owner = actor(engine)
    login(client, owner)
    root=f'workspaces/{owner[1].id}'
    data={'name':'Created Colleague','email':f'{uuid4()}@example.com','username':'suyash','role':'ACCOUNTANT'}
    response=mutate(client,root+'/accounts',data)
    assert response.status_code==201, response.text
    alias=response.json()['login_id']
    assert alias.startswith('suyash@') and alias.endswith('.aligned.com')
    members=client.get('/api/'+root+'/team').json()['members']
    created=next(m for m in members if m['email']==data['email'])
    assert created['activation_pending'] is True
    assert len(mail)==1 and mail[0][1]=='ACTIVATE_ACCOUNT'
    assert post(client,'login',{'email':alias,'password':PASSWORD}).status_code==401
    client.cookies.clear()  # The recipient opens activation outside the creator's session.
    assert post(client,'account/activate',{'token':mail[0][2],'password':PASSWORD}).status_code==200
    assert post(client,'account/activate',{'token':mail[0][2],'password':PASSWORD}).status_code==400
    assert post(client,'login',{'email':alias,'password':PASSWORD}).status_code==200
    profile=client.get('/api/me').json()
    assert profile['user']['email_verified'] is True
    assert len(profile['workspaces'])==1 and profile['workspaces'][0]['role']=='ACCOUNTANT'
    assert client.get('/api/'+root+'/clients').json()['items']==[]
    assert mutate(client,root+'/accounts',{**data,'email':f'{uuid4()}@example.com','username':'other'}).status_code==403


def test_managed_account_conflicts_suspension_and_resend(auth_api):
    client, mail, engine=auth_api
    owner=actor(engine);login(client,owner)
    root=f'workspaces/{owner[1].id}'
    data={'name':'Created Colleague','email':f'{uuid4()}@example.com','username':'newuser','role':'ADMIN'}
    assert mutate(client,root+'/accounts',data).status_code==201
    assert mutate(client,root+f'/members/{owner[2].id}',{'role':'ACCOUNTANT','status':'ACTIVE'},'PATCH').status_code==403
    assert mutate(client,root+'/accounts',{**data,'email':f'{uuid4()}@example.com'}).status_code==409
    assert mutate(client,root+'/accounts',{**data,'username':'another'}).status_code==409
    member=next(m for m in client.get('/api/'+root+'/team').json()['members'] if m['email']==data['email'])
    assert mutate(client,root+f"/members/{member['id']}/activation").status_code==200
    assert len(mail)==2
    assert mutate(client,root+f"/members/{member['id']}",{'role':'ACCOUNTANT','status':'SUSPENDED'},'PATCH').status_code==200
    client.cookies.clear()
    assert post(client,'account/activate',{'token':mail[0][2],'password':PASSWORD}).status_code==400
    login(client, owner)
    assert mutate(client,root+f"/members/{member['id']}/activation").status_code==404


@pytest.mark.parametrize('signed_in_as', ['creator', 'accountant', 'outsider'])
def test_activation_rejects_signed_in_browser_without_consuming_token(auth_api, signed_in_as):
    client, mail, engine = auth_api
    owner = actor(engine)
    login(client, owner)
    data = {'name':'Invited Colleague', 'email':f'{uuid4()}@example.com', 'username':'recipient', 'role':'ACCOUNTANT'}
    assert mutate(client, f'workspaces/{owner[1].id}/accounts', data).status_code == 201
    raw = mail[-1][2]
    signed_in = owner if signed_in_as == 'creator' else actor(engine, workspace=owner[1], role='ACCOUNTANT') if signed_in_as == 'accountant' else actor(engine)
    login(client, signed_in)
    with Session(engine) as db:
        person = db.exec(select(User).where(User.email_normalized == data['email'])).one()
        uid = person.id
        original_hash = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == uid)).one().password_hash
    response = post(client, 'account/activate', {'token':raw, 'password':PASSWORD})
    assert response.status_code == 409
    assert 'already signed in' in response.json()['detail']
    assert client.get('/api/me').json()['user']['id'] == str(signed_in[0].id)
    with Session(engine) as db:
        assert db.exec(select(PasswordCredential).where(PasswordCredential.user_id == uid)).one().password_hash == original_hash
        assert db.get(User, uid).email_verified_at is None
        assert db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id == uid)).one().activation_pending
        assert db.exec(select(AuthActionToken).where(AuthActionToken.token_hash == digest(raw))).one().consumed_at is None
    client.cookies.clear()
    assert post(client, 'account/activate', {'token':raw, 'password':PASSWORD}).status_code == 200


def test_dashboard_counts_only_current_client_assignments(auth_api):
    from backend.app.db.models import ClientAssignment, ReconciliationRun
    client, _, engine=auth_api
    owner=actor(engine);accountant=actor(engine,workspace=owner[1],role='ACCOUNTANT');outsider=actor(engine)
    with Session(engine) as db:
        visible=Client(workspace_id=owner[1].id,created_by=owner[0].id,name='Visible')
        hidden=Client(workspace_id=owner[1].id,created_by=owner[0].id,name='Private')
        db.add_all([visible,hidden]);db.flush()
        db.add(ClientAssignment(workspace_id=owner[1].id,client_id=visible.id,membership_id=accountant[2].id,assigned_by=owner[0].id))
        db.add_all([ReconciliationRun(workspace_id=owner[1].id,client_id=c.id,created_by=owner[0].id) for c in [visible,hidden]])
        db.commit()
    route=f'/api/workspaces/{owner[1].id}/dashboard'
    login(client,owner);data=client.get(route).json()
    assert data['clients']==2 and data['runs']==2 and data['accountants']==1
    login(client,accountant);data=client.get(route).json()
    assert data['clients']==1 and data['runs']==1 and data['accountants'] is None
    assert [r['client_name'] for r in data['recent']]==['Visible']
    assert sum(d['runs'] for d in data['daily'])==1
    login(client,outsider);assert client.get(route).status_code==404


def mutate(client, path, data=None, method='POST'):
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    return client.request(method, '/api/'+path, json=data or {}, headers={'X-CSRF-Token':csrf})


@pytest.mark.parametrize('other_role', ['ADMIN', 'ACCOUNTANT'])
def test_client_save_is_bound_to_displayed_account_not_shared_cookie(auth_api, other_role):
    client, _, engine = auth_api
    owner = actor(engine)
    other = actor(engine, workspace=owner[1], role=other_role)
    path = f'/api/workspaces/{owner[1].id}/clients'
    login(client, other)  # Another tab replaced the cookie; the page still shows owner.
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    response = client.post(path, json={'name':'Must not be created'}, headers={
        'X-CSRF-Token':csrf, 'X-Expected-User-ID':str(owner[0].id)})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'ACCOUNT_CHANGED'
    with Session(engine) as db:
        assert not db.exec(select(Client).where(Client.workspace_id == owner[1].id)).all()
    # The hint can never grant administrator rights to the cookie's accountant.
    if other_role == 'ACCOUNTANT':
        response = client.post(path, json={'name':'Still forbidden'}, headers={
            'X-CSRF-Token':csrf, 'X-Expected-User-ID':str(other[0].id)})
        assert response.status_code == 403
    # Re-authenticating as the actual administrator allows the same client form.
    login(client, owner)
    csrf = client.get('/api/auth/csrf').json()['csrf_token']
    response = client.post(path, json={'name':'Administrator client', 'legal_name':'Administrator client', 'pan':'ABCDE1234F'}, headers={
        'X-CSRF-Token':csrf, 'X-Expected-User-ID':str(owner[0].id)})
    assert response.status_code == 201
    with Session(engine) as db:
        created = db.exec(select(Client).where(Client.workspace_id == owner[1].id)).one()
        assert created.created_by == owner[0].id


def test_owner_is_immutable_and_transfer_requires_both_email_consents(auth_api):
    client, mail, engine = auth_api
    owner = actor(engine)
    admin = actor(engine, workspace=owner[1], role='ADMIN')
    login(client, admin)
    root=f'workspaces/{owner[1].id}'
    protected=mutate(client,f'{root}/members/{owner[2].id}',{'role':'ACCOUNTANT','status':'REMOVED'},'PATCH')
    assert protected.status_code==403 and 'Owner cannot' in protected.json()['detail']
    assert mutate(client,f'{root}/ownership-transfers',{'recipient_user_id':str(owner[0].id)}).status_code==403
    login(client,owner)
    # Even the Owner cannot bypass the dedicated transfer workflow.
    assert mutate(client,f'{root}/members/{owner[2].id}',{'role':'ACCOUNTANT','status':'REMOVED'},'PATCH').status_code==403
    started=mutate(client,f'{root}/ownership-transfers',{'recipient_user_id':str(admin[0].id)})
    assert started.status_code==202 and mail[-1][:2]==(owner[0].email,'OWNERSHIP_APPROVE')
    owner_token=mail[-1][2]
    assert post(client,'ownership/preview',{'token':owner_token}).json()['stage']=='OWNER'
    assert post(client,'ownership/confirm',{'token':owner_token}).status_code==200
    assert mail[-1][:2]==(admin[0].email,'OWNERSHIP_ACCEPT')
    recipient_token=mail[-1][2]
    with Session(engine) as db:
        assert db.get(WorkspaceMembership,owner[2].id).role=='OWNER'
        assert db.get(WorkspaceMembership,admin[2].id).role=='ADMIN'
    login(client,admin)
    assert post(client,'ownership/preview',{'token':recipient_token}).json()['stage']=='RECIPIENT'
    completed=post(client,'ownership/confirm',{'token':recipient_token})
    assert completed.status_code==200 and 'transferred' in completed.json()['message']
    assert client.get('/api/me').status_code==401
    with Session(engine) as db:
        assert db.get(WorkspaceMembership,owner[2].id).role=='ADMIN'
        assert db.get(WorkspaceMembership,admin[2].id).role=='OWNER'
        actions=[a.action for a in db.exec(select(AuditEvent).where(AuditEvent.workspace_id==owner[1].id)).all()]
        assert actions[-3:]==['OWNERSHIP_TRANSFER_REQUESTED','OWNERSHIP_TRANSFER_OWNER_CONFIRMED','OWNERSHIP_TRANSFER_COMPLETED']


def test_owner_transfer_can_be_cancelled_and_rejects_wrong_recipient(auth_api):
    client, mail, engine=auth_api
    owner=actor(engine);admin=actor(engine,workspace=owner[1],role='ADMIN');stranger=actor(engine)
    login(client,owner);root=f'workspaces/{owner[1].id}'
    assert mutate(client,f'{root}/ownership-transfers',{'recipient_user_id':str(admin[0].id)}).status_code==202
    raw=mail[-1][2]
    login(client,stranger)
    assert post(client,'ownership/preview',{'token':raw}).status_code==403
    login(client,owner)
    assert mutate(client,f'{root}/ownership-transfers/current',method='DELETE').status_code==200
    assert post(client,'ownership/confirm',{'token':raw}).status_code in (403,409)
    with Session(engine) as db:
        assert db.get(WorkspaceMembership,owner[2].id).role=='OWNER'


def test_individual_clients_gstin_conversion_and_validation(auth_api):
    client, _, engine = auth_api
    who = actor(engine, 'INDIVIDUAL')
    login(client, who)
    root = f'workspaces/{who[1].id}'
    assert client.get('/api/'+root+'/team').status_code == 403
    assert mutate(client, root+'/invites', {'email':'someone@example.com'}).status_code == 403
    assert mutate(client, root+'/clients', {'name':'  '}).status_code == 422
    assert mutate(client, root+'/clients', {'name':'Test', 'workspace_id':str(uuid4())}).status_code == 422
    created = mutate(client, root+'/clients', {'name':'Example client', 'pan':'ABCDE1234F'})
    assert created.status_code == 201
    cid = created.json()['id']
    path = root+'/clients/'+cid
    assert mutate(client, path+'/gstins', {'gstin':'27ABCDE1234F1Z5'}).status_code == 201
    assert mutate(client, path+'/gstins', {'gstin':'27ABCDE1234F1Z5'}).status_code == 409
    assert mutate(client, path+'/gstins', {'gstin':'27AAAAA1234F1Z5'}).status_code == 422
    assert mutate(client, path, {'name':'New name', 'pan':'AAAAA1234F'}, 'PATCH').status_code == 422
    gid = client.get('/api/'+path).json()['gstins'][0]['id']
    assert mutate(client, path+'/gstins/'+gid, {'status':'INACTIVE'}, 'PATCH').status_code == 200
    assert mutate(client, path+'/status', {'status':'ARCHIVED'}, 'PATCH').status_code == 200
    assert mutate(client, path+'/gstins', {'gstin':'29ABCDE1234F1Z5'}).status_code == 409
    assert mutate(client, path+'/status', {'status':'ACTIVE'}, 'PATCH').status_code == 200
    assert mutate(client, root+'/convert-to-firm', {'name':'New firm'}).status_code == 200
    assert client.get('/api/'+root+'/team').status_code == 200
    assert client.get('/api/'+path).json()['name'] == 'Example client'
    assert mutate(client, root+'/convert-to-firm', {'name':'Again'}).status_code == 409
    with Session(engine) as db:
        assert db.exec(select(AuditEvent).where(AuditEvent.workspace_id == who[1].id, AuditEvent.action == 'WORKSPACE_CONVERTED_TO_FIRM')).first()


def test_assignments_tenant_isolation_and_revocation(auth_api):
    client, _, engine = auth_api
    admin = actor(engine)
    accountant = actor(engine, workspace=admin[1], role='ACCOUNTANT')
    foreign = actor(engine)
    root = f'workspaces/{admin[1].id}'
    login(client, admin)
    cid = mutate(client, root+'/clients', {'name':'Private client'}).json()['id']
    path = root+'/clients/'+cid
    login(client, accountant)
    assert client.get('/api/'+root+'/clients').json()['items'] == []
    assert client.get('/api/'+path).status_code == 404
    assert mutate(client, root+'/clients', {'name':'Not allowed'}).status_code == 403
    assert client.get('/api/'+root+'/team').status_code == 403
    login(client, foreign)
    assert client.get('/api/'+path).status_code == 404
    assert mutate(client, path, {'name':'Hacked'}, 'PATCH').status_code == 404
    login(client, admin)
    assert mutate(client, path+'/assignments/'+str(foreign[2].id)).status_code == 404
    assignment = path+'/assignments/'+str(accountant[2].id)
    assert mutate(client, assignment).status_code == 200
    login(client, accountant)
    assert len(client.get('/api/'+root+'/clients').json()['items']) == 1
    assert client.get('/api/'+path).json()['membership_ids'] == []
    login(client, admin)
    assert mutate(client, assignment, method='DELETE').status_code == 200
    login(client, accountant)
    assert client.get('/api/'+path).status_code == 404
    login(client, admin)
    mutate(client, assignment)
    mid = str(accountant[2].id)
    assert mutate(client, root+'/members/'+mid, {'role':'ACCOUNTANT','status':'SUSPENDED'}, 'PATCH').status_code == 200
    login(client, accountant)
    assert client.get('/api/'+root+'/clients').status_code == 404
    login(client, admin)
    mutate(client, root+'/members/'+mid, {'role':'ACCOUNTANT','status':'ACTIVE'}, 'PATCH')
    login(client, accountant)
    assert client.get('/api/'+root+'/clients').json()['items'] == []


def test_invite_acceptance_requires_correct_verified_user_and_is_single_use(auth_api):
    client, mail, engine = auth_api
    admin, invited, stranger = actor(engine), actor(engine, verified=False), actor(engine)
    root = f'workspaces/{admin[1].id}'
    login(client, admin)
    assert mutate(client, root+'/invites', {'email':invited[0].email}).status_code == 201
    raw = mail[-1][2]
    team = client.get('/api/'+root+'/team').json()
    assert 'token_hash' not in str(team) and raw not in str(team)
    assert mutate(client, root+'/invites', {'email':invited[0].email}).status_code == 409
    login(client, stranger)
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 403
    login(client, invited)
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 403
    with Session(engine) as db:
        user = db.get(User, invited[0].id)
        user.email_verified_at = utcnow()
        db.add(user)
        db.commit()
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 200
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 400
    assert len(client.get('/api/me').json()['workspaces']) == 2


@pytest.mark.parametrize('invalid', ['revoked','expired','archived'])
def test_invalid_invitations_cannot_grant_access(auth_api, invalid):
    client, mail, engine = auth_api
    admin, invited = actor(engine), actor(engine)
    root = f'workspaces/{admin[1].id}'
    login(client, admin)
    mutate(client, root+'/invites', {'email':invited[0].email})
    raw = mail[-1][2]
    with Session(engine) as db:
        row = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.token_hash == digest(raw))).one()
        if invalid == 'revoked':
            row.status, row.revoked_at = 'REVOKED', utcnow()
        elif invalid == 'expired':
            row.created_at, row.expires_at = utcnow()-timedelta(days=2), utcnow()-timedelta(days=1)
        else:
            workspace = db.get(Workspace, admin[1].id)
            workspace.status = 'ARCHIVED'
            db.add(workspace)
        db.add(row)
        db.commit()
    login(client, invited)
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 400


def test_last_admin_concurrency_is_serialized(auth_api):
    client, _, engine = auth_api
    first = actor(engine)
    second = actor(engine, workspace=first[1], role='ADMIN')
    def remove(pair):
        who, target = pair
        try:
            service.member_update(who[0], first[1].id, target[2].id, MemberInput(role='ADMIN',status='REMOVED'))
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(remove, [(first,second),(second,first)]))
    assert results[0] == 200 and results[1] in (403,404)
    login(client, first)
    assert mutate(client, f'workspaces/{first[1].id}/members/{first[2].id}', {'role':'ACCOUNTANT','status':'ACTIVE'}, 'PATCH').status_code == 403


def test_workspace_requests_require_auth_verification_csrf_and_active_workspace(auth_api):
    client, _, engine = auth_api
    who = actor(engine, verified=False)
    root = f'workspaces/{who[1].id}'
    assert client.get('/api/'+root+'/clients').status_code == 401
    login(client, who)
    assert client.get('/api/'+root+'/clients').status_code == 403
    assert client.post('/api/'+root+'/clients', json={'name':'Blocked'}).status_code == 403
    with Session(engine) as db:
        workspace = db.get(Workspace, who[1].id)
        workspace.status = 'ARCHIVED'
        db.add(workspace)
        db.commit()
    assert client.get('/api/'+root+'/clients').status_code == 404


def test_invited_registration_does_not_create_unrelated_workspace(auth_api):
    client, mail, engine = auth_api
    admin = actor(engine)
    login(client, admin)
    email = f'invited-{uuid4()}@example.com'
    mutate(client, f'workspaces/{admin[1].id}/invites', {'email':email})
    raw = mail[-1][2]
    assert post(client, 'register-invited', {'email':email, 'name':'New colleague', 'password':PASSWORD, 'token':raw}).status_code == 409
    with Session(engine) as db:
        assert db.exec(select(User).where(User.email_normalized == email)).first() is None
    client.cookies.clear()
    assert post(client, 'register-invited', {'email':email, 'name':'New colleague', 'password':PASSWORD, 'token':raw}).status_code == 202
    with Session(engine) as db:
        user = db.exec(select(User).where(User.email_normalized == email)).one()
        assert not db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)).all()
    assert post(client, 'verification/confirm', {'token':mail[-1][2]}).status_code == 200
    assert post(client, 'login', {'email':email,'password':PASSWORD}).status_code == 200
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 200
    assert [w['id'] for w in client.get('/api/me').json()['workspaces']] == [str(admin[1].id)]


def test_concurrent_invitation_acceptance_creates_one_membership(auth_api):
    client, mail, engine = auth_api
    admin, invited = actor(engine), actor(engine)
    login(client, admin)
    mutate(client, f'workspaces/{admin[1].id}/invites', {'email':invited[0].email})
    raw = mail[-1][2]
    def consume(_):
        try:
            service.accept(invited[0], raw)
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [200,400]
    with Session(engine) as db:
        assert len(db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == admin[1].id,
            WorkspaceMembership.user_id == invited[0].id)).all()) == 1


def test_pagination_search_and_audit_rollback(auth_api, monkeypatch):
    client, _, engine = auth_api
    who = actor(engine)
    login(client, who)
    root = f'workspaces/{who[1].id}'
    with Session(engine) as db:
        db.add_all([Client(workspace_id=who[1].id, created_by=who[0].id, name=f'Client {n:02}') for n in range(26)])
        db.commit()
    first = client.get('/api/'+root+'/clients').json()
    second = client.get('/api/'+root+'/clients?page=2').json()
    assert len(first['items']) == 25 and first['has_more']
    assert len(second['items']) == 1 and not second['has_more']
    assert not {c['id'] for c in first['items']} & {c['id'] for c in second['items']}
    assert len(client.get('/api/'+root+'/clients?search=Client%2005').json()['items']) == 1
    assert client.get('/api/'+root+'/clients?search=%25').json()['items'] == []
    def fail_audit(*args, **kwargs):
        raise RuntimeError('Simulated audit failure')
    monkeypatch.setattr(service, 'audit', fail_audit)
    from backend.app.api.workspace_schemas import ClientInput
    with pytest.raises(RuntimeError, match='audit failure'):
        service.save_client(who[0], who[1].id, ClientInput(name='Must roll back'))
    with Session(engine) as db:
        assert not db.exec(select(Client).where(Client.workspace_id == who[1].id, Client.name == 'Must roll back')).first()


def test_cross_workspace_targets_and_csrf_for_patch_delete(auth_api):
    client, mail, engine = auth_api
    admin, other = actor(engine), actor(engine)
    root, foreign = f'workspaces/{admin[1].id}', f'workspaces/{other[1].id}'
    login(client, other)
    cid = mutate(client, foreign+'/clients', {'name':'Foreign client'}).json()['id']
    mutate(client, foreign+'/invites', {'email':'pending@example.com'})
    raw = mail[-1][2]
    iid = client.get('/api/'+foreign+'/team').json()['invites'][0]['id']
    login(client, admin)
    assert mutate(client, root+'/clients/'+cid+'/gstins', {'gstin':'27ABCDE1234F1Z5'}).status_code == 404
    assert mutate(client, root+'/members/'+str(other[2].id), {'role':'ACCOUNTANT','status':'ACTIVE'}, 'PATCH').status_code == 404
    assert mutate(client, root+'/invites/'+iid, method='DELETE').status_code == 404
    login(client, other)
    assert client.patch('/api/'+foreign+'/clients/'+cid, json={'name':'No CSRF'}).status_code == 403
    assert client.delete('/api/'+foreign+'/invites/'+iid).status_code == 403
    assert mutate(client, foreign+'/invites/'+iid, method='DELETE').status_code == 200
    assert post(client, 'invitations/accept', {'token':raw}).status_code == 400


def test_workspace_routes_are_disabled_with_auth_off(auth_api, monkeypatch):
    client, _, engine = auth_api
    who = actor(engine)
    login(client, who)
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    assert client.get(f'/api/workspaces/{who[1].id}/clients').status_code == 503
