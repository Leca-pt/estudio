"""Pedidos de vídeo na fila de geração (Artlist): duração e som de cada pedido.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("duration_s", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("audio", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("audio")
        batch.drop_column("duration_s")
