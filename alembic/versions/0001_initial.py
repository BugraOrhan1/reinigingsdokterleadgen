"""Initial schema.

Revision ID: 0001
Revises:
"""
from alembic import op
from app.database import Base
import app.models  # noqa: F401

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        table.create(bind=bind, checkfirst=False)


def downgrade():
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind=bind, checkfirst=False)
