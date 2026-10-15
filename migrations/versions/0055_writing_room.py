"""Sala de argumentistas: ficheiros de texto do projeto com versões e discussão (o utilizador e as IAs do conector MCP).

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base() -> list:
    return [sa.Column("id", sa.Integer(), primary_key=True), sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "writingfile", *_base(),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("approved_version", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("last_author", sa.String(), nullable=False, server_default=""),
        sa.UniqueConstraint("project_id", "path"),
    )
    op.create_index("ix_writingfile_project_id", "writingfile", ["project_id"])
    op.create_table(
        "writingversion", *_base(),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("writingfile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.String(), nullable=False, server_default=""),
        sa.Column("author", sa.String(), nullable=False, server_default=""),
        sa.Column("message", sa.String(), nullable=False, server_default=""),
    )
    op.create_table(
        "writingcomment", *_base(),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("writingfile.id", ondelete="CASCADE"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(), nullable=False, server_default=""),
        sa.Column("text", sa.String(), nullable=False, server_default=""),
    )
    for t in ("writingversion", "writingcomment"):
        op.create_index(f"ix_{t}_project_id", t, ["project_id"])
        op.create_index(f"ix_{t}_file_id", t, ["file_id"])


def downgrade() -> None:
    op.drop_table("writingcomment")
    op.drop_table("writingversion")
    op.drop_table("writingfile")
