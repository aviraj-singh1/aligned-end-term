"""add local authentication lifecycle

Revision ID: 20260831_02
Revises: 20260831_01
"""
from alembic import op
import sqlalchemy as sa


revision = '20260831_02'
down_revision = '20260831_01'
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL reviewed independently of live model metadata.
    op.create_table('auth_action_tokens',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('purpose', sa.String(length=30), nullable=False),
    sa.Column('email_normalized', sa.String(length=320), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("purpose IN ('VERIFY_EMAIL', 'RESET_PASSWORD')", name=op.f('ck_auth_action_tokens_purpose')),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_auth_action_tokens_token_digest')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_auth_action_tokens_expiry')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_action_tokens_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_action_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_action_tokens_token_hash'))
    )
    op.create_index('ix_auth_action_tokens_expires_at', 'auth_action_tokens', ['expires_at'], unique=False)
    op.create_index('ix_auth_action_tokens_user_id_purpose', 'auth_action_tokens', ['user_id', 'purpose'], unique=False)
    op.create_table('auth_security_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.CheckConstraint("action IN ('REGISTER', 'LOGIN', 'LOGOUT', 'LOGOUT_ALL', 'VERIFY_EMAIL', 'RESET_PASSWORD', 'REQUEST_EMAIL')", name=op.f('ck_auth_security_events_action')),
    sa.CheckConstraint("outcome IN ('SUCCESS', 'DENIED')", name=op.f('ck_auth_security_events_outcome')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_security_events_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_security_events'))
    )
    op.create_index('ix_auth_security_events_user_id_created_at', 'auth_security_events', ['user_id', 'created_at'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('idle_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_auth_sessions_token_digest')),
    sa.CheckConstraint('expires_at > created_at AND idle_expires_at <= expires_at', name=op.f('ck_auth_sessions_expiry')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_sessions_token_hash'))
    )
    op.create_index('ix_auth_sessions_expires_at', 'auth_sessions', ['expires_at'], unique=False)
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'], unique=False)
    op.create_table('password_credentials',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('password_hash', sa.String(length=512), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_password_credentials_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_password_credentials')),
    sa.UniqueConstraint('user_id', name=op.f('uq_password_credentials_user_id'))
    )
    op.execute("CREATE TRIGGER auth_security_events_append_only BEFORE UPDATE OR DELETE ON auth_security_events FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()")
    op.execute("CREATE TRIGGER auth_security_events_no_truncate BEFORE TRUNCATE ON auth_security_events FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()")


def downgrade():
    # Table removal also removes its triggers; the baseline owns the shared function.
    op.drop_table('password_credentials')
    op.drop_index('ix_auth_sessions_user_id', table_name='auth_sessions')
    op.drop_index('ix_auth_sessions_expires_at', table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index('ix_auth_security_events_user_id_created_at', table_name='auth_security_events')
    op.drop_table('auth_security_events')
    op.drop_index('ix_auth_action_tokens_user_id_purpose', table_name='auth_action_tokens')
    op.drop_index('ix_auth_action_tokens_expires_at', table_name='auth_action_tokens')
    op.drop_table('auth_action_tokens')
