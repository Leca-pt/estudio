import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, func, select

from app.db import get_session
from app.forms import FormError, integer, text
from app.models import PROJECT_TYPES, Act, Asset, Episode, Project, Scene, Season
from app.options import duration_choices, duration_key, get_config, project_type_labels
from app.routes import assets, montage, story, storyboard
from app.security import csrf_protect, require_login
from app.stages import STAGE_NAMES, TYPE_ORDER, current_stage, display_position, get_project, render_stage, stage_states
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_login), Depends(csrf_protect)])

PREFIX_RE = re.compile(r"^[A-Z0-9]{2,8}$")

# Campos da bíblia com drop-down (nome do campo = chave da lista).
BIBLE_SELECTS = (
    "genre", "tone", "audience", "dialogue_language", "render_style",
    "proportions", "palette_light", "aspect_ratio",
)
BIBLE_TEXTS = ("premise", "logline", "synopsis", "references", "style_block", "exclusions", "project_rules")


def _count(session: Session, model, project_id: int) -> int:
    return session.exec(select(func.count()).select_from(model).where(model.project_id == project_id)).one()


def type_locked(session: Session, project: Project) -> bool:
    return bool(_count(session, Scene, project.id) or _count(session, Season, project.id) or _count(session, Act, project.id))


def prefix_locked(session: Session, project: Project) -> bool:
    return bool(_count(session, Asset, project.id))


def _validate_identity(session: Session, project: Project | None, name, prefix, type_) -> str | None:
    if not name:
        return "O nome é obrigatório."
    if not prefix or not PREFIX_RE.match(prefix):
        return "O prefixo tem de ter 2 a 8 letras maiúsculas ou números (ex.: AVT)."
    if type_ not in PROJECT_TYPES:
        return "Tipo de projeto inválido."
    clash = session.exec(select(Project).where(Project.prefix == prefix)).first()
    if clash and (project is None or clash.id != project.id):
        return f"Já existe um projeto com o prefixo {prefix}."
    if project is not None:
        if prefix != project.prefix and prefix_locked(session, project):
            return "Não é possível mudar o prefixo: já há assets com o prefixo atual no nome."
        if type_ != project.type and type_locked(session, project):
            return "Não é possível mudar o tipo: o projeto já tem temporadas, atos ou cenas."
    return None


@router.get("/")
def home():
    return RedirectResponse("/projetos", status_code=303)


def _summary(session: Session, project: Project) -> str:
    if project.type == "series":
        seasons = _count(session, Season, project.id)
        episodes = session.exec(
            select(func.count()).select_from(Episode).join(Season, Season.id == Episode.season_id)
            .where(Season.project_id == project.id)
        ).one()
        return f"{seasons} temporada(s) · {episodes} episódio(s)"
    if project.type == "film":
        return f"{_count(session, Act, project.id)} ato(s) · {_count(session, Scene, project.id)} cena(s)"
    return f"{_count(session, Scene, project.id)} cena(s)"


def _cover(session: Session, project: Project):
    from app.models import MediaFile

    if project.cover_media_id:
        media = session.get(MediaFile, project.cover_media_id)
        if media and media.status == "approved":
            return media
    for role in ("sheet", "locview"):
        media = session.exec(select(MediaFile).where(MediaFile.project_id == project.id, MediaFile.role == role,
                                                     MediaFile.status == "approved").order_by(MediaFile.id)).first()
        if media:
            return media
    return None


