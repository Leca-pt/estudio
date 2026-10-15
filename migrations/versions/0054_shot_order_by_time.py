"""Planos numerados pela ordem do início do troço na encenação (stage_start), em empate pelo id.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, scene_id, COALESCE(stage_start, 0) FROM shot")).fetchall()
    by_scene: dict = {}
    for sid, scene, start in rows:
        by_scene.setdefault(scene, []).append((float(start), sid))
    for shots in by_scene.values():
        for n, (_start, sid) in enumerate(sorted(shots), 1):
            conn.execute(sa.text("UPDATE shot SET number = :n WHERE id = :id"), {"n": n, "id": sid})


def downgrade() -> None:
    pass
