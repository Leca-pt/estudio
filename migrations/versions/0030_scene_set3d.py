"""Cenário 3D por cena (em vez de maquete por local): Scene.set3d e reutilização do cenário de outra cena.

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("scene") as batch:
        batch.add_column(sa.Column("set3d", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("set3d_from_id", sa.Integer(),
                                   sa.ForeignKey("scene.id", ondelete="SET NULL", name="fk_scene_set3d_from"), nullable=True))
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("layout3d")


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("layout3d", sa.JSON(), nullable=True))
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("set3d_from_id")
        batch.drop_column("set3d")
