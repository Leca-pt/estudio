"""Referências do plano: para a imagem, para o vídeo ou para os dois.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shotreferencelink") as batch:
        batch.add_column(sa.Column("usage", sa.String(), nullable=False, server_default="both"))


def downgrade() -> None:
    with op.batch_alter_table("shotreferencelink") as batch:
        batch.drop_column("usage")
