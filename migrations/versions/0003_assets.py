"""Assets: nome visível e voz (tipo, timbre).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("label", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""))
        batch.add_column(sa.Column("voice_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch.add_column(sa.Column("voice_timbre", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.execute("UPDATE asset SET label = name WHERE label = ''")


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("voice_timbre")
        batch.drop_column("voice_type")
        batch.drop_column("label")
