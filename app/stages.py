"""Shell do projeto: stepper com estados, árvore lateral e seletor de projeto."""

from fastapi import HTTPException, Request
from sqlmodel import Session, func, select

from app.models import Act, Asset, AssetSceneLink, Episode, MediaFile, Project, Scene, SceneLine, Season, Shot
from app.options import get_options, project_type_labels
from app.templating import templates

STAGES = [
    (1, "Projeto e bíblia", "Bíblia"),
    (2, "História", "História"),
    (3, "Por cena", "Por cena"),
    (4, "Design de assets", "Assets"),
    (5, "Storyboard", "Storyboard"),
    (6, "Geração de vídeo", "Vídeo"),
    (7, "Montagem", "Montagem"),
]
STAGE_NAMES = {n: name for n, name, _ in STAGES}

OK, WIP, PEND = "ok", "wip", "pend"
STATE_LABELS = {OK: "Aprovada", WIP: "Em curso", PEND: "Pendente"}

BIBLE_REQUIRED = ("logline", "genre", "tone", "audience", "render_style", "style_block", "aspect_ratio", "exclusions")


def _progress(done: int, total: int) -> tuple[str, str]:
    if total and done == total:
        return OK, STATE_LABELS[OK]
    if done:
        return WIP, f"{STATE_LABELS[WIP]} · {done}/{total}"
    return PEND, STATE_LABELS[PEND]


def scenes_with_lines(session: Session, project_id: int) -> tuple[set[int], int]:
    """(ids das cenas com guião, total de cenas) do projeto."""
    total = session.exec(select(func.count()).select_from(Scene).where(Scene.project_id == project_id)).one()
    with_lines = set(
        session.exec(
            select(SceneLine.scene_id).join(Scene, Scene.id == SceneLine.scene_id)
            .where(Scene.project_id == project_id, SceneLine.text != "").distinct()
        ).all()
    )
    return with_lines, total


def stage_state(session: Session, project: Project, stage: int, cache: dict | None = None) -> tuple[str, str]:
    if stage == 1:
        return _progress(sum(1 for f in BIBLE_REQUIRED if getattr(project, f)), len(BIBLE_REQUIRED))
    if stage == 2:
        cache = cache if cache is not None else {}
        if "lines" not in cache:
            cache["lines"] = scenes_with_lines(session, project.id)
        with_lines, total = cache["lines"]
        return _progress(len(with_lines), total) if total else (PEND, STATE_LABELS[PEND])
    if stage == 3:
        assets = session.exec(select(func.count()).select_from(Asset).where(Asset.project_id == project.id)).one()
        if not assets:
            return PEND, STATE_LABELS[PEND]
        total = session.exec(select(func.count()).select_from(Scene).where(Scene.project_id == project.id)).one()
        covered = session.exec(
            select(func.count(func.distinct(AssetSceneLink.scene_id)))
            .join(Scene, Scene.id == AssetSceneLink.scene_id).where(Scene.project_id == project.id)
        ).one()
        return _progress(covered, total) if total else (WIP, STATE_LABELS[WIP])
    if stage == 4:
        assets = session.exec(select(Asset).where(Asset.project_id == project.id)).all()
        with_view = set(session.exec(select(MediaFile.asset_id).where(
            MediaFile.project_id == project.id, MediaFile.role == "locview", MediaFile.status == "approved")).all())
        done = sum(1 for a in assets if asset_done(a, a.id in with_view))
        return _progress(done, len(assets)) if assets else (PEND, STATE_LABELS[PEND])
    if stage in (5, 6, 7):
        ids = list(session.exec(select(Shot.id).join(Scene, Scene.id == Shot.scene_id).where(Scene.project_id == project.id)).all())
        total = len(ids)
        done = sum(1 for sh in session.exec(select(Shot).where(Shot.id.in_(ids))).all()
                   if shot_board_state(session, sh) == OK) if stage == 5 else session.exec(
            select(func.count()).select_from(Shot).where(Shot.id.in_(ids), Shot.approved_clip_id.is_not(None))
        ).one() if ids else 0
        if stage == 7:  # montagem: pronta quando todos os clips estão aprovados
            return (OK, STATE_LABELS[OK]) if total and done == total else (PEND, STATE_LABELS[PEND])
        return _progress(done, total) if total else (PEND, STATE_LABELS[PEND])
    return PEND, STATE_LABELS[PEND]


