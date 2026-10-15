"""Cenário 3D por plano (em vez de por cena): Shot.set3d e reutilização do cenário de outro plano.

O cenário que já existia numa cena passa para o primeiro plano dessa cena com Maquete 3D (ou o primeiro plano); os
outros planos da cena com Maquete 3D passam a reutilizá-lo.

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("set3d", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("set3d_from_id", sa.Integer(),
                                   sa.ForeignKey("shot.id", ondelete="SET NULL", name="fk_shot_set3d_from"), nullable=True))
    for scene_id, data in conn.execute(sa.text("select id, set3d from scene where set3d is not null")).fetchall():
        shots = conn.execute(sa.text('select id, method from shot where scene_id = :s order by number, id'),
                             {"s": scene_id}).fetchall()
        if not shots:
            continue
        three = [sid for sid, method in shots if method == "3d"]
        owner = three[0] if three else shots[0][0]
        conn.execute(sa.text("update shot set set3d = :d where id = :i"), {"d": data, "i": owner})
        for sid in three[1:]:
            conn.execute(sa.text("update shot set set3d_from_id = :o where id = :i"), {"o": owner, "i": sid})
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("set3d_from_id")
        batch.drop_column("set3d")


def downgrade() -> None:
    with op.batch_alter_table("scene") as batch:
        batch.add_column(sa.Column("set3d", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("set3d_from_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("set3d_from_id")
        batch.drop_column("set3d")
