"""Artlist: fornecedor de cada trabalho de imagem, confirmação de custo e créditos gastos.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("provider", S(), nullable=False, server_default="gpt"))
        batch.add_column(sa.Column("confirm_cost", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("credits", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("credits")
        batch.drop_column("confirm_cost")
        batch.drop_column("provider")
