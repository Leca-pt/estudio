"""Premissa do projeto: a ideia de partida em texto livre (PT), no topo da bíblia.

Não entra nos prompts; é contexto para o Assistente.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("premise", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_column("premise")
