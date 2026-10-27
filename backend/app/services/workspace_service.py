"""Tenant operations. Lock workspace before membership/client/invite mutations.

All permission checks read current SQL state; UUID possession never grants access.
No reconciliation/session reads occur here. Audit writes share each transaction.
"""
from datetime import timedelta
import secrets

from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from backend.app.auth.security import digest, utcnow, PASSWORD_HASHER
from backend.app.auth.mail import send_action_mail
from backend.app.db.session import get_database_engine
from backend.app.db.models import (User, Workspace, WorkspaceMembership, WorkspaceInvite,
    Client, ClientGSTIN, ClientAssignment, AuditEvent, PasswordCredential, AuthActionToken, AuthSession)
from backend.app.services.auth_service import action_token, event, namespace


def missing():
    raise HTTPException(404, 'Resource not found')


def scope(db, user, workspace_id, admin=False, firm=False):
    workspace = db.exec(select(Workspace).where(Workspace.id == workspace_id).with_for_update()).first()
    member = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user.id, WorkspaceMembership.status == 'ACTIVE')).first()
    if not workspace or workspace.status != 'ACTIVE' or not member:
        missing()
    current = db.get(User, user.id)
    if not current or current.status != 'ACTIVE':
        raise HTTPException(401, 'Sign in to continue')
    if not current.email_verified_at:
        raise HTTPException(403, 'Verify your email before managing workspace data')
    if admin and member.role not in ('OWNER', 'ADMIN'):
        raise HTTPException(403, 'Only workspace administrators can perform this action')
    if firm and workspace.type != 'FIRM':
        raise HTTPException(403, 'Convert this individual workspace to a firm to manage a team')
    return workspace, member


def audit(db, user, workspace_id, action, entity, client_id=None):
    db.add(AuditEvent(workspace_id=workspace_id, actor_user_id=user.id, action=action,
        entity_type=entity.__tablename__, entity_id=entity.id, client_id=client_id))


def client_scope(db, member, client_id):
    workspace = db.get(Workspace, member.workspace_id)
    client = db.exec(select(Client).where(Client.workspace_id == member.workspace_id, Client.id == client_id)).first()
    if not client or (workspace.reset_at and client.created_at <= workspace.reset_at):
        missing()
    if member.role not in ('OWNER', 'ADMIN'):
        assignment = db.exec(select(ClientAssignment).where(ClientAssignment.workspace_id == member.workspace_id,
            ClientAssignment.client_id == client_id, ClientAssignment.membership_id == member.id)).first()
        if not assignment or client.status != 'ACTIVE':
            missing()
    return client


def client_data(db, client, admin):
    gstins = db.exec(select(ClientGSTIN).where(ClientGSTIN.workspace_id == client.workspace_id,
        ClientGSTIN.client_id == client.id).order_by(ClientGSTIN.created_at, ClientGSTIN.id)).all()
    assignments = db.exec(select(ClientAssignment).where(ClientAssignment.workspace_id == client.workspace_id,
        ClientAssignment.client_id == client.id)).all() if admin else []
    return dict(id=client.id, name=client.name, legal_name=client.legal_name, pan=client.pan, status=client.status,
        gstins=[dict(id=g.id, gstin=g.gstin, status=g.status) for g in gstins],
        membership_ids=[a.membership_id for a in assignments])


def clients(user, wid, page, search):
    with Session(get_database_engine()) as db:
        workspace, member = scope(db, user, wid)
        query = select(Client).where(Client.workspace_id == wid)
        if workspace.reset_at:
            query = query.where(Client.created_at > workspace.reset_at)
        if member.role not in ('OWNER', 'ADMIN'):
            query = query.join(ClientAssignment, ClientAssignment.client_id == Client.id).where(
                ClientAssignment.workspace_id == wid, ClientAssignment.membership_id == member.id, Client.status == 'ACTIVE')
        if search:
            query = query.where(Client.name.icontains(search, autoescape=True))
        rows = db.exec(query.order_by(Client.created_at, Client.id).offset((page - 1) * 25).limit(26)).all()
        # List only summary fields; GSTINs/assignments are fetched for one selected client.
        return {'items': [dict(id=c.id, name=c.name, legal_name=c.legal_name, pan=c.pan, status=c.status) for c in rows[:25]], 'has_more': len(rows) > 25}


