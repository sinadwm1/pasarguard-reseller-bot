"""Persist receipt file paths and group ticket message references.

Revision ID: 20260929_02
Revises: 20260926_01
"""
import sqlalchemy as sa

from alembic import op

revision = "20260929_02"
down_revision = "20260926_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column in (
        ("receipts", sa.Column("stored_path", sa.String(512), nullable=True)),
        ("tickets", sa.Column("group_chat_id", sa.BigInteger(), nullable=True)),
        ("tickets", sa.Column("group_message_id", sa.BigInteger(), nullable=True)),
    ):
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)


def downgrade() -> None:
    op.drop_column("tickets", "group_message_id")
    op.drop_column("tickets", "group_chat_id")
    op.drop_column("receipts", "stored_path")
