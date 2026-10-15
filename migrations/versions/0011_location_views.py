"""Vistas dos locais (A frente, B direita, C trás, D esquerda) e a vista de fundo de cada plano.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
VIEWS = "abcd"
VIEW = (
    "View {letter} of {name}, seen at eye level from the centre of the space, looking toward {view}: "
    "{canonical_description}\n"
    "Empty of people; the same materials, colours and light as the establishing view.\n"
    "{lighting}\n"
    "{style_block}\n"
    "{project_exclusions}"
)


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        for v in VIEWS:
            batch.add_column(sa.Column(f"view_{v}_prompt", S(), nullable=True))
            batch.add_column(sa.Column(f"view_{v}_image_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_asset_view_{v}_image", "mediafile", [f"view_{v}_image_id"], ["id"],
                                     ondelete="SET NULL")
    with op.batch_alter_table("shot") as batch:
        batch.add_column(sa.Column("bg_view", S(), nullable=True))

    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).first()
    if row:
        recipes = json.loads(row[0])
        recipes.setdefault("view", VIEW)
        conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'sheet_recipes'"), {"v": json.dumps(recipes)})


def downgrade() -> None:
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("bg_view")
    with op.batch_alter_table("asset") as batch:
        for v in VIEWS:
            batch.drop_constraint(f"fk_asset_view_{v}_image", type_="foreignkey")
            batch.drop_column(f"view_{v}_image_id")
            batch.drop_column(f"view_{v}_prompt")
