"""Grupos de assets (criados pelo utilizador) para organizar a grelha quando há muitos.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("group_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("group_name")
