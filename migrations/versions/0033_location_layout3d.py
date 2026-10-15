"""Maquete 3D opcional do local (ex.: o interior do bar) e o plano poder usá-la como cenário.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("layout3d", sa.JSON(), nullable=True))
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("set3d_asset_id", sa.Integer(),
                                   sa.ForeignKey("asset.id", ondelete="SET NULL", name="fk_shot_set3d_asset"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("set3d_asset_id")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("layout3d")
