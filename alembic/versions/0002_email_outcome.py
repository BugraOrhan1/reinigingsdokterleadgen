"""Add email outcome/error/failed_at columns.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa

from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('emails', sa.Column('outcome', sa.String(30), nullable=False, server_default=''))
    op.add_column('emails', sa.Column('error', sa.String(200), nullable=False, server_default=''))
    op.add_column('emails', sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('emails', 'failed_at')
    op.drop_column('emails', 'error')
    op.drop_column('emails', 'outcome')
