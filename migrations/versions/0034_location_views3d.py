"""Locais com Maquete 3D: método do local, vistas ligadas a posições de câmara na maquete (por divisão) e o destino
"locview" nas imagens e nos pedidos de geração.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("loc_method", sa.String(), nullable=False, server_default="views"))
    op.create_table(
        "locationview",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("zone", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="view"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("cam", sa.JSON(), nullable=False),
        sa.Column("render_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="SET NULL"), nullable=True),
        sa.Column("render_hash", sa.String(), nullable=True),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt", sa.String(), nullable=True),
    )
    with op.batch_alter_table("mediafile") as batch:
        batch.add_column(sa.Column("locview_id", sa.Integer(), sa.ForeignKey("locationview.id", ondelete="CASCADE",
                                                                             name="fk_mediafile_locview"), nullable=True))
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("locview_id", sa.Integer(), sa.ForeignKey("locationview.id", ondelete="CASCADE",
                                                                             name="fk_imagejob_locview"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("locview_id")
    with op.batch_alter_table("mediafile") as batch:
        batch.drop_column("locview_id")
    op.drop_table("locationview")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("loc_method")
