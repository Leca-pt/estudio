"""Modelo e resolução de vídeo: padrão do projeto e escolha por plano; o modelo para que o prompt foi escrito.

Os planos que tinham o modelo por omissão da Configuração passam a seguir o padrão do projeto (model = NULL); os
projetos ficam com esse modelo e essa resolução como padrão.

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-08
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project", sa.Column("video_model", sa.String(), nullable=True))
    op.add_column("project", sa.Column("video_resolution", sa.String(), nullable=True))
    op.add_column("shot", sa.Column("video_resolution", sa.String(), nullable=True))
    op.add_column("shot", sa.Column("video_prompt_model", sa.String(), nullable=True))
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'video_generator'")).fetchone()
    cfg = {}
    if row and row[0]:
        cfg = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    default, quality = cfg.get("default"), cfg.get("quality") or "480p"
    conn.execute(sa.text("UPDATE project SET video_model = :m, video_resolution = :q"), {"m": default, "q": quality})
    if default:
        conn.execute(sa.text("UPDATE shot SET model = NULL WHERE model = :m"), {"m": default})
    # o prompt que já existe foi escrito para o modelo que o plano tinha
    conn.execute(sa.text("UPDATE shot SET video_prompt_model = COALESCE(model, :m) WHERE video_prompt IS NOT NULL AND video_prompt != ''"),
                 {"m": default})


def downgrade() -> None:
    op.drop_column("shot", "video_prompt_model")
    op.drop_column("shot", "video_resolution")
    op.drop_column("project", "video_resolution")
    op.drop_column("project", "video_model")
