"""Versões dos prompts: a impressão digital dos dados parte a parte (guia, bíblia, o que o prompt descreve, imagens,
vozes…), para a app dizer o que mudou quando um prompt fica desatualizado.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("promptversion") as batch:
        batch.add_column(sa.Column("parts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("promptversion") as batch:
        batch.drop_column("parts")
