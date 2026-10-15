"""Agenda: blocos de trabalho e prazos de todos os projetos (Hoje, Quadro, Linha do tempo, Visão geral) e publicações
recorrentes.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "planrecurrence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="post"),
        sa.Column("weekdays", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("first_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "planitem",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="other"),
        sa.Column("start", sa.Date(), nullable=True),
        sa.Column("due", sa.Date(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="todo"),
        sa.Column("done_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recurrence_id", sa.Integer(), sa.ForeignKey("planrecurrence.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_planitem_project_id", "planitem", ["project_id"])
    op.create_index("ix_planitem_recurrence_id", "planitem", ["recurrence_id"])
    op.create_index("ix_planitem_due", "planitem", ["due"])


def downgrade() -> None:
    op.drop_table("planitem")
    op.drop_table("planrecurrence")
