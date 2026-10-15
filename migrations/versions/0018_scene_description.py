"""Descrição da cena: o que acontece, em texto livre, antes de a partir em ações e diálogos.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("scene") as batch:
        batch.add_column(sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("description")
