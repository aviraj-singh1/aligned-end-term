"""SQL-only workspace metrics. Never read financial snapshots for dashboard counts."""
from datetime import timedelta
from sqlalchemy import func
from sqlmodel import Session, select
from backend.app.auth.security import utcnow
from backend.app.db.models import Client, ClientAssignment, ReconciliationRun, WorkspaceMembership
from backend.app.services import workspace_service as workspaces


def dashboard(user, wid):
    with Session(workspaces.get_database_engine()) as db:
        workspace, member = workspaces.scope(db, user, wid)
        clients = select(Client.id).where(Client.workspace_id == wid, Client.status == 'ACTIVE')
        if workspace.reset_at:
            clients = clients.where(Client.created_at > workspace.reset_at)
        if member.role not in ('OWNER', 'ADMIN'):
            clients = clients.join(ClientAssignment, ClientAssignment.client_id == Client.id).where(
                ClientAssignment.workspace_id == wid, ClientAssignment.membership_id == member.id)
        run_filter = (ReconciliationRun.workspace_id == wid, ReconciliationRun.client_id.in_(clients))
        counts = dict(db.exec(select(ReconciliationRun.status, func.count()).where(*run_filter)
            .group_by(ReconciliationRun.status)).all())
        today = utcnow().date()
        day = func.date(func.timezone('UTC', ReconciliationRun.created_at))
        daily = dict(db.exec(select(day, func.count()).where(*run_filter,
            ReconciliationRun.created_at >= today-timedelta(days=6)).group_by(day)).all())
        recent = db.exec(select(ReconciliationRun, Client.name).join(Client, Client.id == ReconciliationRun.client_id)
            .where(*run_filter).order_by(ReconciliationRun.created_at.desc(), ReconciliationRun.id).limit(8)).all()
        accountants = db.exec(select(func.count()).select_from(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == wid, WorkspaceMembership.status == 'ACTIVE',
            WorkspaceMembership.role == 'ACCOUNTANT', WorkspaceMembership.activation_pending.is_(False))).one() if member.role in ('OWNER', 'ADMIN') else None
        return {'workspace_name': workspace.name, 'clients': db.exec(select(func.count()).select_from(clients.subquery())).one(),
            'accountants': accountants, 'runs': sum(counts.values()), 'completed': counts.get('COMPLETED', 0),
            'daily': [{'date': (today-timedelta(days=i)).isoformat(), 'runs': daily.get(today-timedelta(days=i), 0)} for i in range(6,-1,-1)],
            'recent': [{'id':r.id,'client_name':name,'status':r.status,'created_at':r.created_at,
                'deleted':r.deleted_at is not None} for r,name in recent]}
