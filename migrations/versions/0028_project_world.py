"""O mundo do projeto (worldbuilding): o que é verdade neste mundo, em texto livre (PT), no topo da etapa Mundo.

Não entra nos prompts; é contexto para o Assistente (escrever, descrever, avisar de contradições).

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("world", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project") as batch:
        batch.drop_column("world")
