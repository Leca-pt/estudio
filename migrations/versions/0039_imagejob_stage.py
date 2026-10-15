"""Em que ponto está um pedido de imagem (ex.: "Passo 1 de 2: geometria · na fila da fal"), para o cartão "A gerar…".

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("stage", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("stage")
