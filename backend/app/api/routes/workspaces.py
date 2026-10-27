from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from backend.app.auth.dependencies import current_user, require_signed_out
from backend.app.auth.security import validate_csrf, limit_auth
from backend.app.api.auth_schemas import TokenRequest
from backend.app.api.workspace_schemas import (ClientInput, ClientStatus, GSTINInput, GSTINStatus,
    InviteInput, MemberInput, FirmInput, InvitedRegistration, ManagedAccountInput, OwnershipRequest,
    WorkspaceResetRequest)
from backend.app.db.models import User
from backend.app.services import workspace_service as service
from backend.app.services import ownership_service as ownership

router = APIRouter()
mutations = APIRouter(dependencies=[Depends(validate_csrf)])
core_router = APIRouter()
core_mutations = APIRouter(dependencies=[Depends(validate_csrf)])


@mutations.post('/workspaces/{wid}/data-export')
def data_export(wid: UUID, user: User = Depends(current_user)):
    from backend.app.services import data_management_service as data
    return data.export_workspace(user, wid)


@mutations.post('/workspaces/{wid}/members/{mid}/data-export')
def member_data_export(wid: UUID, mid: UUID, user: User = Depends(current_user)):
    from backend.app.services import data_management_service as data
    return data.export_member(user, wid, mid)


@router.get('/workspaces/{wid}/logs')
@core_router.get('/workspaces/{wid}/logs')
def activity_logs(wid: UUID, page: int = Query(1, ge=1, le=100000),
                  actor_user_id: UUID | None = Query(None), user: User = Depends(current_user)):
    from backend.app.services import data_management_service as data
    return data.logs(user, wid, page, actor_user_id)


@mutations.post('/workspaces/{wid}/logs/export')
@core_mutations.post('/workspaces/{wid}/logs/export')
def activity_log_export(wid: UUID, actor_user_id: UUID | None = Query(None),
                        user: User = Depends(current_user)):
    from backend.app.services import data_management_service as data
    return data.export_logs(user, wid, actor_user_id)


@router.get('/workspaces/{wid}/dashboard')
@core_router.get('/workspaces/{wid}/dashboard')
def dashboard(wid: UUID, user: User = Depends(current_user)):
    from backend.app.services.dashboard_service import dashboard as data
    return data(user, wid)


@router.get('/workspaces/{wid}/clients')
@core_router.get('/workspaces/{wid}/clients')
def clients(wid: UUID, page: int = Query(1, ge=1, le=100000), search: str = Query('', max_length=200), user: User = Depends(current_user)):
    return service.clients(user, wid, page, search)


@router.get('/workspaces/{wid}/clients/{cid}')
@core_router.get('/workspaces/{wid}/clients/{cid}')
def client(wid: UUID, cid: UUID, user: User = Depends(current_user)):
    return service.client_detail(user, wid, cid)


@mutations.post('/workspaces/{wid}/clients', status_code=201)
@core_mutations.post('/workspaces/{wid}/clients', status_code=201)
def create_client(wid: UUID, data: ClientInput, user: User = Depends(current_user)):
    return service.save_client(user, wid, data)


@mutations.patch('/workspaces/{wid}/clients/{cid}')
@core_mutations.patch('/workspaces/{wid}/clients/{cid}')
def update_client(wid: UUID, cid: UUID, data: ClientInput, user: User = Depends(current_user)):
    return service.save_client(user, wid, data, cid)


@mutations.patch('/workspaces/{wid}/clients/{cid}/status')
@core_mutations.patch('/workspaces/{wid}/clients/{cid}/status')
def client_status(wid: UUID, cid: UUID, data: ClientStatus, user: User = Depends(current_user)):
    return service.save_client(user, wid, data, cid)


@mutations.post('/workspaces/{wid}/clients/{cid}/gstins', status_code=201)
@core_mutations.post('/workspaces/{wid}/clients/{cid}/gstins', status_code=201)
def create_gstin(wid: UUID, cid: UUID, data: GSTINInput, user: User = Depends(current_user)):
    return service.gstin(user, wid, cid, data)


@mutations.patch('/workspaces/{wid}/clients/{cid}/gstins/{gid}')
@core_mutations.patch('/workspaces/{wid}/clients/{cid}/gstins/{gid}')
def update_gstin(wid: UUID, cid: UUID, gid: UUID, data: GSTINStatus, user: User = Depends(current_user)):
    return service.gstin(user, wid, cid, data, gid)


@router.get('/workspaces/{wid}/team')
def team(wid: UUID, page: int = Query(1, ge=1, le=100000), user: User = Depends(current_user)):
    return service.team(user, wid, page)


@mutations.post('/workspaces/{wid}/ownership-transfers', status_code=202)
def start_transfer(wid: UUID, data: OwnershipRequest, request: Request, user: User = Depends(current_user)):
    limit_auth(request, str(user.id)); return ownership.start(user,wid,data.recipient_user_id)


@router.get('/workspaces/{wid}/ownership-transfers/current')
def transfer_status(wid: UUID, user: User = Depends(current_user)):
    return ownership.status(user,wid)


@mutations.delete('/workspaces/{wid}/ownership-transfers/current')
def cancel_transfer(wid: UUID, user: User = Depends(current_user)):
    return ownership.cancel(user,wid)


@mutations.post('/auth/ownership/preview')
def transfer_preview(data: TokenRequest, request: Request, user: User = Depends(current_user)):
    limit_auth(request,str(user.id)); return ownership.preview(user,data.token)


