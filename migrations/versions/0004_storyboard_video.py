"""Storyboard e vídeo: papel e duração dos ficheiros, conteúdo do clip, checklist de continuidade.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    with op.batch_alter_table("mediafile") as batch:
        batch.add_column(sa.Column("role", S(), nullable=False, server_default=""))
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.execute("UPDATE mediafile SET role = 'sheet' WHERE asset_id IS NOT NULL")

    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("title", S(), nullable=True))
        batch.add_column(sa.Column("action", S(), nullable=True))
        batch.add_column(sa.Column("action_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("dialogue", S(), nullable=True))
        batch.add_column(sa.Column("dialogue_character_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ambience", S(), nullable=True))
        batch.add_column(sa.Column("music", S(), nullable=True))
        batch.create_foreign_key("fk_shot_dialogue_character", "asset", ["dialogue_character_id"], ["id"], ondelete="SET NULL")
        batch.drop_column("check_props")
        batch.drop_column("check_geography")
        batch.drop_column("check_gazes")

    with op.batch_alter_table("shotassetlink") as batch:
        batch.add_column(sa.Column("note", S(), nullable=True))
    with op.batch_alter_table("shotreferencelink") as batch:
        batch.add_column(sa.Column("incoherent", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "shotcheck",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("shot_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("category", S(), nullable=False),
        sa.Column("text", S(), nullable=False),
        sa.Column("note", S(), nullable=True),
        sa.Column("checked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["shot_id"], ["shot.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shotcheck_shot_id", "shotcheck", ["shot_id"])


def downgrade() -> None:
    raise NotImplementedError("Migração sem volta: a checklist em lista não se converte nas três caixas antigas.")