def stage_states(session: Session, project: Project) -> list[tuple[int, str, str, str, str]]:
    cache: dict = {}
    return [(n, name, short, *stage_state(session, project, n, cache)) for n, name, short in STAGES]


def current_stage(states, project: Project | None = None) -> tuple[int, str]:
    """Primeira etapa ainda não aprovada, pela ordem do tipo de projeto: (número interno, nome)."""
    by_n = {n: state for n, _name, _short, state, _label in states}
    order = [n for n in TYPE_ORDER.get(project.type if project else "short", TYPE_ORDER["short"]) if n != "cenas"]
    for n in order:
        if by_n.get(n) != OK:
            return n, (stage_title(project, n)[0] if project else STAGE_NAMES[n])
    return 7, STAGE_NAMES[7]


def display_position(project: Project, n: int) -> int:
    """Número da etapa no stepper deste tipo de projeto."""
    order = TYPE_ORDER.get(project.type, TYPE_ORDER["short"])
    return order.index(n) + 1 if n in order else n


def get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    return project


# ── Estados de planos e cenas (storyboard e vídeo) ───────────

def asset_done(asset: Asset, has_view: bool) -> bool:
    """Asset pronto: folha aprovada (e retrato, nas personagens); um local, com maquete e pelo menos uma vista aprovada."""
    if asset.kind == "location":
        return bool(asset.layout3d) and has_view
    return bool(asset.approved_image_id) and (asset.kind != "character" or bool(asset.portrait_image_id))


def shot_board_state(session: Session, shot: Shot) -> str:
    """Storyboard do plano: pronto quando tem a cena 3D, o vídeo-guia aprovado e, em cada momento da câmara, uma vista
    do local aprovada que serve; em curso quando já tem a cena 3D."""
    from app import moments

    if not shot.scene3d:
        return PEND
    from app.memo import across

    def compute() -> str:
        total, done = moments.state(session, shot)
        return OK if total and done == total and shot.guide_video_id else WIP

    if session.new or session.dirty or session.deleted:
        return compute()
    scene = session.get(Scene, shot.scene_id)
    # guardado entre páginas enquanto o projeto não mudar (o progresso das etapas pede-o a todos os planos)
    return across(scene.project_id, ("board", shot.id, shot.updated_at), compute)


def shot_clip_state(shot: Shot, pending_roles: set[str]) -> str:
    if shot.approved_clip_id:
        return OK
    return WIP if ("clip" in pending_roles or shot.scene3d) else PEND


def pending_roles_by_shot(session: Session, project_id: int) -> dict[int, set[str]]:
    rows = session.exec(
        select(MediaFile.shot_id, MediaFile.role)
        .where(MediaFile.project_id == project_id, MediaFile.status == "pending", MediaFile.shot_id.is_not(None))
    ).all()
    out: dict[int, set[str]] = {}
    for shot_id, role in rows:
        out.setdefault(shot_id, set()).add(role)
    return out


def _group_state(states: list[str]) -> str:
    if not states:
        return PEND
    if all(s == OK for s in states):
        return OK
    return WIP if any(s != PEND for s in states) else PEND


# ── Árvore ───────────────────────────────────────────────────

