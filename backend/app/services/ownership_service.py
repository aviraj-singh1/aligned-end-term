from datetime import timedelta
import secrets
from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session, select

from backend.app.auth.mail import send_action_mail
from backend.app.auth.security import digest, utcnow
from backend.app.db.models import AuthSession, OwnershipTransfer, User, Workspace, WorkspaceMembership
from backend.app.services import workspace_service as work


def _eligible(db, wid, uid):
    row = db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == wid,
        WorkspaceMembership.user_id == uid).with_for_update()).first()
    person = db.get(User, uid)
    if (not row or row.status != 'ACTIVE' or row.activation_pending or row.role != 'ADMIN'
            or not person or person.status != 'ACTIVE' or not person.email_verified_at):
        raise HTTPException(409, 'Choose an active, activated, email-verified team member')
    return row, person


def start(user, wid, recipient_user_id):
    owner_raw = secrets.token_urlsafe(32)
    now = utcnow()
    with Session(work.get_database_engine(), expire_on_commit=False) as db:
        workspace, owner = work.scope(db, user, wid, admin=True, firm=True)
        if owner.role != 'OWNER':
            raise HTTPException(403, 'Only the Owner can transfer ownership')
        recipient, person = _eligible(db, wid, recipient_user_id)
        for previous in db.exec(select(OwnershipTransfer).where(OwnershipTransfer.workspace_id == wid,
                OwnershipTransfer.status.in_(('PENDING_OWNER','PENDING_RECIPIENT'))).with_for_update()).all():
            previous.status = 'EXPIRED' if previous.expires_at <= now else 'CANCELLED'
            previous.updated_at = now
            db.add(previous)
        db.flush()
        transfer = OwnershipTransfer(workspace_id=wid, owner_user_id=user.id, recipient_user_id=person.id,
            owner_email=user.email_normalized, recipient_email=person.email_normalized,
            owner_token_hash=digest(owner_raw), expires_at=now+timedelta(hours=24))
        db.add(transfer); db.flush()
        work.audit(db, user, wid, 'OWNERSHIP_TRANSFER_REQUESTED', transfer)
        db.commit()
    delivered = send_action_mail(user.email, 'OWNERSHIP_APPROVE', owner_raw)
    if not delivered:
        raise HTTPException(503, 'Transfer saved, but local owner confirmation delivery failed. Cancel it before retrying.')
    return {'message':f'Confirmation sent to your registered email {user.email}. Ownership has not changed.'}


def status(user, wid):
    with Session(work.get_database_engine()) as db:
        _, member = work.scope(db, user, wid, admin=True, firm=True)
        if member.role != 'OWNER':
            return {'transfer':None}
        row = db.exec(select(OwnershipTransfer).where(OwnershipTransfer.workspace_id == wid,
            OwnershipTransfer.status.in_(('PENDING_OWNER','PENDING_RECIPIENT'))).order_by(OwnershipTransfer.created_at.desc())).first()
        if not row:
            return {'transfer':None}
        person=db.get(User,row.recipient_user_id)
        return {'transfer':{'id':row.id,'recipient_user_id':row.recipient_user_id,'recipient_name':person.name,
            'recipient_email':row.recipient_email,'status':row.status,'expires_at':row.expires_at}}


def cancel(user, wid):
    with Session(work.get_database_engine()) as db:
        _, owner = work.scope(db, user, wid, admin=True, firm=True)
        if owner.role != 'OWNER': raise HTTPException(403,'Only the Owner can cancel this transfer')
        row=db.exec(select(OwnershipTransfer).where(OwnershipTransfer.workspace_id==wid,
            OwnershipTransfer.status.in_(('PENDING_OWNER','PENDING_RECIPIENT'))).with_for_update()).first()
        if not row: raise HTTPException(404,'No pending ownership transfer')
        row.status='CANCELLED';row.updated_at=utcnow();db.add(row)
        work.audit(db,user,wid,'OWNERSHIP_TRANSFER_CANCELLED',row);db.commit()
    return {'message':'Ownership transfer cancelled. The current Owner is unchanged.'}


def _token(db, raw):
    row=db.exec(select(OwnershipTransfer).where((OwnershipTransfer.owner_token_hash==digest(raw)) |
        (OwnershipTransfer.recipient_token_hash==digest(raw)))).first()
    if not row: raise HTTPException(400,'This ownership link is invalid or expired')
    return row


