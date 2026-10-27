"""Tenant data portability, audit browsing, and guarded destructive lifecycle actions."""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import zipfile
from datetime import date, datetime
from uuid import UUID

from fastapi import HTTPException, Response
from sqlalchemy import delete, update
from sqlmodel import Session, select

from backend.app.auth.security import PASSWORD_HASHER, utcnow, verify_password
from backend.app.db.models import (
    AuditEvent, AuthActionToken, AuthIdentity, AuthSession, Client, ClientAssignment,
    ClientGSTIN, GstrMergeRun, OwnershipTransfer, PasswordCredential, ReconciliationRun,
    RunOperation, User, Workspace, WorkspaceInvite, WorkspaceMembership,
)
from backend.app.db.revision import gstr_schema_available
from backend.app.db.session import get_database_engine
from backend.app.services import session_manager as sessions
from backend.app.services.workspace_service import missing, scope


def _json_value(value):
    if isinstance(value, (UUID, datetime, date)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    return value


def _record(row, fields):
    return {field: _json_value(getattr(row, field)) for field in fields}


GSTR_MERGE_EXPORT_FIELDS = [
    'id', 'client_id', 'client_gstin_id', 'created_by', 'mode', 'financial_year',
    'status', 'engine_version', 'version', 'attempt_count', 'error_code',
    'safe_error_message', 'started_at', 'completed_at', 'failed_at', 'expires_at',
    'deleted_at', 'created_at', 'updated_at',
]


def _csv_bytes(rows):
    output = io.StringIO(newline='')
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, separators=(',', ':'), default=_json_value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    return output.getvalue().encode('utf-8-sig')


def _safe_name(value):
    return re.sub(r'[^a-zA-Z0-9._-]+', '-', value.strip()).strip('-')[:80] or 'aligned-export'


def _zip_response(name, files):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return Response(target.getvalue(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="{_safe_name(name)}.zip"',
        'Cache-Control': 'no-store, private',
    })


def _csv_response(name, rows):
    return Response(_csv_bytes(rows), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{_safe_name(name)}.csv"',
        'Cache-Control': 'no-store, private',
    })


def _admin(db, user, wid):
    workspace, member = scope(db, user, wid, admin=True)
    return workspace, member


def _owner(db, user, wid):
    workspace, member = scope(db, user, wid, admin=True)
    if member.role != 'OWNER':
        raise HTTPException(403, 'Only the workspace Owner can perform this action')
    return workspace, member


def _audit_query(wid, actor_user_id=None):
    query = select(AuditEvent, User).join(User, User.id == AuditEvent.actor_user_id, isouter=True).where(AuditEvent.workspace_id == wid)
    if actor_user_id:
        query = query.where(AuditEvent.actor_user_id == actor_user_id)
    return query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())


def _audit_rows(rows):
    return [dict(
        id=str(event.id), timestamp=event.created_at.isoformat(), action=event.action,
        actor_user_id=str(event.actor_user_id) if event.actor_user_id else '',
        actor_name=person.name if person else 'System', actor_email=person.email if person else '',
        entity_type=event.entity_type, entity_id=str(event.entity_id) if event.entity_id else '',
        client_id=str(event.client_id) if event.client_id else '',
        reconciliation_run_id=str(event.reconciliation_run_id) if event.reconciliation_run_id else '',
        metadata=event.event_metadata or {},
    ) for event, person in rows]


def logs(user, wid, page, actor_user_id=None):
    with Session(get_database_engine()) as db:
        _, member = scope(db, user, wid)
        if member.role not in ('OWNER', 'ADMIN'):
            actor_user_id = user.id
        elif actor_user_id and not db.exec(select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == wid, WorkspaceMembership.user_id == actor_user_id)).first():
            missing()
        start = (page - 1) * 50
        rows = db.exec(_audit_query(wid, actor_user_id).offset(start).limit(51)).all()
        actors = []
        if member.role in ('OWNER', 'ADMIN'):
            people = db.exec(select(WorkspaceMembership, User).join(User, User.id == WorkspaceMembership.user_id)
                .where(WorkspaceMembership.workspace_id == wid).order_by(User.name, User.id)).all()
            actors = [{'user_id': str(person.id), 'name': person.name, 'email': person.email,
                'role': membership.role, 'status': membership.status} for membership, person in people]
        return {'items': _audit_rows(rows[:50]), 'has_more': len(rows) > 50,
                'can_filter_people': member.role in ('OWNER', 'ADMIN'), 'actors': actors}


