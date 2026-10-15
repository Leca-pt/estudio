"""Storyboard só com painéis: uma fonte para cada coisa.

- A imagem do plano passa a ser a imagem do painel 1 (papel "panel"); o End Frame é sempre o último painel.
- Cada painel tem o seu prompt (ShotPanel.prompt, None = montado); o prompt da imagem do plano sai.
- Saem do plano: estado inicial (= legenda do painel 1), ação e duração da ação (= os painéis), imagem do plano e
  End Frame, a opção de usar a imagem do plano como referência e o modo do vídeo (passa a ser o do modelo:
  com referências quando o modelo as aceita, senão início/fim).
- As referências passam para os painéis: cada painel tem o seu elenco (personagens e adereços, com notas), o local e a
  vista, a iluminação e referências extra. O elenco e o local do plano passam para todos os painéis dele; as referências
  extra do plano, para o painel 1. O plano fica com o enquadramento (tipo, ângulo, movimento).

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    with op.batch_alter_table("shotpanel") as batch:
        batch.add_column(sa.Column("prompt", sa.String(), nullable=True))
        batch.add_column(sa.Column("bg_view", sa.String(), nullable=True))
        batch.add_column(sa.Column("lighting", sa.String(), nullable=True))
    op.create_table(
        "panelassetlink",
        sa.Column("panel_id", sa.Integer(), sa.ForeignKey("shotpanel.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("note", sa.String(), nullable=True),
    )
    op.create_table(
        "panelreferencelink",
        sa.Column("panel_id", sa.Integer(), sa.ForeignKey("shotpanel.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("media_file_id", sa.Integer(), sa.ForeignKey("mediafile.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incoherent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # Imagem do plano → painel 1; o estado inicial passa a legenda do painel 1 quando esta está vazia.
    for shot_id, start_id, state in conn.execute(sa.text("select id, start_frame_id, state from shot")).fetchall():
        pid = conn.execute(sa.text('select id from shotpanel where shot_id = :s and "order" = 1'), {"s": shot_id}).scalar()
        if pid is None:
            continue
        conn.execute(sa.text("update mediafile set role = 'panel', panel_id = :p where shot_id = :s and role = 'start'"),
                     {"p": pid, "s": shot_id})
        conn.execute(sa.text("update imagejob set role = 'panel', panel_id = :p where shot_id = :s and role = 'start'"),
                     {"p": pid, "s": shot_id})
        conn.execute(sa.text("update shotpanel set image_id = :i where id = :p"), {"i": start_id, "p": pid})
        if state:
            conn.execute(sa.text("update shotpanel set caption = :c where id = :p and (caption is null or caption = '')"),
                         {"c": state, "p": pid})
    # Elenco, local e referências: do plano para os painéis
    for shot_id, bg in conn.execute(sa.text("select id, bg_view from shot")).fetchall():
        panels = [r[0] for r in conn.execute(sa.text('select id from shotpanel where shot_id = :s order by "order"'),
                                             {"s": shot_id}).fetchall()]
        cast = conn.execute(sa.text("select l.asset_id, l.note from shotassetlink l join asset a on a.id = l.asset_id "
                                    "where l.shot_id = :s and a.kind != 'location'"), {"s": shot_id}).fetchall()
        for pid in panels:
            conn.execute(sa.text("update shotpanel set bg_view = :b where id = :p"), {"b": bg, "p": pid})
            for asset_id, note in cast:
                conn.execute(sa.text("insert into panelassetlink (panel_id, asset_id, note) values (:p, :a, :n)"),
                             {"p": pid, "a": asset_id, "n": note})
        if panels:
            for media_id, order, incoherent in conn.execute(sa.text(
                    'select media_file_id, "order", incoherent from shotreferencelink where shot_id = :s'), {"s": shot_id}).fetchall():
                conn.execute(sa.text('insert into panelreferencelink (panel_id, media_file_id, "order", incoherent) '
                                     "values (:p, :m, :o, :i)"), {"p": panels[0], "m": media_id, "o": order, "i": incoherent})
    # Versões e propostas do Assistente para campos que deixam de existir
    conn.execute(sa.text("delete from promptversion where field_key like 'shot:%.image_prompt'"))
    for attr in ("image_prompt", "state", "action", "action_seconds"):
        conn.execute(sa.text("delete from chatproposal where field_key like :k"), {"k": f"shot:%.{attr}"})

    with op.batch_alter_table("shot") as batch:
        for column in ("state", "image_prompt", "start_frame_id", "end_frame_id", "action", "action_seconds", "still_as_ref",
                       "mode", "bg_view"):
            batch.drop_column(column)
    op.drop_table("shotreferencelink")
    op.drop_table("shotassetlink")


def downgrade() -> None:
    raise NotImplementedError("Sem retorno: a imagem do plano e o End Frame passaram a painéis.")