def client_detail(user, wid, cid):
    with Session(get_database_engine()) as db:
        _, member = scope(db, user, wid)
        return client_data(db, client_scope(db, member, cid), member.role in ('OWNER', 'ADMIN'))


def save_client(user, wid, data, cid=None):
    with Session(get_database_engine()) as db:
        _, member = scope(db, user, wid, admin=True)
        client = client_scope(db, member, cid) if cid else Client(workspace_id=wid, created_by=user.id, **data.model_dump())
        if cid:
            if getattr(data, 'pan', None) and db.exec(select(ClientGSTIN).where(ClientGSTIN.workspace_id == wid,
                ClientGSTIN.client_id == cid)).first():
                gstins = db.exec(select(ClientGSTIN).where(ClientGSTIN.workspace_id == wid, ClientGSTIN.client_id == cid)).all()
                if any(g.gstin[2:12] != data.pan for g in gstins):
                    raise HTTPException(422, 'Client PAN must match all existing GSTINs')
            for key, value in data.model_dump().items():
                setattr(client, key, value)
            client.updated_at = utcnow()
        db.add(client)
        db.flush()
        audit(db, user, wid, 'CLIENT_UPDATED' if cid else 'CLIENT_CREATED', client, client.id)
        result = client_data(db, client, True)
        db.commit()
        return result


def gstin(user, wid, cid, data, gid=None):
    with Session(get_database_engine()) as db:
        _, member = scope(db, user, wid, admin=True)
        client = client_scope(db, member, cid)
        if client.status != 'ACTIVE':
            raise HTTPException(409, 'Restore the client before editing GSTINs')
        if gid:
            row = db.exec(select(ClientGSTIN).where(ClientGSTIN.id == gid, ClientGSTIN.client_id == cid,
                ClientGSTIN.workspace_id == wid)).first()
            if not row:
                missing()
            row.status = data.status
            row.updated_at = utcnow()
        else:
            if client.pan and data.gstin[2:12] != client.pan:
                raise HTTPException(422, 'GSTIN must contain the client PAN')
            if db.exec(select(ClientGSTIN).where(ClientGSTIN.workspace_id == wid, ClientGSTIN.gstin == data.gstin,
                    ClientGSTIN.status == 'ACTIVE')).first():
                raise HTTPException(409, 'This GSTIN already exists in the workspace; use its existing client record')
            row = ClientGSTIN(workspace_id=wid, client_id=cid, gstin=data.gstin)
        db.add(row)
        db.flush()
        audit(db, user, wid, 'GSTIN_UPDATED' if gid else 'GSTIN_CREATED', row, cid)
        db.commit()
    return {'message': 'GSTIN saved'}


def team(user, wid, page):
    with Session(get_database_engine()) as db:
        scope(db, user, wid, admin=True, firm=True)
        rows = db.exec(select(WorkspaceMembership, User).join(User, User.id == WorkspaceMembership.user_id).where(
            WorkspaceMembership.workspace_id == wid, WorkspaceMembership.status != 'REMOVED').order_by(
            WorkspaceMembership.created_at, WorkspaceMembership.id).offset((page-1)*25).limit(26)).all()
        invites = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid,
            WorkspaceInvite.status == 'PENDING', WorkspaceInvite.expires_at > utcnow()).order_by(WorkspaceInvite.created_at, WorkspaceInvite.id).offset((page-1)*25).limit(26)).all()
        return {'members': [dict(id=m.id, user_id=u.id, name=u.name, email=u.email, role=m.role, status=m.status,
            login_id=m.login_id, activation_pending=m.activation_pending, email_verified=u.email_verified_at is not None) for m,u in rows[:25]],
            'invites': [dict(id=i.id, email=i.email_normalized, role=i.role, expires_at=i.expires_at) for i in invites[:25]],
            'has_more': len(rows)>25 or len(invites)>25}


