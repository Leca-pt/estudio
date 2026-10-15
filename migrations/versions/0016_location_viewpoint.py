"""Imagem geral dos locais: sem "at eye level" fixo (o ponto de vista vem da descrição, ex.: vista aérea).

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "Environment reference of {name}: {canonical_description}\nWide establishing view at eye level, empty of people.\n{lighting}\n{style_block}\n{project_exclusions}"
NEW = "Environment reference of {name}: {canonical_description}\nWide establishing view, empty of people.\n{lighting}\n{style_block}\n{project_exclusions}"


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if row:
        recipes = json.loads(row[0])
        if recipes.get("location") == OLD:
            recipes["location"] = NEW
            conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(recipes)})


def downgrade() -> None:
    pass
