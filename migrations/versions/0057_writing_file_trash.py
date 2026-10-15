"""Sala de argumentistas: ficheiros apagados ficam em «Apagados» (repõem-se) em vez de desaparecerem.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("writingfile", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("writingfile", sa.Column("deleted_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("writingfile", "deleted_by")
    op.drop_column("writingfile", "deleted_at")
