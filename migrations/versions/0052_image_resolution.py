"""Resolução das imagens geradas, por projeto (1K, 2K, 4K; por omissão 2K).

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project", sa.Column("image_resolution", sa.String(), nullable=True, server_default="2K"))
    op.execute("UPDATE project SET image_resolution = '2K' WHERE image_resolution IS NULL")


def downgrade() -> None:
    op.drop_column("project", "image_resolution")