def create_account(user, wid, data):
    """Create the principal now; only its recipient can activate a usable password."""
    if data.username == 'admin' or str(data.email).split('@')[-1].endswith('.aligned.com'):
        raise HTTPException(422, 'Use a colleague username and a real contact email')
    encoded = PASSWORD_HASHER.hash(secrets.token_urlsafe(48))
    try:
        with Session(get_database_engine(), expire_on_commit=False) as db:
            workspace, _ = scope(db, user, wid, admin=True, firm=True)
            if db.exec(select(User).where(User.email_normalized == str(data.email))).first():
                raise HTTPException(409, 'That contact already has an account. Use an existing-account invitation; its password and identity cannot be replaced.')
            if not workspace.login_namespace:
                workspace.login_namespace = namespace(workspace)
                db.add(workspace)
            login_id = f'{data.username}@{workspace.login_namespace}.aligned.com'
            person = User(name=data.name, email=str(data.email), email_normalized=str(data.email))
            db.add(person)
            db.flush()
            db.add(PasswordCredential(user_id=person.id, password_hash=encoded))
            member = WorkspaceMembership(workspace_id=wid,user_id=person.id,role=data.role,
                login_id=login_id,activation_pending=True)
            db.add(member)
            db.flush()
            raw = action_token(db, person, 'ACTIVATE_ACCOUNT')
            audit(db, user, wid, 'ACCOUNT_CREATED', member)
            db.commit()
    except IntegrityError:
        raise HTTPException(409, 'This username or contact email is already in use. No account was created.') from None
    delivered = send_action_mail(person.email, 'ACTIVATE_ACCOUNT', raw)
    return {'message': f'Account created: {login_id}. ' + ('Activation instructions are in the local mailbox; the colleague only needs to set a password.' if delivered else 'Local message delivery failed. Use Resend activation after fixing the mailbox.'), 'login_id':login_id}


def resend_activation(user, wid, mid):
    with Session(get_database_engine(), expire_on_commit=False) as db:
        scope(db,user,wid,admin=True,firm=True)
        member=db.exec(select(WorkspaceMembership).where(WorkspaceMembership.id==mid,WorkspaceMembership.workspace_id==wid)).first()
        if not member or not member.activation_pending or member.status!='ACTIVE':
            missing()
        person=db.get(User,member.user_id)
        if person.status!='ACTIVE':
            missing()
        raw=action_token(db,person,'ACTIVATE_ACCOUNT')
        audit(db,user,wid,'ACCOUNT_ACTIVATION_REQUESTED',member)
        db.commit()
    delivered=send_action_mail(person.email,'ACTIVATE_ACCOUNT',raw)
    return {'message':'Activation instructions saved to the local mailbox.' if delivered else 'Local message delivery failed; fix the mailbox and retry.'}


