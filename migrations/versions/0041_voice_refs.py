"""Vozes de referência das personagens (Asset.voice_ref_id → áudio aprovado) e parâmetros dos pedidos de geração
(ImageJob.params: a voz escolhida, a descrição da voz…).

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("voice_ref_id", sa.Integer(), sa.ForeignKey(
            "mediafile.id", ondelete="SET NULL", name="fk_asset_voice_ref"), nullable=True))
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("params", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("params")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("voice_ref_id")
