"""Etapa 6 — Geração de vídeo, por plano: o modelo, o som, as imagens do pedido (as vistas do local nos momentos da
câmara e as folhas do elenco), o vídeo-guia da cena 3D, o prompt (escrito pelo escritor de prompts), takes e fotogramas."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app import bytedance, guide_check, image_models, moments, prompt_targets as PT, scene3d, video_cost, video_inputs
from app.db import get_session
from app.forms import FormError, integer, text
from app.media import MAX_VIDEO_BYTES, media_root
from app.media_service import media_list, scene_code
from app.models import Asset, ImageJob, MediaFile, Project, Scene, SceneLine, Shot
from app.options import get_config, video_settings
from app.routes import prompt_ops
from app.routes.files import flash, flash_info, save_uploads
from app.routes.gpt import artlist_enabled, bytedance_enabled, fal_enabled
from app.routes.production import approved_media, get_shot, scene_assets, scene_shots
from app.security import csrf_protect, require_login
from app.stages import get_project, pending_roles_by_shot, render_stage, shot_clip_state
from app.memo import memo

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])

STAGE = 6
MAX_VIDEO_REFS = 9  # limite das imagens quando o modelo não diz o seu


def limits(model: dict | None) -> dict:
    """O que o modelo aceita num pedido: imagens, vídeos (o vídeo-guia), vozes, segundos de voz no total e ficheiros no
    total (dos esquemas dos fabricantes; o que o modelo não diz fica pelo mais seguro)."""
    m = model or {}
    return {"images": int(m.get("max_images") or MAX_VIDEO_REFS),
            "videos": int(m.get("max_videos") if m.get("max_videos") is not None else (3 if m.get("video_refs") else 0)),
            "audios": int(m.get("max_audios") if m.get("max_audios") is not None else (3 if m.get("audio_refs") else 0)),
            "audio_s": float(m.get("max_audio_s") or 15), "files": int(m.get("max_files") or 99)}


# ── Modelos ───────────────────────────────────────────────────

@memo
def video_models(session: Session) -> list[dict]:
    """Modelos de vídeo: o catálogo do Artlist e, com a chave da fal, os modelos de vídeo da fal. Cada um diz o que
    aceita (refs = várias imagens de referência; video_refs = vídeo de referência; end_frame = imagem final)."""
    from app import artlist

    vs = video_settings(session)
    fal = [{"name": m["name"], "kind": "video", "group": None, "provider": "fal", "key": k, "cost": None,
            "cost_text": f"≈ ${image_models.fal_video_cost(k, m['res'][0], 10)} ({m['res'][0]}, 10 s)",
            "resolution": " / ".join(m["res"]), "durations_s": m["durations"], "refs": True, "video_refs": True,
            "audio_refs": True, "end_frame": True, "audio": True, "audio_always": False,
            **{k: m[k] for k in ("max_images", "max_videos", "max_audios", "max_audio_s", "max_files") if k in m}}
           for k, m in image_models.FAL_VIDEO.items()] if fal_enabled(session) else []
    art = [{"name": m["name"], "kind": "video", "group": gid, "provider": "artlist", "cost": m.get("cost"),
            "video_refs": bool(m.get("video_refs")), "audio_refs": bool(m.get("audio_refs")), "resolution": " / ".join(lab for lab, _v in m["res"]),
            "durations_s": sorted({int(d) for d in m["durations"]}), "refs": bool(m.get("refs")),
            "end_frame": m.get("end", True), "audio": m.get("audio", False), "audio_always": m.get("audio_always", False),
            **{k: m[k] for k in ("max_images", "max_videos", "max_audios", "max_audio_s", "max_files") if k in m}}
           for gid, m in artlist.VIDEO_MODELS.items()] if artlist_enabled(session) else []
    bd = [{"name": m["name"], "kind": "video", "group": None, "provider": "bytedance", "key": k, "cost": None,
           "cost_text": bytedance.cost_text(k, vs["quality"] if vs["quality"] in m["res"] else m["res"][-1], 10) or "",
           "resolution": " / ".join(m["res"]), "durations_s": m["durations"], "refs": True, "video_refs": True,
           "audio_refs": True, "end_frame": True, "audio": True, "audio_always": False,
           **{x: m[x] for x in ("max_images", "max_videos", "max_audios", "max_audio_s", "max_files")}}
          for k, m in bytedance.MODELS.items()] if bytedance_enabled(session) else []
    models = bd + fal + art
    default = (get_config(session, "video_generator", {}) or {}).get("default")
    names = {m["name"] for m in models}
    if default not in names:
        default = next((m["name"] for m in models if m.get("refs")), models[0]["name"] if models else None)
    for m in models:
        m["default"] = m["name"] == default
        m["quality"] = vs["quality"]
    return models


def _model(session: Session, name: str | None) -> dict | None:
    models = video_models(session)
    return next((m for m in models if m["name"] == name), None) or next((m for m in models if m.get("default")), None)


RESOLUTIONS = ("480p", "720p", "1080p")


def resolutions_of(model: dict | None) -> list[str]:
    """As resoluções que o modelo aceita (pela ordem)."""
    have = str((model or {}).get("resolution") or "")
    return [r for r in RESOLUTIONS if r in have] or ["480p"]


def project_model(session: Session, project: Project) -> dict | None:
    """O modelo de vídeo padrão do projeto (se já não estiver disponível, o da Configuração)."""
    return _model(session, project.video_model)


def project_resolution(session: Session, project: Project) -> str:
    return project.video_resolution if project.video_resolution in RESOLUTIONS else video_settings(session)["quality"]


def _project_of(session: Session, shot: Shot) -> Project:
    scene = session.get(Scene, shot.scene_id)
    return session.get(Project, scene.project_id)


def shot_model(session: Session, shot: Shot) -> dict | None:
    """O modelo com que o plano gera: o escolhido no plano ou, sem escolha, o padrão do projeto."""
    if shot.model:
        chosen = next((m for m in video_models(session) if m["name"] == shot.model), None)
        if chosen:
            return chosen
    return project_model(session, _project_of(session, shot))


def shot_resolution(session: Session, shot: Shot, model: dict | None = None) -> str:
    """A resolução do plano (a escolhida nele ou a padrão do projeto), dentro das que o modelo aceita."""
    model = model or shot_model(session, shot)
    want = shot.video_resolution if shot.video_resolution in RESOLUTIONS else project_resolution(session, _project_of(session, shot))
    ok = resolutions_of(model)
    return want if want in ok else min(ok, key=lambda r: abs(RESOLUTIONS.index(r) - RESOLUTIONS.index(want)))


def fit_duration(session: Session, shot: Shot, model: dict) -> None:
    """A duração do plano passa a uma das que o modelo aceita (e a cena 3D acompanha)."""
    allowed = model.get("durations_s") or []
    if shot.duration_s and allowed and shot.duration_s not in allowed:
        new = min(allowed, key=lambda d: (abs(d - shot.duration_s), d))
        if shot.scene3d:
            from app.routes.storyboard import set_shot_duration

            set_shot_duration(session, shot, new)
        shot.duration_s = new


def choose_model(session: Session, shot: Shot, name: str | None) -> dict:
    """O plano passa a usar este modelo (None/"default" = o padrão do projeto). Devolve o modelo em uso."""
    if name in (None, "", "default"):
        shot.model = None
    else:
        m = next((x for x in video_models(session) if x["name"] == name), None)
        if m is None:
            raise ValueError(f"Modelo desconhecido: {name}.")
        shot.model = m["name"]
    model = shot_model(session, shot)
    if model:
        fit_duration(session, shot, model)
    session.add(shot)
    return model


def use_model(session: Session, shot: Shot, model: dict) -> None:
    """O plano passa a usar este modelo; a duração ajusta-se às que ele aceita (e a cena 3D acompanha)."""
    choose_model(session, shot, model["name"])


def apply_project_default(session: Session, project: Project) -> int:
    """Depois de mudar o modelo padrão do projeto: os planos que o seguem passam a ter uma duração que ele aceita."""
    model = project_model(session, project)
    n = 0
    for shot in session.exec(select(Shot).join(Scene, Scene.id == Shot.scene_id).where(
            Scene.project_id == project.id, Shot.model.is_(None))).all():
        before = shot.duration_s
        if model:
            fit_duration(session, shot, model)
        if shot.duration_s != before:
            session.add(shot)
            n += 1
    return n


def shot_mode(session: Session, shot: Shot, model: dict | None) -> str:
    """O tipo de pedido do plano: "frames" se uma imagem está como frame inicial (ou o modelo só aceita frames)."""
    from app import video_inputs as VI

    return "frames" if VI.frames(session, shot, model) else video_mode(model)


def video_mode(model: dict | None) -> str:
    """Com referências quando o modelo as aceita; senão só imagem inicial (e final)."""
    return "frames" if model and not model.get("refs") else "references"


def _people(session: Session, shot: Shot) -> int:
    """Quantas personagens a câmara do plano vê (para o «exactly N people» do prompt)."""
    from app.scene3d import in_frame

    codes = in_frame(session, shot)
    if not codes:
        return 0
    scene = session.get(Scene, shot.scene_id)
    return len(session.exec(select(Asset).where(Asset.project_id == scene.project_id, Asset.kind == "character",
                                                Asset.name.in_(codes))).all())


def _talk_rows(session: Session, shot: Shot) -> dict:
    """As falas do plano para a página Vídeo (só leitura): quem fala, o texto e se a voz de referência está aprovada."""
    from app.models import ShotLineLink
    from app.prompt_context import shot_talk

    linked = session.exec(select(ShotLineLink).where(ShotLineLink.shot_id == shot.id)).first() is not None
    rows = []
    for c, txt in shot_talk(session, shot):
        a = session.get(Asset, c) if c else None
        rows.append({"who": a, "text": txt, "voice": bool(a and a.voice_ref_id)})
    return {"rows": rows, "linked": linked}


def _dialogue_lines(session: Session, scene: Scene) -> list[tuple[int, SceneLine, Asset | None]]:
    lines = session.exec(
        select(SceneLine).where(SceneLine.scene_id == scene.id, SceneLine.kind == "dialogue", SceneLine.text != "")
        .order_by(SceneLine.order)
    ).all()
    return [(i, ln, session.get(Asset, ln.character_id) if ln.character_id else None) for i, ln in enumerate(lines)]


def left_out(session: Session, shot: Shot, model: dict | None) -> list[str]:
    """Quem a câmara vê mas cuja folha não segue no pedido (lista de imagens fixada à mão, ou cortada pelo limite do
    modelo): o prompt fala deles e o modelo não sabe como são."""
    if not shot.scene3d or shot_mode(session, shot, model) != "references":
        return []
    from app.scene3d import in_frame

    scene = session.get(Scene, shot.scene_id)
    sent = {m.id for m in request_plan(session, shot, model)["images"]}
    out = []
    for code in in_frame(session, shot):
        a = session.exec(select(Asset).where(Asset.project_id == scene.project_id, Asset.name == code)).first()
        if a is not None and a.approved_image_id and a.approved_image_id not in sent:
            out.append(a.label or a.name)
    return out


def take_labels(session: Session, clips: list[MediaFile]) -> dict[int, str]:
    """"Take 5 · Seedance 2.0" por clip: o número vem da versão do ficheiro (v5), o modelo do pedido que o gerou."""
    from app import artlist

    ids = [c.id for c in clips]
    jobs = {j.result_media_id: j for j in session.exec(select(ImageJob).where(ImageJob.result_media_id.in_(ids))).all()} if ids else {}
    out = {}
    for c in clips:
        m = re.search(r"v(\d+)\.\w+$", c.path)
        job = jobs.get(c.id)
        model = None
        if job and job.provider == "fal":
            model = (image_models.FAL_VIDEO.get(job.model or "") or {}).get("name") or job.model
        elif job and job.provider == "bytedance":
            from app import bytedance

            model = (bytedance.MODELS.get(job.model or "") or {}).get("name") or job.model
        elif job and job.model_group_id:
            model = (artlist.VIDEO_MODELS.get(job.model_group_id) or {}).get("name")
        asked = job.duration_s if job else None
        plan = session.get(Shot, c.shot_id).duration_s if c.shot_id else None
        got = c.duration_ms / 1000 if c.duration_ms else None
        # mais comprido do que o plano é folga para cortar na montagem; só um take mais curto do que o plano é problema
        if got and plan and got < plan - 0.3:
            wrong = f"⚠ {got:.0f} s, mais curto do que o plano ({plan:g} s)"
        elif got and asked and abs(got - asked) > 1:
            wrong = f"{got:.0f} s em vez de {asked:g} s (o que sobra corta-se na montagem)"
        elif got and plan and got > plan + 0.3:
            wrong = f"{got:.0f} s, com {got - plan:.0f} s de folga para cortar"
        else:
            wrong = None
        params = (job.params or {}) if job else {}
        res = params.get("resolution")
        cost = f"{job.credits} cr." if job and job.credits else (f"{params['tokens'] // 1000}k tokens" if params.get("tokens") else None)
        out[c.id] = " · ".join(x for x in [f"Take {m.group(1)}" if m else "Take", model or ("carregado" if not job else None),
                                           res, cost, "1080p do rascunho" if job and job.kind == "draft_render" else None,
                                           wrong] if x)
    return out


DRAFT_GROUP, DRAFT_DAYS = 515, 7  # Seedance 2.5 do Artlist: um take a 480p é um rascunho que passa a 1080p


def take_info(session: Session, clips: list[MediaFile]) -> dict[int, dict]:
    """Por take: o prompt com que foi gerado, o modelo, a resolução, o custo e se pode passar a 1080p (rascunho)."""
    from datetime import datetime, timedelta, timezone

    ids = [c.id for c in clips]
    if not ids:
        return {}
    jobs = {j.result_media_id: j for j in session.exec(select(ImageJob).where(ImageJob.result_media_id.in_(ids))).all()}
    rendered = {str((j.params or {}).get("draft_of")) for j in session.exec(select(ImageJob).where(
        ImageJob.kind == "draft_render", ImageJob.status.in_(("queued", "running", "done")))).all()}
    out = {}
    for c in clips:
        j = jobs.get(c.id)
        if j is None:
            continue
        params = j.params or {}
        gid = params.get("generation_id")
        made = j.finished_at or j.created_at
        made = made.replace(tzinfo=timezone.utc) if made and made.tzinfo is None else made
        draft = (j.provider == "artlist" and j.model_group_id == DRAFT_GROUP and j.kind != "draft_render"
                 and params.get("resolution") == "480p" and gid and gid not in rendered
                 and made and datetime.now(timezone.utc) - made < timedelta(days=DRAFT_DAYS))
        out[c.id] = {"prompt": j.prompt, "resolution": params.get("resolution"), "credits": j.credits,
                     "sent": params.get("sent"), "guide": params.get("guide"), "quote": params.get("quote"),
                     "model": params.get("model_name"), "draft": bool(draft),
                     "draft_until": (made + timedelta(days=DRAFT_DAYS)) if draft else None}
    return out


# ── Imagens do pedido ─────────────────────────────────────────

MAX_VIEWS_WITH_GUIDE = 2


@memo
def auto_refs(session: Session, shot: Shot, cap: int = MAX_VIDEO_REFS) -> list[MediaFile]:
    """A escolha automática: o frame-chave (se aprovado), as vistas do local nos momentos (sem repetir) e as folhas do
    elenco. Se passam do limite, o frame-chave e o elenco ficam e cortam-se as vistas do meio. O frame-chave já mostra o
    sítio do primeiro momento: a vista desse momento deixa de seguir."""
    from app import keyframe

    views, cast = moments.view_images(session, shot), moments.cast_images(session, shot)
    key = keyframe.approved(session, shot)
    lead = [key] if key is not None else []
    if key is not None:
        first = (moments.rows(session, shot)[:1] or [{}])[0].get("image")
        views = [v for v in views if v != first]
        # sem cena 3D, as folhas que foram para o frame-chave (as personagens da cena) seguem com ele
        cast = [m for m in (cast if shot.scene3d else keyframe.cast(session, shot)) if m != key]
    if shot.scene3d and shot.guide_video_id:
        # com o vídeo-guia, a câmara e o caminho vêm dele: bastam as vistas do início e do fim (várias imagens de sítios
        # diferentes levam o modelo a cortar de uma para a outra, como se fossem planos); com o frame-chave, o início
        # já vem nele
        keep = 1 if key is not None else MAX_VIEWS_WITH_GUIDE
        if len(views) > keep:
            views = views[-1:] if keep == 1 else [views[0], views[-1]]
    room = max(0, cap - len(cast) - len(lead))
    if len(views) > room:
        views = (views[:1] + views[len(views) - room + 1:]) if room > 1 else views[:room]
    return (lead + views + cast)[:cap]


@memo
def video_candidates(session: Session, shot: Shot) -> list[tuple[str, list[MediaFile]]]:
    """Tudo o que pode seguir no pedido, por grupos: o frame-chave, as vistas dos momentos, o elenco, as outras vistas
    do local, os outros assets da cena e os fotogramas guardados dos outros planos da cena."""
    from app import keyframe

    scene = session.get(Scene, shot.scene_id)
    views, cast = moments.view_images(session, shot), moments.cast_images(session, shot)
    key = keyframe.approved(session, shot)
    keys = [key] if key is not None else []
    seen = {m.id for m in views + cast + keys}
    loc = moments.location(session, shot)
    other_views = []
    if loc is not None:
        from app import location3d

        found = approved_media(session, [v.image_id for v in location3d.location_views(session, loc)])
        other_views = [m for m in found.values() if m.id not in seen]
        seen |= {m.id for m in other_views}
    ids = []
    from app import looks

    for a in scene_assets(session, scene):
        if a.kind != "location":
            ids += looks.ref_ids(session, a, scene)
    found = approved_media(session, ids)
    others = [found[i] for i in dict.fromkeys(ids) if i in found and i not in seen]
    frames = list(session.exec(select(MediaFile).join(Shot, Shot.id == MediaFile.shot_id)
                               .where(Shot.scene_id == scene.id, MediaFile.role == "frame", MediaFile.status == "approved")
                               .order_by(MediaFile.id)).all())
    refs = list(session.exec(select(MediaFile).where(MediaFile.shot_id == shot.id, MediaFile.role == "shotref",
                                                     MediaFile.status == "approved").order_by(MediaFile.id)).all())
    return [("Frame-chave do plano", keys), ("Referências do plano (frames de takes)", refs),
            ("Vistas do local nos momentos", views), ("Elenco", cast),
            ("Outras vistas do local", other_views), ("Outros da cena", others), ("Fotogramas guardados", frames)]


@memo
def send_refs(session: Session, shot: Shot, cap: int = MAX_VIDEO_REFS) -> list[MediaFile]:
    """Modo referências: as imagens que seguem, pela ordem: a escolha feita na página ou, sem escolha, a automática."""
    if shot.video_refs is None:
        return auto_refs(session, shot, cap)
    pool = {m.id: m for _g, items in video_candidates(session, shot) for m in items}
    return [pool[i] for i in shot.video_refs if i in pool][:cap]


def frame_inputs(session: Session, shot: Shot, model: dict | None) -> list[MediaFile]:
    """Modelos só com imagem inicial (e final): o frame-chave (ou, sem ele, a vista do local do 1.º momento) e, se o
    modelo aceitar, a vista do último."""
    from app import keyframe

    views = moments.view_images(session, shot)
    key = keyframe.approved(session, shot)
    if key is not None:
        end = views[-1] if len(views) > 1 and (model is None or model.get("end_frame", True)) else None
        return [key] + ([end] if end is not None else [])
    if not views:
        return []
    end = views[-1] if len(views) > 1 and (model is None or model.get("end_frame", True)) else None
    return [m for m in (views[0], end) if m]


def inputs_for(session: Session, shot: Shot, model: dict | None) -> list[MediaFile]:
    return request_plan(session, shot, model)["images"]


@memo
def request_plan(session: Session, shot: Shot, model: dict | None) -> dict:
    """O que segue num pedido, dentro dos limites do modelo: {"images", "guide", "voices", "missing" (quem fala sem voz
    aprovada), "dropped" [(personagem, motivo)] (vozes que não cabem), "limits"}."""
    from app import video_inputs as VI

    lim = limits(model)
    chosen_frames = VI.frames(session, shot, model)  # frame inicial (e final) escolhida à mão: imagem-para-vídeo
    mode = "frames" if chosen_frames else video_mode(model)
    cfg = VI.config(shot)
    guide = guide_for_video(session, shot, model).get("media") if lim["videos"] > 0 else None
    voices, missing, dropped, total_s = [], [], [], 0.0
    if mode == "references":
        found = approved_media(session, [a.voice_ref_id for a in speakers(session, shot)])
        for a in speakers(session, shot):
            m = found.get(a.voice_ref_id) if a.voice_ref_id else None
            if m is None:
                missing.append(a)
            elif m.id in cfg["voices_off"]:
                dropped.append((a, "desligada neste plano"))
            elif lim["audios"] <= 0:
                dropped.append((a, "o modelo não aceita vozes"))
            elif len(voices) >= lim["audios"]:
                dropped.append((a, f"o modelo aceita só {lim['audios']} voz(es)"))
            elif m.duration_ms and total_s + m.duration_ms / 1000 > lim["audio_s"]:
                dropped.append((a, f"as vozes juntas passam dos {lim['audio_s']:g} s que o modelo aceita"))
            else:
                voices.append(m)
                total_s += (m.duration_ms or 0) / 1000
    if mode == "references":
        cap = max(1, min(lim["images"], lim["files"] - (1 if guide is not None else 0) - len(voices)))
        images = send_refs(session, shot, cap)
    else:
        images = chosen_frames or frame_inputs(session, shot, model)
    return {"images": images, "guide": guide, "voices": voices, "missing": missing, "dropped": dropped, "limits": lim,
            "voice_s": total_s, "mode": mode}


# ── Vozes de quem fala no plano ───────────────────────────────

@memo
def speakers(session: Session, shot: Shot) -> list[Asset]:
    """As personagens que falam neste plano, pela ordem das falas (as linhas do guião marcadas para o plano; ver
    prompt_context.shot_talk)."""
    from app.prompt_context import shot_talk

    ids = [c for c, _t in shot_talk(session, shot) if c]
    out = []
    for i in dict.fromkeys(ids):
        a = session.get(Asset, i)
        if a is not None and a.kind == "character":
            out.append(a)
    return out


def voice_budget(session: Session, asset: Asset) -> list[dict]:
    """Para cortar a voz de uma personagem: em cada plano onde fala, quantos segundos de voz o modelo desse plano aceita
    no total, quanto ocupam as vozes aprovadas das outras personagens e o que sobra para esta."""
    from app.media_service import shot_code

    out = []
    shots = session.exec(select(Shot).join(Scene, Scene.id == Shot.scene_id)
                         .where(Scene.project_id == asset.project_id).order_by(Scene.number, Shot.number)).all()
    for shot in shots:
        who = speakers(session, shot)
        if asset not in who:
            continue
        model = shot_model(session, shot)
        lim = limits(model)
        others = []
        for a in who:
            m = session.get(MediaFile, a.voice_ref_id) if a.id != asset.id and a.voice_ref_id else None
            if m is not None and m.duration_ms:
                others.append((a.label or a.name, m.duration_ms / 1000))
        used = sum(sec for _n, sec in others)
        out.append({"shot": shot_code(session, shot), "model": (model or {}).get("name") or "—", "limit": lim["audio_s"],
                    "others": others, "free": max(0.0, lim["audio_s"] - used)})
    return out


def voice_inputs(session: Session, shot: Shot, model: dict | None) -> list[MediaFile]:
    """As vozes de referência que seguem (as de quem fala, dentro dos limites do modelo)."""
    return request_plan(session, shot, model)["voices"]


def voice_note(session: Session, shot: Shot, model: dict | None) -> dict:
    """Para a página: as vozes que seguem e os avisos (quem fala sem voz, quem não cabe no modelo)."""
    who = speakers(session, shot)
    if not who:
        return {}
    plan = request_plan(session, shot, model)
    warns = []
    if plan["missing"]:
        warns.append("Sem voz de referência aprovada: " + ", ".join(a.name for a in plan["missing"])
                     + " (gera-a na ficha da personagem).")
    for a, why in plan["dropped"]:
        warns.append(f"{a.name} fica sem voz de referência: {why}.")
    if warns:
        warns.append("Quem fica sem voz fala com uma voz inventada a partir da descrição (pode mudar de take para take).")
    return {"media": plan["voices"], "warn": " ".join(warns), "who": len(who), "limits": plan["limits"],
            "voice_s": plan["voice_s"]}


# ── Vídeo-guia e envio ────────────────────────────────────────

GUIDE_LINE = ("Follow the reference video @Video1 exactly for camera movement, framing, lens and timing, and for the "
              "positions and movements of everyone and everything in it; take the look of the characters, places and "
              "objects from the reference images and never show the grey previs look.")
EXTRAS_LINE = ("The light-grey figures in @Video1 are background extras: ordinary passers-by dressed for the place and "
               "era in varied, colourful everyday clothes, following exactly the positions and movements of @Video1.")
MOTION = "moving with fluid, natural, realistic body motion"


@memo
def guide_for_video(session: Session, shot: Shot, model: dict | None) -> dict:
    """O vídeo-guia da cena 3D para o pedido: {"media"} se segue, ou {"warn"} a dizer porque não."""
    if not shot.scene3d:
        return {"warn": "Este plano ainda não tem a cena 3D (Storyboard): sem vídeo-guia, o modelo não segue a câmara."}
    from app import video_inputs as VI

    if not VI.config(shot)["guide"]:
        return {"warn": "O vídeo-guia está desligado neste plano: o modelo não segue a câmara da cena 3D (o prompt "
                        "descreve-a por palavras).", "off": True}
    if VI.frames(session, shot, model):
        return {"warn": "Com frame inicial o pedido é de imagem-para-vídeo: o vídeo-guia não segue.", "off": True}
    if not (model and model.get("video_refs")):
        return {"warn": f"O {model['name'] if model else 'modelo'} não aceita vídeo de referência: o vídeo não vai "
                        "seguir o movimento de câmara da cena 3D (escolhe um modelo que aceite, ex.: Seedance 2.0)."}
    guide = session.get(MediaFile, shot.guide_video_id) if shot.guide_video_id else None
    if guide is None or guide.status != "approved":
        return {"warn": "Falta aprovar o vídeo-guia: grava-o na cena 3D do plano (Storyboard) e aprova-o."}
    if guide.duration_ms and shot.duration_s and abs(guide.duration_ms - shot.duration_s * 1000) > 50:
        return {"warn": "O vídeo-guia tem outra duração (a cena mudou depois de o gravares): grava-o de novo no Storyboard."}
    from app import scene3d

    gh = moments.guide_hash(shot, guide.id)
    if gh and gh != scene3d.preview_hash(scene3d.compose(session, session.get(Scene, shot.scene_id).project_id, shot=shot)):
        return {"media": guide, "warn": "A encenação da cena ou a câmara mudou depois de gravares o vídeo-guia: grava-o de "
                                        "novo no Storyboard (o que segue é o antigo)."}
    return {"media": guide}


def media_markers(model: dict | None) -> tuple[str, str] | None:
    """Como o prompt nomeia o vídeo-guia e as vozes no modelo escolhido: ("@Video1", "@Audio{n}") na fal;
    ("@video1", "@audio{n}") nos Seedance do Artlist (o Artlist entrega image_urls, video_urls e audio_urls ao modelo,
    pela ordem, como a ByteDance); None nos outros modelos do Artlist (vão por palavras)."""
    from app import model_profiles

    if not model:
        return None
    if model.get("provider") == "fal":
        return "@Video1", "@Audio{n}"
    if model_profiles.profile_for(model.get("name"), True).startswith("seedance"):
        return "@video1", "@audio{n}"
    return None


def final_prompt(prompt: str, model: dict | None, guide: MediaFile | None, voices: list | None = None,
                 session: Session | None = None, shot: Shot | None = None) -> str:
    """O prompt tal como segue: o vídeo-guia e as vozes de referência ligados aos marcadores do modelo (media_markers;
    por palavras quando o modelo não os tem), com a frase que falta se o texto não os refere. Com figurantes no
    vídeo-guia, garante que o modelo sabe que são pessoas reais (chamar-lhes «mannequins» fazia aparecer manequins)."""
    import re as _re

    text_ = prompt or ""
    if guide is not None and "@Video1" not in text_:  # antes da frase dos figurantes, que também nomeia o @Video1
        text_ = text_.rstrip() + "\n" + GUIDE_LINE
    if guide is not None:
        # o vídeo-guia dá o caminho; sem pedir um corpo fluido, o modelo copia os bonecos rígidos do previs.
        # Falar da «stiff animation» só chamava a atenção para ela.
        text_ = _re.sub(r"(?i),?\s*(?:or|and) (?:its|the) stiff (?:previs )?animation", "", text_)
        if MOTION not in text_.lower():
            act = _re.search(r"(?im)^\s*Action\s*:.*$", text_)
            line = f"Everyone is {MOTION}."
            text_ = (text_[:act.end()].rstrip() + " " + line + text_[act.end():]) if act else text_.rstrip() + "\n" + line
    if guide is not None and shot is not None and session is not None:
        from app import scene3d

        if scene3d.extras_note(session, shot)[0]:
            text_ = _re.sub(r"(?i)\b(light[- ]gr[ae]y|gr[ae]y|white)\s+mannequins?\b", r"\1 figures", text_)
            if not _re.search(r"(?i)\b(passers-by|background extras|pedestrians)\b", text_):
                text_ = text_.rstrip() + "\n" + EXTRAS_LINE
            elif not _re.search(r"(?i)colou?rful|varied", text_):  # senão vestem-nos da cor da figura (cinzento)
                text_ = _re.sub(r"(?i)\bpassers-by\b", "passers-by in varied, colourful everyday clothes", text_, count=1)
    marks = media_markers(model)
    notes = {}  # como fala cada personagem (Asset.voice_note): vai a seguir à voz, em cada fala
    if session is not None and "@" in text_:
        pid = session.get(Scene, shot.scene_id).project_id if shot is not None else None
        if pid:
            notes = {a.name: a.voice_note.strip().rstrip(".") for a in session.exec(select(Asset).where(
                Asset.project_id == pid, Asset.voice_note.is_not(None))).all() if (a.voice_note or "").strip()}
    for n, m in enumerate(voices or [], 1):
        a = session.get(Asset, m.asset_id) if session is not None and m.asset_id else None
        code = a.name if a else ""
        target = (marks[1].replace("{n}", str(n)) if marks else
                  ("the reference voice audio" if len(voices) == 1 else f"reference voice audio {n}"))
        note = f", {notes[code]}" if code in notes else ""
        if code and f"@{code}.voice" in text_:
            text_ = text_.replace(f"@{code}.voice", target + note)
        elif code:
            text_ = text_.rstrip() + f"\n{code} speaks with exactly the voice in {target}{note} (same timbre, accent and pace)."
    text_ = _re.sub(r"@([A-Z][A-Z0-9_]*)\.voice\b",
                    lambda mm: "the character's own voice" + (f", {notes[mm.group(1)]}" if mm.group(1) in notes else ""), text_)  # sem voz que siga

    if guide is None or marks is None:
        text_ = text_.replace("the reference video @Video1", "the reference video").replace("@Video1", "the reference video")
    else:
        text_ = text_.replace("@Video1", marks[0])
    return text_


def send_preview(session: Session, shot: Shot, model: dict | None) -> dict:
    """O que vai ser enviado ao modelo: as imagens pela ordem (com o marcador de cada uma), o vídeo-guia e o prompt tal
    como sai (marcadores já trocados pela sintaxe do modelo)."""
    from app import model_profiles, ref_tokens

    mode = shot_mode(session, shot, model)
    plan = request_plan(session, shot, model)
    images, guide, voices = plan["images"], plan["guide"], plan["voices"]
    fmt = model_profiles.ref_format_for(model["name"] if model else None) if mode == "references" else None
    resolved, problems = ref_tokens.resolve(session, final_prompt(shot.video_prompt or "", model, guide, voices, session, shot),
                                            images, fmt, mode != "references", bare=mode == "references")
    marker = (fmt or "Image {n}") if mode == "references" else None
    return {"images": [(m, ref_tokens.token_for(session, m)[1], marker.replace("{n}", str(n)) if marker else
                        ("imagem inicial" if n == 1 else "imagem final")) for n, m in enumerate(images, 1)],
            "guide": guide, "prompt": resolved, "problems": problems,
            "voices": [(m, ref_tokens.token_for(session, m)[1],
                        media_markers(model)[1].replace("{n}", str(n)) if media_markers(model) else "áudio")
                       for n, m in enumerate(voices, 1)]}


def sent_list(session: Session, images: list, guide, voices: list, mode: str) -> list[dict]:
    """O que seguiu no pedido, para o histórico do take: [{"ref", "kind", "mode"}]."""
    from app import ref_tokens

    out = [{"ref": ref_tokens.token_for(session, m)[0], "kind": "image",
            "mode": ("start_frame" if n == 0 else "end_frame") if mode == "frames" else "ref"} for n, m in enumerate(images)]
    if guide is not None:
        out.append({"ref": "@Video1", "kind": "video", "mode": "guide"})
    out += [{"ref": ref_tokens.token_for(session, m)[0], "kind": "audio", "mode": "ref"} for m in voices]
    return out


def queue_video(session: Session, project: Project, shot: Shot, prompt: str, audio: bool) -> tuple[bool, str]:
    """Pede um take (sem pedido HTTP: serve também ao escritor de prompts). Devolve (pedido feito, mensagem)."""
    from app import jobs

    model = shot_model(session, shot)
    if model is None:
        return False, "Não há modelos de vídeo disponíveis (liga o Artlist ou guarda a chave da fal na Configuração)."
    provider = model.get("provider") or "artlist"
    fal_model = provider == "fal"
    if fal_model and not fal_enabled(session):
        return False, "Este modelo é da fal.ai: guarda a chave em Configuração → Chaves de API."
    if provider == "bytedance" and not bytedance_enabled(session):
        return False, "Este modelo é da ByteDance: guarda as chaves da BytePlus em Configuração → Chaves de API."
    if provider == "artlist" and not artlist_enabled(session):
        return False, "Este modelo é do Artlist: liga a app ao Artlist (Configuração → Ligações)."
    if any(j.status in ("queued", "running") for j in jobs.jobs_for(session, project, "clip", shot_id=shot.id)):
        return False, "Já há um vídeo a ser gerado para este plano. Espera que termine."
    mode = shot_mode(session, shot, model)
    plan = request_plan(session, shot, model)
    images = plan["images"]
    if not prompt.strip():
        return False, "Falta o prompt de vídeo."
    if not images:
        return False, ("Faltam as imagens: aprova as vistas do local nos momentos do plano (Storyboard) e as folhas "
                       "do elenco.")
    guide, voices = plan["guide"], plan["voices"]
    inputs = [m.id for m in images] + ([guide.id] if guide is not None else []) + [m.id for m in voices]
    jobs.enqueue(session, project, "clip", final_prompt(prompt, model, guide, voices, session, shot),
                 (project.aspect_ratio or "9:16").split(" ")[0],
                 inputs, kind="video_refs" if mode == "references" else "video_frames",
                 provider=provider, model_group_id=model["key"] if provider in ("fal", "bytedance") else model["group"],
                 duration_s=shot.duration_s, audio=audio, shot_id=shot.id,
                 params={"resolution": shot_resolution(session, shot, model), "model_name": model["name"],
                         "quote": (video_cost.shot_cached(video_cost.shot_args(session, shot)) or {}).get("credits"),
                         "sent": sent_list(session, images, guide, voices, mode), "guide": guide is not None})
    session.flush()
    return True, (f"Vídeo pedido ao {model['name']}. Demora alguns minutos; o novo take aparece em Takes e a página "
                  "atualiza-se sozinha.")


# ── Página ────────────────────────────────────────────────────

def _view(request: Request, session: Session, project: Project, shot: Shot, scene: Scene):
    model = shot_model(session, shot)
    mode = shot_mode(session, shot, model)
    shots = scene_shots(session, scene)
    idx = next(i for i, s in enumerate(shots) if s.id == shot.id)
    pending = pending_roles_by_shot(session, project.id).get(shot.id, set())
    clip = approved_media(session, [shot.approved_clip_id]).get(shot.approved_clip_id)
    sent = inputs_for(session, shot, model)
    t = PT.get(session, project, "video", str(shot.id))
    cast = moments.cast_images(session, shot)
    vs = video_settings(session)
    ctx = {
        "prompt": prompt_ops.panel(session, t, "Prompt de vídeo", people=_people(session, shot), dialogue=shot.dialogue),
        "moments": moments.rows(session, shot), "location": moments.location(session, shot),
        "sent": [(m, PT_label(session, m)) for m in sent],
        "vref_groups": [(g, [(m.id, m, PT_label(session, m)) for m in items]) for g, items in video_candidates(session, shot)]
                       if mode == "references" else [],
        "chosen_ids": {m.id for m in sent}, "custom_refs": shot.video_refs is not None, "max_refs": limits(model)["images"] if mode == "references" else MAX_VIDEO_REFS,
        "frames_saved": [(fm, PT_label(session, fm)) for fm in session.exec(
            select(MediaFile).where(MediaFile.shot_id == shot.id, MediaFile.role == "frame", MediaFile.status == "approved")
            .order_by(MediaFile.id)).all()],
        "shot": shot, "scene": scene, "scene_code": scene_code(session, scene), "cast_n": len(cast),
        "prev_shot": shots[idx - 1] if idx > 0 else None, "next_shot": shots[idx + 1] if idx + 1 < len(shots) else None,
        "clip": clip,
        "clip_results": media_list(session, shot_id=shot.id, role="clip", status="pending"),
        "rejected_clips": [m for m in reversed(media_list(session, shot_id=shot.id, role="clip", status="rejected"))
                           if (media_root() / m.path).is_file()],
        "vin": video_inputs.items(session, shot, model), "vin_voices_off": video_inputs.config(shot)["voices_off"],
        "take_info": take_info(session, [*media_list(session, shot_id=shot.id, role="clip"), *([clip] if clip else [])]),
        "proj_model": project_model(session, project), "proj_res": project_resolution(session, project),
        "res_options": resolutions_of(model), "shot_res": shot_resolution(session, shot, model),
        "cost_now": video_cost.shot_cached(cost_args := video_cost.shot_args(session, shot)),
        "cost_key": "|".join(str(x) for x in ((model or {}).get("name"), cost_args["res"], cost_args["seconds"],
                                              int(bool(cost_args["audio"])), cost_args["sig"])),
        "prompt_other_model": (shot.video_prompt and shot.video_prompt_model and model
                               and shot.video_prompt_model != model["name"]) and shot.video_prompt_model,
        "takes": take_labels(session, [*media_list(session, shot_id=shot.id, role="clip"), *([clip] if clip else [])]),
        "models": video_models(session), "model": model, "mode": mode,
        "guide_note": guide_for_video(session, shot, model), "voice_note": voice_note(session, shot, model),
        "voice_items": [(m, PT_label(session, m).split(" · ")[0]) for m in voice_inputs(session, shot, model)],
        "preview": send_preview(session, shot, model) if shot.video_prompt else None,
        "guide_issues": guide_check.check(session, shot, shot.video_prompt or "") if shot.video_prompt else [],
        "crowd": scene3d.crowd(session, shot) if shot.scene3d else None,
        "left_out": left_out(session, shot, model),
        "talk": _talk_rows(session, shot),
        "state": shot_clip_state(shot, pending),
        "can_generate": bool(sent) and model is not None,
        "video_jobs": _video_jobs(session, project, shot), "video_quality": vs["quality"], "video_audio": vs["audio"],
        "default_model": next((m for m in video_models(session) if m.get("default")), None),
        "max_upload_mb": MAX_VIDEO_BYTES // (1024 * 1024), "main_form": "video-form",
        "assistant_key": f"video_shot:{shot.id}",
    }
    from app import keyframe

    ctx["key"] = {"media": keyframe.approved(session, shot), "stale": keyframe.stale(session, shot)}
    from app import scene_nav

    ctx["nav"] = scene_nav.build(session, project, scene, "video", shot)
    return render_stage(request, session, project, STAGE, "video/shot.html", ctx, current=("shot", shot.id))


def PT_label(session: Session, media: MediaFile) -> str:  # noqa: N802
    """Rótulo de uma imagem do pedido (o marcador e o que é)."""
    from app import ref_tokens

    token, desc = ref_tokens.token_for(session, media)
    return f"{token} · {desc}"


@router.get("/video/planos/{shot_id}")
def video_page(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    shot, scene = get_shot(session, project, shot_id)
    return _view(request, session, project, shot, scene)


@router.post("/video/planos/{shot_id}")
async def video_save(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    shot, scene = get_shot(session, project, shot_id)
    form = await request.form()
    op = text(form, "op") or ""
    url = f"/projetos/{project.id}/video/planos/{shot.id}"
    try:
        if "model" in form:  # «default» = o padrão do projeto
            before = (shot_model(session, shot) or {}).get("name")
            try:
                choose_model(session, shot, text(form, "model") or "default")
            except ValueError as exc:
                raise FormError(str(exc)) from exc
            model_changed = before != (shot_model(session, shot) or {}).get("name")
        else:
            model_changed = False
        if "video_resolution" in form:
            res = text(form, "video_resolution") or "default"
            if res != "default" and res not in RESOLUTIONS:
                raise FormError("Resolução inválida.")
            shot.video_resolution = None if res == "default" else res
        model = shot_model(session, shot)
        if model and "duration_s" in form:
            duration = integer(form, "duration_s", "Duração", minimum=1)
            allowed = model.get("durations_s") or []
            if duration is not None and allowed and duration not in allowed and model_changed:
                duration = min(allowed, key=lambda d: (abs(d - duration), d))  # modelo novo: a duração mais próxima
            if duration is not None and allowed and duration not in allowed:
                raise FormError(f"{model['name']} só gera clips de {', '.join(str(d) for d in allowed)} s.")
            if duration is not None and duration != shot.duration_s:
                from app.routes.storyboard import change_duration

                try:  # os troços dos planos seguintes andam com a diferença; o guia e os previs ficam por refazer
                    notes = change_duration(session, shot, duration)
                except ValueError as exc:
                    raise FormError(str(exc)) from exc
                if notes:
                    flash_info(request, "Duração mudada: " + "; ".join(notes) + ".")
            elif duration is None:
                shot.duration_s = None
        for field in ("sound_effects", "ambience", "music"):
            if field in form:
                setattr(shot, field, text(form, field))
        if op == "vref_auto":
            shot.video_refs, shot.video_inputs = None, None
        elif "vin_present" in form:  # o que segue no pedido: imagens (e o modo de cada), vídeo-guia e vozes
            st = video_inputs.items(session, shot, model)
            imgs_on = {int(v) for v in form.getlist("vin_img") if str(v).isdigit()}
            voices_on = {int(v) for v in form.getlist("vin_voice") if str(v).isdigit()}
            data = {"guide": bool(form.get("vin_guide")) if st["guide"]["media"] and st["accepts"]["video"] else st["guide"]["wanted"],
                    "items": [{"ref": i["token"], "on": i["id"] in imgs_on,
                               "mode": (text(form, f"vin_mode:{i['id']}") or "ref") if i["id"] in imgs_on else "ref"}
                              for i in st["images"]]
                             + ([{"ref": v["token"], "on": v["id"] in voices_on} for v in st["voices"]] if st["accepts"]["audio"] else [])}
            try:
                refs, cfg, notes = video_inputs.parse(session, shot, model, data)
            except ValueError as exc:
                raise FormError(str(exc)) from exc
            shot.video_refs, shot.video_inputs = refs, cfg
            if notes:
                flash_info(request, "Atenção: " + "; ".join(notes) + ".")
        if "dialogue" in form:
            choice = text(form, "dialogue")
            if choice is None:
                shot.dialogue, shot.dialogue_character_id = None, None
            elif choice.isdigit():
                lines = _dialogue_lines(session, scene)
                idx = int(choice)
                if idx >= len(lines):
                    raise FormError("Fala inválida.")
                _i, line, speaker = lines[idx]
                shot.dialogue, shot.dialogue_character_id = line.text, speaker.id if speaker else None
    except FormError as exc:
        session.rollback()
        flash(request, str(exc))
        return RedirectResponse(url, status_code=303)
    session.add(shot)
    prompt_ops.save(session, project, form, op)
    session.commit()
    if prompt_ops.run(request, session, project, form, op):
        session.commit()
        return RedirectResponse(url + "#resultados", status_code=303)
    return RedirectResponse(url + ("" if op else "?guardado=1"), status_code=303)


def _video_jobs(session: Session, project: Project, shot: Shot) -> list[dict]:
    from app.routes.gpt import jobs_ctx

    return jobs_ctx(session, project, "clip", shot_id=shot.id)


# ── Fotogramas e takes carregados ─────────────────────────────

MAX_FRAME_BYTES = 25 * 1024 * 1024


@router.post("/video/planos/{shot_id}/fotogramas")
async def frame_save(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    """Fotograma de um take (capturado no browser, em PNG): fica guardado no plano e pode seguir no pedido de vídeo dos
    outros planos da cena, para dar continuidade."""
    from app.media import safe_segment, write_file

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    form = await request.form()
    take = session.get(MediaFile, int(form.get("take") or 0)) if str(form.get("take") or "").isdigit() else None
    if take is None or take.shot_id != shot.id or take.role != "clip" or take.status == "rejected":
        raise HTTPException(status_code=400, detail="Take inválido.")
    upload = form.get("frame")
    data = await upload.read(MAX_FRAME_BYTES + 1) if upload is not None and hasattr(upload, "read") else b""
    if not data.startswith(b"\x89PNG") or len(data) > MAX_FRAME_BYTES:
        raise HTTPException(status_code=400, detail="Fotograma inválido.")
    try:
        t_ms = max(0, int(float(form.get("t") or 0) * 1000))
    except ValueError:
        t_ms = 0
    take_n = re.search(r"v(\d+)\.\w+$", take.path)
    base = f"{safe_segment(project.prefix)}/planos/{shot.id}/fotogramas"
    n = len(session.exec(select(MediaFile).where(MediaFile.shot_id == shot.id, MediaFile.role == "frame")).all()) + 1
    while True:
        rel = f"{base}/v{n}_take{take_n.group(1) if take_n else 0}_t{t_ms}.png"
        if not (media_root() / rel).exists():
            break
        n += 1
    write_file(rel, data)
    session.add(MediaFile(project_id=project.id, path=rel, kind="image", role="frame", source="upload",
                          status="approved", shot_id=shot.id))
    session.commit()
    flash_info(request, "Fotograma guardado. Pode seguir no pedido de vídeo dos outros planos da cena.")
    return Response(status_code=204)


@router.post("/video/planos/{shot_id}/fotogramas/{media_id}/plano-seguinte")
async def frame_to_next(request: Request, project_id: int, shot_id: int, media_id: int,
                        session: Session = Depends(get_session)):
    """Junta o fotograma às imagens do pedido do plano seguinte (a primeira): o próximo take continua daqui."""
    project = get_project(session, project_id)
    shot, scene = get_shot(session, project, shot_id)
    frame = session.get(MediaFile, media_id)
    if frame is None or frame.shot_id != shot.id or frame.role != "frame" or frame.status != "approved":
        raise HTTPException(status_code=404)
    shots = scene_shots(session, scene)
    idx = next(i for i, s in enumerate(shots) if s.id == shot.id)
    if idx + 1 >= len(shots):
        flash(request, "Este é o último plano da cena.")
        return RedirectResponse(f"/projetos/{project.id}/video/planos/{shot.id}", status_code=303)
    nxt = shots[idx + 1]
    current = [m.id for m in send_refs(session, nxt)]
    nxt.video_refs = [frame.id] + [i for i in current if i != frame.id][:MAX_VIDEO_REFS - 1]
    session.add(nxt)
    session.commit()
    flash_info(request, f"O fotograma é a primeira imagem do pedido do P{nxt.number:02d}.")
    return RedirectResponse(f"/projetos/{project.id}/video/planos/{nxt.id}#imagens", status_code=303)


@router.post("/video/planos/{shot_id}/clips")
async def clip_upload(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    await save_uploads(request, session, project, f"planos/{shot.id}/clip", "video", "clip", shot_id=shot.id)
    return RedirectResponse(f"/projetos/{project.id}/video/planos/{shot.id}#resultados", status_code=303)


# ── Custo e rascunho ──────────────────────────────────────────

@router.get("/video/planos/{shot_id}/custo")
async def video_cost_view(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    """O custo estimado do próximo take (pedido ao fornecedor; a página carrega-o à parte)."""
    from fastapi.responses import HTMLResponse

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    args = video_cost.shot_args(session, shot)
    q = await video_cost.shot_quote(args)
    if not q:
        return HTMLResponse('<span class="dim">custo indisponível</span>')
    m = args["model"]
    return HTMLResponse(f'<span title="{m["name"]} · {args["res"]} · {args["seconds"]} s · {len(args["images"])} imagem(ns)'
                        f'{" + vídeo-guia" if args["videos"] else ""} · cotado por {q["source"]}">≈ {q["text"]}</span>')


@router.get("/video/planos/{shot_id}/rascunho/{media_id}/custo")
async def draft_cost_view(request: Request, project_id: int, shot_id: int, media_id: int, session: Session = Depends(get_session)):
    from fastapi.responses import HTMLResponse

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    job = session.exec(select(ImageJob).where(ImageJob.result_media_id == media_id, ImageJob.shot_id == shot.id)).first()
    gid = (job.params or {}).get("generation_id") if job else None
    credits = await video_cost.draft_quote(gid) if gid else None
    return HTMLResponse(f"{credits} créditos" if credits is not None else "custo indisponível")


@router.post("/video/planos/{shot_id}/rascunho/{media_id}")
async def draft_render(request: Request, project_id: int, shot_id: int, media_id: int, session: Session = Depends(get_session)):
    """«Passar a 1080p (mesmo take)»: o rascunho 480p do Seedance 2.5 renderizado em 1080p (um take novo)."""
    from app import jobs

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    url = f"/projetos/{project.id}/video/planos/{shot.id}"
    media = session.get(MediaFile, media_id)
    info = take_info(session, [media]).get(media_id) if media and media.shot_id == shot.id else None
    if not info or not info["draft"]:
        flash(request, "Este take já não pode passar a 1080p (não é um rascunho do Seedance 2.5, já passou ou tem mais de 7 dias).")
        return RedirectResponse(url + "#resultados", status_code=303)
    job = session.exec(select(ImageJob).where(ImageJob.result_media_id == media_id)).first()
    jobs.enqueue(session, project, "clip", job.prompt, job.aspect_ratio, [], kind="draft_render", provider="artlist",
                 model_group_id=DRAFT_GROUP, duration_s=job.duration_s, audio=job.audio, shot_id=shot.id,
                 params={"draft_of": job.params["generation_id"], "resolution": "1080p",
                         "model_name": (job.params or {}).get("model_name"), "from_take": media_id})
    session.commit()
    jobs.wake()
    flash_info(request, "A passar o take a 1080p no Artlist: aparece em Takes como um take novo.")
    return RedirectResponse(url + "#resultados", status_code=303)


@router.post("/video/padrao")
async def project_video_default(request: Request, project_id: int, session: Session = Depends(get_session)):
    """O modelo e a resolução padrão do projeto (etapa Vídeo)."""
    project = get_project(session, project_id)
    form = await request.form()
    name, res = text(form, "video_model"), text(form, "video_resolution")
    if name and any(m["name"] == name for m in video_models(session)):
        project.video_model = name
    if res in RESOLUTIONS:
        project.video_resolution = res
    session.add(project)
    session.flush()
    n = apply_project_default(session, project)
    session.commit()
    flash_info(request, f"Padrão do projeto: {project.video_model} · {project.video_resolution}."
               + (f" {n} plano(s) mudaram de duração para uma que o modelo aceita." if n else ""))
    return RedirectResponse(f"/projetos/{project.id}/etapa/6", status_code=303)