def activate_account(raw, password):
    if not password:
        raise HTTPException(422,'Set a password to activate your account')
    encoded=PASSWORD_HASHER.hash(password)
    now=utcnow()
    with Session(get_database_engine()) as db:
        candidate=db.exec(select(AuthActionToken).where(AuthActionToken.token_hash==digest(raw))).first()
        member=db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id==candidate.user_id,
            WorkspaceMembership.activation_pending.is_(True))).first() if candidate else None
        if not member:
            raise HTTPException(400,'This activation link is invalid or expired')
        workspace=db.exec(select(Workspace).where(Workspace.id==member.workspace_id).with_for_update()).one()
        member=db.exec(select(WorkspaceMembership).where(WorkspaceMembership.id==member.id).execution_options(populate_existing=True)).one()
        credential=db.exec(select(PasswordCredential).where(PasswordCredential.user_id==member.user_id).with_for_update()).one()
        token=db.exec(select(AuthActionToken).where(AuthActionToken.id==candidate.id).with_for_update().execution_options(populate_existing=True)).one()
        person=db.exec(select(User).where(User.id==member.user_id).with_for_update()).one()
        if (workspace.status!='ACTIVE' or member.status!='ACTIVE' or not member.activation_pending or person.status!='ACTIVE'
            or token.purpose!='ACTIVATE_ACCOUNT' or token.revoked_at or token.consumed_at or token.expires_at<=now
            or token.email_normalized!=person.email_normalized):
            raise HTTPException(400,'This activation link is invalid or expired')
        credential.password_hash=encoded
        credential.version+=1
        person.email_verified_at=now
        member.activation_pending=False
        member.joined_at=now
        token.consumed_at=now
        db.add_all([credential,person,member,token])
        db.execute(update(AuthActionToken).where(AuthActionToken.user_id==person.id,AuthActionToken.id!=token.id,
            AuthActionToken.revoked_at.is_(None)).values(revoked_at=now))
        db.execute(update(AuthSession).where(AuthSession.user_id==person.id,AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        audit(db,person,workspace.id,'ACCOUNT_ACTIVATED',member)
        db.commit()


def invite(user, wid, data):
    raw = secrets.token_urlsafe(32)
    purpose = 'WORKSPACE_INVITE'
    with Session(get_database_engine(), expire_on_commit=False) as db:
        scope(db, user, wid, admin=True, firm=True)
        existing = db.exec(select(WorkspaceMembership, User).join(User, User.id == WorkspaceMembership.user_id).where(
            WorkspaceMembership.workspace_id == wid, User.email_normalized == data.email)).first()
        if existing:
            membership, person = existing
            if membership.status != 'REMOVED':
                raise HTTPException(409, 'This person is already a member; manage their access in Team')
            if membership.activation_pending:
                membership.status, membership.role, membership.updated_at = 'ACTIVE', data.role, utcnow()
                raw = action_token(db, person, 'ACTIVATE_ACCOUNT')
                purpose = 'ACTIVATE_ACCOUNT'
                db.add(membership)
                audit(db, user, wid, 'PENDING_ACCOUNT_RESTORED', membership)
                db.commit()
                delivered = send_action_mail(str(data.email), purpose, raw)
                return {'message': 'The pending account was restored. New activation instructions are in the local mailbox.' if delivered else
                    'The pending account was restored, but local message delivery failed. Use Resend activation after fixing the mailbox.'}
        prior = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid,
            WorkspaceInvite.email_normalized == data.email, WorkspaceInvite.status == 'PENDING')).first()
        if prior:
            if prior.expires_at > utcnow():
                raise HTTPException(409, 'A pending invitation exists. Revoke it before sending a new invitation.')
            prior.status = 'EXPIRED'
            db.add(prior)
            db.flush()
        row = WorkspaceInvite(workspace_id=wid, email_normalized=str(data.email), role=data.role,
            token_hash=digest(raw), expires_at=utcnow()+timedelta(days=7), invited_by=user.id)
        db.add(row)
        db.flush()
        audit(db, user, wid, 'TEAM_INVITED', row)
        db.commit()
    delivered = send_action_mail(str(data.email), purpose, raw)
    return {'message': 'Invitation saved. Open the invitation .txt in scratch/auth-mail.' if delivered else
        'Invitation saved, but the local message could not be written. Revoke it and retry after fixing mail configuration.'}


def revoke(user, wid, iid):
    with Session(get_database_engine()) as db:
        scope(db, user, wid, admin=True, firm=True)
        row = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid, WorkspaceInvite.id == iid)).first()
        if not row:
            missing()
        if row.status != 'PENDING':
            raise HTTPException(409, 'This invitation is no longer pending')
        row.status, row.revoked_at = 'REVOKED', utcnow()
        db.add(row)
        audit(db, user, wid, 'INVITE_REVOKED', row)
        db.commit()
    return {'message': 'Invitation revoked'}


def member_update(user, wid, mid, data):
    with Session(get_database_engine()) as db:
        scope(db, user, wid, admin=True, firm=True)
        row = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == wid, WorkspaceMembership.id == mid)).first()
        if not row:
            missing()
        if row.role == 'OWNER':
            raise HTTPException(403, 'The Owner cannot be demoted, suspended, or removed. Use the ownership transfer process.')
        if row.role == 'ADMIN' and row.status == 'ACTIVE' and (data.role != 'ADMIN' or data.status != 'ACTIVE'):
            other = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == wid,
                WorkspaceMembership.id != mid, WorkspaceMembership.role.in_(('OWNER', 'ADMIN')), WorkspaceMembership.status == 'ACTIVE',
                WorkspaceMembership.activation_pending.is_(False))).first()
            if not other:
                raise HTTPException(409, 'The workspace must retain at least one active administrator')
        # Revoked/demoted access must not reappear silently on later reactivation.
        if data.status != 'ACTIVE' or row.role != data.role:
            db.exec(delete(ClientAssignment).where(ClientAssignment.workspace_id == wid, ClientAssignment.membership_id == mid))
        row.role, row.status, row.updated_at = data.role, data.status, utcnow()
        db.add(row)
        audit(db, user, wid, 'MEMBERSHIP_UPDATED', row)
        db.commit()
    return {'message': 'Team access updated'}


