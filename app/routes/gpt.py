"""GPT Image (Codex CLI): gerar e editar imagens, estado dos trabalhos e capa do projeto."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app import artlist, gpt_image, jobs
from app.db import get_session
from app.forms import text
from app.media_service import media_list
from app.models import IMAGE_JOB_ROLES, ImageJob, MediaFile, Project
from app.options import get_config
from app.routes.files import flash, flash_info, save_uploads
from app.routes.production import approved_media
from app.security import csrf_protect, require_login
from app.stages import get_project
from app.templating import is_htmx, templates

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])



def enabled(session: Session) -> bool:
    return bool((get_config(session, "gpt_image", {}) or {}).get("enabled", True)) and gpt_image.configured()


def artlist_enabled(session: Session) -> bool:
    return bool((get_config(session, "artlist", {}) or {}).get("enabled", True)) and artlist.connected()


def fal_enabled(session: Session) -> bool:
    from app import api_keys

    return bool(api_keys.get(get_config(session, "api_keys", {}) or {}, "fal"))


def bytedance_enabled(session: Session) -> bool:
    """Vídeo pela ByteDance (BytePlus ModelArk): basta a chave de API; o registo das personagens usa a Access Key."""
    from app import api_keys

    return bool(api_keys.get(get_config(session, "api_keys", {}) or {}, "byteplus"))


def providers(session: Session) -> dict:
    """Fornecedores de imagem disponíveis agora (os botões de gerar/editar só aparecem para estes)."""
    return {"gpt": enabled(session), "artlist": artlist_enabled(session), "fal": fal_enabled(session)}


def any_enabled(session: Session) -> bool:
    return any(providers(session).values())


PROVIDER_NAMES = {"gpt": "GPT Image", "artlist": "Artlist", "fal": "fal.ai", "bytedance": "ByteDance"}


def provider_ok(request: Request, session: Session, provider: str) -> bool:
    if provider == "artlist" and not artlist_enabled(session):
        flash(request, "O Artlist não está disponível (liga a app ao Artlist em Configuração → Ligações).")
        return False
    if provider == "fal":
        if not fal_enabled(session):
            flash(request, "A fal.ai não está disponível (guarda a chave em Configuração → Chaves de API).")
            return False
        return True
    if provider not in ("artlist", "fal") and not enabled(session):
        flash(request, "O GPT Image não está disponível (liga o ChatGPT em Configuração → Ligações).")
        return False
    return True


def op_provider(verb: str) -> str | None:
    """"gpt_generate" → "gpt"; "artlist_generate" → "artlist"; outra coisa → None."""
    return {"gpt_generate": "gpt", "artlist_generate": "artlist"}.get(verb)


# ── Escolha do modelo no botão Gerar ─────────────────────────

def generator_options(session: Session, use: str = "generate") -> list[tuple[str, str]]:
    """Modelos disponíveis agora para um uso ("generate", "edit", "maquete"): [(valor, rótulo)]. Os que não servem
    para esse uso não aparecem; os que servem com limitações levam o aviso no rótulo."""
    from app import image_models

    pv = providers(session)
    raw = [("gpt", "GPT Image · subscrição", "gpt", None)] if pv["gpt"] else []
    if pv["artlist"]:
        raw += [(f"artlist:{gid}", f"{m['name']} · " + (f"{m['cost']} cr." if m.get("cost") else "só com referências"),
                 "artlist", gid) for gid, m in artlist.MODELS.items()]
    if pv["fal"]:
        image_models.sync(get_config(session, "fal_models", {}) or {})  # os modelos do catálogo ativados
        raw += [(f"fal:{mid}", image_models.label(m), "fal", mid) for mid, m in image_models.FAL.items()]
    out = []
    for value, label, provider, model in raw:
        ok, note = image_models.fit(image_models.caps_of(provider, model), use)
        if ok:
            out.append((value, label + (f" · ⚠ {note}" if note else "")))
    return out


def default_generator(session: Session, edit: bool = False, use: str | None = None, project: Project | None = None) -> str | None:
    """Modelo pré-escolhido nos botões: o padrão do projeto (Bíblia › Formato e modelos), senão o da Configuração
    (Modelos e geração → Imagens); edit = para editar. Se nenhum serve para este uso, o primeiro que sirva."""
    return generator_default(session, use or ("edit" if edit else "generate"), project)[0]


def generator_default(session: Session, use: str, project: Project | None = None) -> tuple[str | None, str]:
    """(modelo pré-escolhido, de onde vem: "project", "config" ou "auto")."""
    options = generator_options(session, use)
    values = [v for v, _l in options]
    cfg = get_config(session, "image_generator", {}) or {}
    if project is not None:
        v = project.image_edit_model if use == "edit" else project.image_model
        if v in values:
            return v, "project"
    if use == "edit" and cfg.get("edit") in values:
        return cfg["edit"], "config"
    wanted = cfg.get("default")
    if wanted in values:
        return wanted, "config"
    acfg = get_config(session, "artlist", {}) or {}
    fallback = f"artlist:{acfg.get('model_group_id') or artlist.DEFAULT_MODEL}"
    clean = [v for v, l in options if "⚠" not in l]
    for v in ("gpt", fallback):
        if v in values:
            return v, "auto"
    return (clean or values or [None])[0], "auto"


def fits(session: Session, provider: str, model, use: str) -> bool:
    from app import image_models

    return image_models.fit(image_models.caps_of(provider, model), use)[0]


def generator_label(session: Session, value: str | None = None) -> str:
    """Nome legível do modelo de imagem (por omissão, o da Configuração): etiqueta dos blocos de prompt."""
    value = value or default_generator(session)
    if not value:
        return ""
    provider, group = parse_generator(value)
    if provider == "artlist":
        return f"{artlist.model(group)['name']} · Artlist"
    if provider == "fal":
        from app import image_models

        return image_models.FAL[group]["name"] + " · fal.ai"
    return "GPT Image"


def parse_generator(value: str | None) -> tuple[str, int | str | None]:
    """"gpt" → ("gpt", None); "artlist:513" → ("artlist", 513); "fal:<id>" → ("fal", "<id>")."""
    if value and value.startswith("fal:"):
        from app import image_models

        if value[4:] not in image_models.FAL:
            image_models.sync_from_db()
        if value[4:] in image_models.FAL:
            return "fal", value[4:]
    if value and value.startswith("artlist:") and value[8:].isdigit() and int(value[8:]) in artlist.MODELS:
        return "artlist", int(value[8:])
    return ("artlist", None) if value == "artlist" else ("gpt", None)


def op_generator(session: Session, verb: str, form, field: str) -> tuple[str, int | None] | None:
    """Botão Gerar: (fornecedor, grupo Artlist) a partir do seletor "<campo>_gen"; None se não é um pedido de gerar."""
    if verb == "generate":
        return parse_generator(form.get(f"{field}_gen") or default_generator(session))  # sem projeto: só formulários antigos
    legacy = op_provider(verb)
    return (legacy, None) if legacy else None


FORMAT_MISSING = ("O projeto ainda não tem formato (proporção da imagem): escolhe-o na Bíblia › 05 Formato. Sem ele, "
                  "as imagens e os vídeos sairiam com a proporção errada.")


def project_aspect(project: Project) -> str | None:
    """A proporção do projeto (ex.: "16:9"), ou None se ainda não foi escolhida — nunca se adivinha."""
    return (project.aspect_ratio or "").split(" ")[0] or None


def aspect_for(session: Session, project: Project, role: str) -> str | None:
    """A proporção a pedir: folhas, retratos e capa têm a sua; o resto é a do projeto (None se não está escolhida)."""
    guides = get_config(session, "prompt_guides", {}) or {}
    if role in ("sheet", "look"):
        return guides.get("sheet_aspect") or "16:9"
    if role == "portrait":
        return guides.get("portrait_aspect") or "1:1"
    if role == "cover":
        return "16:9"
    return project_aspect(project)


def provider_problem(session: Session, provider: str) -> str | None:
    if provider == "artlist" and not artlist_enabled(session):
        return "O Artlist não está disponível (liga a app ao Artlist em Configuração → Ligações)."
    if provider == "fal" and not fal_enabled(session):
        return "A fal.ai não está disponível (guarda a chave em Configuração → Chaves de API)."
    if provider == "gpt" and not enabled(session):
        return "O GPT Image não está disponível (liga o ChatGPT em Configuração → Ligações)."
    return None


def queue_image(session: Session, project: Project, role: str, prompt: str, inputs: list[int], provider: str,
                model, use: str = "generate", resolution: str | None = None, params: dict | None = None,
                **target) -> tuple[bool, str]:
    """Pede uma imagem (sem pedido HTTP: serve também ao escritor de prompts, que gera logo a seguir a escrever).
    Devolve (pedido feito, mensagem para o utilizador)."""
    from app import image_models

    problem = provider_problem(session, provider)
    if problem:
        return False, problem
    caps = image_models.caps_of(provider, model)
    if not image_models.fit(caps, use)[0]:
        return False, "Esse modelo não serve para gerar esta imagem (escolhe outro no seletor do botão)."
    if "t2i" not in caps and not inputs:
        return False, "Esse modelo só gera a partir de imagens de referência, e aqui não segue nenhuma: escolhe outro."
    if not (prompt or "").strip():
        return False, "Não há prompt para gerar."
    if aspect_for(session, project, role) is None:
        return False, FORMAT_MISSING
    if any(j.status in ("queued", "running") for j in jobs.jobs_for(session, project, role, **target)):
        return False, "Já há uma imagem a ser gerada para aqui. Espera que termine antes de pedir outra."
    allowed = image_models.resolutions(provider, model)
    if resolution and allowed and resolution not in allowed:
        return False, f"Esse modelo não gera a {resolution}: aceita {', '.join(allowed)}."
    res = image_models.fit_resolution(resolution or project.image_resolution, allowed)
    params = {**(params or {}), **({"resolution": res} if res else {})}
    jobs.enqueue(session, project, role, prompt, aspect_for(session, project, role), inputs, provider=provider,
                 model_group_id=model, params=params or None, **target)
    session.flush()
    name = artlist.model(model)["name"] + " (Artlist)" if provider == "artlist" and model else PROVIDER_NAMES[provider]
    if provider == "fal" and model:
        name = image_models.FAL[model]["name"] + " (fal.ai)"
    if res:
        name += f" a {res}"
    return True, (f"Pedido enviado ao {name}. Demora cerca de 1 minuto; a imagem aparece em Resultados e a página "
                  "atualiza-se sozinha.")


def job_view(session: Session, job: ImageJob) -> dict:
    if job.provider == "fal" and job.role == "voice":
        from app import voice_models

        label = (voice_models.FAL_VOICE.get(job.model or "") or {}).get("name", "voz") + " · fal.ai"
    elif job.provider == "fal" and job.role == "clip":
        from app import image_models

        label = image_models.fal_video(job.model).get("name", "fal.ai")
    elif job.provider == "bytedance":
        from app import bytedance

        label = (bytedance.MODELS.get(job.model or "") or {}).get("name", "ByteDance")
    elif job.provider == "fal":
        from app import image_models

        image_models.sync(get_config(session, "fal_models", {}) or {})
        label = (image_models.FAL.get(job.model or "") or {}).get("name", "fal.ai") + " · fal.ai"
    elif job.provider == "artlist":
        label = (artlist.model(job.model_group_id)["name"] if job.model_group_id and job.role not in ("clip", "voice")
                 else "Artlist · voz" if job.role == "voice" else "Artlist")
    else:
        label = "GPT Image"
    return {"job": job, "position": jobs.queue_position(session, job) if job.status == "queued" else 0,
            "provider_label": label}


def jobs_ctx(session: Session, project: Project, role: str, **target) -> list[dict]:
    return [job_view(session, j) for j in jobs.jobs_for(session, project, role, **target)]


# ── Estado dos trabalhos ─────────────────────────────────────

def _job(session: Session, project: Project, job_id: int) -> ImageJob:
    job = session.get(ImageJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status_code=404, detail="Trabalho não encontrado.")
    return job


@router.get("/imagens/trabalhos/{job_id}")
def job_tile(request: Request, project_id: int, job_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    job = _job(session, project, job_id)
    if job.status == "done" and is_htmx(request):
        return Response(status_code=204, headers={"HX-Trigger": "estudio-refresh"})  # a página atualiza-se (morph)
    return templates.TemplateResponse(request, "gpt/_job.html", {"project": project, "item": job_view(session, job)})


@router.post("/imagens/trabalhos/{job_id}/cancelar")
async def job_cancel(request: Request, project_id: int, job_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    job = _job(session, project, job_id)
    if job.status in ("queued", "failed"):
        session.delete(job)
        session.commit()
    form = await request.form()
    return RedirectResponse(_back(project, text(form, "back"), request), status_code=303)


@router.post("/imagens/trabalhos/{job_id}/repetir")
async def job_retry(request: Request, project_id: int, job_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    job = _job(session, project, job_id)
    if job.status == "failed":
        if job.provider == "artlist" and (job.error or "").startswith("O Artlist pede confirmação"):
            job.confirm_cost = True  # Repetir depois do aviso de custo = confirmar o custo
        job.status, job.error, job.started_at, job.finished_at, job.stage = "queued", None, None, None, None
        session.add(job)
        session.commit()
        jobs.wake()
    form = await request.form()
    return RedirectResponse(_back(project, text(form, "back"), request), status_code=303)


def _back(project: Project, back: str | None, request: Request | None = None) -> str:
    if (not back or "/imagens/trabalhos/" in back) and request is not None:
        from urllib.parse import urlsplit

        ref = urlsplit(request.headers.get("referer") or "")
        back = ref.path + (f"?{ref.query}" if ref.query else "")
    if back and back.startswith(f"/projetos/{project.id}/") and "//" not in back and "/imagens/trabalhos/" not in back:
        return back
    return f"/projetos/{project.id}"


# ── Editar um resultado ──────────────────────────────────────

@router.post("/imagens/editar/{media_id}")
async def edit_image(request: Request, project_id: int, media_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    media = session.get(MediaFile, media_id)
    if media is None or media.project_id != project.id or media.status == "rejected" or media.kind != "image":
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")
    form = await request.form()
    change = text(form, "change")
    back = _back(project, text(form, "back"))
    role = text(form, "role") or media.role
    provider, group = parse_generator(text(form, "gen_model") or text(form, "provider")
                                      or default_generator(session, edit=True, project=project))
    if role not in IMAGE_JOB_ROLES or role != media.role:
        raise HTTPException(status_code=400, detail="Papel inválido.")
    if not change:
        flash(request, "Descreve a alteração a fazer.")
        return RedirectResponse(back, status_code=303)
    if not provider_ok(request, session, provider):
        return RedirectResponse(back, status_code=303)
    if not fits(session, provider, group, "edit"):
        flash(request, "Esse modelo não edita imagens (escolhe outro).")
        return RedirectResponse(back, status_code=303)
    if aspect_for(session, project, role) is None:
        flash(request, FORMAT_MISSING)
        return RedirectResponse(back, status_code=303)
    target = {k: getattr(media, k) for k in ("asset_id", "shot_id", "locview_id") if getattr(media, k)}
    jobs.enqueue(session, project, role, change, aspect_for(session, project, role), [media.id], kind="edit",
                 provider=provider, model_group_id=group, **target)
    session.commit()
    jobs.wake()
    flash_info(request, f"Edição enviada ao {PROVIDER_NAMES[provider]}. Demora cerca de 1 minuto; a nova versão aparece em Resultados.")
    return RedirectResponse(back, status_code=303)


# ── Capa do projeto ──────────────────────────────────────────

def cover_ctx(session: Session, project: Project) -> dict:
    from app import prompt_targets as PT
    from app.routes import prompt_ops

    cover = approved_media(session, [project.cover_media_id]).get(project.cover_media_id)
    return {
        "cover": cover, "cover_panel": prompt_ops.panel(session, PT.get(session, project, "capa", str(project.id)),
                                                        "Prompt da capa"),
        "cover_results": media_list(session, project_id=project.id, role="cover", status="pending"),
        "cover_jobs": jobs_ctx(session, project, "cover"),
        "gpt_enabled": any_enabled(session),
    }


@router.post("/capa")
async def cover_save(request: Request, project_id: int, session: Session = Depends(get_session)):
    from app.routes import prompt_ops

    project = get_project(session, project_id)
    form = await request.form()
    op = text(form, "op") or ""
    prompt_ops.save(session, project, form, op)
    session.commit()
    prompt_ops.run(request, session, project, form, op)
    session.commit()
    return RedirectResponse(f"/projetos/{project.id}/etapa/1" + ("" if op else "?guardado=1") + "#capa", status_code=303)


@router.post("/capa/resultados")
async def cover_upload(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    await save_uploads(request, session, project, "capa", "image", "cover")
    return RedirectResponse(f"/projetos/{project.id}/etapa/1#capa", status_code=303)