def export_logs(user, wid, actor_user_id=None):
    with Session(get_database_engine()) as db:
        workspace, member = scope(db, user, wid)
        if member.role not in ('OWNER', 'ADMIN'):
            actor_user_id = user.id
        elif actor_user_id and not db.exec(select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == wid, WorkspaceMembership.user_id == actor_user_id)).first():
            missing()
        db.add(AuditEvent(workspace_id=wid, actor_user_id=user.id, action='AUDIT_LOG_EXPORTED',
            entity_type='audit_events', event_metadata={'filtered_actor_user_id': str(actor_user_id) if actor_user_id else None}))
        db.commit()
        rows = _audit_rows(db.exec(_audit_query(wid, actor_user_id)).all())
        return _csv_response(f'{workspace.name}-audit-log', rows)


def export_workspace(user, wid):
    with Session(get_database_engine()) as db:
        workspace, _ = _owner(db, user, wid)
        db.add(AuditEvent(workspace_id=wid, actor_user_id=user.id, action='WORKSPACE_DATA_EXPORTED',
            entity_type='workspaces', entity_id=workspace.id))
        db.commit()
        members = db.exec(select(WorkspaceMembership, User).join(User, User.id == WorkspaceMembership.user_id)
                          .where(WorkspaceMembership.workspace_id == wid).order_by(WorkspaceMembership.created_at)).all()
        clients = db.exec(select(Client).where(Client.workspace_id == wid).order_by(Client.created_at)).all()
        gstins = db.exec(select(ClientGSTIN).where(ClientGSTIN.workspace_id == wid).order_by(ClientGSTIN.created_at)).all()
        assignments = db.exec(select(ClientAssignment).where(ClientAssignment.workspace_id == wid).order_by(ClientAssignment.created_at)).all()
        runs = db.exec(select(ReconciliationRun).where(ReconciliationRun.workspace_id == wid).order_by(ReconciliationRun.created_at)).all()
        has_gstr_schema = gstr_schema_available(db)
        gstr_merge_runs = (
            db.exec(select(GstrMergeRun).where(
                GstrMergeRun.workspace_id == wid).order_by(GstrMergeRun.created_at)).all()
            if has_gstr_schema else []
        )
        run_operations = db.exec(select(RunOperation).where(RunOperation.workspace_id == wid).order_by(RunOperation.created_at)).all()
        invites = db.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid).order_by(WorkspaceInvite.created_at)).all()
        transfers = db.exec(select(OwnershipTransfer).where(OwnershipTransfer.workspace_id == wid).order_by(OwnershipTransfer.created_at)).all()
        member_rows = [{**_record(m, ['id','user_id','role','status','login_id','activation_pending','joined_at','created_at','updated_at']),
                        'name': person.name, 'email': person.email, 'email_verified': bool(person.email_verified_at),
                        'phone_number': person.phone_number, 'phone_verified': bool(person.phone_verified_at)} for m, person in members]
        client_rows = [_record(row, ['id','name','legal_name','pan','status','created_by','created_at','updated_at']) for row in clients]
        gstin_rows = [_record(row, ['id','client_id','gstin','status','created_at','updated_at']) for row in gstins]
        assignment_rows = [_record(row, ['id','client_id','membership_id','assigned_by','created_at','updated_at']) for row in assignments]
        run_rows = [_record(row, ['id','client_id','client_gstin_id','created_by','status','engine_version','input_version','result_version','summary_json','started_at','completed_at','failed_at','session_expires_at','artifacts_available','storage_version','deleted_at','created_at','updated_at']) for row in runs]
        gstr_merge_run_rows = [_record(row, GSTR_MERGE_EXPORT_FIELDS) for row in gstr_merge_runs]
        operation_rows = [_record(row, ['id','client_id','run_id','actor_user_id','kind','expected_version','state','created_at','updated_at']) for row in run_operations]
        invite_rows = [_record(row, ['id','email_normalized','role','status','invited_by','accepted_by','accepted_at','revoked_at','expires_at','created_at','updated_at']) for row in invites]
        transfer_rows = [_record(row, ['id','owner_user_id','recipient_user_id','owner_email','recipient_email','status','expires_at','owner_confirmed_at','completed_at','created_at','updated_at']) for row in transfers]
        audit_rows = _audit_rows(db.exec(_audit_query(wid)).all())
        manifest = {
            'format': 'ALIGNED workspace export', 'version': 1, 'exported_at': utcnow().isoformat(),
            'workspace_id': str(workspace.id), 'workspace_type': workspace.type,
            'includes': ['workspace.json','members.csv','clients.csv','gstins.csv','assignments.csv',
                'reconciliation_runs.csv', *(['gstr_merge_runs.csv'] if has_gstr_schema else []),
                'run_operations.csv','invitations.csv','ownership_transfers.csv',
                'audit_log.csv','audit_log.json'],
            'excludes': ['password hashes', 'authentication tokens', 'session cookies',
                'temporary uploaded workbooks and generated result artifacts',
                'GSTR input manifests, result summaries, object references, checksums and processing leases'],
        }
        workspace_json = _record(workspace, ['id','name','type','legal_name','login_namespace','status','reset_at',
            'address_line1','address_line2','city','state','postal_code','country_code','created_at','updated_at'])
        files = {
            'manifest.json': json.dumps(manifest, indent=2), 'workspace.json': json.dumps(workspace_json, indent=2),
            'members.csv': _csv_bytes(member_rows), 'clients.csv': _csv_bytes(client_rows),
            'gstins.csv': _csv_bytes(gstin_rows), 'assignments.csv': _csv_bytes(assignment_rows),
            'reconciliation_runs.csv': _csv_bytes(run_rows),
            'run_operations.csv': _csv_bytes(operation_rows),
            'invitations.csv': _csv_bytes(invite_rows), 'ownership_transfers.csv': _csv_bytes(transfer_rows),
            'audit_log.csv': _csv_bytes(audit_rows),
            'audit_log.json': json.dumps(audit_rows, indent=2, default=_json_value),
        }
        if has_gstr_schema:
            files['gstr_merge_runs.csv'] = _csv_bytes(gstr_merge_run_rows)
        return _zip_response(f'{workspace.name}-data', files)


