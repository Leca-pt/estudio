"""GPT Image (Codex CLI): trabalhos de geração, origem dos resultados, capa do projeto.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = sqlmodel.sql.sqltypes.AutoString
T = sqlmodel.sql.sqltypes.UTCDateTime


def upgrade() -> None:
    with op.batch_alter_table("mediafile") as batch:
        batch.add_column(sa.Column("source", S(), nullable=False, server_default="upload"))
    with op.batch_alter_table("project") as batch:
        batch.add_column(sa.Column("cover_media_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cover_prompt", S(), nullable=True))
        batch.create_foreign_key("fk_project_cover_media", "mediafile", ["cover_media_id"], ["id"], ondelete="SET NULL")
    op.create_table(
        "imagejob",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", T(), nullable=False),
        sa.Column("updated_at", T(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("role", S(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("shot_id", sa.Integer(), nullable=True),
        sa.Column("kind", S(), nullable=False),
        sa.Column("prompt", S(), nullable=False),
        sa.Column("aspect_ratio", S(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("status", S(), nullable=False),
        sa.Column("error", S(), nullable=True),
        sa.Column("result_media_id", sa.Integer(), nullable=True),
        sa.Column("started_at", T(), nullable=True),
        sa.Column("finished_at", T(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["asset.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scene.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shot.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_media_id"], ["mediafile.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_imagejob_project_id", "imagejob", ["project_id"])

    # O GPT Image entra na lista de modelos de imagem permitidos.
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'models'")).first()
    if row:
        models = json.loads(row[0])
        if not any(m.get("name") == "GPT Image 2 (Codex)" for m in models):
            models.append({"name": "GPT Image 2 (Codex)", "kind": "image", "resolution": "1K–2K", "durations_s": [],
                           "modes": "references", "active": True, "default": False})
            conn.execute(sa.text("UPDATE globalconfig SET value = :v WHERE key = 'models'"), {"v": json.dumps(models)})


def downgrade() -> None:
    op.drop_table("imagejob")
    with op.batch_alter_table("project") as batch:
        batch.drop_constraint("fk_project_cover_media", type_="foreignkey")
        batch.drop_column("cover_prompt")
        batch.drop_column("cover_media_id")
    with op.batch_alter_table("mediafile") as batch:
        batch.drop_column("source")
