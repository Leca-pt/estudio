"""Fluxo simplificado (docs/DECISOES.md, 2026-10-02): acabam as vistas A–D, os painéis e as imagens mestras (os dados
são apagados, por decisão do utilizador); todos os locais usam a maquete 3D e todos os planos a maquete do seu local;
os prompts passam a ser escritos pelo escritor de prompts (tarefas guardadas na base de dados).

- Planos com cenário próprio: o cenário passa para a maquete do local da cena (se o local ainda não tiver maquete);
  o plano passa a usar a maquete do local.
- Shot.moments: os momentos da câmara do plano (render da maquete e a vista do local escolhida em cada um).
- PromptVersion.basis: impressão digital dos dados com que o escritor escreveu (para saber quando fica desatualizado).
- PromptTask: o escritor de prompts a trabalhar (sobrevive a reinícios; uma tarefa a meio fica como falhada).

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VIEW_COLUMNS = [f"view_{v}_{k}" for v in "abcd" for k in ("notes", "prompt", "image_id")]
GONE_ROLES = ("view_a", "view_b", "view_c", "view_d", "panel", "master")


def _delete_files(paths: list[str]) -> None:
    """Apaga do disco os ficheiros das imagens que saíram (só no fim, depois de o esquema mudar sem erros)."""
    try:
        from app.media import delete_file
    except Exception:  # noqa: BLE001 — sem a app (ex.: alembic fora do contentor): ficam só os ficheiros órfãos
        return
    for path in paths:
        try:
            delete_file(path)
        except Exception:  # noqa: BLE001
            pass


def upgrade() -> None:
    bind = op.get_bind()
    import json

    # 1. Cenários próprios dos planos → maquete do local da cena
    rows = bind.execute(sa.text(
        "SELECT shot.id, shot.set3d, shot.set3d_from_id, shot.set3d_asset_id, scene.location_id "
        "FROM shot JOIN scene ON scene.id = shot.scene_id")).fetchall()
    sets = {r[0]: r[1] for r in rows}
    for shot_id, set3d, from_id, asset_id, location_id in rows:
        own = set3d if set3d else (sets.get(from_id) if from_id else None)
        if asset_id or not location_id:
            continue
        layout = bind.execute(sa.text("SELECT layout3d FROM asset WHERE id = :i"), {"i": location_id}).scalar()
        if own and not layout:
            bind.execute(sa.text("UPDATE asset SET layout3d = :l WHERE id = :i"),
                         {"l": own if isinstance(own, str) else json.dumps(own), "i": location_id})
            layout = own
        if layout:
            bind.execute(sa.text("UPDATE shot SET set3d_asset_id = :a WHERE id = :s"), {"a": location_id, "s": shot_id})

    # 2. Vistas A–D, painéis e mestras: ficheiros, registos e pedidos
    roles = ", ".join(f"'{r}'" for r in GONE_ROLES)
    gone = f"role IN ({roles}) OR panel_id IS NOT NULL"
    paths = [p for (p,) in bind.execute(sa.text(f"SELECT path FROM mediafile WHERE {gone}")).fetchall()]
    bind.execute(sa.text(f"DELETE FROM imagejob WHERE role IN ({roles}) OR panel_id IS NOT NULL"))
    bind.execute(sa.text(f"UPDATE project SET cover_media_id = NULL WHERE cover_media_id IN (SELECT id FROM mediafile WHERE {gone})"))
    bind.execute(sa.text(f"DELETE FROM mediafile WHERE {gone}"))
    bind.execute(sa.text("DELETE FROM panelreferencelink"))
    bind.execute(sa.text("DELETE FROM panelassetlink"))
    bind.execute(sa.text("DELETE FROM shotpanel"))
    bind.execute(sa.text("DELETE FROM promptversion WHERE field_key LIKE 'panel:%' OR field_key LIKE '%.view\\_%' ESCAPE '\\' "
                         "OR field_key LIKE 'scene:%.master_prompt'"))
    bind.execute(sa.text("DELETE FROM chatproposal WHERE status = 'pending' AND (field_key LIKE 'panel%' "
                         "OR field_key LIKE '%view\\_%' ESCAPE '\\' OR field_key LIKE '%master%')"))
    bind.execute(sa.text("UPDATE shot SET video_refs = NULL"))  # a escolha antiga apontava para painéis

    # 3. Formato: "9:16 + 16:9" deixa de existir (só gerava a primeira); a resolução de trabalho nunca foi usada
    bind.execute(sa.text("UPDATE project SET aspect_ratio = '9:16' WHERE aspect_ratio LIKE '9:16 +%'"))
    bind.execute(sa.text("DELETE FROM optionlist WHERE key = 'working_resolution' OR (key = 'aspect_ratio' AND value LIKE '%+%')"))
    recipes = bind.execute(sa.text("SELECT value FROM globalconfig WHERE key = 'sheet_recipes'")).scalar()
    recipes = json.loads(recipes) if isinstance(recipes, str) else (recipes or {})
    has_guides = bind.execute(sa.text("SELECT 1 FROM globalconfig WHERE key = 'prompt_guides'")).scalar()
    if not has_guides:  # as receitas dão lugar às guias do escritor; só as proporções passam
        guides = {"sheet_aspect": recipes.get("aspect_ratio") or "16:9",
                  "portrait_aspect": recipes.get("portrait_aspect_ratio") or "1:1"}
        bind.execute(sa.text("INSERT INTO globalconfig (key, value, created_at, updated_at) "
                             "VALUES ('prompt_guides', :v, datetime('now'), datetime('now'))"), {"v": json.dumps(guides)})
    bind.execute(sa.text("DELETE FROM globalconfig WHERE key IN ('models', 'video_prompt_recipe', 'video_prompt_recipe_b', "
                         "'video_recipe_active', 'prompt_writes', 'sheet_recipes')"))

    # 4. Colunas que saem
    with op.batch_alter_table("imagejob") as batch:
        batch.drop_column("panel_id")
    op.drop_index("ix_mediafile_panel_id", table_name="mediafile")
    with op.batch_alter_table("mediafile") as batch:
        batch.drop_column("panel_id")
    op.drop_table("panelreferencelink")
    op.drop_table("panelassetlink")
    op.drop_table("shotpanel")
    with op.batch_alter_table("scene") as batch:
        batch.drop_column("master_image_id")
        batch.drop_column("master_prompt")
    with op.batch_alter_table("asset") as batch:
        for col in VIEW_COLUMNS:
            batch.drop_column(col)
        batch.drop_column("loc_method")
    with op.batch_alter_table("project") as batch:
        batch.drop_column("working_resolution")
    with op.batch_alter_table("shot") as batch:
        batch.drop_column("set3d_from_id")
        batch.drop_column("set3d")
        batch.drop_column("method")
        batch.add_column(sa.Column("moments", sa.JSON(), nullable=True))

    # 5. Escritor de prompts
    with op.batch_alter_table("promptversion") as batch:
        batch.add_column(sa.Column("basis", sa.String(), nullable=True))
    op.create_table(
        "prompttask",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("model", sa.String(), nullable=False, server_default=""),
        sa.Column("generate", sa.JSON(), nullable=True),
        sa.Column("request", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
    )
    op.create_index("ix_prompttask_target", "prompttask", ["project_id", "kind", "ref"])
    _delete_files(paths)


def downgrade() -> None:
    raise NotImplementedError("Os dados das vistas A–D, painéis e mestras foram apagados: repõe a cópia de segurança.")
