"""Templates de folha, retrato, vistas mais curtos (o texto fixo comia o limite de 80 palavras).

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = {
    "character": "Character reference sheet of {name}: {canonical_description}\nFront view, three-quarter view, side view and back view, full body, neutral standing pose, arms relaxed, arranged left to right on a plain light-grey background.\nEven soft studio lighting from the front, no harsh shadows.\n{style_block}\nExactly one character shown in each view, the same person in all views. {project_exclusions}",
    "portrait": "Head-and-shoulders portrait of {name}: {canonical_description}\nFacing the camera straight on, neutral expression, plain light-grey background.\nEven soft studio lighting from the front, no harsh shadows.\n{style_block}\nExactly one person, the face sharp and fully visible. {project_exclusions}",
    "location": "Environment reference of {name}: {canonical_description}\nWide establishing view from eye level, empty of people.\n{lighting}\n{style_block}\n{project_exclusions}",
    "prop": "Prop reference of {name}: {canonical_description}\nShown from the front and at three-quarter angle, isolated on a plain light-grey background.\nEven soft studio lighting.\n{style_block}\nOnly one {name} in each view. {project_exclusions}",
    "view": "{view_title} of {name}. {view_turn}\nOn this side: {view_notes}\nImage 1 shows this place from its establishing viewpoint: use it only for materials, colours, light and style. Do not copy its composition or viewpoint.\nEye level, a single still moment, empty of people.\n{lighting}\n{style_block}\n{project_exclusions}"
}
NEW = {
    "character": "Character sheet of {name}: {canonical_description}\nFront, three-quarter, side and back views, full body, neutral pose, plain light-grey background, soft even studio light.\n{style_block}\nOne person per view, the same person in all views. {project_exclusions}",
    "portrait": "Head-and-shoulders portrait of {name}: {canonical_description}\nFacing the camera, neutral expression, plain light-grey background, soft even studio light.\n{style_block}\nExactly one person. {project_exclusions}",
    "location": "Environment reference of {name}: {canonical_description}\nWide establishing view at eye level, empty of people.\n{lighting}\n{style_block}\n{project_exclusions}",
    "prop": "Prop reference of {name}: {canonical_description}\nFront and three-quarter views, plain light-grey background, soft even studio light.\n{style_block}\nOnly one {name} per view. {project_exclusions}",
    "view": "{view_title} of {name}. {view_turn}\nOn this side: {view_notes}\nUse Image 1 only for materials, colours and light, not for composition or viewpoint.\nEye level, empty of people.\n{lighting}\n{style_block}\n{project_exclusions}"
}


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if not row:
        return
    recipes = json.loads(row[0])
    for key, text in NEW.items():
        if recipes.get(key) in (None, "", OLD[key]):  # só os que nunca foram editados
            recipes[key] = text
    conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(recipes)})


def downgrade() -> None:
    pass