def assignment(user, wid, cid, mid, remove=False):
    with Session(get_database_engine()) as db:
        _, member = scope(db, user, wid, admin=True, firm=True)
        client = client_scope(db, member, cid)
        target = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == wid,
            WorkspaceMembership.id == mid)).first()
        if not target:
            missing()
        row = db.exec(select(ClientAssignment).where(ClientAssignment.workspace_id == wid,
            ClientAssignment.client_id == cid, ClientAssignment.membership_id == mid)).first()
        if remove:
            if row:
                audit(db, user, wid, 'CLIENT_UNASSIGNED', row, cid)
                db.delete(row)
        else:
            if target.status != 'ACTIVE' or target.role != 'ACCOUNTANT' or client.status != 'ACTIVE':
                raise HTTPException(409, 'Assign only active accountants to active clients')
            if not row:
                row = ClientAssignment(workspace_id=wid, client_id=cid, membership_id=mid, assigned_by=user.id)
                db.add(row)
                db.flush()
                audit(db, user, wid, 'CLIENT_ASSIGNED', row, cid)
        db.commit()
    return {'message': 'Client assignment updated'}


def convert(user, wid, data):
    with Session(get_database_engine()) as db:
        workspace, member = scope(db, user, wid, admin=True)
        if workspace.type != 'INDIVIDUAL':
            raise HTTPException(409, 'This workspace is already a firm')
        workspace.type, workspace.name, workspace.updated_at = 'FIRM', data.name, utcnow()
        workspace.login_namespace = namespace(workspace)
        member.login_id = f'admin@{workspace.login_namespace}.aligned.com'
        db.add(member)
        db.add(workspace)
        audit(db, user, wid, 'WORKSPACE_CONVERTED_TO_FIRM', workspace)
        db.commit()
    return {'message': 'Workspace converted to firm; your existing clients are retained'}


def invitation(db, raw):
    candidate = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.token_hash == digest(raw))).first()
    if not candidate:
        raise HTTPException(400, 'Invitation is invalid or expired')
    workspace = db.exec(select(Workspace).where(Workspace.id == candidate.workspace_id).with_for_update()).one()
    row = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.id == candidate.id).execution_options(populate_existing=True)).one()
    if workspace.type != 'FIRM' or workspace.status != 'ACTIVE' or row.status != 'PENDING' or row.expires_at <= utcnow():
        raise HTTPException(400, 'Invitation is invalid or expired')
    return row


def accept(user, raw):
    with Session(get_database_engine()) as db:
        row = invitation(db, raw)
        current = db.get(User, user.id)
        if not current or current.status != 'ACTIVE' or not current.email_verified_at or current.email_normalized != row.email_normalized:
            raise HTTPException(403, 'Sign in with the verified email address that received this invitation')
        member = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == row.workspace_id,
            WorkspaceMembership.user_id == user.id)).first()
        if member and member.status != 'REMOVED':
            raise HTTPException(409, 'You already have a membership; ask the firm administrator to manage access')
        if member:
            member.role, member.status, member.joined_at, member.updated_at = row.role, 'ACTIVE', utcnow(), utcnow()
            db.add(member)
        else:
            db.add(WorkspaceMembership(workspace_id=row.workspace_id, user_id=user.id, role=row.role, joined_at=utcnow()))
        row.status, row.accepted_by, row.accepted_at = 'ACCEPTED', user.id, utcnow()
        db.add(row)
        audit(db, user, row.workspace_id, 'INVITE_ACCEPTED', row)
        db.commit()
    return {'message': 'Invitation accepted. Select your firm in Clients or Team.'}


def register_invited(data):
    encoded = PASSWORD_HASHER.hash(data.password)
    with Session(get_database_engine(), expire_on_commit=False) as db:
        row = invitation(db, data.token)
        if row.email_normalized != data.email:
            raise HTTPException(400, 'Use the email address that received this invitation')
        if db.exec(select(User).where(User.email_normalized == data.email)).first():
            return
        user = User(name=data.name, email=str(data.email), email_normalized=str(data.email))
        db.add(user)
        db.flush()
        db.add(PasswordCredential(user_id=user.id, password_hash=encoded))
        token = action_token(db, user, 'VERIFY_EMAIL')
        event(db, 'REGISTER', user.id)
        db.commit()
    send_action_mail(user.email, 'VERIFY_EMAIL', token)