def build_tree(session: Session, project: Project, current: tuple = ("project",), stage: int = 1) -> dict:
    base = f"/projetos/{project.id}"
    hist = f"{base}/historia"
    with_lines, total_scenes = scenes_with_lines(session, project.id)
    scenes = list(session.exec(select(Scene).where(Scene.project_id == project.id).order_by(Scene.number, Scene.id)).all())
    shots = list(session.exec(
        select(Shot).join(Scene, Scene.id == Shot.scene_id).where(Scene.project_id == project.id).order_by(Shot.number, Shot.id)
    ).all())
    pending = pending_roles_by_shot(session, project.id)
    shots_by_scene: dict[int, list[Shot]] = {}
    for sh in shots:
        shots_by_scene.setdefault(sh.scene_id, []).append(sh)

    production = stage >= 5  # storyboard, vídeo, montagem: a árvore mostra planos
    def shot_state(sh: Shot, roles: set[str]) -> str:
        return shot_clip_state(sh, roles) if stage >= 6 else shot_board_state(session, sh)
    kind, cur_id = current[0], (current[1] if len(current) > 1 else None)
    cur_shot = next((sh for sh in shots if kind == "shot" and sh.id == cur_id), None)
    cur_scene_id = cur_shot.scene_id if cur_shot else (cur_id if kind == "scene" else None)
    cur_scene = next((s for s in scenes if s.id == cur_scene_id), None)

    def scene_state(s: Scene) -> str:
        if production:
            return _group_state([shot_state(sh, pending.get(sh.id, set())) for sh in shots_by_scene.get(s.id, [])])
        return OK if s.id in with_lines else PEND

    def scene_href(s: Scene, story_base: str) -> str:
        if stage == 5:
            return f"{base}/storyboard/cenas/{s.id}"
        if stage >= 6:
            return f"{base}/etapa/{stage}#cena-{s.id}"
        return f"{story_base}?cena={s.id}#cena-{s.id}"

    def scene_nodes(s: Scene, level: int, story_base: str) -> list[dict]:
        scene_shots = shots_by_scene.get(s.id, [])
        is_open = production and cur_scene is not None and s.id == cur_scene.id
        nodes_ = [{"level": level, "code": f"C{s.number:02d}", "title": s.title or "Sem título",
                   "href": scene_href(s, story_base), "dot": scene_state(s),
                   "chevron": (("down" if is_open else "right") if scene_shots else "none") if production else None,
                   "on": cur_scene is not None and s.id == cur_scene.id and cur_shot is None}]
        if is_open:
            for sh in scene_shots:
                label = " · ".join(x for x in (sh.shot_type, sh.title) if x) or "Novo plano"
                href = f"{base}/video/planos/{sh.id}" if stage >= 6 else f"{base}/storyboard/planos/{sh.id}"
                nodes_.append({"level": level + 2, "code": f"P{sh.number:02d}", "title": label, "href": href,
                               "dot": shot_state(sh, pending.get(sh.id, set())),
                               "on": cur_shot is not None and sh.id == cur_shot.id})
        return nodes_

    nodes = [{"level": 1, "prefix": project.prefix, "title": "Projeto", "href": f"{base}/etapa/1",
              "on": current == ("project",)}]
    if project.type == "short":
        for s in scenes:
            nodes += scene_nodes(s, 1, f"{base}/etapa/2")
        footer = f"{len(scenes)} cena(s)"
    elif project.type == "series":
        seasons = session.exec(select(Season).where(Season.project_id == project.id).order_by(Season.number)).all()
        episodes = session.exec(
            select(Episode).join(Season, Season.id == Episode.season_id)
            .where(Season.project_id == project.id).order_by(Episode.number)
        ).all()
        open_episode = cur_id if kind == "episode" else (cur_scene.episode_id if cur_scene else None)
        n_eps = 0
        for season in seasons:
            eps = [e for e in episodes if e.season_id == season.id]
            n_eps += len(eps)
            season_scenes = [s for s in scenes if s.episode_id in {e.id for e in eps}]
            nodes.append({"level": 1, "code": f"T{season.number}", "title": season.title or f"Temporada {season.number}",
                          "href": f"{base}/etapa/2#temporada-{season.id}",
                          "dot": _group_state([scene_state(s) for s in season_scenes]),
                          "chevron": "down" if eps else "none", "on": current == ("season", season.id)})
            if not eps:
                nodes.append({"level": 3, "empty": "Sem episódios"})
            for e in eps:
                ep_scenes = [s for s in scenes if s.episode_id == e.id]
                is_open = e.id == open_episode
                nodes.append({"level": 2, "code": f"E{e.number:02d}", "title": e.title or f"Episódio {e.number}",
                              "href": f"{hist}/episodios/{e.id}", "dot": _group_state([scene_state(s) for s in ep_scenes]),
                              "chevron": ("down" if is_open else "right") if ep_scenes else "none",
                              "on": current == ("episode", e.id)})
                if is_open:
                    for s in ep_scenes:
                        nodes += scene_nodes(s, 3, f"{hist}/episodios/{e.id}")
        footer = f"{len(seasons)} temporada(s) · {n_eps} episódio(s)"
    else:
        acts = session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all()
        open_act = cur_id if kind == "act" else (cur_scene.act_id if cur_scene else None)
        for a in acts:
            act_scenes = [s for s in scenes if s.act_id == a.id]
            is_open = a.id == open_act
            nodes.append({"level": 1, "code": f"A{a.number}", "title": a.title or f"Ato {a.number}",
                          "href": f"{hist}/atos/{a.id}", "dot": _group_state([scene_state(s) for s in act_scenes]),
                          "chevron": ("down" if is_open else "right") if act_scenes else "none",
                          "on": current == ("act", a.id)})
            if is_open:
                for s in act_scenes:
                    nodes += scene_nodes(s, 2, f"{hist}/atos/{a.id}")
        footer = f"{len(acts)} ato(s) · {total_scenes} cena(s)"
    if shots:
        footer += f" · {len(shots)} plano(s)"
    return {"nodes": nodes, "footer": footer}


