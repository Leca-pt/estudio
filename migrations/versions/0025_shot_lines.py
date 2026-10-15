"""Guião marcado: que linhas do guião da cena cada plano cobre.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shotlinelink",
        sa.Column("shot_id", sa.Integer(), sa.ForeignKey("shot.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("sceneline.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("shotlinelink")
