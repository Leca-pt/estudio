"""Dados do design: guião em linhas, texto EN nas listas, episódios previstos,
configuração geral no formato novo.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("optionlist") as batch:
        batch.add_column(sa.Column("prompt_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    with op.batch_alter_table("season") as batch:
        batch.add_column(sa.Column("planned_episodes", sa.Integer(), nullable=True))

    op.create_table(
        "sceneline",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("scene_id", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=True),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["scene_id"], ["scene.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_id"], ["asset.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sceneline_scene_id", "sceneline", ["scene_id"])

    # O guião antigo (texto único) passa a ser uma linha de ação.
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat(sep=" ")
    for scene_id, script in conn.execute(sa.text("SELECT id, script FROM scene WHERE script IS NOT NULL AND script != ''")):
        conn.execute(
            sa.text(
                'INSERT INTO sceneline (created_at, updated_at, scene_id, "order", kind, text) '
                "VALUES (:now, :now, :sid, 0, 'action', :text)"
            ),
            {"now": now, "sid": scene_id, "text": script},
        )
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("script")

    # Configuração geral: formato do design.
    from app.seeds import DEFAULT_MODELS, DEFAULT_RECIPE, DEFAULT_RULES

    def get(key):
        row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = :k"), {"k": key}).first()
        return None if row is None else json.loads(row[0])

    def put(key, value):
        conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = :k"), {"k": key, "v": json.dumps(value)})

    models = get("models")
    if models is not None:
        defaults = {m["name"]: m for m in DEFAULT_MODELS}
        converted = []
        for m in models:
            base = defaults.get(m["name"], {})
            converted.append({
                "name": m["name"],
                "kind": m.get("kind", "image"),
                "resolution": m.get("resolution", ""),
                "durations_s": m.get("durations_s", []),
                "modes": "frames_or_references" if m.get("frames_xor_references") else "references",
                "active": True,
                "default": base.get("default", False),
            })
        put("models", converted)
    recipe = get("video_prompt_recipe")
    if recipe is not None and recipe.get("steps") and isinstance(recipe["steps"][0], str):
        put("video_prompt_recipe", DEFAULT_RECIPE)
    rules = get("prompt_rules")
    if rules is not None and rules and isinstance(rules[0], str):
        put("prompt_rules", DEFAULT_RULES)
    conn.execute(sa.text("DELETE FROM globalconfig WHERE key = 'models_note'"))


def downgrade() -> None:
    raise NotImplementedError("Migração sem volta: o guião em linhas não se converte de volta sem perdas.")
