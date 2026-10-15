"""Modelo 3D (GLB) de um asset: importado pelo utilizador, desenhado nas maquetes no lugar de uma caixa (em cinzento,
como o resto da maquete).

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("model3d_id", sa.Integer(), sa.ForeignKey(
            "mediafile.id", ondelete="SET NULL", name="fk_asset_model3d"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("model3d_id")
