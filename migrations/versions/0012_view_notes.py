"""Vistas dos locais: o que se vê em cada direção e um template com rotação explícita.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
OLD_VIEW = (
    "View {letter} of {name}, seen at eye level from the centre of the space, looking toward {view}: "
    "{canonical_description}\n"
    "Empty of people; the same materials, colours and light as the establishing view.\n"
    "{lighting}\n"
    "{style_block}\n"
    "{project_exclusions}"
)
NEW_VIEW = (
    "{view_title} of {name}. {view_turn}\n"
    "On this side: {view_notes}\n"
    "Image 1 shows this place from its establishing viewpoint: use it only for materials, colours, light and style. "
    "Do not copy its composition or viewpoint.\n"
    "The place: {canonical_description}\n"
    "Eye level, a single still moment, empty of people.\n"
    "{lighting}\n"
    "{style_block}\n"
    "{project_exclusions}"
)


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        for v in "abcd":
            batch.add_column(sa.Column(f"view_{v}_notes", S(), nullable=True))
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if row:
        recipes = json.loads(row[0])
        if recipes.get("view") in (None, "", OLD_VIEW):  # só troca o template se nunca foi editado
            recipes["view"] = NEW_VIEW
            conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(recipes)})


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        for v in "abcd":
            batch.drop_column(f"view_{v}_notes")
