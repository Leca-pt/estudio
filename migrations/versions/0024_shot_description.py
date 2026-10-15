"""Plano: o que acontece (descrição em português, base para propor os painéis) e as imagens escolhidas para o vídeo.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("description", sa.String(), nullable=True))
        batch.add_column(sa.Column("video_refs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("video_refs")
        batch.drop_column("description")
