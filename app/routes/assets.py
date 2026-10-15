"""Design de assets (grelha, ficha, prompt da folha, resultados) e a vista "Por cena" (quadro assets × cenas)."""

import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, delete, func, select, update

from app.db import get_session
from app.forms import text
from app.media import MAX_IMAGE_BYTES
from app.media_service import reject_media
from app.routes.files import flash_info, save_uploads


def gpt_jobs(session, project, role, **target):
    from app.routes.gpt import jobs_ctx

    return jobs_ctx(session, project, role, **target)


def gpt_enabled(session) -> bool:
    """Há algum fornecedor de imagens disponível (GPT Image ou Artlist)."""
    from app.routes.gpt import any_enabled

    return any_enabled(session)
from app.models import (
    ASSET_KINDS, ASSET_KINDS_PLURAL, Act, Asset, AssetSceneLink, Episode, MediaFile, Project, Scene, SceneLine, Season,
    Shot,
)
from app import prompt_targets as PT, prompt_writer
from app.asset_refs import asset_photos, location_image, sheet_ref_assets
from app.options import get_config
from app.routes import prompt_ops
from app.security import csrf_protect, require_login
from app.stages import get_project, render_stage

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])

ASSET_STATES = {"ok": "Aprovado", "wip": "Em curso", "no": "Rejeitado", "pend": "Pendente"}


# ── Utilitários ──────────────────────────────────────────────

def asset_code(session: Session, project: Project, label: str) -> str:
    """PREFIXO_NOME em maiúsculas, sem acentos; único no projeto."""
    ascii_label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_label).strip("_").upper() or "ASSET"
    base = f"{project.prefix}_{slug}"
    existing = set(session.exec(select(Asset.name).where(Asset.project_id == project.id)).all())
    code, n = base, 2
    while code in existing:
        code, n = f"{base}_{n}", n + 1
    return code


CODE_RE = re.compile(r"^[A-Z0-9_]+$")


def _replace_code_in_texts(session: Session, project: Project, old: str, new: str) -> int:
    """Troca o código antigo pelo novo em todos os textos do projeto (prompts, estados, descrições, notas…)."""
    from app.models import ImageJob, LocationView, Shot, ShotCheck

    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])")
    scene_ids = list(session.exec(select(Scene.id).where(Scene.project_id == project.id)).all())
    shot_ids = list(session.exec(select(Shot.id).where(Shot.scene_id.in_(scene_ids))).all()) if scene_ids else []
    rows: list = [project]
    rows += list(session.exec(select(Asset).where(Asset.project_id == project.id)).all())
    rows += list(session.exec(select(Scene).where(Scene.project_id == project.id)).all())
    if scene_ids:
        rows += list(session.exec(select(SceneLine).where(SceneLine.scene_id.in_(scene_ids))).all())
    if shot_ids:
        rows += list(session.exec(select(Shot).where(Shot.id.in_(shot_ids))).all())
        rows += list(session.exec(select(ShotCheck).where(ShotCheck.shot_id.in_(shot_ids))).all())
    rows += list(session.exec(select(LocationView).join(Asset, Asset.id == LocationView.asset_id)
                              .where(Asset.project_id == project.id)).all())
    rows += list(session.exec(select(ImageJob).where(ImageJob.project_id == project.id,
                                                     ImageJob.status.in_(("queued", "failed")))).all())
    from app.models import ChatProposal

    rows += list(session.exec(select(ChatProposal).where(ChatProposal.project_id == project.id,
                                                         ChatProposal.status == "pending")).all())
    changed = 0
    for row in rows:
        for field, value in list(row.__dict__.items()):
            if field.startswith("_") or field == "name" or not isinstance(value, str) or old not in value:
                continue
            updated = pattern.sub(new, value)
            if updated != value:
                setattr(row, field, updated)
                session.add(row)
                changed += 1
    return changed


def _source_ctx(session: Session, asset: Asset) -> dict | None:
    """Asset importado: de onde veio (para o aviso e o botão «Atualizar a partir do original»)."""
    if not asset.source_asset_id:
        return None
    src = session.get(Asset, asset.source_asset_id)
    if src is None:
        return {"gone": True}
    return {"asset": src, "project": session.get(Project, src.project_id), "newer": src.updated_at > asset.updated_at}


def _suggest_code(session: Session, project: Project, asset: Asset) -> str:
    """Código a partir do nome atual (o próprio código, se já corresponde)."""
    if not asset.label:
        return asset.name
    ascii_label = unicodedata.normalize("NFKD", asset.label).encode("ascii", "ignore").decode()
    base = f"{project.prefix}_" + (re.sub(r"[^A-Za-z0-9]+", "_", ascii_label).strip("_").upper() or "ASSET")
    return asset.name if base == asset.name else asset_code(session, project, asset.label)


def rename_asset(session: Session, project: Project, asset: Asset, new_code: str) -> int:
    """Muda o código do asset: pasta e caminhos dos ficheiros, e o código em todos os textos do projeto."""
    from app.media import media_root, safe_segment

    old = asset.name
    if new_code == old:
        return 0
    if not new_code.startswith(f"{project.prefix}_") or not CODE_RE.match(new_code) or new_code == f"{project.prefix}_":
        raise ValueError(f"O código tem de começar por {project.prefix}_ e ter só maiúsculas, números e _ (ex.: {project.prefix}_RUA_DO_BAR).")
    if session.exec(select(Asset).where(Asset.project_id == project.id, Asset.name == new_code)).first():
        raise ValueError(f"Já existe um asset com o código {new_code}.")
    base = media_root() / safe_segment(project.prefix) / "assets"
    old_dir, new_dir = base / safe_segment(old), base / safe_segment(new_code)
    if new_dir.exists():
        raise ValueError(f"Já existe uma pasta de ficheiros com o nome {new_code}.")
    old_prefix = f"{safe_segment(project.prefix)}/assets/{safe_segment(old)}/"
    new_prefix = f"{safe_segment(project.prefix)}/assets/{safe_segment(new_code)}/"
    moved = False
    try:
        if old_dir.is_dir():
            old_dir.rename(new_dir)
            moved = True
        for m in session.exec(select(MediaFile).where(MediaFile.project_id == project.id)).all():
            if m.path.startswith(old_prefix):
                m.path = new_prefix + m.path[len(old_prefix):]
                session.add(m)
        changed = _replace_code_in_texts(session, project, old, new_code)
        for sc in session.exec(select(Scene).where(Scene.project_id == project.id)).all():  # a encenação 3D guarda códigos em JSON
            if sc.stage3d and any(a.get("code") == old for a in sc.stage3d.get("actors") or []):
                st = dict(sc.stage3d)
                st["actors"] = [dict(a, code=new_code) if a.get("code") == old else a for a in st["actors"]]
                sc.stage3d = st
                changed += 1
            if sc.cast3d and old in sc.cast3d:
                sc.cast3d = {(new_code if k == old else k): v for k, v in sc.cast3d.items()}
            session.add(sc)
        asset.name = new_code
        session.add(asset)
        session.commit()
    except Exception:
        session.rollback()
        if moved:
            new_dir.rename(old_dir)
        raise
    return changed


def _asset(session: Session, project: Project, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail="Asset não encontrado.")
    return asset


