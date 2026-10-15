"""Registo das imagens das personagens na biblioteca de assets da ByteDance (Seedance por asset://).

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "characterregistration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="byteplus"),
        sa.Column("group_id", sa.String(), nullable=False, server_default=""),
        sa.Column("remote_id", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="processing"),
        sa.Column("error", sa.String(), nullable=True),
    )
    for col in ("project_id", "asset_id", "media_id"):
        op.create_index(f"ix_characterregistration_{col}", "characterregistration", [col])


def downgrade() -> None:
    op.drop_table("characterregistration")
