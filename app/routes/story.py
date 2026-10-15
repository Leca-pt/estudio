"""Etapa 2 — História: hierarquia (temporadas/episódios ou atos) e cenas.

Cada página tem um único formulário. Os botões de operação (↑/↓, apagar, adicionar,
linhas do guião) submetem o formulário com `op`: primeiro grava o que foi escrito,
depois aplica a operação.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, delete, func, select

from app.db import get_session
from app.forms import FormError, integer, text
from app.models import SCENE_LINE_KINDS, Act, Asset, AssetSceneLink, Episode, Project, Scene, SceneLine, Season
from app.options import duration_choices
from app.routes.production import durations
from app.security import csrf_protect, require_login
from app.stages import get_project, render_stage

router = APIRouter(
    prefix="/projetos/{project_id}/historia",
    dependencies=[Depends(require_login), Depends(csrf_protect)],
)

STAGE = 2


# ── Consultas ────────────────────────────────────────────────

def _container_filter(project: Project, episode: Episode | None = None, act: Act | None = None):
    if episode is not None:
        return Scene.episode_id == episode.id
    if act is not None:
        return Scene.act_id == act.id
    return (Scene.project_id == project.id) & Scene.episode_id.is_(None) & Scene.act_id.is_(None)


def _scenes(session: Session, where) -> list[Scene]:
    return list(session.exec(select(Scene).where(where).order_by(Scene.number, Scene.id)).all())


def _lines(session: Session, scene: Scene) -> list[SceneLine]:
    return list(session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order, SceneLine.id)).all())


def _assets(session: Session, project: Project, kind: str) -> list[Asset]:
    return list(session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == kind).order_by(Asset.name)).all())


def _seasons(session: Session, project: Project) -> list[Season]:
    return list(session.exec(select(Season).where(Season.project_id == project.id).order_by(Season.number, Season.id)).all())


def _episodes(session: Session, season: Season) -> list[Episode]:
    return list(session.exec(select(Episode).where(Episode.season_id == season.id).order_by(Episode.number, Episode.id)).all())


def _acts(session: Session, project: Project) -> list[Act]:
    return list(session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number, Act.id)).all())


def _shot_counts(session: Session, scene_ids: list[int]) -> dict[int, int]:
    from sqlalchemy import func

    from app.models import Shot

    if not scene_ids:
        return {}
    rows = session.exec(select(Shot.scene_id, func.count(Shot.id)).where(Shot.scene_id.in_(scene_ids)).group_by(Shot.scene_id)).all()
    return dict(rows)


def _line_counts(session: Session, scene_ids: list[int]) -> dict[int, int]:
    if not scene_ids:
        return {}
    return dict(session.exec(
        select(SceneLine.scene_id, func.count()).where(SceneLine.scene_id.in_(scene_ids), SceneLine.text != "")
        .group_by(SceneLine.scene_id)
    ).all())


def _scene_counts(session: Session, column) -> dict[int, int]:
    return dict(session.exec(select(column, func.count()).where(column.is_not(None)).group_by(column)).all())


# ── Carregar com verificação de pertença ────────────────────

def _season(session: Session, project: Project, season_id: int) -> Season:
    season = session.get(Season, season_id)
    if season is None or season.project_id != project.id:
        raise HTTPException(status_code=404, detail="Temporada não encontrada.")
    return season


def _episode(session: Session, project: Project, episode_id: int) -> tuple[Episode, Season]:
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episódio não encontrado.")
    return episode, _season(session, project, episode.season_id)


def _act(session: Session, project: Project, act_id: int) -> Act:
    act = session.get(Act, act_id)
    if act is None or act.project_id != project.id:
        raise HTTPException(status_code=404, detail="Ato não encontrado.")
    return act


def _scene(session: Session, project: Project, scene_id: int) -> Scene:
    scene = session.get(Scene, scene_id)
    if scene is None or scene.project_id != project.id:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    return scene


def _asset_of(session: Session, project: Project, asset_id: int | None, kind: str) -> int | None:
    if asset_id is None:
        return None
    asset = session.get(Asset, asset_id)
    if asset is None or asset.project_id != project.id or asset.kind != kind:
        raise FormError("Asset inválido.")
    return asset.id


# ── Páginas ──────────────────────────────────────────────────

def _expanded_id(request: Request, scenes: list[Scene]) -> int | None:
    raw = request.query_params.get("cena")
    if raw is not None:
        try:
            wanted = int(raw)
        except ValueError:
            wanted = 0
        return wanted if any(s.id == wanted for s in scenes) else None
    return scenes[0].id if scenes else None


KIND_GROUPS = (("character", "Personagens"), ("location", "Locais"), ("prop", "Adereços"), ("element", "Elementos do mundo"))


def _scene_cast(session: Session, project: Project, scene: Scene) -> dict:
    """Quem e o que aparece na cena: o resumo (miniaturas) e os grupos do modal de seleção."""
    from app.routes.production import approved_media

    everything = session.exec(select(Asset).where(Asset.project_id == project.id).order_by(Asset.name)).all()
    linked = set(session.exec(select(AssetSceneLink.asset_id).where(AssetSceneLink.scene_id == scene.id)).all())
    media = approved_media(session, [a.approved_image_id for a in everything])
    groups = []
    for kind, label in KIND_GROUPS:
        items = [(a.id, media.get(a.approved_image_id), a.name + (f" · {a.group_name}" if a.group_name else ""))
                 for a in everything if a.kind == kind and a.id != scene.location_id]
        groups.append((label, items))
    chosen = [a for a in everything if a.id in linked and a.id != scene.location_id]
    chosen.sort(key=lambda a: [k for k, _l in KIND_GROUPS].index(a.kind))
    return {"cast_groups": groups, "cast_ids": [a.id for a in chosen],
            "cast_picked": [(media.get(a.approved_image_id), a.name) for a in chosen]}


def _scenes_ctx(request: Request, session: Session, project: Project, scenes: list[Scene]) -> dict:
    expanded = _expanded_id(request, scenes)
    expanded_scene = next((s for s in scenes if s.id == expanded), None)
    return {
        "scenes": scenes,
        "expanded": expanded,
        "lines": _lines(session, expanded_scene) if expanded_scene else [],
        "line_counts": _line_counts(session, [s.id for s in scenes]),
        "shot_counts": _shot_counts(session, [s.id for s in scenes]),
        "locations": {a.id: a.name for a in _assets(session, project, "location")},
        **(_scene_cast(session, project, expanded_scene) if expanded_scene else {}),
        "characters": {a.id: a.name for a in _assets(session, project, "character")},
        "line_kinds": SCENE_LINE_KINDS,
        "main_form": "story-form",
    }


def overview_page(request: Request, session: Session, project: Project, error=None, status_code=200):
    ctx: dict = {"error": error, "main_form": "story-form", "action": f"/projetos/{project.id}/historia"}
    if project.type != "series":
        all_ids = list(session.exec(select(Scene.id).where(Scene.project_id == project.id)).all())
        ctx.update({"durations": durations(session, all_ids), "target": project.target_duration_s})
    current = ("project",)
    if project.type == "short":
        ctx.update(_scenes_ctx(request, session, project, _scenes(session, _container_filter(project))))
        if ctx["expanded"]:
            current = ("scene", ctx["expanded"])
            from app import scene_nav

            ctx["nav"] = scene_nav.build(session, project, session.get(Scene, ctx["expanded"]), "script")
    elif project.type == "series":
        seasons = _seasons(session, project)
        ctx["seasons"] = seasons
        ctx["episodes_by_season"] = {s.id: _episodes(session, s) for s in seasons}
        ctx["scene_counts"] = _scene_counts(session, Scene.episode_id)
    else:
        ctx["acts"] = _acts(session, project)
        ctx["scene_counts"] = _scene_counts(session, Scene.act_id)
    ctx.update({"assistant_key": "story", "assistant_scene": ctx.get("expanded")})
    return render_stage(request, session, project, STAGE, "stages/stage_2.html", ctx, status_code, current)


def _episode_view(request, session, project, episode, season, error=None, status_code=200):
    ctx = _scenes_ctx(request, session, project, _scenes(session, _container_filter(project, episode=episode)))
    ctx.update({"durations": durations(session, [s.id for s in ctx["scenes"]]), "target": episode.target_duration_s})
    ctx.update({"episode": episode, "season": season, "error": error,
                "duration_choices": duration_choices(session, project.type),
                "action": f"/projetos/{project.id}/historia/episodios/{episode.id}"})
    ctx.update({"assistant_key": f"episode:{episode.id}", "assistant_scene": ctx["expanded"]})
    current = ("scene", ctx["expanded"]) if ctx["expanded"] else ("episode", episode.id)
    if ctx.get("expanded"):
        from app import scene_nav

        ctx["nav"] = scene_nav.build(session, project, session.get(Scene, ctx["expanded"]), "script")
    return render_stage(request, session, project, STAGE, "story/episode.html", ctx, status_code, current)


def _act_view(request, session, project, act, error=None, status_code=200):
    ctx = _scenes_ctx(request, session, project, _scenes(session, _container_filter(project, act=act)))
    ctx.update({"durations": durations(session, [s.id for s in ctx["scenes"]]), "target": None})
    ctx.update({"act": act, "error": error, "action": f"/projetos/{project.id}/historia/atos/{act.id}"})
    ctx.update({"assistant_key": f"act:{act.id}", "assistant_scene": ctx["expanded"]})
    current = ("scene", ctx["expanded"]) if ctx["expanded"] else ("act", act.id)
    if ctx.get("expanded"):
        from app import scene_nav

        ctx["nav"] = scene_nav.build(session, project, session.get(Scene, ctx["expanded"]), "script")
    return render_stage(request, session, project, STAGE, "story/act.html", ctx, status_code, current)


@router.get("/episodios/{episode_id}")
def episode_page(request: Request, project_id: int, episode_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    episode, season = _episode(session, project, episode_id)
    return _episode_view(request, session, project, episode, season)


@router.get("/atos/{act_id}")
def act_page(request: Request, project_id: int, act_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    return _act_view(request, session, project, _act(session, project, act_id))


@router.get("/temporadas/{season_id}")
def season_page(project_id: int, season_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    season = _season(session, project, season_id)
    return RedirectResponse(f"/projetos/{project.id}/etapa/2#temporada-{season.id}", status_code=303)


@router.get("/cenas/{scene_id}")
def scene_page(project_id: int, scene_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    scene = _scene(session, project, scene_id)
    return RedirectResponse(_container_url(project, scene.episode_id, scene.act_id, scene.id), status_code=303)


def _container_url(project: Project, episode_id=None, act_id=None, scene_id=None) -> str:
    if episode_id:
        url = f"/projetos/{project.id}/historia/episodios/{episode_id}"
    elif act_id:
        url = f"/projetos/{project.id}/historia/atos/{act_id}"
    else:
        url = f"/projetos/{project.id}/etapa/2"
    if scene_id is not None:
        url += f"?cena={scene_id}#cena-{scene_id}"
    return url


# ── Gravar ───────────────────────────────────────────────────

def _set_if_present(obj, form, field: str, name: str | None = None) -> None:
    name = name or field
    if name in form:
        setattr(obj, field, text(form, name))


def _renumber(session: Session, items: list) -> None:
    for n, item in enumerate(items, start=1):
        if item.number != n:
            item.number = n
            session.add(item)


def _move(session: Session, items: list, item_id: int, delta: int) -> None:
    idx = next((i for i, it in enumerate(items) if it.id == item_id), None)
    if idx is None:
        raise HTTPException(status_code=404)
    j = idx + delta
    if 0 <= j < len(items):
        items[idx], items[j] = items[j], items[idx]
    _renumber(session, items)


def _save_scene(session: Session, project: Project, form, scene_ids: list[int], op: str | None) -> Scene | None:
    """Grava a cena aberta (campos + linhas do guião) e aplica operações de linha."""
    scene_id = integer(form, "scene_id", "Cena")
    if scene_id is None:
        return None
    if scene_id not in scene_ids:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    scene = _scene(session, project, scene_id)
    _set_if_present(scene, form, "title", "scene_title")
    _set_if_present(scene, form, "time_of_day", "scene_time_of_day")
    _set_if_present(scene, form, "description", "scene_description")
    if "scene_location_id" in form:
        scene.location_id = _asset_of(session, project, integer(form, "scene_location_id", "Local"), "location")
        if scene.location_id and not session.get(AssetSceneLink, (scene.location_id, scene.id)):
            session.add(AssetSceneLink(asset_id=scene.location_id, scene_id=scene.id))  # o local principal conta como "aparece na cena"
    session.add(scene)

    # Quem e o que aparece (modal de seleção): a escolha completa; o local principal fica sempre.
    if "scene_cast_sent" in form:
        valid = set(session.exec(select(Asset.id).where(Asset.project_id == project.id)).all())
        wanted = {int(v) for v in form.getlist("scene_cast") if str(v).isdigit() and int(v) in valid}
        if scene.location_id:
            wanted.add(scene.location_id)
        for link in session.exec(select(AssetSceneLink).where(AssetSceneLink.scene_id == scene.id)).all():
            if link.asset_id not in wanted:
                session.delete(link)
            wanted.discard(link.asset_id)
        for asset_id in wanted:
            session.add(AssetSceneLink(asset_id=asset_id, scene_id=scene.id))
        session.flush()

    kinds = form.getlist("line_kind")
    chars = form.getlist("line_character")
    texts = form.getlist("line_text")
    lines = []
    for kind, char, txt in zip(kinds, chars, texts):
        kind = kind if kind in SCENE_LINE_KINDS else "action"
        char_id = _asset_of(session, project, int(char), "character") if char.isdigit() and kind == "dialogue" else None
        lines.append({"kind": kind, "character_id": char_id, "text": txt.strip().replace("\r\n", "\n")})

    verb, _, arg = (op or "").partition(":")
    if verb in ("line_del", "line_up", "line_down") and arg.isdigit() and int(arg) < len(lines):
        i = int(arg)
        if verb == "line_del":
            lines.pop(i)
        elif verb == "line_up" and i > 0:
            lines[i - 1], lines[i] = lines[i], lines[i - 1]
        elif verb == "line_down" and i < len(lines) - 1:
            lines[i + 1], lines[i] = lines[i], lines[i + 1]
    if op is None:
        lines = [ln for ln in lines if ln["text"]]
    if verb in ("line_action", "line_dialogue"):
        lines.append({"kind": verb.removeprefix("line_"), "character_id": None, "text": ""})

    session.exec(delete(SceneLine).where(SceneLine.scene_id == scene.id))
    for n, ln in enumerate(lines):
        session.add(SceneLine(scene_id=scene.id, order=n, **ln))
    # Quem fala na cena aparece na cena.
    for char_id in {ln["character_id"] for ln in lines if ln["character_id"]}:
        if not session.get(AssetSceneLink, (char_id, scene.id)):
            session.add(AssetSceneLink(asset_id=char_id, scene_id=scene.id))
    return scene


def _add_scene(session: Session, project: Project, where, episode=None, act=None) -> Scene:
    current = session.exec(select(func.max(Scene.number)).where(where)).one()
    scene = Scene(project_id=project.id, episode_id=episode.id if episode else None,
                  act_id=act.id if act else None, number=(current or 0) + 1)
    session.add(scene)
    session.flush()
    return scene


def _scene_ops(session: Session, project: Project, op: str, scenes: list[Scene], where, episode=None, act=None):
    """Operações sobre a lista de cenas. Devolve o id da cena a abrir (ou None se não aplicável)."""
    verb, _, arg = op.partition(":")
    if verb == "open" and arg.isdigit():
        return int(arg)
    if verb == "add_scene":
        return _add_scene(session, project, where, episode, act).id
    if verb in ("scene_up", "scene_down") and arg.isdigit():
        _move(session, scenes, int(arg), -1 if verb == "scene_up" else 1)
        return int(arg)
    if verb == "del_scene" and arg.isdigit():
        scene = next((s for s in scenes if s.id == int(arg)), None)
        if scene is None:
            raise HTTPException(status_code=404)
        from app import trash

        trash.trash_scene(session, project, scene)  # recuperável na Reciclagem (renumera as outras)
        return 0
    return None


def _redirect(url: str, scene_id: int | None, saved: bool) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if scene_id is not None:
        url += f"{sep}cena={scene_id}"
        sep = "&"
    if saved:
        url += f"{sep}guardado=1"
    if scene_id:
        url += f"#cena-{scene_id}"
    return RedirectResponse(url, status_code=303)


@router.post("")
async def overview_save(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    op = text(form, "op")
    try:
        open_scene = None

        if project.type == "short":
            where = _container_filter(project)
            scenes = _scenes(session, where)
            scene = _save_scene(session, project, form, [s.id for s in scenes], op)
            open_scene = scene.id if scene else None
            if op:
                result = _scene_ops(session, project, op, scenes, where)
                open_scene = result if result is not None else open_scene

        elif project.type == "series":
            seasons = _seasons(session, project)
            for season in seasons:
                key = f"season-{season.id}-"
                _set_if_present(season, form, "title", key + "title")
                _set_if_present(season, form, "arc", key + "arc")
                _set_if_present(season, form, "summary", key + "summary")
                if key + "planned" in form:
                    season.planned_episodes = integer(form, key + "planned", "Episódios previstos", minimum=1)
                session.add(season)
            verb, _, arg = (op or "").partition(":")
            if verb == "add_season":
                session.add(Season(project_id=project.id, number=len(seasons) + 1))
            elif verb in ("season_up", "season_down") and arg.isdigit():
                _move(session, seasons, int(arg), -1 if verb == "season_up" else 1)
            elif verb == "del_season" and arg.isdigit():
                from app import trash

                trash.trash_container(session, project, _season(session, project, int(arg)))
            elif verb == "add_episode" and arg.isdigit():
                season = _season(session, project, int(arg))
                episode = Episode(season_id=season.id, number=len(_episodes(session, season)) + 1,
                                  target_duration_s=project.target_duration_s)  # herda o formato da série
                session.add(episode)
                session.commit()
                return RedirectResponse(f"/projetos/{project.id}/historia/episodios/{episode.id}", status_code=303)
            elif verb in ("episode_up", "episode_down") and arg.isdigit():
                episode, season = _episode(session, project, int(arg))
                _move(session, _episodes(session, season), episode.id, -1 if verb == "episode_up" else 1)

        else:
            acts = _acts(session, project)
            for act in acts:
                key = f"act-{act.id}-"
                _set_if_present(act, form, "title", key + "title")
                _set_if_present(act, form, "arc", key + "arc")
                _set_if_present(act, form, "summary", key + "summary")
                session.add(act)
            verb, _, arg = (op or "").partition(":")
            if verb == "add_act":
                session.add(Act(project_id=project.id, number=len(acts) + 1))
            elif verb in ("act_up", "act_down") and arg.isdigit():
                _move(session, acts, int(arg), -1 if verb == "act_up" else 1)
            elif verb == "del_act" and arg.isdigit():
                from app import trash

                trash.trash_container(session, project, _act(session, project, int(arg)))
    except FormError as exc:
        session.rollback()
        return overview_page(request, session, project, str(exc), 400)

    session.commit()
    if (op or "").startswith(("del_scene", "del_season", "del_act")):
        request.session["flash_info"] = "Foi para a Reciclagem (no topo da página): podes recuperá-lo durante 30 dias."
    return _redirect(f"/projetos/{project.id}/etapa/2", open_scene, saved=op is None)


@router.post("/episodios/{episode_id}")
async def episode_save(request: Request, project_id: int, episode_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    episode, season = _episode(session, project, episode_id)
    form = await request.form()
    op = text(form, "op")
    url = f"/projetos/{project.id}/historia/episodios/{episode.id}"
    try:
        for field in ("title", "logline", "synopsis"):
            _set_if_present(episode, form, field, "ep_" + field)
        if "ep_target_duration_s" in form:
            episode.target_duration_s = integer(form, "ep_target_duration_s", "Duração-alvo", minimum=1)
        session.add(episode)
        where = _container_filter(project, episode=episode)
        scenes = _scenes(session, where)
        scene = _save_scene(session, project, form, [s.id for s in scenes], op)
        open_scene = scene.id if scene else None
        if op == "del_episode":
            from app import trash

            trash.trash_container(session, project, episode)
            session.commit()
            request.session["flash_info"] = "Foi para a Reciclagem (no topo da página): podes recuperá-lo durante 30 dias."
            return RedirectResponse(f"/projetos/{project.id}/etapa/2#temporada-{season.id}", status_code=303)
        if op:
            result = _scene_ops(session, project, op, scenes, where, episode=episode)
            open_scene = result if result is not None else open_scene
    except FormError as exc:
        session.rollback()
        return _episode_view(request, session, project, episode, season, str(exc), 400)
    session.commit()
    if (op or "").startswith("del_scene"):
        request.session["flash_info"] = "Foi para a Reciclagem (no topo da página): podes recuperá-lo durante 30 dias."
    return _redirect(url, open_scene, saved=op is None)


@router.post("/atos/{act_id}")
async def act_save(request: Request, project_id: int, act_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    act = _act(session, project, act_id)
    form = await request.form()
    op = text(form, "op")
    url = f"/projetos/{project.id}/historia/atos/{act.id}"
    try:
        for field in ("title", "arc", "summary"):
            _set_if_present(act, form, field, "act_" + field)
        session.add(act)
        where = _container_filter(project, act=act)
        scenes = _scenes(session, where)
        scene = _save_scene(session, project, form, [s.id for s in scenes], op)
        open_scene = scene.id if scene else None
        if op:
            result = _scene_ops(session, project, op, scenes, where, act=act)
            open_scene = result if result is not None else open_scene
    except FormError as exc:
        session.rollback()
        return _act_view(request, session, project, act, str(exc), 400)
    session.commit()
    if (op or "").startswith("del_scene"):
        request.session["flash_info"] = "Foi para a Reciclagem (no topo da página): podes recuperá-lo durante 30 dias."
    return _redirect(url, open_scene, saved=op is None)