def _assets(session: Session, project: Project) -> list[Asset]:
    order = {k: i for i, k in enumerate(ASSET_KINDS)}
    rows = session.exec(select(Asset).where(Asset.project_id == project.id).order_by(Asset.name)).all()
    return sorted(rows, key=lambda a: order.get(a.kind, 9))


def _media_by_asset(session: Session, project: Project) -> dict[int, list[MediaFile]]:
    out: dict[int, list[MediaFile]] = {}
    rows = session.exec(
        select(MediaFile).where(MediaFile.project_id == project.id, MediaFile.asset_id.is_not(None))
        .order_by(MediaFile.created_at, MediaFile.id)
    ).all()
    for m in rows:
        out.setdefault(m.asset_id, []).append(m)
    return out


def asset_state(asset: Asset, media: list[MediaFile]) -> tuple[str, str, int]:
    """(estado, rótulo, nº de resultados por decidir). Um local: com maquete e vistas aprovadas."""
    pending = sum(1 for m in media if m.status == "pending")
    if asset.kind == "location":
        views = sum(1 for m in media if m.role == "locview" and m.status == "approved")
        if not asset.layout3d:
            return "pend", "Sem maquete", pending
        state = "ok" if views else "wip"
        return state, f"{views} vista(s) aprovada(s)" if views else "Maquete sem vistas aprovadas", pending
    portrait_ok = asset.kind != "character" or asset.portrait_image_id
    if asset.approved_image_id and portrait_ok:
        state = "ok"
    elif asset.approved_image_id or asset.portrait_image_id:
        return "wip", "Falta o retrato" if asset.approved_image_id else "Falta a folha", pending
    elif pending:
        state = "wip"
    elif media and media[-1].status == "rejected":
        state = "no"
    else:
        state = "pend"
    return state, ASSET_STATES[state], pending


def _flash(request: Request, message: str) -> None:
    request.session["flash_error"] = message


# ── Assets por cena (quadro assets × cenas, vista dos Assets) ─

def _scene_groups(session: Session, project: Project) -> list[tuple[int, str]]:
    """Filtros de colunas para séries (episódios) e filmes (atos)."""
    if project.type == "series":
        rows = session.exec(
            select(Episode, Season).join(Season, Season.id == Episode.season_id)
            .where(Season.project_id == project.id).order_by(Season.number, Episode.number)
        ).all()
        return [(e.id, f"T{s.number} · E{e.number:02d}" + (f" · {e.title}" if e.title else "")) for e, s in rows]
    if project.type == "film":
        acts = session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all()
        return [(a.id, f"Ato {a.number}" + (f" · {a.title}" if a.title else "")) for a in acts]
    return []


def breakdown_page(request: Request, session: Session, project: Project, status_code: int = 200):
    groups = _scene_groups(session, project)
    group = request.query_params.get("grupo")
    group_id = int(group) if group and group.isdigit() and any(g[0] == int(group) for g in groups) else None
    if groups and group_id is None:
        from app.stages import current_episode

        ep = current_episode(request, session, project) if project.type == "series" else None
        group_id = ep.id if ep else groups[0][0]
    stmt = select(Scene).where(Scene.project_id == project.id)
    if project.type == "series":
        stmt = stmt.where(Scene.episode_id == group_id)
    elif project.type == "film":
        stmt = stmt.where(Scene.act_id == group_id)
    scenes = session.exec(stmt.order_by(Scene.number, Scene.id)).all()
    assets = _assets(session, project)
    links = set(session.exec(
        select(AssetSceneLink.asset_id, AssetSceneLink.scene_id)
        .join(Asset, Asset.id == AssetSceneLink.asset_id).where(Asset.project_id == project.id)
    ).all())
    totals: dict[int, int] = {}
    for a_id, _s in links:
        totals[a_id] = totals.get(a_id, 0) + 1
    approved = {m.id: m for m in session.exec(
        select(MediaFile).where(MediaFile.project_id == project.id, MediaFile.status == "approved")).all()}
    ctx = {
        "scenes": scenes, "assets": assets, "links": links, "totals": totals, "approved": approved,
        "groups": groups, "group_id": group_id, "kinds": ASSET_KINDS, "kinds_plural": ASSET_KINDS_PLURAL,
        "main_form": "breakdown-form" if assets and scenes else None, "assistant_key": "breakdown",
    }
    return render_stage(request, session, project, 4, "stages/stage_3.html", ctx, status_code)


