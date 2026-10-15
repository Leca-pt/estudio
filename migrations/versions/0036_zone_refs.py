"""Locais com maquete: imagens de referência escolhidas para cada divisão ou zona (fotos do sítio real, a imagem geral, referências de outras zonas).

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("zone_refs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("zone_refs")