def _list_page(request: Request, session: Session, error=None, form=None, status_code=200):
    filter_ = request.query_params.get("tipo")
    if filter_ not in PROJECT_TYPES and filter_ != "arquivados":
        filter_ = None
    stmt = select(Project).order_by(Project.updated_at.desc())
    if filter_ == "arquivados":
        stmt = stmt.where(Project.archived_at.is_not(None))
    else:
        stmt = stmt.where(Project.archived_at.is_(None))
        if filter_:
            stmt = stmt.where(Project.type == filter_)
    cards = []
    for p in session.exec(stmt).all():
        states = stage_states(session, p)
        n, name = current_stage(states, p)
        order = [x for x in TYPE_ORDER.get(p.type, TYPE_ORDER["short"]) if x != "cenas"]
        states = sorted((st for st in states if st[0] in order), key=lambda st: order.index(st[0]))  # barra de progresso pela ordem do tipo
        cards.append({"project": p, "states": states, "stage": (display_position(p, n), name), "summary": _summary(session, p),
                      "cover": _cover(session, p)})
    return templates.TemplateResponse(
        request,
        "projects/list.html",
        {
            "cards": cards,
            "filter": filter_,
            "total": session.exec(select(func.count()).select_from(Project).where(Project.archived_at.is_(None))).one(),
            "archived_n": session.exec(select(func.count()).select_from(Project).where(Project.archived_at.is_not(None))).one(),
            "notice": request.session.pop("flash_info", None),
            "project_types": project_type_labels(session),
            "error": error,
            "form": form or {},
            "all_projects": session.exec(select(Project).order_by(Project.name)).all(),
        },
        status_code=status_code,
    )


@router.get("/projetos")
def project_list(request: Request, session: Session = Depends(get_session)):
    return _list_page(request, session)


@router.post("/projetos")
def project_create(
    request: Request,
    name: str = Form(""),
    prefix: str = Form(""),
    type: str = Form(""),
    parent: str = Form(""),
    session: Session = Depends(get_session),
):
    name, prefix = name.strip(), prefix.strip().upper()
    error = _validate_identity(session, None, name, prefix, type)
    if error:
        return _list_page(request, session, error, {"name": name, "prefix": prefix, "type": type, "parent": parent}, 400)
    project = Project(name=name, prefix=prefix, type=type)
    source = session.get(Project, int(parent)) if parent.isdigit() else None
    if source is not None:  # parte de outro projeto (ex.: um spot da série): a bíblia visual e o mundo vêm dele
        from app.asset_import import copy_bible

        project.parent_id = source.id
        copy_bible(source, project)
    session.add(project)
    session.commit()
    return RedirectResponse(f"/projetos/{project.id}/etapa/1", status_code=303)


