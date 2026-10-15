"""Vistas: o template deixa de repetir a descrição canónica inteira (cada vista só com o que se vê desse lado).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PREV = (
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
NEW = (
    "{view_title} of {name}. {view_turn}\n"
    "On this side: {view_notes}\n"
    "Image 1 shows this place from its establishing viewpoint: use it only for materials, colours, light and style. "
    "Do not copy its composition or viewpoint.\n"
    "Eye level, a single still moment, empty of people.\n"
    "{lighting}\n"
    "{style_block}\n"
    "{project_exclusions}"
)


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if row:
        recipes = json.loads(row[0])
        if recipes.get("view") in (None, "", PREV):
            recipes["view"] = NEW
            conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(recipes)})


def downgrade() -> None:
    pass
