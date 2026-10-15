"""Nota de voz das personagens (EN): acrescentada a cada fala no prompt de vídeo, a seguir à voz de referência.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("voice_note", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "voice_note")