def preview(user, raw):
    with Session(work.get_database_engine()) as db:
        row=_token(db,raw);now=utcnow()
        stage='OWNER' if row.owner_token_hash==digest(raw) else 'RECIPIENT'
        expected=row.owner_user_id if stage=='OWNER' else row.recipient_user_id
        required='PENDING_OWNER' if stage=='OWNER' else 'PENDING_RECIPIENT'
        if row.status!=required or row.expires_at<=now or user.id!=expected or user.email_normalized!=(row.owner_email if stage=='OWNER' else row.recipient_email):
            raise HTTPException(403,'Sign in with the registered email address that received this active ownership link')
        owner=db.get(User,row.owner_user_id);recipient=db.get(User,row.recipient_user_id)
        return {'workspace_id':row.workspace_id,'workspace_name':db.get(Workspace,row.workspace_id).name,
            'stage':stage,'owner_name':owner.name,'recipient_name':recipient.name,'expires_at':row.expires_at}


def confirm(user, raw):
    recipient_mail=None
    with Session(work.get_database_engine(), expire_on_commit=False) as db:
        candidate=_token(db,raw)
        workspace, actor=work.scope(db,user,candidate.workspace_id,admin=True,firm=True)
        row=db.exec(select(OwnershipTransfer).where(OwnershipTransfer.id==candidate.id).with_for_update().execution_options(populate_existing=True)).one()
        now=utcnow();stage='OWNER' if row.owner_token_hash==digest(raw) else 'RECIPIENT'
        expected=row.owner_user_id if stage=='OWNER' else row.recipient_user_id
        if row.expires_at<=now or user.id!=expected or user.email_normalized!=(row.owner_email if stage=='OWNER' else row.recipient_email):
            raise HTTPException(403,'Sign in with the registered email address that received this active ownership link')
        if stage=='OWNER':
            if row.status!='PENDING_OWNER' or actor.role!='OWNER': raise HTTPException(409,'Ownership transfer is no longer awaiting owner confirmation')
            _eligible(db,row.workspace_id,row.recipient_user_id)
            raw_recipient=secrets.token_urlsafe(32);row.recipient_token_hash=digest(raw_recipient)
            row.owner_confirmed_at=now;row.status='PENDING_RECIPIENT';recipient_mail=(row.recipient_email,raw_recipient)
            work.audit(db,user,row.workspace_id,'OWNERSHIP_TRANSFER_OWNER_CONFIRMED',row)
        else:
            if row.status!='PENDING_RECIPIENT': raise HTTPException(409,'Ownership transfer is no longer awaiting recipient consent')
            old=db.exec(select(WorkspaceMembership).where(WorkspaceMembership.workspace_id==row.workspace_id,
                WorkspaceMembership.user_id==row.owner_user_id).with_for_update()).one()
            new,_=_eligible(db,row.workspace_id,row.recipient_user_id)
            if old.role!='OWNER' or old.status!='ACTIVE': raise HTTPException(409,'The current Owner changed; this transfer cannot continue')
            old.role='ADMIN';old.updated_at=now;db.add(old);db.flush()
            new.role='OWNER';new.updated_at=now
            row.status='COMPLETED';row.completed_at=now;row.updated_at=now
            db.add_all([old,new]);db.flush();db.add(row)
            work.audit(db,user,row.workspace_id,'OWNERSHIP_TRANSFER_COMPLETED',row)
            # Revoke both principals' sessions so every tab reloads authoritative roles.
            db.execute(update(AuthSession).where(AuthSession.user_id.in_((row.owner_user_id,row.recipient_user_id)),
                AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        db.add(row);db.commit()
    if recipient_mail:
        if not send_action_mail(recipient_mail[0],'OWNERSHIP_ACCEPT',recipient_mail[1]):
            raise HTTPException(503,'Owner confirmed, but recipient email delivery failed. Cancel and restart the transfer.')
        return {'message':f'Owner confirmed. Consent request sent to the recipient’s registered email {recipient_mail[0]}.'}
    return {'message':'Ownership transferred. Both users were signed out to reload their new roles. Sign in again.'}