@router.get("/projetos/{project_id}")
def project_detail(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    last = (request.session.get("places") or {}).get(str(project.id))
    if last and last.startswith(f"/projetos/{project.id}/"):  # volta à página onde estavas neste projeto
        return RedirectResponse(last, status_code=303)
    stage, _ = current_stage(stage_states(session, project), project)
    target = stage  # a etapa em que o projeto está (a que o cartão mostra)
    return RedirectResponse(f"/projetos/{project_id}/etapa/{target}", status_code=303)


def bible_context(session: Session, project: Project) -> dict:
    return {
        "type_locked": type_locked(session, project),
        "prefix_locked": prefix_locked(session, project),
        "max_chars": get_config(session, "prompt_max_chars", 2000),
        "duration_choices": duration_choices(session, project.type),
        "main_form": "bible-form",
        "assistant_key": "bible",
    }


@router.get("/projetos/{project_id}/etapa/{stage}")
def project_stage(request: Request, project_id: int, stage: int, session: Session = Depends(get_session)):
    if stage not in STAGE_NAMES:
        raise HTTPException(status_code=404, detail="Etapa inexistente.")
    project = get_project(session, project_id)
    if stage == 1:
        from app.routes.gpt import cover_ctx

        return render_stage(request, session, project, 1, "stages/stage_1.html",
                            bible_context(session, project) | cover_ctx(session, project))
    if stage == 2:
        return story.overview_page(request, session, project)
    if stage == 3:
        qs = request.url.query
        return RedirectResponse(f"/projetos/{project.id}/etapa/4?vista=cenas" + (f"&{qs}" if qs else ""), status_code=303)
    if stage == 4:
        return assets.assets_page(request, session, project)
    if stage == 5:
        return storyboard.overview_page(request, session, project)
    return montage.montage_page(request, session, project, stage)  # 6 e 7


@router.post("/projetos/{project_id}/biblia")
async def project_update(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()

    name = text(form, "name") or ""
    prefix = (text(form, "prefix") or project.prefix).upper()
    type_ = text(form, "type") or project.type
    error = _validate_identity(session, project, name, prefix, type_)
    try:
        target_duration_s = integer(form, "target_duration_s", "Duração-alvo", minimum=1)
    except FormError as exc:
        error = error or str(exc)

    if error:
        draft = project.model_dump()
        draft.update({"name": name, "prefix": prefix, "type": type_})
        draft.update({f: text(form, f) for f in BIBLE_SELECTS + BIBLE_TEXTS + ("target_duration_s",)})
        from app.routes.gpt import cover_ctx

        ctx = bible_context(session, project) | cover_ctx(session, project) | {"draft": draft, "error": error}
        return render_stage(request, session, project, 1, "stages/stage_1.html", ctx, status_code=400)

    if duration_key(type_) != duration_key(project.type):
        target_duration_s = None  # a lista de durações muda (segundos ↔ minutos)
    project.name, project.prefix, project.type = name, prefix, type_
    project.target_duration_s = target_duration_s
    for field in BIBLE_SELECTS + BIBLE_TEXTS:
        setattr(project, field, text(form, field))
    if text(form, "image_resolution") in ("1K", "2K", "4K"):
        project.image_resolution = text(form, "image_resolution")
    session.add(project)
    session.commit()
    return RedirectResponse(f"/projetos/{project.id}/etapa/1?guardado=1", status_code=303)


# ── Reciclagem (cenas e planos apagados pelo conector MCP) ──

@router.post("/projetos/{project_id}/arquivar")
def project_archive(request: Request, project_id: int, session: Session = Depends(get_session)):
    """Arquivar (sai da lista e do seletor; nada se apaga) ou voltar a pôr na lista."""
    from app.models import utcnow

    project = get_project(session, project_id)
    project.archived_at = None if project.archived_at else utcnow()
    session.add(project)
    session.commit()
    request.session["flash_info"] = (f"«{project.name}» foi arquivado: está em Projetos › Arquivados, de onde o podes "
                                     "voltar a pôr na lista." if project.archived_at else f"«{project.name}» voltou à lista.")
    if project.archived_at:
        if request.session.get("last_project") == project.id:
            request.session.pop("last_project", None)
        return RedirectResponse("/projetos", status_code=303)
    return RedirectResponse(f"/projetos/{project.id}/etapa/1", status_code=303)


@router.get("/projetos/{project_id}/reciclagem")
def trash_page(request: Request, project_id: int, session: Session = Depends(get_session)):
    from app import trash
    from app.stages import get_project

    project = get_project(session, project_id)
    trash.purge_expired(session)
    session.commit()
    return templates.TemplateResponse(request, "projects/trash.html", {
        "project": project, "items": trash.items(session, project.id),
        "notice": request.session.pop("flash_info", None), "error": request.session.pop("flash_error", None)})


@router.post("/projetos/{project_id}/reciclagem/{item_id}/recuperar")
def trash_restore(request: Request, project_id: int, item_id: int, session: Session = Depends(get_session)):
    from app import trash
    from app.models import TrashItem
    from app.stages import get_project

    project = get_project(session, project_id)
    item = session.get(TrashItem, item_id)
    if item is None or item.project_id != project.id:
        raise HTTPException(status_code=404)
    try:
        label = trash.restore(session, item)
        session.commit()
        request.session["flash_info"] = f"Recuperado: {label}."
    except ValueError as exc:
        session.rollback()
        request.session["flash_error"] = str(exc)
    return RedirectResponse(f"/projetos/{project.id}/reciclagem", status_code=303)
