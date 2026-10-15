"""Encenação 3D por cena: a cena guarda o local da maquete, a encenação (elenco e figurantes ao longo da cena) e as
cores do elenco; cada plano guarda só a câmara e o segundo da cena onde começa.

Os dados existentes passam assim: a encenação de cada cena é a do primeiro plano com cena 3D (o elenco dele, os
objetos e a duração), o local e as cores vêm dos planos, e cada plano fica com a sua câmara a começar no segundo 0.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-03
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load(v):
    if v is None:
        return None
    return v if isinstance(v, (dict, list)) else json.loads(v)


def upgrade() -> None:
    with op.batch_alter_table("scene") as batch:
        batch.add_column(sa.Column("set3d_asset_id", sa.Integer(), sa.ForeignKey(
            "asset.id", ondelete="SET NULL", name="fk_scene_set3d"), nullable=True))
        batch.add_column(sa.Column("stage3d", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("cast3d", sa.JSON(), nullable=True))

    conn = op.get_bind()
    shots = conn.execute(sa.text("SELECT id, scene_id, number, set3d_asset_id, scene3d, cast3d FROM shot "
                                 "ORDER BY scene_id, number")).fetchall()
    scenes: dict[int, dict] = {}
    for sid, scene_id, _n, set3d, spec, cast in shots:
        spec, cast = _load(spec), _load(cast) or {}
        s = scenes.setdefault(scene_id, {"set3d": None, "stage": None, "cast": {}})
        if s["set3d"] is None and set3d:
            s["set3d"] = set3d
        for code, color in cast.items():
            if code not in s["cast"] and color not in s["cast"].values():
                s["cast"][code] = color
        if spec and s["stage"] is None and (spec.get("actors") or spec.get("objects")):
            s["stage"] = {"duration": spec.get("duration") or 5, "objects": spec.get("objects") or [],
                          "actors": spec.get("actors") or []}
        camera = (spec or {}).get("camera")
        new = {"camera": camera} if camera else None
        conn.execute(sa.text("UPDATE shot SET scene3d = :v WHERE id = :id"),
                     {"v": json.dumps(new) if new else None, "id": sid})
    for scene_id, s in scenes.items():
        conn.execute(sa.text("UPDATE scene SET set3d_asset_id = :a, stage3d = :st, cast3d = :c WHERE id = :id"),
                     {"a": s["set3d"], "st": json.dumps(s["stage"]) if s["stage"] else None,
                      "c": json.dumps(s["cast"]) if s["cast"] else None, "id": scene_id})

    with op.batch_alter_table("shot") as batch:
        batch.drop_column("cast3d")
        batch.drop_column("set3d_asset_id")
        batch.add_column(sa.Column("stage_start", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("stage_start")
        batch.add_column(sa.Column("set3d_asset_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cast3d", sa.JSON(), nullable=True))
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("cast3d")
        batch.drop_column("stage3d")
        batch.drop_column("set3d_asset_id")