@mutations.post('/auth/ownership/confirm')
def transfer_confirm(data: TokenRequest, request: Request, user: User = Depends(current_user)):
    limit_auth(request,str(user.id)); return ownership.confirm(user,data.token)


@mutations.post('/workspaces/{wid}/invites', status_code=201)
def invite(wid: UUID, data: InviteInput, request: Request, user: User = Depends(current_user)):
    limit_auth(request, str(user.id))
    return service.invite(user, wid, data)


@mutations.delete('/workspaces/{wid}/invites/{iid}')
def revoke(wid: UUID, iid: UUID, user: User = Depends(current_user)):
    return service.revoke(user, wid, iid)


@mutations.patch('/workspaces/{wid}/members/{mid}')
def member(wid: UUID, mid: UUID, data: MemberInput, user: User = Depends(current_user)):
    return service.member_update(user, wid, mid, data)


@mutations.delete('/workspaces/{wid}/members/{mid}')
def remove_member(wid: UUID, mid: UUID, user: User = Depends(current_user)):
    from backend.app.services import data_management_service as data
    return data.remove_member(user, wid, mid)


@mutations.post('/workspaces/{wid}/reset')
def reset_workspace(wid: UUID, payload: WorkspaceResetRequest, user: User = Depends(current_user)):
    from backend.app.services import data_management_service as data
    return data.reset_workspace(user, wid, payload.confirmation)


@mutations.post('/workspaces/{wid}/clients/{cid}/assignments/{mid}')
def assign(wid: UUID, cid: UUID, mid: UUID, user: User = Depends(current_user)):
    return service.assignment(user, wid, cid, mid)


@mutations.delete('/workspaces/{wid}/clients/{cid}/assignments/{mid}')
def unassign(wid: UUID, cid: UUID, mid: UUID, user: User = Depends(current_user)):
    return service.assignment(user, wid, cid, mid, remove=True)


@mutations.post('/workspaces/{wid}/convert-to-firm')
def convert(wid: UUID, data: FirmInput, user: User = Depends(current_user)):
    return service.convert(user, wid, data)


@mutations.post('/auth/invitations/accept')
def accept(data: TokenRequest, request: Request, user: User = Depends(current_user)):
    limit_auth(request, str(user.id))
    return service.accept(user, data.token)


@mutations.post('/auth/register-invited', status_code=202, dependencies=[Depends(require_signed_out)])
def register_invited(data: InvitedRegistration, request: Request):
    limit_auth(request, str(data.email))
    service.register_invited(data)
    return {'message': 'If eligible, your account was created. Open the verification .txt in scratch/auth-mail, verify, then sign in and reopen the original invitation. Existing users can sign in.'}


@mutations.post('/workspaces/{wid}/accounts', status_code=201)
def create_account(wid: UUID, data: ManagedAccountInput, request: Request, user: User = Depends(current_user)):
    limit_auth(request, str(user.id))
    return service.create_account(user, wid, data)


@mutations.post('/workspaces/{wid}/members/{mid}/activation')
def resend_activation(wid: UUID, mid: UUID, request: Request, user: User = Depends(current_user)):
    limit_auth(request, str(user.id))
    return service.resend_activation(user, wid, mid)


router.include_router(mutations)
core_router.include_router(core_mutations)


@router.get('/workspaces/{wid}/clients/{cid}/runs')
@core_router.get('/workspaces/{wid}/clients/{cid}/runs')
def runs(wid: UUID, cid: UUID, page: int = Query(1, ge=1, le=100000), user: User = Depends(current_user)):
    from backend.app.services import owned_reconciliation as owned
    return owned.history(user, wid, cid, page)


@router.get('/workspaces/{wid}/clients/{cid}/runs/{sid}/open')
@core_router.get('/workspaces/{wid}/clients/{cid}/runs/{sid}/open')
def open_run(wid: UUID, cid: UUID, sid: UUID, user: User = Depends(current_user)):
    from backend.app.services import owned_reconciliation as owned
    if not owned.enabled():
        from fastapi import HTTPException
        raise HTTPException(503, 'Owned reconciliation is not enabled')
    from sqlmodel import Session
    with Session(owned.get_database_engine()) as db:
        run = owned.load_run(db, user, sid)
        if run.workspace_id != wid or run.client_id != cid:
            owned.missing()
    snapshot = owned.read(user, str(sid))
    return {'version': snapshot['owned_version'], 'phase': snapshot['owned_phase'],
        'data': owned.response_for(snapshot, 'INSPECT' if snapshot['owned_phase'] == 'READY' else 'RECONCILE')}


@router.post('/workspaces/{wid}/clients/{cid}/runs/{sid}/recover', dependencies=[Depends(validate_csrf)])
@core_router.post('/workspaces/{wid}/clients/{cid}/runs/{sid}/recover', dependencies=[Depends(validate_csrf)])
def recover_run(wid: UUID, cid: UUID, sid: UUID, user: User = Depends(current_user)):
    from backend.app.services import owned_reconciliation as owned
    if not owned.enabled():
        from fastapi import HTTPException
        raise HTTPException(503, 'Owned reconciliation is not enabled')
    from sqlmodel import Session
    with Session(owned.get_database_engine()) as db:
        run = owned.load_run(db, user, sid)
        if run.workspace_id != wid or run.client_id != cid:
            owned.missing()
    owned.recover(user, str(sid), cancel_unpublished=True)
    return {'message': 'Recovery finalized a stored receipt or canceled an unpublished operation. Refresh the run list; missing outcomes remain unresolved and are never replayed automatically.'}