@router.post("/desglose")
async def breakdown_save(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    scene_ids = {int(v) for v in form.getlist("scene_id") if v.isdigit()}
    valid_scenes = set(session.exec(select(Scene.id).where(Scene.project_id == project.id, Scene.id.in_(scene_ids))).all())
    valid_assets = set(session.exec(select(Asset.id).where(Asset.project_id == project.id)).all())
    wanted = set()
    for raw in form.getlist("link"):
        a, _, s = raw.partition(":")
        if a.isdigit() and s.isdigit() and int(a) in valid_assets and int(s) in valid_scenes:
            wanted.add((int(a), int(s)))
    if valid_scenes:
        session.exec(delete(AssetSceneLink).where(AssetSceneLink.scene_id.in_(valid_scenes)))
    for a, s in wanted:
        session.add(AssetSceneLink(asset_id=a, scene_id=s))
    session.commit()
    group = text(form, "grupo")
    return RedirectResponse(f"/projetos/{project.id}/etapa/4?vista=cenas&guardado=1" + (f"&grupo={group}" if group else ""), status_code=303)


@router.post("/mundo")
async def world_save(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    project.world = (text(form, "world") or "").replace("\r\n", "\n") or None
    project.world_prompt = " ".join((text(form, "world_prompt") or "").split())[:600] or None
    session.add(project)
    session.commit()
    return RedirectResponse(f"/projetos/{project.id}/etapa/4?guardado=1#mundo", status_code=303)


@router.post("/assets")
async def asset_create(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    label = text(form, "label")
    kind = text(form, "kind")
    back = text(form, "next") or f"/projetos/{project.id}/etapa/4"
    if not back.startswith(f"/projetos/{project.id}/"):
        back = f"/projetos/{project.id}/etapa/4"
    if not label or kind not in ASSET_KINDS:
        _flash(request, "Indica o nome e o tipo do asset.")
        return RedirectResponse(back, status_code=303)
    asset = Asset(project_id=project.id, kind=kind, label=label, name=asset_code(session, project, label),
                  group_name=text(form, "group_name"))
    session.add(asset)
    session.commit()
    if text(form, "next"):  # criado a partir de outra página (ex.: a cena): volta lá
        return RedirectResponse(back, status_code=303)
    flash_info(request, f"«{asset.label}» criado. Escreve a descrição e gera ou carrega a folha.")
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}", status_code=303)  # abre a ficha nova


# ── Etapa 4: grelha e ficha ──────────────────────────────────

def project_groups(session: Session, project: Project) -> list[str]:
    rows = session.exec(select(Asset.group_name).where(Asset.project_id == project.id, Asset.group_name.is_not(None))
                        .distinct()).all()
    return sorted({g.strip() for g in rows if g and g.strip()}, key=str.casefold)


def assets_page(request: Request, session: Session, project: Project):
    params = request.query_params
    if params.get("vista") == "cenas":
        return breakdown_page(request, session, project)
    kind = params.get("tipo") if params.get("tipo") in ASSET_KINDS else None
    state = params.get("estado") if params.get("estado") in ASSET_STATES else None
    view = "lista" if params.get("vista") == "lista" else "cartoes"
    q = (params.get("q") or "").strip()
    groups_ep = _scene_groups(session, project)  # episódios (série) ou atos (filme)
    ep = int(params["ep"]) if (params.get("ep") or "").isdigit() and any(g[0] == int(params["ep"]) for g in groups_ep) else None
    media = _media_by_asset(session, project)
    all_assets = _assets(session, project)
    scene_counts = dict(session.exec(
        select(AssetSceneLink.asset_id, func.count()).join(Asset, Asset.id == AssetSceneLink.asset_id)
        .where(Asset.project_id == project.id).group_by(AssetSceneLink.asset_id)).all())
    in_ep = None
    if ep is not None:
        column = Scene.episode_id if project.type == "series" else Scene.act_id
        in_ep = set(session.exec(select(AssetSceneLink.asset_id).join(Scene, Scene.id == AssetSceneLink.scene_id)
                                 .where(column == ep)).all())
    cards = []
    for a in all_assets:
        st, label, pending = asset_state(a, media.get(a.id, []))
        cards.append({"asset": a, "state": st, "label": label, "pending": pending, "scenes": scene_counts.get(a.id, 0),
                      "search": " ".join(x for x in (a.name, a.label, a.group_name, a.canonical_description) if x).lower(),
                      "approved": (location_image(session, a) if a.kind == "location" else
                                   next((m for m in media.get(a.id, []) if m.id == a.approved_image_id), None)),
                      "portrait": next((m for m in media.get(a.id, []) if a.portrait_image_id and m.id == a.portrait_image_id), None)})
    counts = {k: sum(1 for c in cards if c["asset"].kind == k) for k in ASSET_KINDS}
    approved_n = sum(1 for c in cards if c["state"] == "ok")
    visible = [c for c in cards if (not kind or c["asset"].kind == kind) and (not state or c["state"] == state)
               and (in_ep is None or c["asset"].id in in_ep) and (not q or q.lower() in c["search"])]
    # Secções por grupo (ordem alfabética); os assets sem grupo no fim.
    by_group: dict[str, list] = {}
    for c in visible:
        by_group.setdefault((c["asset"].group_name or "").strip(), []).append(c)
    sections = [(g, by_group[g]) for g in sorted((g for g in by_group if g), key=str.casefold)]
    if "" in by_group:
        sections.append((None, by_group[""]))
    ctx = {"cards": visible, "sections": sections, "total": len(cards), "counts": counts, "approved_n": approved_n,
           "kind": kind, "state": state, "view": view, "q": q, "ep": ep, "ep_options": groups_ep,
           "ep_label": "Episódio" if project.type == "series" else "Ato",
           "kinds": ASSET_KINDS, "kinds_plural": ASSET_KINDS_PLURAL, "asset_states": ASSET_STATES,
           "groups": project_groups(session, project), "assistant_key": "assets"}
    from app.asset_import import importable

    ctx["importable"] = importable(session, project)
    return render_stage(request, session, project, 4, "stages/stage_4.html", ctx)


def _prompt_ctx(session: Session, project: Project, asset: Asset) -> dict:
    """Blocos de prompt da folha e do retrato (os locais não têm folha: as imagens são as vistas da maquete)."""
    if asset.kind == "location":
        return {"sheet_panel": None, "portrait": None}
    people = 1 if asset.kind == "character" else 0
    sheet = prompt_ops.panel(session, PT.get(session, project, "folha", str(asset.id)), "Prompt da folha", people=people)
    portrait = None
    if asset.kind == "character":
        portrait = {"panel": prompt_ops.panel(session, PT.get(session, project, "retrato", str(asset.id)),
                                              "Prompt do retrato", people=1)}
    return {"sheet_panel": sheet, "portrait": portrait}


def _sheet_ref_groups(session: Session, project: Project, asset: Asset) -> list[tuple[str, list]]:
    """Assets do projeto que podem servir de referência à folha, por tipo, para o modal de seleção."""
    from app.routes.production import approved_media

    others = [a for a in _assets(session, project) if a.id != asset.id]
    found = approved_media(session, [a.approved_image_id for a in others])
    return [(ASSET_KINDS_PLURAL.get(kind, kind), [(a.id, found.get(a.approved_image_id), a.name + ("" if found.get(a.approved_image_id) else " · sem folha"))
                                                 for a in others if a.kind == kind])
            for kind in ASSET_KINDS]


def _layout_ctx(session: Session, project: Project, asset: Asset) -> dict:
    """Maquete 3D opcional de um local: o visualizador e em quantos planos é usada."""
    if asset.kind != "location":
        return {"layout_spec": None, "layout_uses": 0, "preview_hash": None, "preview_have": ""}
    from app import scene3d

    uses = session.exec(select(func.count()).select_from(Shot).join(Scene, Scene.id == Shot.scene_id)
                        .where(Scene.set3d_asset_id == asset.id)).one()
    spec = scene3d.compose(session, project.id, location=asset)
    digest = scene3d.preview_hash(spec)
    thumb = None  # a vista de cima da maquete (por dentro, se houver), para o cartão
    for suffix in ("_inside0.png", "_top.png"):
        thumb = session.exec(select(MediaFile).where(MediaFile.asset_id == asset.id, MediaFile.role == "preview3d",
                                                     MediaFile.path.like(f"%{suffix}")).order_by(MediaFile.id.desc())).first()
        if thumb:
            break
    return {"layout_spec": spec, "layout_uses": uses, "preview_hash": digest, "maquete_thumb": thumb,
            "preview_have": scene3d.preview_have(session, asset_id=asset.id),
            **_views3d_ctx(session, project, asset, digest)}


def _views3d_ctx(session: Session, project: Project, asset: Asset, digest: str | None) -> dict:
    """Vistas da maquete, por zona: render, imagem aprovada, resultados por decidir, pedidos e o bloco do prompt."""
    from app import location3d

    rows = []
    for v in location3d.location_views(session, asset):
        media = {m.id: m for m in session.exec(select(MediaFile).where(MediaFile.locview_id == v.id)).all()}
        render = media.get(v.render_id)
        panel = prompt_ops.panel(session, PT.get(session, project, "vista", str(v.id)), "Prompt da vista")
        rows.append({"view": v, "render": render, "image": media.get(v.image_id) if v.image_id else None,
                     "panel": panel, "state": panel["state"],
                     "results": [m for m in media.values() if m.role == "locview" and m.status == "pending"],
                     "jobs": gpt_jobs(session, project, "locview", locview_id=v.id),
                     "stale": render is None or v.render_hash != digest})
    groups: dict = {z["name"]: [] for z in location3d.zones(asset.layout3d)} if asset.layout3d else {}
    for r in rows:
        groups.setdefault(r["view"].zone, []).append(r)
    with_depth = set(session.exec(select(MediaFile.locview_id).where(
        MediaFile.asset_id == asset.id, MediaFile.role == "locdepth")).all())
    meta = {z["name"]: {"outdoor": z["outdoor"], "note": (asset.zone_notes or {}).get(z["name"] or "", ""),
                        "refs": location3d.zone_refs(asset, z["name"]),
                        "design_n": len(location3d.design_refs(session, asset, z["name"]))}
            for z in location3d.zones(asset.layout3d)}
    names = [z["name"] for z in location3d.zones(asset.layout3d)] if asset.layout3d else []
    tasks = {zi: prompt_writer.task_for(session, project.id, "zona", f"{asset.id}-{zi}") for zi in range(1, len(names) + 1)}
    return {"views3d": list(groups.items()), "zone_meta": meta, "zone_ref_options": location3d.ref_options(session, asset),
            "zone_tasks": {zi: t for zi, t in tasks.items() if t is not None},
            "max_zone_refs": location3d.MAX_ZONE_REFS,
            "views3d_js": [{"id": r["view"].id, "zone": r["view"].zone, "kind": r["view"].kind, "label": r["view"].label,
                            "cam": r["view"].cam, "fresh": not r["stale"] and r["view"].id in with_depth,
                            "outdoor": bool((meta.get(r["view"].zone) or {}).get("outdoor"))} for r in rows]}


@router.post("/assets/{asset_id}/vistas3d")
async def views3d_save(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Vistas da maquete: propor as automáticas, mudar o nome, apagar; prompts e Gerar (app/routes/prompt_ops.py);
    começar a maquete com um espaço simples."""
    from app import location3d

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    form = await request.form()
    op = text(form, "op") or ""
    verb, _, arg = op.partition(":")
    url = f"/projetos/{project.id}/assets/{asset.id}"
    views = {v.id: v for v in location3d.location_views(session, asset)}
    notes = dict(asset.zone_notes or {})
    for key in form.keys():  # descrição de cada zona (EN): entra no prompt das imagens dessa zona
        if key.startswith("zn_name:"):
            name = text(form, key) or ""
            value = (text(form, "zn:" + key.partition(":")[2]) or "").strip()[:1500]
            if value:
                notes[name] = value
            else:
                notes.pop(name, None)
    if notes != (asset.zone_notes or {}):
        asset.zone_notes = notes or None
        session.add(asset)
    for key in form.keys():  # nomes e prompts editados
        kind, _, raw = key.partition(":")
        v = views.get(int(raw)) if raw.isdigit() else None
        if v is None:
            continue
        if kind == "lv_label":
            v.label = (text(form, key) or v.label)[:40]
        session.add(v)
    if verb == "lv_auto":
        n = location3d.auto_views(session, asset)
        flash_info(request, f"{n} vista(s) proposta(s): a referência de cada divisão e 4 vistas a partir do centro." if n
                   else "Já existem as vistas automáticas de todas as divisões.")
    elif verb == "layout_blank" and not asset.layout3d:
        from app import scene3d

        asset.layout3d = scene3d.validate_set(scene3d.BLANK_ROOM)
        session.add(asset)
        flash_info(request, "Maquete começada com uma sala simples. Ajusta-a com o Assistente (ex.: «junta a cozinha "
                            "atrás do balcão») e propõe as vistas automáticas.")
    elif verb == "lv_del" and int(arg or 0) in views:
        for m in session.exec(select(MediaFile).where(MediaFile.locview_id == int(arg))).all():
            reject_media(session, m)
        session.delete(views[int(arg)])
    prompt_ops.save(session, project, form, op)
    session.commit()
    if prompt_ops.run(request, session, project, form, op):
        session.commit()
        _p, _k, ref = op.partition(":")[2].partition(":")
        return RedirectResponse(f"{url}#vista3d-{ref}" if ref.isdigit() else f"{url}#maquete", status_code=303)
    anchor = text(form, "anchor") or ""
    return RedirectResponse(url + "#" + (anchor if re.fullmatch(r"zona-\d+", anchor) else "maquete"), status_code=303)


@router.post("/assets/{asset_id}/vistas3d/refs")
async def views3d_refs(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Referências visuais de uma zona, escolhidas no modal."""
    from app import location3d

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    form = await request.form()
    zone = text(form, "zone") or ""
    allowed = {o["id"] for o in location3d.ref_options(session, asset)}
    chosen = [int(x) for x in form.getlist("zr") if str(x).isdigit() and int(x) in allowed]
    refs = dict(asset.zone_refs or {})
    if chosen:
        refs[zone] = list(dict.fromkeys(chosen))
    else:
        refs.pop(zone, None)
    asset.zone_refs = refs or None
    session.add(asset)
    session.commit()
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}", status_code=303)


@router.post("/assets/{asset_id}/vistas3d/nova")
async def views3d_add(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """"Guardar vista aqui": a posição da câmara da vista livre passa a ser uma vista (na divisão onde está)."""
    from fastapi.responses import JSONResponse

    from app import location3d
    from app.models import LocationView

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    data = await request.json()
    try:
        cam = location3d.clean_cam(data.get("cam") or {})
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    zone = location3d.zone_at(asset.layout3d, cam["pos"][0], cam["pos"][1], cam["pos"][2])
    if zone is None and any(z["name"] for z in location3d.zones(asset.layout3d)):
        zone = location3d.nearest_outdoor(asset.layout3d, cam["pos"][0], cam["pos"][1], cam["pos"][2])
        flash_info(request, (f"A câmara estava fora das zonas marcadas: a vista ficou em «{zone}», a zona ao ar livre mais "
                             "perto. Para ficar certo, pede ao Assistente para alargar essa zona (ou criar uma para esta "
                             "parte da rua)." if zone else "A câmara estava fora de todas as zonas: a vista ficou sem zona. "
                             "Pede ao Assistente uma zona para esta parte da maquete."))
    count = len([v for v in location3d.location_views(session, asset) if v.zone == zone])
    view = LocationView(asset_id=asset.id, zone=zone, kind="ref" if data.get("kind") == "ref" else "view", order=count,
                        label=(str(data.get("label") or "").strip() or f"vista {count + 1}")[:40], cam=cam)
    session.add(view)
    session.commit()
    return JSONResponse({"ok": True, "id": view.id, "zone": zone})


@router.post("/assets/{asset_id}/vistas3d/{view_id}/render")
async def views3d_render(request: Request, project_id: int, asset_id: int, view_id: int,
                         session: Session = Depends(get_session)):
    """Render de uma vista tirado pelo navegador (substitui o anterior), com a versão da maquete."""
    import re as _re

    from fastapi.responses import JSONResponse

    from app.media import image_extension, read_limited
    from app.models import LocationView

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    view = session.get(LocationView, view_id)
    if view is None or view.asset_id != asset.id:
        raise HTTPException(status_code=404, detail="Vista não encontrada.")
    form = await request.form()
    digest = str(form.get("hash") or "")
    upload = form.get("file")
    if not _re.fullmatch(r"[0-9a-f]{12}", digest) or upload is None or not hasattr(upload, "read"):
        return JSONResponse({"ok": False, "error": "Pedido incompleto."}, status_code=400)
    data = await read_limited(upload, MAX_IMAGE_BYTES)
    image_extension(data, "render.png")
    depth = form.get("depth")  # mapa de profundidade da mesma posição (controlo dos modelos de estrutura)
    ddata = None
    if depth is not None and hasattr(depth, "read"):
        ddata = await read_limited(depth, MAX_IMAGE_BYTES)
        image_extension(ddata, "depth.png")
    save_view_render(session, project, asset, view, digest, data, ddata)
    session.commit()
    return JSONResponse({"ok": True})


def save_view_render(session: Session, project: Project, asset: Asset, view, digest: str, data: bytes,
                     ddata: bytes | None) -> None:
    """Guarda o render de uma vista (substitui o anterior) e o mapa de profundidade, com a versão da maquete."""
    from pathlib import PurePosixPath

    from app.media import delete_file, safe_segment, write_file

    old = session.get(MediaFile, view.render_id) if view.render_id else None
    rel = PurePosixPath(safe_segment(project.prefix), "assets", safe_segment(asset.name), "maquete",
                        f"render_vista{view.id}_{digest}.png")
    write_file(str(rel), data)
    media = MediaFile(project_id=project.id, path=str(rel), kind="image", role="locrender", status="approved",
                      source="maquete", asset_id=asset.id, locview_id=view.id)
    session.add(media)
    session.flush()
    view.render_id, view.render_hash = media.id, digest
    session.add(view)
    if old is not None:
        if old.path != media.path:
            delete_file(old.path)
        session.delete(old)
    if ddata is not None:
        for prev in session.exec(select(MediaFile).where(MediaFile.locview_id == view.id, MediaFile.role == "locdepth")).all():
            delete_file(prev.path)
            session.delete(prev)
        drel = rel.with_name(f"render_vista{view.id}_{digest}_depth.png")
        write_file(str(drel), ddata)
        session.add(MediaFile(project_id=project.id, path=str(drel), kind="image", role="locdepth", status="approved",
                              source="maquete", asset_id=asset.id, locview_id=view.id))
    session.flush()


@router.get("/assets/{asset_id}/vistas.zip")
def views_zip(project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """As imagens aprovadas das vistas do local, num zip (o nome de cada uma diz a zona e a vista)."""
    from app import location3d
    from app.media_service import download_name
    from app.routes.files import media_available, zip_response

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    entries, seen = [], set()
    for v in location3d.location_views(session, asset):
        m = session.get(MediaFile, v.image_id) if v.image_id else None
        if m is not None and media_available(m):
            name = download_name(session, project, m)
            while name in seen:
                name = name.replace(".", f"_{v.id}.", 1)
            seen.add(name)
            entries.append((name, m))
    if not entries:
        raise HTTPException(status_code=404, detail="Este local ainda não tem vistas com imagem aprovada.")
    return zip_response(f"{asset.name}_vistas.zip", entries)


@router.post("/assets/{asset_id}/maquete/previas")
async def layout_previews(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Vistas da maquete do local tiradas pelo navegador (de cima e as 4 a partir do centro), para o Assistente."""
    from fastapi.responses import JSONResponse

    from app import scene3d

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    await scene3d.save_previews(request, session, project, f"assets/{asset.name}/maquete", asset_id=asset.id)
    return JSONResponse({"ok": True})


def _bytedance_rows(session: Session, asset: Asset) -> list[dict] | None:
    """O registo da personagem na ByteDance (só com a chave da BytePlus guardada)."""
    from app import character_registry
    from app.routes.gpt import bytedance_enabled

    if asset.kind != "character" or not bytedance_enabled(session):
        return None
    return character_registry.status_rows(session, asset)


def _detail(request: Request, session: Session, project: Project, asset: Asset, status_code=200):
    media = session.exec(select(MediaFile).where(MediaFile.asset_id == asset.id).order_by(MediaFile.created_at, MediaFile.id)).all()
    st, label, _pending = asset_state(asset, list(media))
    approved = next((m for m in media if m.id == asset.approved_image_id), None)
    portrait = next((m for m in media if asset.portrait_image_id and m.id == asset.portrait_image_id), None)
    ctx = {
        "asset": asset, "kinds": ASSET_KINDS, "kinds_plural": ASSET_KINDS_PLURAL,
        "state": st, "state_label": label,
        "results": [m for m in media if m.status == "pending" and m.role in ("sheet", "")],
        "rejected_n": sum(1 for m in media if m.status == "rejected" and m.role in ("sheet", "")),
        "portrait_results": [m for m in media if m.status == "pending" and m.role == "portrait"],
        "portrait_rejected_n": sum(1 for m in media if m.status == "rejected" and m.role == "portrait"),
        "portrait_approved": portrait,
        "portrait_jobs": gpt_jobs(session, project, "portrait", asset_id=asset.id) if asset.kind == "character" else [],
        **(_voice_ctx(session, project, asset, list(media)) if asset.kind == "character" else {}),
        "looks": _looks_ctx(session, project, asset, list(media)) if asset.kind == "character" else [],
        "bd_rows": _bytedance_rows(session, asset),
        "sheet_refs": sheet_ref_assets(session, asset),
        "photos": asset_photos(session, asset),
        "model3d": (lambda m: m if m is not None and m.status == "approved" else None)(
            session.get(MediaFile, asset.model3d_id) if asset.model3d_id else None),
        "model_kinds": MODEL_KINDS,
        **_layout_ctx(session, project, asset),
        "suggested_code": _suggest_code(session, project, asset),
        "source": _source_ctx(session, asset),
        "groups": project_groups(session, project),
        "sheet_ref_options": [a for a in _assets(session, project)
                              if a.id != asset.id and a.id not in (asset.sheet_refs or [])],
        "sheet_ref_groups": _sheet_ref_groups(session, project, asset),
        "approved": approved, "key_image": location_image(session, asset) if asset.kind == "location" else None,
        "scene_count": session.exec(select(func.count()).select_from(AssetSceneLink).where(AssetSceneLink.asset_id == asset.id)).one(),
        "main_form": "asset-form", "assistant_key": f"asset:{asset.id}",
        "gpt_jobs": gpt_jobs(session, project, "sheet", asset_id=asset.id), "gpt_enabled": gpt_enabled(session),
        "max_upload_mb": MAX_IMAGE_BYTES // (1024 * 1024),
    } | _prompt_ctx(session, project, asset)
    return render_stage(request, session, project, 4, "assets/detail.html", ctx, status_code)


# ── Guarda-roupa (personagens) ───────────────────────────────

def _looks_ctx(session: Session, project: Project, asset: Asset, media: list[MediaFile]) -> list[dict]:
    from app import looks as L

    scenes = session.exec(select(Scene).where(Scene.project_id == project.id).order_by(Scene.number)).all()
    out = []
    for lk in L.looks_of(session, asset):
        mine = [m for m in media if m.look_id == lk.id]
        out.append({
            "look": lk,
            "panel": prompt_ops.panel(session, PT.get(session, project, "look", str(lk.id)),
                                      f"Prompt da folha do look «{lk.name}»", people=1),
            "approved": next((m for m in mine if m.id == lk.image_id and m.status == "approved"), None),
            "results": [m for m in mine if m.status == "pending"],
            "rejected_n": sum(1 for m in mine if m.status == "rejected"),
            "jobs": gpt_jobs(session, project, "look", look_id=lk.id),
            "scenes": [s for s in scenes if (s.looks or {}).get(str(asset.id)) == lk.id],
        })
    return out


@router.post("/assets/{asset_id}/looks")
async def look_create(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Look novo no guarda-roupa da personagem (nome e roupa); a folha gera-se depois, a partir da folha aprovada."""
    from app.models import CharacterLook

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    if asset.kind != "character":
        raise HTTPException(status_code=400, detail="Só as personagens têm guarda-roupa.")
    form = await request.form()
    name = (text(form, "look_name") or "").strip()
    if not name:
        _flash(request, "Dá um nome ao look (ex.: Rua, Pijama, Fato de trabalho).")
        return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#looks", status_code=303)
    count = len(session.exec(select(CharacterLook).where(CharacterLook.asset_id == asset.id)).all())
    session.add(CharacterLook(project_id=project.id, asset_id=asset.id, name=name[:60],
                              description=text(form, "look_description"), order=count))
    session.commit()
    flash_info(request, f"Look «{name}» criado. Gera a folha dele quando a roupa estiver descrita.")
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#looks", status_code=303)


@router.post("/assets/{asset_id}/looks/{look_id}/ficheiros")
async def look_upload(request: Request, project_id: int, asset_id: int, look_id: int,
                      session: Session = Depends(get_session)):
    from app.models import CharacterLook

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    lk = session.get(CharacterLook, look_id)
    if lk is None or lk.asset_id != asset.id:
        raise HTTPException(status_code=404, detail="Look não encontrado.")
    await save_uploads(request, session, project, f"assets/{asset.name}/looks/look{lk.id}", "image", "look",
                       asset_id=asset.id, look_id=lk.id)
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#look-{lk.id}", status_code=303)


def delete_look(session: Session, look) -> None:
    """Apaga um look: as folhas dele e a escolha nas cenas (essas cenas voltam à folha base)."""
    from app.models import CharacterLook

    for m in session.exec(select(MediaFile).where(MediaFile.look_id == look.id)).all():
        reject_media(session, m)
    for sc in session.exec(select(Scene).where(Scene.project_id == look.project_id)).all():
        if (sc.looks or {}).get(str(look.asset_id)) == look.id:
            data = dict(sc.looks)
            data.pop(str(look.asset_id))
            sc.looks = data or None
            session.add(sc)
    session.delete(session.get(CharacterLook, look.id))


# ── Voz de referência (personagens) ──────────────────────────

def _voice_defaults(session: Session, project: Project, asset: Asset) -> tuple[str, str]:
    """(texto de amostra: as falas da personagem no guião; descrição da voz: a da ficha, com tipo e timbre)."""
    from app.options import prompt_texts

    lines = session.exec(select(SceneLine).where(SceneLine.character_id == asset.id, SceneLine.kind == "dialogue",
                                                 SceneLine.text != "").order_by(SceneLine.id)).all()
    sample = ""
    for ln in lines:
        if len(sample) + len(ln.text) > 280:
            break
        sample = (sample + " " + ln.text.strip()).strip()
    t = prompt_texts(session)
    parts = [asset.voice, t.get(("voice_type", asset.voice_type or "")), t.get(("timbre", asset.voice_timbre or ""))]
    return sample, ", ".join(p.strip() for p in parts if p and p.strip())


def _voice_ctx(session: Session, project: Project, asset: Asset, media: list[MediaFile]) -> dict:
    from app import voice_models
    from app.routes.video import voice_budget

    sample, description = _voice_defaults(session, project, asset)
    sample = asset.voice_sample or sample  # o texto guardado na ficha; sem ele, as falas do guião
    description = asset.voice or description
    cfg = (get_config(session, "voice_generator", {}) or {})
    options = voice_models.options(session)
    return {
        "voice_ref": next((m for m in media if asset.voice_ref_id and m.id == asset.voice_ref_id), None),
        "voice_results": [m for m in media if m.role == "voice" and m.status == "pending"],
        "voice_jobs": gpt_jobs(session, project, "voice", asset_id=asset.id),
        "voice_options": options, "voice_default": _voice_pick(options, cfg.get("default")),
        "voice_can_design": any("design" in o["caps"] for o in options),
        "voice_sample": sample, "voice_description": description,
        "voice_budget": voice_budget(session, asset),
        "artlist_voices": voice_models.artlist_voices(session),
    }


def _voice_pick(options: list[dict], last: str | None) -> str | None:
    """O modelo pré-escolhido: o último usado, se for de descrever a voz; senão o primeiro que descreve; senão o
    último usado ou o primeiro."""
    by_value = {o["value"]: o for o in options}
    if last in by_value and "design" in by_value[last]["caps"]:
        return last
    design = next((o["value"] for o in options if "design" in o["caps"]), None)
    return design or (last if last in by_value else (options[0]["value"] if options else None))


@router.post("/assets/{asset_id}/bytedance")
async def bytedance_register(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Regista as imagens aprovadas da personagem na biblioteca de assets da ByteDance (em segundo plano)."""
    from app import bytedance, character_registry
    from app.options import get_config

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    creds = bytedance.keys(get_config(session, "api_keys", {}) or {})
    if not (creds.get("access") and creds.get("secret")):
        flash_info(request, "Faltam a Access Key e a Secret Key da BytePlus (Configuração → Chaves de API).")
    elif asset.kind == "character":
        character_registry.start_registration(asset.id)
        flash_info(request, "A registar na ByteDance: demora alguns minutos. Atualiza a página para ver o estado.")
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#bytedance", status_code=303)


@router.post("/assets/{asset_id}/voz")
async def voice_generate(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Gera a voz de referência de uma personagem com o modelo e a voz escolhidos (o resultado fica por aprovar)."""
    from app import jobs, voice_models
    from app.options import set_config
    from app.routes.gpt import provider_problem

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    form = await request.form()
    url = f"/projetos/{project.id}/assets/{asset.id}#voz"
    value = text(form, "voice_model") or ""
    provider, model = voice_models.parse(value)
    sample = (text(form, "voice_text") or "").strip()
    voice = (text(form, "voice_id") or "").strip()
    description = (text(form, "voice_description") or "").strip()
    if "voice_text" in form:  # o texto e a descrição ficam na ficha da personagem (o Assistente também os pode propor)
        asset.voice_sample = sample or None
    if "voice_description" in form and description:
        asset.voice = description
    session.add(asset)
    if text(form, "op") == "save":
        session.commit()
        flash_info(request, "Texto e descrição da voz guardados.")
        return RedirectResponse(url, status_code=303)
    problem = provider_problem(session, provider)
    if asset.kind != "character":
        problem = "Só as personagens têm voz de referência."
    elif not sample:
        problem = "Escreve o texto que a voz vai dizer (uma ou duas falas da personagem, na língua dos diálogos)."
    elif provider == "artlist" and not voice.isdigit():
        problem = "Escolhe uma voz do Artlist (carrega primeiro a lista de vozes)."
    elif provider == "fal":
        try:
            voice_models.fal_request(model, sample, voice, description)
        except ValueError as exc:
            problem = str(exc)
    if not problem and any(j.status in ("queued", "running") for j in jobs.jobs_for(session, project, "voice", asset_id=asset.id)):
        problem = "Já há uma voz a ser gerada para esta personagem. Espera que termine."
    if problem:
        _flash(request, problem)
        return RedirectResponse(url, status_code=303)
    jobs.enqueue(session, project, "voice", sample, None, [], kind="tts", provider=provider, model_group_id=model,
                 params={"voice": voice, "description": description}, asset_id=asset.id)
    set_config(session, "voice_generator", {**(get_config(session, "voice_generator", {}) or {}), "default": value})
    session.commit()
    jobs.wake()
    flash_info(request, "Pedido de voz enviado. Demora até um minuto; a voz aparece por baixo, para ouvires e aprovares.")
    return RedirectResponse(url, status_code=303)


@router.post("/assets/{asset_id}/voz/ficheiros")
async def voice_upload(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    if asset.kind != "character":
        raise HTTPException(status_code=400, detail="Só as personagens têm voz de referência.")
    await save_uploads(request, session, project, f"assets/{asset.name}/voz", "audio", "voice", asset_id=asset.id)
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#voz", status_code=303)


@router.post("/assets/{asset_id}/voz/{media_id}/cortar")
async def voice_trim(request: Request, project_id: int, asset_id: int, media_id: int,
                     session: Session = Depends(get_session)):
    """Corta uma voz de referência (de um segundo a outro). O corte fica como versão nova; o original não muda."""
    from pathlib import PurePosixPath

    from app.media import UploadError, media_root, trim_audio, write_file
    from app.media_service import approve_media
    from app.routes.files import flash

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    media = session.get(MediaFile, media_id)
    url = f"/projetos/{project.id}/assets/{asset.id}#voz"
    if media is None or media.project_id != project.id or media.asset_id != asset.id or media.role != "voice":
        raise HTTPException(status_code=404, detail="Voz não encontrada.")
    form = await request.form()

    def seconds(name: str) -> float | None:
        raw = (text(form, name) or "").replace(",", ".")
        try:
            return round(float(raw), 2)
        except ValueError:
            return None

    start, end = seconds("start_s"), seconds("end_s")
    total = (media.duration_ms or 0) / 1000
    if start is None or end is None or start < 0 or end - start < 0.5 or (total and start >= total):
        flash(request, "Escolhe um início e um fim com pelo menos meio segundo entre eles.")
        return RedirectResponse(url, status_code=303)
    if total:
        end = min(end, total)
    try:
        data, duration = trim_audio(media.path, start, end)
    except UploadError as exc:
        flash(request, str(exc))
        return RedirectResponse(url, status_code=303)
    base = PurePosixPath(media.path).parent
    n = 1
    rel = base / f"v{n}.mp3"
    while (media_root() / str(rel)).exists():
        n += 1
        rel = base / f"v{n}.mp3"
    write_file(str(rel), data)
    new = MediaFile(project_id=project.id, path=str(rel), kind="audio", role="voice", source=media.source,
                    status="pending", duration_ms=duration, asset_id=asset.id)
    session.add(new)
    session.flush()
    length = f"{(duration or 0) / 1000:.1f}".replace(".", ",")
    if form.get("approve"):
        approve_media(session, new)
        flash_info(request, f"Corte de {length} s guardado e aprovado: é esta a voz que segue nos vídeos. "
                            "A anterior ficou em «Por decidir».")
    else:
        flash_info(request, f"Corte de {length} s guardado em «Por decidir»; o original não mudou.")
    session.commit()
    return RedirectResponse(url, status_code=303)


VOICE_TASKS: set = set()


@router.post("/vozes-artlist")
async def artlist_voices_refresh(request: Request, project_id: int, session: Session = Depends(get_session)):
    """Lê o catálogo de vozes do Artlist em segundo plano (demora; a página mostra o estado)."""
    import asyncio
    import time

    from app import voice_models

    project = get_project(session, project_id)
    data = voice_models.artlist_voices(session)
    if data.get("status") != "loading":
        voice_models.save_artlist_voices(session, {**data, "status": "loading", "error": None})
        session.commit()

        async def run():
            from app import artlist
            from app.db import engine

            try:
                voices = await artlist.list_voices()
                result = {"voices": voices, "updated": time.strftime("%Y-%m-%d %H:%M"), "status": "ok", "error": None}
            except Exception as exc:  # noqa: BLE001
                result = {**data, "status": "error", "error": str(exc)[:300]}
            with Session(engine) as s2:
                voice_models.save_artlist_voices(s2, result)
                s2.commit()

        t = asyncio.get_event_loop().create_task(run())
        VOICE_TASKS.add(t)
        t.add_done_callback(VOICE_TASKS.discard)
    back = request.headers.get("referer") or f"/projetos/{project.id}"
    return RedirectResponse(back.split("#")[0] + "#voz", status_code=303)


@router.get("/assets/{asset_id}")
def asset_page(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    return _detail(request, session, project, _asset(session, project, asset_id))


@router.post("/assets/{asset_id}")
async def asset_save(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    form = await request.form()
    op = text(form, "op")

    if op == "delete":
        from app import trash

        trash.trash_asset(session, project, asset)  # recuperável na Reciclagem, com folhas, looks, vistas e maquete
        session.commit()
        flash_info(request, f"«{asset.label or asset.name}» foi para a Reciclagem (no topo da página): podes recuperá-lo "
                            "durante 30 dias.")
        back = text(form, "back_list") or ""
        return RedirectResponse(f"/projetos/{project.id}/etapa/4" + (back if back.startswith("?") else ""), status_code=303)

    label = text(form, "label")
    if not label:
        _flash(request, "O nome é obrigatório.")
        return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}", status_code=303)
    asset.label = label
    kind = text(form, "kind") or asset.kind
    if kind in ASSET_KINDS and kind != asset.kind:
        # Deixa de poder ser usado onde o tipo antigo era exigido.
        if asset.kind == "location":
            session.exec(update(Scene).where(Scene.location_id == asset.id).values(location_id=None))
        if asset.kind == "character":
            session.exec(update(SceneLine).where(SceneLine.character_id == asset.id).values(character_id=None))
        asset.kind = kind
    if "group_name" in form:
        asset.group_name = text(form, "group_name")
    asset.canonical_description = text(form, "canonical_description")
    for field in ("profile", "voice_type", "voice_timbre", "voice", "key_features", "voice_note"):
        if field in form:
            setattr(asset, field, text(form, field))

    # Referências para gerar a folha (outros assets do projeto)
    verb, _, arg = op.partition(":") if op else ("", "", "")
    refs = [i for i in (asset.sheet_refs or []) if i != asset.id]
    if "sheet_refs_sent" in form:  # modal de seleção: a escolha completa, pela ordem dos assets
        chosen = {int(v) for v in form.getlist("sheet_ref") if str(v).isdigit()}
        refs = [a.id for a in _assets(session, project) if a.id in chosen and a.id != asset.id]
    elif verb == "add_sheet_ref" and (text(form, "sheet_ref_add") or "").isdigit():
        ref = session.get(Asset, int(text(form, "sheet_ref_add")))
        if ref and ref.project_id == project.id and ref.id != asset.id and ref.id not in refs:
            refs.append(ref.id)
    elif verb == "remove_sheet_ref" and arg.isdigit():
        refs = [i for i in refs if i != int(arg)]
    asset.sheet_refs = refs

    # Guarda-roupa: nome e roupa de cada look; apagar um look
    from app import looks as L

    for lk in L.looks_of(session, asset) if asset.kind == "character" else []:
        if f"look_name:{lk.id}" in form and (text(form, f"look_name:{lk.id}") or "").strip():
            lk.name = text(form, f"look_name:{lk.id}").strip()[:60]
        if f"look_desc:{lk.id}" in form:
            lk.description = text(form, f"look_desc:{lk.id}")
        session.add(lk)
        if verb == "delete_look" and arg == str(lk.id):
            delete_look(session, lk)
            session.commit()
            flash_info(request, f"Look «{lk.name}» apagado; as cenas onde estava voltam à folha base.")
            return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#looks", status_code=303)

    session.add(asset)
    prompt_ops.save(session, project, form, op)
    session.commit()
    if prompt_ops.run(request, session, project, form, op):
        session.commit()
        anchor = ("#retrato" if ":retrato:" in f":{op}" else f"#look-{op.rsplit(':', 1)[-1]}" if ":look:" in f":{op}"
                  else "#resultados")
        return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}{anchor}", status_code=303)
    anchor = "#referencias-folha" if verb in ("add_sheet_ref", "remove_sheet_ref") else ""
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}" + ("" if op else "?guardado=1") + anchor, status_code=303)


@router.post("/importar-assets")
async def assets_import(request: Request, project_id: int, session: Session = Depends(get_session)):
    """Copia para este projeto os assets escolhidos de outro projeto (ficha, imagens, voz, looks, maquete e vistas)."""
    from app.asset_import import import_assets, missing_refs

    project = get_project(session, project_id)
    form = await request.form()
    ids = [int(v) for v in form.getlist("asset") if str(v).isdigit()]
    if not ids:
        _flash(request, "Escolhe pelo menos um asset para importar.")
        return RedirectResponse(f"/projetos/{project.id}/etapa/4", status_code=303)
    made = import_assets(session, project, ids)
    missing = missing_refs(session, project, made)
    session.commit()
    flash_info(request, (f"{len(made)} asset(s) importado(s): " + ", ".join(a.name for a in made) + ". São cópias: "
                         "o que mudares aqui não mexe no projeto de origem."
                         + (" Os prompts deles referem assets que não importaste: " + ", ".join(missing)
                            + " (importa-os também, ou o modelo recebe só o nome)." if missing else "")) if made else
               "Nada importado: esses assets já tinham sido importados.")
    return RedirectResponse(f"/projetos/{project.id}/etapa/4", status_code=303)


@router.post("/assets/{asset_id}/atualizar-origem")
async def asset_refresh(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Traz as mudanças do asset original (texto, imagens aprovadas, voz, looks, maquete e vistas)."""
    from app.asset_import import ImportError_, refresh

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    try:
        refresh(session, asset)
        session.commit()
        flash_info(request, "Atualizado a partir do original. As imagens que estavam aprovadas aqui e mudaram voltaram "
                            "a «Por decidir».")
    except ImportError_ as exc:
        session.rollback()
        _flash(request, str(exc))
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}", status_code=303)


@router.post("/assets/{asset_id}/codigo")
async def asset_rename(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    form = await request.form()
    new_code = (text(form, "new_code") or "").strip().upper()
    old = asset.name
    try:
        changed = rename_asset(session, project, asset, new_code)
        if new_code != old:
            request.session["flash_info"] = (f"Código mudado de {old} para {new_code}: ficheiros movidos e "
                                             f"{changed} texto(s) do projeto atualizados.")
    except ValueError as exc:
        _flash(request, str(exc))
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}", status_code=303)


@router.post("/assets/{asset_id}/resultados")
async def asset_upload(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    await save_uploads(request, session, project, f"assets/{asset.name}", "image", "sheet", asset_id=asset.id)
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#resultados", status_code=303)


@router.post("/assets/{asset_id}/fotos")
async def photo_upload(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Fotos de referência: ficam logo como referência (não há aprovar/rejeitar; "Tirar" apaga)."""
    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    form = await request.form()
    zone = form.get("zone")  # carregadas a partir de uma zona da maquete: ficam logo escolhidas como referência dela
    before = {m.id for m in asset_photos(session, asset)}
    await save_uploads(request, session, project, f"assets/{asset.name}/fotos", "image", "photo", status="approved",
                       asset_id=asset.id)
    if zone is not None:
        new = [m.id for m in asset_photos(session, asset) if m.id not in before]
        if new:
            refs = dict(asset.zone_refs or {})
            refs[str(zone)] = list(dict.fromkeys([*refs.get(str(zone), []), *new]))
            asset.zone_refs = refs
            session.add(asset)
            session.commit()
        anchor = str(form.get("anchor") or "")
        return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#"
                                + (anchor if re.fullmatch(r"zona-\d+", anchor) else "maquete"), status_code=303)
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#fotos", status_code=303)


@router.post("/assets/{asset_id}/retrato")
async def portrait_upload(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    if asset.kind != "character":
        raise HTTPException(status_code=400, detail="Só as personagens têm retrato.")
    await save_uploads(request, session, project, f"assets/{asset.name}/retrato", "image", "portrait", asset_id=asset.id)
    return RedirectResponse(f"/projetos/{project.id}/assets/{asset.id}#retrato", status_code=303)


# ── Modelo 3D (GLB) ───────────────────────────────────────────

MAX_MODEL_BYTES = 60 * 1024 * 1024
MODEL_KINDS = ("prop", "element")  # as personagens ficam cápsulas; os locais são a própria maquete


@router.post("/assets/{asset_id}/modelo3d")
async def model3d_upload(request: Request, project_id: int, asset_id: int, session: Session = Depends(get_session)):
    """Modelo 3D importado (.glb, um só ficheiro com tudo dentro): passa a ser a forma do asset nas maquetes e na
    encenação. O anterior fica em «Rejeitados»."""
    from app.media import UploadError, read_limited, safe_segment, write_file
    from app.media_service import approve_media
    from app.models import MediaFile
    from app.routes.files import flash

    project = get_project(session, project_id)
    asset = _asset(session, project, asset_id)
    url = f"/projetos/{project.id}/assets/{asset.id}#modelo3d"
    if asset.kind not in MODEL_KINDS:
        flash(request, "Só os adereços e os elementos do mundo têm modelo 3D.")
        return RedirectResponse(url, status_code=303)
    form = await request.form()
    upload = form.get("file") or (form.getlist("files") or [None])[0]
    try:
        if upload is None or not hasattr(upload, "read"):
            raise UploadError("Escolhe um ficheiro .glb.")
        data = await read_limited(upload, MAX_MODEL_BYTES)
        if data[:4] != b"glTF":
            raise UploadError(f"{upload.filename}: não é um modelo .glb (glTF binário). Exporta como .glb, com tudo "
                              "dentro de um só ficheiro (no Blender: Exportar → glTF 2.0 → formato .glb).")
    except UploadError as exc:
        flash(request, str(exc))
        return RedirectResponse(url, status_code=303)
    previous = session.get(MediaFile, asset.model3d_id) if asset.model3d_id else None
    folder = f"{safe_segment(project.prefix)}/assets/{safe_segment(asset.name)}/modelo3d"
    n = session.exec(select(func.count()).select_from(MediaFile).where(MediaFile.asset_id == asset.id,
                                                                       MediaFile.role == "model3d")).one() + 1
    rel = f"{folder}/v{n}.glb"
    write_file(rel, data)
    media = MediaFile(project_id=project.id, path=rel, kind="model", role="model3d", source="upload", status="pending",
                      asset_id=asset.id)
    session.add(media)
    session.flush()
    approve_media(session, media)
    if previous is not None and previous.id != media.id:
        reject_media(session, previous)
    session.commit()
    flash_info(request, f"Modelo 3D de {asset.label or asset.name} carregado ({len(data) / 1048576:.1f} MB): passa a "
                        "ser a forma dele nas maquetes, em cinzento.")
    return RedirectResponse(url, status_code=303)
