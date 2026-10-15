"""O que segue no pedido de vídeo de cada plano: vídeo-guia ligado/desligado, vozes desligadas e o modo das imagens.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("shot", sa.Column("video_inputs", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shot", "video_inputs")
