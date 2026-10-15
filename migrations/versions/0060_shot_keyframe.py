"""Frame-chave do plano: a imagem do início do take com as personagens no sítio (gerada a partir do previs do primeiro
momento, das folhas do elenco e da vista do local) que segue como referência no pedido de vídeo.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("key_prompt", sa.String(), nullable=True))
        batch.add_column(sa.Column("key_image_id", sa.Integer(), sa.ForeignKey(
            "mediafile.id", ondelete="SET NULL", name="fk_shot_key_image"), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("key_image_id")
        batch.drop_column("key_prompt")
