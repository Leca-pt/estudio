"""Guarda-roupa: looks das personagens (characterlook), a folha de cada look (mediafile.look_id, imagejob.look_id) e o
look de cada personagem em cada cena (scene.looks).

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "characterlook",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("prompt", sa.String(), nullable=True),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="SET NULL"), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_characterlook_project_id", "characterlook", ["project_id"])
    op.create_index("ix_characterlook_asset_id", "characterlook", ["asset_id"])
    with op.batch_alter_table("mediafile") as batch:
        batch.add_column(sa.Column("look_id", sa.Integer(), sa.ForeignKey(
            "characterlook.id", ondelete="CASCADE", name="fk_mediafile_look"), nullable=True))
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("look_id", sa.Integer(), sa.ForeignKey(
            "characterlook.id", ondelete="CASCADE", name="fk_imagejob_look"), nullable=True))
    with op.batch_alter_table("scene") as batch:
        batch.add_column(sa.Column("looks", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("looks")
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("look_id")
    with op.batch_alter_table("mediafile") as batch:
        batch.drop_column("look_id")
    op.drop_table("characterlook")
