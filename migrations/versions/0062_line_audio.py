"""Áudio de falas do guião (ex.: narração gerada no ElevenLabs): um ficheiro cobre uma ou mais linhas seguidas de uma
cena. MediaFile com role line_audio guarda as linhas (ids) e o texto delas quando foi carregado.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("mediafile") as batch:
        batch.add_column(sa.Column("lines", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("basis", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mediafile") as batch:
        batch.drop_column("basis")
        batch.drop_column("lines")
