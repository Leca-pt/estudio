"""Modelos padrão por projeto: gerar imagens, editar imagens e vozes (o de vídeo já existia). None = os da
Configuração.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0063"
down_revision: Union[str, None] = "0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("image_model", sa.String(), nullable=True))
        batch.add_column(sa.Column("image_edit_model", sa.String(), nullable=True))
        batch.add_column(sa.Column("voice_model", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_column("voice_model")
        batch.drop_column("image_edit_model")
        batch.drop_column("image_model")