def export_member(user, wid, mid):
    with Session(get_database_engine()) as db:
        workspace, actor = _admin(db, user, wid)
        pair = db.exec(select(WorkspaceMembership, User).join(User, User.id == WorkspaceMembership.user_id).where(
            WorkspaceMembership.workspace_id == wid, WorkspaceMembership.id == mid)).first()
        if not pair:
            missing()
        member, person = pair
        if (actor.role != 'OWNER' and member.user_id != user.id
                and not (actor.role == 'ADMIN' and member.role == 'ACCOUNTANT')):
            raise HTTPException(403, 'You cannot export data for a member at or above your role')
        db.add(AuditEvent(workspace_id=wid, actor_user_id=user.id, action='MEMBER_DATA_EXPORTED',
            entity_type='workspace_memberships', entity_id=member.id,
            event_metadata={'target_user_id': str(person.id)}))
        db.commit()
        assignments = db.exec(select(ClientAssignment, Client.name).join(Client, Client.id == ClientAssignment.client_id).where(
            ClientAssignment.workspace_id == wid, ClientAssignment.membership_id == mid)).all()
        runs = db.exec(select(ReconciliationRun, Client.name).join(Client, Client.id == ReconciliationRun.client_id).where(
            ReconciliationRun.workspace_id == wid, ReconciliationRun.created_by == person.id).order_by(ReconciliationRun.created_at)).all()
        has_gstr_schema = gstr_schema_available(db)
        gstr_merge_runs = (
            db.exec(select(GstrMergeRun, Client.name).join(Client, Client.id == GstrMergeRun.client_id).where(
                GstrMergeRun.workspace_id == wid, GstrMergeRun.created_by == person.id).order_by(GstrMergeRun.created_at)).all()
            if has_gstr_schema else []
        )
        profile = {'workspace_id': str(wid), 'workspace_name': workspace.name, 'membership': {
            **_record(member, ['id','role','status','login_id','activation_pending','joined_at','created_at','updated_at']),
            'user_id': str(person.id), 'name': person.name, 'email': person.email,
            'email_verified': bool(person.email_verified_at),
        }}
        assignment_rows = [{**_record(row, ['id','client_id','assigned_by','created_at','updated_at']), 'client_name': name} for row, name in assignments]
        run_rows = [{**_record(row, ['id','client_id','status','result_version','summary_json','started_at','completed_at','failed_at','deleted_at','created_at']), 'client_name': name} for row, name in runs]
        gstr_merge_run_rows = [{**_record(row, GSTR_MERGE_EXPORT_FIELDS), 'client_name': name}
            for row, name in gstr_merge_runs]
        audit_rows = _audit_rows(db.exec(_audit_query(wid, person.id)).all())
        files = {
            'manifest.json': json.dumps({'format':'ALIGNED member export','version':1,'exported_at':utcnow().isoformat(),
                'excludes':['password hashes','authentication tokens','session cookies','temporary uploaded files',
                    'GSTR input manifests, result summaries, object references, checksums and processing leases']}, indent=2),
            'member.json': json.dumps(profile, indent=2), 'client_assignments.csv': _csv_bytes(assignment_rows),
            'reconciliation_runs.csv': _csv_bytes(run_rows),
            'audit_log.csv': _csv_bytes(audit_rows),
        }
        if has_gstr_schema:
            files['gstr_merge_runs.csv'] = _csv_bytes(gstr_merge_run_rows)
        return _zip_response(f'{workspace.name}-{person.name}-data', files)


