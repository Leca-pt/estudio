"""Projetos ligados (ex.: um spot da série: project.parent_id) e assets importados de outro projeto
(asset.source_asset_id, asset.source_map).

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("parent_id", sa.Integer(), sa.ForeignKey(
            "project.id", ondelete="SET NULL", name="fk_project_parent"), nullable=True))
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("source_asset_id", sa.Integer(), sa.ForeignKey(
            "asset.id", ondelete="SET NULL", name="fk_asset_source"), nullable=True))
        batch.add_column(sa.Column("source_map", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("source_map")
        batch.drop_column("source_asset_id")
    with op.batch_alter_table("project") as batch:
        batch.drop_column("parent_id")
