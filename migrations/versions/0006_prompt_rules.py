"""Regras de prompts (docs/PROMPT_RULES.md): features-chave, efeitos sonoros, versões de prompts,
templates de folha, Template B de vídeo e validações.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString

# Receitas de folha antigas (só instruções). Se não foram editadas, passam aos templates novos.
OLD_SHEETS = {
    "character": "Character reference sheet of {code}: exactly 1 person shown three times (front view, "
                 "three-quarter view, side view), full body, neutral standing pose, arms relaxed, plain light "
                 "grey background, even studio light.",
    "location": "Location reference sheet of {code}: wide establishing view of the empty location, no people, "
                "clear layout with every landmark visible, even natural light, eye-level camera.",
    "prop": "Prop reference sheet of {code}: exactly one object shown three times (front view, three-quarter "
            "view, side view), plain light grey background, even studio light, no people, no hands.",
}


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("key_features", S(), nullable=True))
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("sound_effects", S(), nullable=True))
    op.create_table(
        "promptversion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("field_key", S(), nullable=False),
        sa.Column("text", S(), nullable=False),
        sa.Column("source", S(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promptversion_project_id", "promptversion", ["project_id"])
    op.create_index("ix_promptversion_field_key", "promptversion", ["field_key"])

    from app.seeds import DEFAULT_SHEET_RECIPES

    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if row:
        sheets = json.loads(row[0])
        for kind, old in OLD_SHEETS.items():
            if sheets.get(kind) == old:
                sheets[kind] = DEFAULT_SHEET_RECIPES[kind]
        conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(sheets)})


def downgrade() -> None:
    op.drop_table("promptversion")
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("sound_effects")
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("key_features")
