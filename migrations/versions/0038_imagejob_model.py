"""Modelo de imagem de fornecedores com chave de API (ex.: "fal:fal-ai/flux-pro/v1/depth"): o id do modelo, em texto.

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("model", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("model")
