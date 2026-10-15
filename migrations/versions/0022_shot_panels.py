"""Storyboard com painéis: cada plano tem uma sequência de momentos (legenda, segundo, imagem).

O painel 1 é a imagem do plano (Shot.start_frame_id). Um End Frame existente passa a painel 2.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from datetime import datetime, timezone

import sqlalchemy as sa
import sqlmodel
from alembic import op

T = sqlmodel.sql.sqltypes.UTCDateTime

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shotpanel",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", T(), nullable=False),
        sa.Column("updated_at", T(), nullable=False),
        sa.Column("shot_id", sa.Integer(), sa.ForeignKey("shot.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("at_s", sa.Float(), nullable=True),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="SET NULL"), nullable=True),
    )
    with op.batch_alter_table("mediafile") as batch:
        batch.add_column(sa.Column("panel_id", sa.Integer(), sa.ForeignKey("shotpanel.id", ondelete="CASCADE",
                                                                           name="fk_mediafile_panel"), nullable=True))
        batch.create_index("ix_mediafile_panel_id", ["panel_id"])
    with op.batch_alter_table("imagejob") as batch:
        batch.add_column(sa.Column("panel_id", sa.Integer(), sa.ForeignKey("shotpanel.id", ondelete="CASCADE",
                                                                           name="fk_imagejob_panel"), nullable=True))

    conn = op.get_bind()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    shots = conn.execute(sa.text("select id, state, end_frame_id from shot")).fetchall()
    for shot_id, _state, end_id in shots:
        conn.execute(sa.text('insert into shotpanel (created_at, updated_at, shot_id, "order", caption, at_s) '
                             "values (:n, :n, :s, 1, NULL, 0)"), {"s": shot_id, "n": now})
        ends = conn.execute(sa.text("select id from mediafile where shot_id = :s and role = 'end'"), {"s": shot_id}).fetchall()
        if ends:  # o End Frame (e as versões por decidir) passam a painel 2
            conn.execute(sa.text('insert into shotpanel (created_at, updated_at, shot_id, "order", image_id) '
                                 "values (:n, :n, :s, 2, :i)"), {"s": shot_id, "i": end_id, "n": now})
            pid = conn.execute(sa.text('select id from shotpanel where shot_id = :s and "order" = 2'), {"s": shot_id}).scalar()
            conn.execute(sa.text("update mediafile set role = 'panel', panel_id = :p where shot_id = :s and role = 'end'"),
                         {"p": pid, "s": shot_id})
            conn.execute(sa.text("update imagejob set role = 'panel', panel_id = :p where shot_id = :s and role = 'end'"),
                         {"p": pid, "s": shot_id})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("update mediafile set role = 'end' where role = 'panel' and panel_id in "
                         '(select id from shotpanel where "order" = 2)'))
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("panel_id")
    with op.batch_alter_table("mediafile") as batch:
        batch.drop_index("ix_mediafile_panel_id")
        batch.drop_column("panel_id")
    op.drop_table("shotpanel")
