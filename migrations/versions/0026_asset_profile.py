"""Perfil da personagem: texto livre (PT) sobre quem é, o passado, o que quer, forças e fraquezas.

Não entra nos prompts de imagem nem de vídeo; é contexto para o Assistente.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("profile", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("profile")
