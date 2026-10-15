"""Maquete 3D: método do plano, cena 3D do plano, maquete dos locais, render de cada painel e vídeo-guia.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("method", sa.String(), nullable=False, server_default="panels"))
        batch.add_column(sa.Column("scene3d", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("guide_video_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="SET NULL", name="fk_shot_guide_video"),
                                   nullable=True))
    with op.batch_alter_table("shotpanel") as batch:
        batch.add_column(sa.Column("render_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="SET NULL", name="fk_shotpanel_render"),
                                   nullable=True))
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("layout3d", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("layout3d")
    with op.batch_alter_table("shotpanel") as batch:
        batch.drop_column("render_id")
    with op.batch_alter_table("shot") as batch:
        for c in ("guide_video_id", "scene3d", "method"):
            batch.drop_column(c)