# ── Etapas por tipo de projeto ───────────────────────────────
# Série e filme: o mundo (assets) faz-se antes das cenas. Série: as etapas a seguir a Temporadas são do episódio atual.
# Curta: a ordem clássica (guião primeiro). Os números internos das etapas (URLs /etapa/N) não mudam.

# O quadro "quem entra em cada cena" (antigo Desglose, /etapa/3) já não é etapa: marca-se em cada cena e vê-se na
# vista "Por cena" dos Assets.
TYPE_ORDER = {"short": [1, 2, 4, 5, 6, 7], "series": [1, 4, 2, "cenas", 5, 6, 7], "film": [1, 4, 2, 5, 6, 7]}
EPISODE_STEPS = {"cenas", 5, 6, 7}


def stage_title(project: Project, n) -> tuple[str, str]:
    """(nome, nome curto) de uma etapa conforme o tipo de projeto."""
    if n == "cenas":
        return "Cenas do episódio", "Cenas"
    if n == 4 and project.type in ("series", "film"):
        return "Mundo", "Mundo"
    if n == 2 and project.type == "series":
        return "Temporadas e episódios", "Temporadas"
    _n, name, short = STAGES[n - 1]
    return name, short


def project_episodes(session: Session, project: Project) -> list[tuple[Episode, Season]]:
    if project.type != "series":
        return []
    return list(session.exec(
        select(Episode, Season).join(Season, Season.id == Episode.season_id)
        .where(Season.project_id == project.id).order_by(Season.number, Episode.number)
    ).all())


def current_episode(request: Request, session: Session, project: Project, override: int | None = None) -> Episode | None:
    """Episódio atual de uma série (fica na sessão; ?ep= ou a página do episódio mudam-no)."""
    eps = project_episodes(session, project)
    if not eps:
        return None
    ids = {e.id: e for e, _s in eps}
    key = f"ep:{project.id}"
    qp = request.query_params
    ambito = qp.get("ambito") or ""
    raw = override if override is not None else (
        qp.get("ep") or qp.get("grupo") or (ambito[3:] if ambito.startswith("ep-") else None))
    if raw is not None and str(raw).isdigit() and int(raw) in ids:
        request.session[key] = int(raw)
        return ids[int(raw)]
    stored = request.session.get(key)
    return ids.get(stored) or eps[0][0]


def _episode_state(session: Session, project: Project, n, episode: Episode) -> tuple[str, str]:
    scene_ids = list(session.exec(select(Scene.id).where(Scene.episode_id == episode.id)).all())
    total = len(scene_ids)
    if n == "cenas":
        with_lines = set(session.exec(select(SceneLine.scene_id).where(SceneLine.scene_id.in_(scene_ids),
                                                                       SceneLine.text != "").distinct()).all()) if total else set()
        return _progress(len(with_lines), total) if total else (PEND, STATE_LABELS[PEND])
    if n == 3:
        covered = session.exec(select(func.count(func.distinct(AssetSceneLink.scene_id)))
                               .where(AssetSceneLink.scene_id.in_(scene_ids))).one() if total else 0
        return _progress(covered, total) if total else (PEND, STATE_LABELS[PEND])
    shot_ids = list(session.exec(select(Shot.id).where(Shot.scene_id.in_(scene_ids))).all()) if total else []
    shots_total = len(shot_ids)
    if n == 5:
        done = sum(1 for sh in session.exec(select(Shot).where(Shot.id.in_(shot_ids))).all()
                   if shot_board_state(session, sh) == OK) if shot_ids else 0
    else:
        done = session.exec(select(func.count()).select_from(Shot).where(Shot.id.in_(shot_ids),
                                                                         Shot.approved_clip_id.is_not(None))).one() if shot_ids else 0
    if n == 7:
        return (OK, STATE_LABELS[OK]) if shots_total and done == shots_total else (PEND, STATE_LABELS[PEND])
    return _progress(done, shots_total) if shots_total else (PEND, STATE_LABELS[PEND])


def _seasons_state(session: Session, project: Project) -> tuple[str, str]:
    eps = project_episodes(session, project)
    if not eps:
        return PEND, STATE_LABELS[PEND]
    return _progress(sum(1 for e, _s in eps if e.logline and e.synopsis), len(eps))