def remove_member(user, wid, mid):
    with Session(get_database_engine()) as db:
        _admin(db, user, wid)
        row = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == wid,
            WorkspaceMembership.id == mid).with_for_update()).first()
        if not row:
            missing()
        if row.role == 'OWNER':
            raise HTTPException(403, 'The Owner cannot be removed. Transfer ownership first.')
        if row.user_id == user.id:
            raise HTTPException(409, 'You cannot remove your own membership from this screen')
        db.exec(delete(ClientAssignment).where(ClientAssignment.workspace_id == wid, ClientAssignment.membership_id == mid))
        row.status, row.updated_at = 'REMOVED', utcnow()
        db.add(row)
        db.add(AuditEvent(workspace_id=wid, actor_user_id=user.id, action='MEMBER_REMOVED',
            entity_type='workspace_memberships', entity_id=row.id, event_metadata={'removed_user_id': str(row.user_id)}))
        db.commit()
    return {'message': 'Team member removed and all client assignments cleared'}


def reset_workspace(user, wid, confirmation):
    now = utcnow()
    expired_sessions = []
    with Session(get_database_engine()) as db:
        workspace, owner = _owner(db, user, wid)
        if confirmation != workspace.name:
            raise HTTPException(422, 'Enter the workspace name exactly to confirm the reset')
        clients = db.exec(select(Client).where(Client.workspace_id == wid, Client.created_at > (workspace.reset_at or datetime.min.replace(tzinfo=now.tzinfo)))).all()
        runs = db.exec(select(ReconciliationRun).where(ReconciliationRun.workspace_id == wid,
            ReconciliationRun.deleted_at.is_(None))).all()
        has_gstr_schema = gstr_schema_available(db)
        gstr_merge_runs = (
            db.exec(select(GstrMergeRun).where(GstrMergeRun.workspace_id == wid,
                GstrMergeRun.deleted_at.is_(None))).all()
            if has_gstr_schema else []
        )
        expired_sessions = [str(row.redis_session_id) for row in runs if row.redis_session_id]
        counts = {'clients': len(clients), 'runs': len(runs), 'members_removed': 0}
        if has_gstr_schema:
            counts['gstr_merge_runs'] = len(gstr_merge_runs)
        db.exec(delete(ClientAssignment).where(ClientAssignment.workspace_id == wid))
        for row in db.exec(select(ClientGSTIN).where(ClientGSTIN.workspace_id == wid, ClientGSTIN.status == 'ACTIVE')).all():
            row.status, row.updated_at = 'INACTIVE', now; db.add(row)
        for row in clients:
            row.status, row.updated_at = 'ARCHIVED', now; db.add(row)
        for row in runs:
            row.deleted_at, row.artifacts_available, row.session_expires_at, row.updated_at = now, False, now, now; db.add(row)
        for row in gstr_merge_runs:
            row.status, row.deleted_at, row.expires_at, row.updated_at = 'DELETED', now, row.expires_at or now, now
            row.lease_token = row.lease_expires_at = None
            row.version += 1
            db.add(row)
        for row in db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == wid,
                WorkspaceMembership.id != owner.id, WorkspaceMembership.status != 'REMOVED')).all():
            row.status, row.updated_at = 'REMOVED', now; db.add(row); counts['members_removed'] += 1
        for row in db.exec(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id == wid,
                WorkspaceInvite.status == 'PENDING')).all():
            row.status, row.revoked_at, row.updated_at = 'REVOKED', now, now; db.add(row)
        for row in db.exec(select(OwnershipTransfer).where(OwnershipTransfer.workspace_id == wid,
                OwnershipTransfer.status.in_(('PENDING_OWNER','PENDING_RECIPIENT')))).all():
            row.status, row.updated_at = 'CANCELLED', now; db.add(row)
        workspace.reset_at, workspace.updated_at = now, now
        db.add(workspace)
        db.add(AuditEvent(workspace_id=wid, actor_user_id=user.id, action='WORKSPACE_RESET',
            entity_type='workspaces', entity_id=workspace.id, event_metadata={**counts,
                'retained': 'Immutable audit log and historical database tombstones'}))
        db.commit()
    for sid in expired_sessions:
        try:
            sessions.delete_session(sid)
        except Exception:
            pass
    return {'message': 'Workspace reset complete. Clients, runs, invitations and non-owner team access were retired; the immutable audit log was retained.'}


