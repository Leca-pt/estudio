"""Modelo escolhido no botão Gerar (grupo do Artlist) guardado em cada trabalho de imagem.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("model_group_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("model_group_id")