def build_steps(session: Session, project: Project, stage: int, current: tuple, episode: Episode | None) -> list[dict]:
    base = f"/projetos/{project.id}"
    cache: dict = {}
    on_episode_page = stage == 2 and current and current[0] in ("episode", "scene") and project.type == "series"
    out = []
    for pos, n in enumerate(TYPE_ORDER.get(project.type, TYPE_ORDER["short"]), start=1):
        name, short = stage_title(project, n)
        per_episode = project.type == "series" and n in EPISODE_STEPS
        if per_episode and episode is not None:
            state = _episode_state(session, project, n, episode)
        elif project.type == "series" and n == 2:
            state = _seasons_state(session, project)
        elif n == "cenas":
            state = (PEND, STATE_LABELS[PEND])
        else:
            state = stage_state(session, project, n, cache)
        if n == "cenas":
            href = f"{base}/historia/episodios/{episode.id}" if episode else f"{base}/etapa/2"
        elif per_episode and episode is not None:
            href = f"{base}/etapa/{n}?" + (f"grupo={episode.id}" if n == 3 else
                                          f"ambito=ep-{episode.id}" if n in (6, 7) else f"ep={episode.id}")
        else:
            href = f"{base}/etapa/{n}"
        if current and current[0] == "shot" and len(current) > 1 and n in (5, 6):  # na página de um plano: o mesmo plano
            href = f"{base}/{'storyboard' if n == 5 else 'video'}/planos/{current[1]}"
        elif current and current[0] == "scene" and len(current) > 1 and n in (5, 6) and stage in (5, 6, 7):
            href = f"{base}/storyboard/cenas/{current[1]}" if n == 5 else f"{href}#cena-{current[1]}"
        on = (n == "cenas" and on_episode_page) or (n == stage and not (n == 2 and on_episode_page))
        out.append({"pos": pos, "n": n, "name": name, "short": short, "state": state[0], "label": state[1],
                    "href": href, "on": on, "episode": per_episode})
    return out


def stage_name_for(project: Project, stage: int, current: tuple) -> str:
    if project.type == "series" and stage == 2 and current and current[0] in ("episode", "scene"):
        return stage_title(project, "cenas")[0]
    return stage_title(project, stage)[0]


# ── Renderização ─────────────────────────────────────────────

def all_projects(session: Session) -> list[Project]:
    return list(session.exec(select(Project).order_by(Project.name)).all())


def render_stage(
    request: Request,
    session: Session,
    project: Project,
    stage: int,
    content_template: str,
    ctx: dict | None = None,
    status_code: int = 200,
    current: tuple = ("project",),
):
    """Desenha uma vista dentro da shell do projeto, com a etapa `stage` ativa."""
    states = stage_states(session, project)
    episode = current_episode(request, session, project,
                              override=current[1] if current and current[0] == "episode" else None)
    steps = build_steps(session, project, stage, current, episode)
    full_ctx = {
        "project": project,
        "projects": all_projects(session),
        "project_types": project_type_labels(session),
        "options": get_options(session),
        "stages": states,
        "stage": stage,
        "stage_name": stage_name_for(project, stage, current),
        "stage_state": states[stage - 1][3:],
        "steps": steps,
        "stage_pos": next((st["pos"] for st in steps if st["on"]), display_position(project, stage)),
        "step_titles": {st["n"]: (st["pos"], st["name"]) for st in steps},
        "cur_episode": episode,
        "episode_options": [(e.id, f"T{se.number} · E{e.number:02d}" + (f" · {e.title}" if e.title else ""))
                            for e, se in project_episodes(session, project)],
        "tree": build_tree(session, project, current, stage),
        "content_template": content_template,
        "error": None,
        "saved": request.query_params.get("guardado"),
    }
    full_ctx.update(ctx or {})
    from app.routes.assistant import panel_data

    full_ctx["asst"] = panel_data(session, project, full_ctx.get("assistant_key") or f"stage:{stage}",
                                  full_ctx.get("assistant_scene"))
    full_ctx["back_url"] = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    full_ctx["notice"] = request.session.pop("flash_info", None)
    flash = request.session.pop("flash_error", None)
    if flash and not full_ctx.get("error"):
        full_ctx["error"] = flash
    return templates.TemplateResponse(request, "projects/shell.html", full_ctx, status_code=status_code)