def check_account_deletion(user, password, confirmation, external_verified=False):
    if confirmation != 'DELETE MY ACCOUNT':
        raise HTTPException(422, 'Type DELETE MY ACCOUNT exactly')
    with Session(get_database_engine()) as db:
        current = db.get(User, user.id)
        credential = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == user.id)).first()
        password_verified = bool(credential and password and verify_password(credential.password_hash, password))
        if not current or current.status != 'ACTIVE' or not (password_verified or external_verified):
            raise HTTPException(403, 'Confirm with your password or Google sign-in')
        has_external_identity = db.exec(select(AuthIdentity).where(AuthIdentity.user_id == user.id)).first() is not None
        if has_external_identity and not external_verified:
            raise HTTPException(403, 'Verify the linked Supabase identity before deleting this account')
        owned = db.exec(select(Workspace, WorkspaceMembership).join(WorkspaceMembership,
            WorkspaceMembership.workspace_id == Workspace.id).where(WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.role == 'OWNER', Workspace.status == 'ACTIVE')).all()
        for workspace, membership in owned:
            another = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace.id,
                WorkspaceMembership.id != membership.id, WorkspaceMembership.status == 'ACTIVE')).first()
            if workspace.type == 'FIRM' and another:
                raise HTTPException(409, f'Transfer ownership of {workspace.name} before deleting your account')
        return {'has_external_identity': has_external_identity}


def delete_account(user, password, confirmation, external_verified=False):
    check_account_deletion(user, password, confirmation, external_verified)
    now = utcnow()
    with Session(get_database_engine()) as db:
        current = db.exec(select(User).where(User.id == user.id).with_for_update()).first()
        credential = db.exec(select(PasswordCredential).where(PasswordCredential.user_id == user.id)).first()
        owned = db.exec(select(Workspace, WorkspaceMembership).join(WorkspaceMembership,
            WorkspaceMembership.workspace_id == Workspace.id).where(WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.role == 'OWNER', Workspace.status == 'ACTIVE')).all()
        for workspace, membership in owned:
            workspace.status, workspace.updated_at = 'ARCHIVED', now
            db.add(workspace)
            db.add(AuditEvent(workspace_id=workspace.id, actor_user_id=user.id, action='OWNER_ACCOUNT_DELETED',
                entity_type='users', entity_id=user.id, event_metadata={'workspace_archived': True}))
        memberships = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.role != 'OWNER')).all()
        for membership in memberships:
            db.exec(delete(ClientAssignment).where(ClientAssignment.workspace_id == membership.workspace_id,
                ClientAssignment.membership_id == membership.id))
            membership.status, membership.updated_at = 'REMOVED', now; db.add(membership)
            db.add(AuditEvent(workspace_id=membership.workspace_id, actor_user_id=user.id,
                action='ACCOUNT_LEFT_WORKSPACE', entity_type='workspace_memberships', entity_id=membership.id))
        db.exec(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        db.exec(update(AuthActionToken).where(AuthActionToken.user_id == user.id, AuthActionToken.revoked_at.is_(None)).values(revoked_at=now))
        db.exec(delete(AuthIdentity).where(AuthIdentity.user_id == user.id))
        if credential:
            credential.password_hash, credential.version, credential.updated_at = PASSWORD_HASHER.hash(secrets.token_urlsafe(48)), credential.version + 1, now
            db.add(credential)
        current.name = 'Deleted user'
        current.email = current.email_normalized = f'deleted-{current.id}@deleted.invalid'
        current.email_verified_at, current.phone_number, current.phone_verified_at = None, None, None
        current.status, current.updated_at = 'DISABLED', now
        db.add(current)
        db.commit()
    return {'message': 'Account deleted. Your identity was anonymized and all sessions were revoked.'}
