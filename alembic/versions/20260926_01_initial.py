"""Initial complete schema.

Revision ID: 20260926_01
Revises: None
"""
from alembic import op
from pasarbot.db.models import Base

revision = "20260926_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
