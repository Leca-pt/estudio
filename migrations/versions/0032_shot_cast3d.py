"""Elenco da maquete 3D por plano: que assets entram e a cor de cada um ({código: cor}).

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("cast3d", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("cast3d")
