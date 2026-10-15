"""Assistente: mensagens e propostas.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
T = sqlmodel.sql.sqltypes.UTCDateTime


def upgrade() -> None:
    op.create_table(
        "chatmessage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", T(), nullable=False),
        sa.Column("updated_at", T(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("context_key", S(), nullable=False),
        sa.Column("context_label", S(), nullable=False),
        sa.Column("role", S(), nullable=False),
        sa.Column("text", S(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatmessage_project_id", "chatmessage", ["project_id"])
    op.create_index("ix_chatmessage_context_key", "chatmessage", ["context_key"])
    op.create_table(
        "chatproposal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", T(), nullable=False),
        sa.Column("updated_at", T(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("field_key", S(), nullable=False),
        sa.Column("field_label", S(), nullable=False),
        sa.Column("value", S(), nullable=False),
        sa.Column("old_value", S(), nullable=True),
        sa.Column("reason", S(), nullable=True),
        sa.Column("status", S(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chatmessage.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatproposal_message_id", "chatproposal", ["message_id"])
    op.create_index("ix_chatproposal_project_id", "chatproposal", ["project_id"])


def downgrade() -> None:
    op.drop_table("chatproposal")
    op.drop_table("chatmessage")
