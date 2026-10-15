"""Filas de geração de imagens, em segundo plano: GPT Image um de cada vez; Artlist vários em paralelo."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlmodel import Session, func, select

from app import artlist, gpt_image
from app.db import engine
from app.media import media_root, mp4_duration_ms, safe_segment, write_file
from app.models import Asset, ImageJob, MediaFile, Project
from app.options import get_config

log = logging.getLogger("estudio.jobs")
from app.image_models import PAID_PROVIDERS, PROVIDER_PARALLEL

PROVIDERS = tuple(PROVIDER_PARALLEL)  # uma fila por fornecedor (a lista vem de app/image_models.py)
_task: list[asyncio.Task] | None = None
_wake = {p: asyncio.Event() for p in PROVIDERS}


def folder_for(session: Session, job: ImageJob) -> str:
    if job.role == "sheet":
        asset = session.get(Asset, job.asset_id)
        return f"assets/{asset.name}"
    if job.role == "portrait":
        asset = session.get(Asset, job.asset_id)
        return f"assets/{asset.name}/retrato"
    if job.role == "clip":
        return f"planos/{job.shot_id}/clip"
    if job.role == "voice":
        asset = session.get(Asset, job.asset_id)
        return f"assets/{asset.name}/voz"
    if job.role == "look":
        asset = session.get(Asset, job.asset_id)
        return f"assets/{asset.name}/looks/look{job.look_id}"
    if job.role == "locview":
        asset = session.get(Asset, job.asset_id)
        return f"assets/{asset.name}/maquete/vista{job.locview_id}"
    return "capa"


def target_of(job_or_media) -> dict:
    return {k: getattr(job_or_media, k) for k in ("asset_id", "scene_id", "shot_id", "locview_id", "look_id") if getattr(job_or_media, k, None)}


def store_result(session: Session, project: Project, job: ImageJob, data: bytes) -> MediaFile:
    base = PurePosixPath(safe_segment(project.prefix), *[safe_segment(p) for p in folder_for(session, job).split("/")])
    stmt = select(func.count()).select_from(MediaFile).where(MediaFile.project_id == project.id, MediaFile.role == job.role)
    for k, v in target_of(job).items():
        stmt = stmt.where(getattr(MediaFile, k) == v)
    n = session.exec(stmt).one() + 1
    rel = base / f"v{n}.png"
    while (media_root() / rel).exists():
        n += 1
        rel = base / f"v{n}.png"
    video = job.role == "clip"
    audio_ext = None
    audio_ms = None
    if job.role == "voice":  # vozes: em MP3 ou WAV (o que os modelos de vídeo aceitam), com a duração medida
        from app.media import normalize_audio

        data, audio_ext, audio_ms = normalize_audio(data)
    ext = "mp4" if video else audio_ext
    if ext:
        rel = rel.with_suffix(f".{ext}")
        while (media_root() / rel).exists():
            n += 1
            rel = base / f"v{n}.{ext}"
    write_file(str(rel), data)
    media = MediaFile(project_id=project.id, path=str(rel), kind="video" if video else "audio" if audio_ext else "image",
                      role=job.role,
                      status="pending", source={"artlist": "artlist", "fal": "fal"}.get(job.provider, "gpt-image"),
                      duration_ms=mp4_duration_ms(data) if video else audio_ms, **target_of(job))
    session.add(media)
    session.flush()
    return media


def _inputs(session: Session, job: ImageJob) -> list[tuple[Path, str, MediaFile]]:
    from app.media_service import download_name

    out = []
    for n, mid in enumerate(job.inputs or []):
        media = session.get(MediaFile, mid)
        path = media_root() / media.path if media else None
        if media and media.status != "rejected" and path.is_file():
            project = session.get(Project, media.project_id)
            label = download_name(session, project, media).rsplit(".", 1)[0]
            out.append((path, label, media))
    return out


def _resolve_refs(session: Session, job: ImageJob, prompt: str, medias: list[MediaFile]) -> str:
    """Marcadores @CÓDIGO → sintaxe do modelo, pela ordem exata das imagens que seguem (ref_tokens)."""
    from app import model_profiles, ref_tokens

    if job.role == "clip" and job.provider == "fal":
        from app.image_models import FAL_VIDEO

        model = {"name": (FAL_VIDEO.get(job.model or "") or {}).get("name"), "refs": True}
    elif job.role == "clip" and job.provider == "bytedance":
        from app.bytedance import MODELS

        model = {"name": (MODELS.get(job.model or "") or {}).get("name"), "refs": True}
    elif job.role == "clip":
        model = artlist.VIDEO_MODELS.get(int(job.model_group_id or artlist.DEFAULT_VIDEO_MODEL)) or {}
    if job.role == "clip":
        if job.kind == "video_refs" and model.get("refs"):
            fmt, frames = model_profiles.ref_format_for(model.get("name")), False
        else:  # modo Frames, ou modelo sem referências múltiplas (a 1.ª imagem segue como Start Frame)
            fmt, frames = None, True
    else:
        fmt, frames = "Image {n}", False  # o Artlist e o GPT Image numeram as imagens anexadas como "Image n"
    bare = job.role == "clip" and not frames  # vídeo com referências: até os códigos sem @ passam à sintaxe do modelo
    text, problems = ref_tokens.resolve(session, prompt, medias, fmt, frames, bare=bare)
    for p in problems:
        log.info("job %s: %s", job.id, p)
    return text


def _name_inputs(message: str, tokens: dict) -> str:
    """"…em image_urls.3…" → "…em @PRJ_ANA (image_urls.3)…": o utilizador sabe que imagem foi recusada."""
    for key, token in sorted(tokens.items(), key=lambda kv: -len(kv[0])):
        if key in message:
            message = message.replace(key, f"{token} ({key})")
    return message


def _stage(job_id: int, text: str | None) -> None:
    """Em que ponto está o pedido (o cartão "A gerar…" mostra-o); falhas ao gravar não param a geração."""
    try:
        with Session(engine) as session:
            job = session.get(ImageJob, job_id)
            if job is not None and job.stage != text:
                job.stage = text
                session.add(job)
                session.commit()
    except Exception:  # noqa: BLE001 — só informativo
        pass


def _provider_ctx(session: Session, job: ImageJob) -> dict:
    """O que um pedido precisa de saber do fornecedor (lido com a sessão aberta): o modelo, a chave da fal e o mapa de
    profundidade das vistas da maquete."""
    from app import api_keys

    acfg = get_config(session, "artlist", {}) or {}
    from app import image_models

    image_models.sync(get_config(session, "fal_models", {}) or {})  # modelos do catálogo da fal ativados
    ctx = {"job_id": job.id, "model": job.model, "fal_key": api_keys.get(get_config(session, "api_keys", {}) or {}, "fal"),
           "artlist": {"cfg": acfg, "confirm": job.confirm_cost, "group": job.model_group_id}, "credits": 0}
    if job.provider == "artlist":
        ctx["model"] = str(job.model_group_id or acfg.get("model_group_id") or artlist.DEFAULT_MODEL)
    if job.role == "locview" and job.locview_id:  # mapa de profundidade da vista (controlo dos modelos de estrutura)
        depth = session.exec(select(MediaFile).where(MediaFile.locview_id == job.locview_id,
                                                     MediaFile.role == "locdepth")).first()
        if depth and (media_root() / depth.path).is_file():
            ctx["depth"] = media_root() / depth.path
    return ctx


async def _image(provider: str, ctx: dict, prompt: str, aspect: str | None, inputs: list, mode: str, timeout: int,
                 model: str | None = None) -> bytes:
    """Um pedido de imagem, em qualquer fornecedor por subscrição ou chave (GPT Image, fal). mode: "generate"
    (texto + referências), "edit" (a 1.ª imagem é a editada), "base" (a 1.ª imagem é a geometria a seguir),
    "structure" (a 1.ª imagem é o controlo de geometria; só modelos de estrutura)."""
    names = {"gpt": "GPT Image", "artlist": "Artlist", "fal": "fal.ai"}

    def say(detail: str) -> None:
        _stage(ctx.get("job_id"), detail)

    say(f"a gerar no {names.get(provider, provider)}")
    from app import image_models

    inputs = inputs[: image_models.max_inputs(provider, model or ctx.get("model"))]  # o limite do modelo escolhido
    if provider == "artlist":
        a = ctx["artlist"]
        acfg = a["cfg"]
        group = int(model or ctx.get("model") or (acfg.get("edit_model_group_id") if mode == "edit" else None)
                    or acfg.get("model_group_id") or artlist.DEFAULT_MODEL)
        text = gpt_image.BASE_NOTE + "\n" + prompt if mode == "base" else prompt
        data, credits = await artlist.generate(
            text, aspect, inputs, mode == "edit", int(acfg.get("timeout_s") or 300), group,
            (ctx.get("params") or {}).get("resolution"), a["confirm"], public_base=str(acfg.get("public_base") or ""),
            agent_model=str(acfg.get("agent_model") or "haiku"))
        ctx["credits"] += credits or 0
        return data
    if provider == "fal":
        from app import fal, image_models

        model = model or ctx.get("model")
        if not ctx.get("fal_key"):
            raise fal.FalError("Falta a chave da fal.ai (Configuração → Chaves de API).")
        if model not in image_models.FAL:
            raise fal.FalError(f"Modelo da fal.ai desconhecido: {model}.")
        urls = [fal.data_uri(path) for path, _label in inputs]
        text = {"base": gpt_image.BASE_NOTE + "\n" + prompt,
                "edit": "Edit Image 1: apply only the change below and keep everything else exactly as it is.\n" + prompt,
                }.get(mode, prompt)
        try:
            endpoint, args = image_models.request(model, "structure" if mode == "structure" else "generate", text,
                                                  aspect, urls, depth=bool(ctx.get("control_is_depth")),
                                                  res=(ctx.get("params") or {}).get("resolution"))
        except ValueError as exc:
            raise fal.FalError(str(exc)) from exc
        return await fal.run(ctx["fal_key"], endpoint, args, timeout, on_status=say)
    return await gpt_image.generate(prompt, aspect, inputs, mode == "edit", timeout, base=mode == "base")


async def _fal_video(ctx: dict, prompt: str, aspect: str | None, images: list, videos: list, kind: str,
                     duration: int | None, audio: bool, quality: str, timeout: int, audios: list | None = None) -> bytes:
    """Clip pela fal: com referências (imagens @Image1…, o vídeo-guia @Video1) ou imagem inicial (e final)."""
    from app import fal, image_models

    key = ctx.get("model") or ""
    if key not in image_models.FAL_VIDEO:
        raise fal.FalError(f"Modelo de vídeo da fal desconhecido: {key}.")
    if not ctx.get("fal_key"):
        raise fal.FalError("Falta a chave da fal.ai (Configuração → Chaves de API).")
    mode = "references" if kind == "video_refs" else "frames"
    if not images:
        raise fal.FalError("Faltam as imagens do plano (painéis ou referências).")
    image_urls = []  # o ficheiro original (não reduzido): as referências e a frame inicial seguem como estão
    for path, _label in images:
        ext = path.suffix.lower().lstrip(".")
        image_urls.append(await fal.upload(ctx["fal_key"], path, {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                                                                   "webp": "image/webp"}.get(ext, "image/png")))
    video_urls = []
    for path, _label in videos if mode == "references" else []:
        video_urls.append(await fal.upload(ctx["fal_key"], path, "video/webm" if path.suffix.lower() == ".webm" else "video/mp4"))
    from app.media import AUDIO_TYPES

    audio_urls = []
    for path, _label in (audios or []) if mode == "references" else []:  # vozes de referência das personagens
        audio_urls.append(await fal.upload(ctx["fal_key"], path, AUDIO_TYPES.get(path.suffix.lower().lstrip("."), "audio/mpeg")))
    endpoint, args = image_models.fal_video_request(key, mode, prompt, aspect, image_urls, video_urls, duration,
                                                    quality, audio)
    if audio_urls:
        args["audio_urls"] = audio_urls[:3]
    _stage(ctx.get("job_id"), "a gerar o vídeo na fal" + (" (com o vídeo-guia)" if video_urls else "")
           + (" (com as vozes)" if audio_urls else ""))
    return await fal.run(ctx["fal_key"], endpoint, args, timeout, output="video",
                         on_status=lambda t: _stage(ctx.get("job_id"), t))


async def _bytedance_video(job_id: int, model_id: str, prompt: str, aspect: str | None, found: list, kind: str,
                           duration: int | None, audio: bool, quality: str, timeout: int, task_id: str | None) -> bytes:
    """Clip pela ByteDance (Seedance direto): as imagens das personagens vão como asset:// (registadas na biblioteca de
    assets: por URL, o Seedance recusa caras realistas), as outras, o vídeo-guia e as vozes por links temporários."""
    from app import bytedance, character_registry
    from app.artlist import share
    from app.config import settings

    if model_id not in bytedance.MODELS:
        raise bytedance.ByteDanceError(f"Modelo de vídeo da ByteDance desconhecido: {model_id}.")
    with Session(engine) as session:
        creds = bytedance.keys(get_config(session, "api_keys", {}) or {})
    base = settings.public_url.rstrip("/")

    def link(path: Path, default_ext: str) -> str:
        ext = path.suffix.lower() if path.suffix.lower() in (".png", ".mp4", ".webm", ".mp3", ".wav", ".m4a", ".ogg") else default_ext
        return f"{base}/pub/ref/{share(path, ttl_s=6 * 3600)}{'.png' if ext in ('.jpg', '.jpeg', '.webp') else ext}"

    body = None
    if task_id is None:  # depois de um reinício, o pedido já existe: só se espera por ele
        images = [(p, m) for p, _l, m in found if m.kind == "image"]
        assets = await character_registry.ensure([m.id for _p, m in images], on_status=lambda t: _stage(job_id, t))
        image_urls = [f"asset://{assets[m.id]}" if m.id in assets else link(p, ".png") for p, m in images]
        videos = [link(p, ".mp4") for p, _l, m in found if m.kind == "video"]
        audios = [link(p, ".mp3") for p, _l, m in found if m.kind == "audio"]
        body = bytedance.video_body(model_id, prompt, aspect, image_urls, videos, audios, kind == "video_frames",
                                    duration, quality, audio)
        log.info("bytedance job %s: %s, %s imagens (%s asset://), %s vídeo(s), %s áudio(s), %s, %s s", job_id, model_id,
                 len(image_urls), len(assets), len(videos), len(audios), body["resolution"], body.get("duration"))
        _stage(job_id, "a enviar à ByteDance" + (" (com o vídeo-guia)" if videos else ""))

    def keep(tid: str) -> None:  # guarda o id do pedido: um reinício retoma a espera em vez de pagar outra vez
        with Session(engine) as session:
            job = session.get(ImageJob, job_id)
            job.params = {**(job.params or {}), "task_id": tid}
            session.add(job)
            session.commit()

    data, task = await bytedance.run_video(creds, body, timeout, task_id=task_id, on_task=keep,
                                           on_status=lambda t: _stage(job_id, t))
    usage = task.get("usage") or {}
    if usage.get("completion_tokens"):
        with Session(engine) as session:
            job = session.get(ImageJob, job_id)
            job.params = {**(job.params or {}), "tokens": usage.get("completion_tokens")}
            session.add(job)
            session.commit()
    return data


async def _locview(provider: str, ctx: dict, prompt: str, aspect: str | None, inputs: list, timeout: int) -> bytes:
    """Vista da maquete, numa só geração, para qualquer modelo pelo que ele sabe fazer. Image 1 = render (a geometria),
    Image 2 = referência da zona ou imagem geral, Image 3+ = referências escolhidas. As fotografias reais não vão para
    o gerador (copiam a composição): o escritor de prompts vê-as e descreve o aspeto delas no texto; vão as folhas dos
    elementos do mundo (fundo neutro: só o design das coisas).
    - segue uma imagem de controlo ("structure"): o mapa de profundidade da maquete (ou o render) e o prompt;
    - aceita imagens ("refs"/"edit"): o render, a referência e as folhas;
    - só texto: só o prompt (o seletor avisa que não segue a maquete).
    """
    from app import image_models

    caps = image_models.caps_of(provider, ctx.get("model"))
    roles = ctx.get("roles") or []
    looks = [x for n, x in enumerate(inputs[2:], 2) if (roles[n] if n < len(roles) else "") != "photo"]
    if "structure" in caps:
        lines = [ln for ln in prompt.split("\n") if ln.strip() and "Image" not in ln]
        control = [(ctx["depth"], "depth map")] if ctx.get("depth") else inputs[:1]
        return await _image(provider, ctx | {"control_is_depth": bool(ctx.get("depth"))}, "\n".join(lines), aspect,
                            control, "structure", timeout)
    if "refs" in caps or "edit" in caps:
        text = prompt if looks else "\n".join(ln for ln in prompt.split("\n") if "Image 3" not in ln)
        return await _image(provider, ctx, text, aspect, inputs[:2] + looks, "base", timeout)
    return await _image(provider, ctx, prompt, aspect, [], "generate", timeout)


async def _run_one(job_id: int) -> None:
    with Session(engine) as session:
        job = session.get(ImageJob, job_id)
        job.status, job.started_at, job.stage = "running", datetime.now(timezone.utc), None
        session.add(job)
        session.commit()
        prompt, aspect, edit = job.prompt, job.aspect_ratio, job.kind == "edit"
        provider, confirm, chosen_model, chosen_name = job.provider, job.confirm_cost, job.model_group_id, job.model
        role, kind, duration, audio = job.role, job.kind, job.duration_s, job.audio
        found = _inputs(session, job)
        missing = [mid for mid in (job.inputs or []) if mid not in {m.id for _p, _l, m in found}]
        inputs = [(path, label) for path, label, _m in found]
        ctx = _provider_ctx(session, job)
        ctx["roles"] = [m.role for _p, _l, m in found]
        ctx["params"] = dict(job.params or {})
        kinds = [m.kind for _p, _l, m in found]  # imagens e vídeos (o vídeo-guia da maquete vai à parte)
        if role != "voice":  # a voz é o texto a falar, tal como está
            prompt = _resolve_refs(session, job, prompt, [m for _p, _l, m in found if m.kind == "image"])
        from app import ref_tokens

        imgs = [m for _p, _l, m in found if m.kind == "image"]
        ctx["tokens"] = {f"image_urls.{n}": ref_tokens.token_for(session, m)[0] for n, m in enumerate(imgs)}
        ctx["tokens"].update({f"video_urls.{n}": "o vídeo-guia" for n, _m in enumerate(
            [m for _p, _l, m in found if m.kind == "video"])})
        ctx["tokens"].update({f"audio_urls.{n}": ref_tokens.token_for(session, m)[0] for n, m in enumerate(
            [m for _p, _l, m in found if m.kind == "audio"])})
        timeout = int((get_config(session, "gpt_image", {}) or {}).get("timeout_s") or 300)
        acfg = get_config(session, "artlist", {}) or {}
        from app.options import video_settings

        vcfg = video_settings(session)
        if (job.params or {}).get("resolution"):  # a resolução escolhida para este take (plano ou padrão do projeto)
            vcfg = {**vcfg, "quality": job.params["resolution"]}
    credits = None
    extra_params: dict = {}
    try:
        if role == "clip" and missing:  # sem isto o take seguia sem o ficheiro (ex.: o vídeo-guia) e pagava-se na mesma
            gone = [session.get(MediaFile, mid) for mid in missing]
            names = ", ".join("o vídeo-guia" if g is not None and g.role == "guide" else (g.path.rsplit("/", 1)[-1] if g else f"#{mid}")
                              for g, mid in zip(gone, missing))
            raise RuntimeError(f"Já não existe {names} (foi rejeitado ou apagado depois do pedido): o take não foi "
                               "gerado. Volta a pedir.")
        if edit and not inputs and role != "clip":
            raise gpt_image.GPTImageError("A imagem a editar já não existe.")
        if role == "clip" and provider == "fal":
            try:
                data = await _fal_video(ctx, prompt, aspect, [x for x, k in zip(inputs, kinds) if k == "image"],
                                        [x for x, k in zip(inputs, kinds) if k == "video"], kind, duration, audio,
                                        vcfg["quality"], vcfg["timeout_s"],
                                        audios=[x for x, k in zip(inputs, kinds) if k == "audio"])
            except Exception as exc:  # a fal diz a entrada em causa (image_urls.3): passa a ser o marcador da imagem
                raise RuntimeError(_name_inputs(str(exc), ctx.get("tokens") or {})) from exc
        elif role == "clip" and provider == "bytedance":
            data = await _bytedance_video(job_id, chosen_name or "", prompt, aspect, found, kind, duration, audio,
                                          vcfg["quality"], vcfg["timeout_s"], (ctx.get("params") or {}).get("task_id"))
        elif role == "clip" and kind == "draft_render":  # o mesmo take de um rascunho 480p, em 1080p (Artlist)
            data, credits, gid = await artlist.render_draft(str((ctx.get("params") or {}).get("draft_of") or ""),
                                                            vcfg["timeout_s"], confirm,
                                                            agent_model=str(acfg.get("agent_model") or "haiku"))
            extra_params["generation_id"] = gid
        elif role == "clip":
            images = [x for x, k in zip(inputs, kinds) if k == "image"]
            videos = [x for x, k in zip(inputs, kinds) if k == "video"]
            audios = [x for x, k in zip(inputs, kinds) if k == "audio"]
            screen = (ctx.get("params") or {}).get("quote")  # o custo que o ecrã mostrava (para comparar)
            try:
                data, credits, gid = await artlist.generate_video(
                prompt, aspect, images if kind == "video_frames" else [], images if kind == "video_refs" else [],
                int(chosen_model or artlist.DEFAULT_VIDEO_MODEL), vcfg["quality"], duration,
                audio, vcfg["timeout_s"], confirm, public_base=str(acfg.get("public_base") or ""),
                agent_model=str(acfg.get("agent_model") or "haiku"), videos=videos, audios=audios)
            except artlist.CostConfirmationRequired as exc:
                import re as _re

                asked = _re.search(r"custa (\d+)", str(exc))
                if screen and asked and int(asked.group(1)) != int(screen):
                    raise artlist.CostConfirmationRequired(
                        f"{exc} Atenção: o ecrã mostrava {screen} créditos e o Artlist pede {asked.group(1)}.") from exc
                raise
            extra_params["generation_id"] = gid  # para passar o rascunho a 1080p (Seedance 2.5)
        elif role == "voice":  # voz de referência de uma personagem
            params = ctx.get("params") or {}
            if provider == "artlist":
                data, credits = await artlist.generate_voice(
                    prompt, int(params.get("voice") or 0), int(acfg.get("timeout_s") or 300), confirm,
                    agent_model=str(acfg.get("agent_model") or "haiku"))
            else:
                from app import fal, voice_models

                if not ctx.get("fal_key"):
                    raise fal.FalError("Falta a chave da fal.ai (Configuração → Chaves de API).")
                args = voice_models.fal_request(ctx.get("model") or "", prompt, params.get("voice"),
                                                params.get("description"))
                data = await fal.run(ctx["fal_key"], ctx.get("model"), args, timeout, output="audio",
                                     on_status=lambda t: _stage(ctx.get("job_id"), t))
        elif role == "locview" and not edit:
            # vistas da maquete: partem do render; o caminho depende do que o modelo sabe fazer
            data = await _locview(provider, ctx, prompt, aspect, inputs, timeout)
        else:
            data = await _image(provider, ctx, prompt, aspect, inputs, "edit" if edit else "generate", timeout)
        if role not in ("clip", "voice"):
            credits = ctx.get("credits") or None  # créditos do Artlist somados em todos os passos
        error = None
    except Exception as exc:  # o erro fica visível no trabalho
        data, error = None, str(exc)[:500]
        log.warning("image job %s failed: %s", job_id, error)
    with Session(engine) as session:
        job = session.get(ImageJob, job_id)
        if job is None:
            return
        project = session.get(Project, job.project_id)
        if data is not None and project is not None:
            try:
                media = store_result(session, project, job, data)
                job.result_media_id, job.status, job.credits = media.id, "done", credits
                if extra_params:
                    job.params = {**(job.params or {}), **{k: v for k, v in extra_params.items() if v}}
            except Exception as exc:  # alvo apagado entretanto, disco, etc.
                job.status, job.error = "failed", str(exc)[:500]
        else:
            job.status, job.error = "failed", error
        job.finished_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()


def _limit(provider: str) -> int:
    """Pedidos ao mesmo tempo: o valor de PROVIDER_PARALLEL; o do Artlist vem da Configuração → Artlist."""
    fixed = PROVIDER_PARALLEL.get(provider)
    if fixed:
        return fixed
    with Session(engine) as session:
        value = (get_config(session, "artlist", {}) or {}).get("parallel") or 3
    return max(1, min(int(value), 6))


async def _loop(provider: str) -> None:
    running: set[asyncio.Task] = set()
    wake = _wake[provider]
    while True:
        try:
            job_id = None
            if len(running) < _limit(provider):
                with Session(engine) as session:
                    job = session.exec(select(ImageJob).where(ImageJob.status == "queued", ImageJob.provider == provider)
                                       .order_by(ImageJob.id)).first()
                    if job is not None:
                        job.status, job.started_at = "running", datetime.now(timezone.utc)
                        session.add(job)
                        session.commit()
                        job_id = job.id
            if job_id is not None:
                task = asyncio.get_event_loop().create_task(_run_one(job_id))
                running.add(task)
                task.add_done_callback(lambda t: (running.discard(t), wake.set()))
                continue
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("image job loop error (%s)", provider)
            await asyncio.sleep(5)


def start() -> None:
    """Arranque: trabalhos interrompidos voltam à fila, menos os do Artlist (gastam créditos: o pedido pode já ter
    chegado lá, e repeti-lo sozinho pagava duas vezes; ficam falhados e o utilizador decide se repete).
    Uma fila por fornecedor."""
    global _task
    with Session(engine) as session:
        for job in session.exec(select(ImageJob).where(ImageJob.status == "running")).all():
            if job.provider == "bytedance" and (job.params or {}).get("task_id"):
                job.status = "queued"  # o pedido já existe na ByteDance: retoma-se a espera, sem pagar outra vez
            elif job.provider in PAID_PROVIDERS:  # pagos por pedido: não se repetem sozinhos
                job.status, job.finished_at = "failed", datetime.now(timezone.utc)
                job.error = (f"Interrompido por um reinício da app. Pode já ter sido gerado (e pago) no "
                             f"{ {'artlist': 'Artlist', 'bytedance': 'ByteDance'}.get(job.provider, 'fal.ai')}: confirma lá antes de carregar em Repetir.")
            else:
                job.status = "queued"
            session.add(job)
        session.commit()
    loop = asyncio.get_event_loop()
    _task = [loop.create_task(_loop(p)) for p in PROVIDERS]


def wake() -> None:
    for event in _wake.values():
        event.set()


def enqueue(session: Session, project: Project, role: str, prompt: str, aspect: str | None,
            inputs: list[int] | None = None, kind: str = "generate", provider: str = "gpt",
            model_group_id: int | str | None = None, duration_s: int | None = None, audio: bool = True,
            params: dict | None = None, **target) -> ImageJob:
    if provider not in PROVIDERS:  # sem fila, o pedido ficava parado para sempre
        raise ValueError(f"O fornecedor «{provider}» ainda não está ligado à geração de imagens.")
    job = ImageJob(project_id=project.id, role=role, prompt=prompt, aspect_ratio=aspect, inputs=inputs or [],
                   kind=kind, provider=provider, model_group_id=model_group_id if provider == "artlist" else None,
                   model=str(model_group_id) if provider in ("fal", "bytedance") and model_group_id else None,
                   duration_s=duration_s, audio=audio, params=params, **target)
    session.add(job)
    session.flush()
    return job


def jobs_for(session: Session, project: Project, role: str, **target) -> list[ImageJob]:
    """Trabalhos ativos e as falhas recentes deste alvo (para mostrar nos resultados)."""
    stmt = select(ImageJob).where(ImageJob.project_id == project.id, ImageJob.role == role,
                                  ImageJob.status.in_(("queued", "running", "failed")))
    for k, v in target.items():
        stmt = stmt.where(getattr(ImageJob, k) == v)
    rows = list(session.exec(stmt.order_by(ImageJob.id.desc()).limit(6)).all())
    active = [j for j in rows if j.status != "failed"]
    failed = [j for j in rows if j.status == "failed"][:2]
    return sorted(active + failed, key=lambda j: j.id)


def queue_position(session: Session, job: ImageJob) -> int:
    return session.exec(select(func.count()).select_from(ImageJob)
                        .where(ImageJob.status == "queued", ImageJob.provider == job.provider, ImageJob.id < job.id)).one()
