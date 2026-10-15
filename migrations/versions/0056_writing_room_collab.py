"""Sala de argumentistas: propostas de alteração, tarefas editoriais, respostas e menções nos comentários, notificações;
o utilizador passa a assinar com o nome de AUTHOR_NAME (.env).

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import os

import sqlalchemy as sa
from alembic import op

AUTHOR = os.environ.get("AUTHOR_NAME") or "Autor"

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base() -> list:
    return [sa.Column("id", sa.Integer(), primary_key=True), sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "writingproposal", *_base(),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("writingfile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("rationale", sa.String(), nullable=False, server_default=""),
        sa.Column("impacts", sa.String(), nullable=False, server_default=""),
        sa.Column("decisions", sa.String(), nullable=False, server_default=""),
        sa.Column("content", sa.String(), nullable=False, server_default=""),
        sa.Column("base_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("author", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("alternative_to", sa.Integer(), sa.ForeignKey("writingproposal.id", ondelete="SET NULL"), nullable=True),
        sa.Column("applied_version", sa.Integer(), nullable=True),
        sa.Column("closed_by", sa.String(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "writingtask", *_base(),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("details", sa.String(), nullable=False, server_default=""),
        sa.Column("assignee", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("result", sa.String(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(), nullable=False, server_default=""),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("writingfile.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("writingproposal.id", ondelete="SET NULL"), nullable=True),
        sa.Column("done_at", sa.DateTime(), nullable=True),
    )
    with op.batch_alter_table("writingcomment") as b:
        b.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("proposal_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("task_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("mentions", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("verdict", sa.String(), nullable=True))
        b.add_column(sa.Column("revision", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_writingcomment_parent", "writingcomment", ["parent_id"], ["id"], ondelete="SET NULL")
        b.create_foreign_key("fk_writingcomment_proposal", "writingproposal", ["proposal_id"], ["id"], ondelete="CASCADE")
        b.create_foreign_key("fk_writingcomment_task", "writingtask", ["task_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_writingcomment_proposal_id", ["proposal_id"])
        b.create_index("ix_writingcomment_task_id", ["task_id"])
    op.create_table(
        "writingnotice", *_base(),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default=""),
        sa.Column("text", sa.String(), nullable=False, server_default=""),
        sa.Column("author", sa.String(), nullable=False, server_default=""),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("writingfile.id", ondelete="CASCADE"), nullable=True),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("writingproposal.id", ondelete="CASCADE"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("writingtask.id", ondelete="CASCADE"), nullable=True),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("writingcomment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for t in ("writingproposal", "writingtask", "writingnotice"):
        op.create_index(f"ix_{t}_project_id", t, ["project_id"])
    op.create_index("ix_writingproposal_file_id", "writingproposal", ["file_id"])
    op.create_index("ix_writingnotice_recipient", "writingnotice", ["recipient"])
    for t in ("writingcomment", "writingversion"):
        op.execute(sa.text(f"UPDATE {t} SET author = :n WHERE author = 'Utilizador'").bindparams(n=AUTHOR))
    op.execute(sa.text("UPDATE writingfile SET last_author = :n WHERE last_author = 'Utilizador'").bindparams(n=AUTHOR))


def downgrade() -> None:
    op.drop_table("writingnotice")
    with op.batch_alter_table("writingcomment") as b:
        for c in ("parent_id", "proposal_id", "task_id", "mentions", "verdict", "revision"):
            b.drop_column(c)
    op.drop_table("writingtask")
    op.drop_table("writingproposal")
