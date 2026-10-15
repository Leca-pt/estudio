"""Retrato das personagens: prompt e imagem aprovada próprios, separados da folha de vistas.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString

PORTRAIT = (
    "Head-and-shoulders portrait of {name}: {canonical_description}\n"
    "Facing the camera straight on, neutral expression, plain light-grey background.\n"
    "Even soft studio lighting from the front, no harsh shadows.\n"
    "{style_block}\n"
    "Exactly one person, the face sharp and fully visible. {project_exclusions}"
)


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.add_column(sa.Column("portrait_prompt", S(), nullable=True))
        batch.add_column(sa.Column("portrait_image_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_asset_portrait_image", "mediafile", ["portrait_image_id"], ["id"], ondelete="SET NULL")

    # Template do retrato na Configuração (as receitas de folha já existentes ficam como estão).
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if row:
        recipes = json.loads(row[0])
        recipes.setdefault("portrait", PORTRAIT)
        recipes.setdefault("portrait_aspect_ratio", "1:1")
        conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(recipes)})


def downgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.drop_constraint("fk_asset_portrait_image", type_="foreignkey")
        batch.drop_column("portrait_image_id")
        batch.drop_column("portrait_prompt")
