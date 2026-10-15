"""Ferramentas MCP para o vídeo de cada plano e as vozes das personagens: gerar um take na app (só depois de o
utilizador aceitar o custo), carregar takes gerados fora (ex.: no Artlist pelo Claude), aprovar/rejeitar/repor takes,
e carregar, aprovar e rejeitar a voz de referência de uma personagem."""

import base64
from pathlib import PurePosixPath
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlmodel import Session, func, select

from app import prompt_targets as PT
from app.db import engine
from app.mcp_server import READ, WRITE, _project, log, server
from app.mcp_takes import _link, _shot
from app.models import Asset, ImageJob, MediaFile, Project

PAID = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)


async def _fetch(url: str | None, b64: str | None, limit: int, what: str) -> bytes:
    if bool(url) == bool(b64):
        raise ToolError(f"Envia {what}_url ou {what}_base64 (só um).")
    if url:
        if not url.startswith("https://"):
            raise ToolError(f"O {what}_url tem de ser https://.")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise ToolError(f"Não consegui descarregar o ficheiro: {exc}") from exc
        if resp.status_code >= 400:
            raise ToolError(f"Não consegui descarregar o ficheiro (HTTP {resp.status_code}).")
        data = resp.content
    else:
        try:
            data = base64.b64decode(b64.split(",", 1)[-1], validate=False)
        except ValueError as exc:
            raise ToolError(f"{what}_base64 inválido.") from exc
    if len(data) > limit:
        raise ToolError(f"O ficheiro passa de {limit // (1024 * 1024)} MB.")
    return data


def _store(s: Session, p: Project, folder: str, ext: str, data: bytes, **fields) -> MediaFile:
    """Grava o ficheiro como a app grava os carregados (mesma pasta e nomes v1, v2…) e devolve o MediaFile."""
    from app.media import media_root, safe_segment, write_file

    base = PurePosixPath(safe_segment(p.prefix), *[safe_segment(x) for x in folder.split("/")])
    q = select(func.count()).select_from(MediaFile).where(MediaFile.role == fields["role"])
    for k in ("shot_id", "asset_id"):
        if fields.get(k):
            q = q.where(getattr(MediaFile, k) == fields[k])
    n = s.exec(q).one() + 1
    rel = base / f"v{n}.{ext}"
    while (media_root() / rel).exists():
        n += 1
        rel = base / f"v{n}.{ext}"
    write_file(str(rel), data)
    m = MediaFile(project_id=p.id, path=str(rel), status="pending", **fields)
    s.add(m)
    s.flush()
    return m


def _take(s: Session, project_id: int, shot_id: int, take_id: int):
    sh = _shot(s, project_id, shot_id)
    m = s.get(MediaFile, take_id)
    if m is None or m.shot_id != sh.id or m.role != "clip":
        raise ToolError(f"O take {take_id} não é do plano {shot_id}. Vê list_takes.")
    return sh, m


# ── Gerar ─────────────────────────────────────────────────────

@server.tool(annotations=WRITE, title="Escrever prompt de vídeo",
             description="Corre o escritor de prompts da app para o vídeo de um plano (como o botão «Escrever» na página "
                         "do plano), grava-o em video_prompt (com versão) e devolve-o. Demora 1 a 3 minutos. Em alternativa "
                         "escreve-o tu com set_field(screen=\"video_shot:N\", field=\"shot:N.video_prompt\").")
async def write_video_prompt(project_id: int, shot_id: int) -> str:
    from app import prompt_writer as W
    from app.models import PromptTask

    with Session(engine) as s:
        p = _project(s, project_id)
        _shot(s, project_id, shot_id)
        busy = W.task_for(s, p.id, "video", str(shot_id))
        if busy is not None and busy.status == "running":
            raise ToolError("O escritor já está a escrever este prompt. Espera que termine.")
        if busy is not None:
            s.delete(busy)
        task = PromptTask(project_id=p.id, kind="video", ref=str(shot_id), model=W.writer_label(s))
        s.add(task)
        s.commit()
        tid = task.id
    await W._run(tid)
    with Session(engine) as s:
        task = s.get(PromptTask, tid)
        if task.status != "done":
            raise ToolError(f"O escritor falhou: {task.error or 'sem motivo'}")
        t = PT.get(s, s.get(Project, project_id), "video", str(shot_id))
        return f"Prompt de vídeo escrito e gravado ({t.what}):\n\n{t.prompt}"


@server.tool(annotations=PAID, structured_output=True, title="Gerar vídeo na app",
             description="Gera um take do plano na app, como o botão «Gerar vídeo»: o modelo, a resolução, a duração, o "
                         "prompt de vídeo e o que segue no pedido (video_inputs) são os do plano. É PAGO. Sem "
                         "confirm_cost não gera nada: devolve o custo cotado pelo fornecedor para mostrares ao utilizador. "
                         "Só com confirm_cost=true, DEPOIS de o utilizador aceitar esse custo, o pedido segue. audio: com "
                         "som (por omissão o da Configuração). Corre em segundo plano (alguns minutos): devolve um "
                         "generation_id para generation_status; o take aparece em list_takes por decidir.")
async def generate_video(project_id: int, shot_id: int, confirm_cost: bool = False, audio: bool | None = None) -> dict[str, Any]:
    from app import jobs, video_cost
    from app.options import video_settings
    from app.routes.video import shot_model

    with Session(engine) as s:
        p = _project(s, project_id)
        sh = _shot(s, project_id, shot_id)
        t = PT.get(s, p, "video", str(sh.id))
        if not (t.prompt or "").strip() or PT.needs_writing(s, t):
            raise ToolError("O prompt de vídeo deste plano está por escrever ou desatualizado: write_video_prompt ou "
                            f"set_field(screen=\"video_shot:{sh.id}\", field=\"shot:{sh.id}.video_prompt\") primeiro.")
        if any(j.status in ("queued", "running") for j in jobs.jobs_for(s, p, "clip", shot_id=sh.id)):
            raise ToolError("Já há um vídeo a ser gerado para este plano. Espera que termine (list_takes).")
        model = shot_model(s, sh)
        if model is None:
            raise ToolError("Não há modelos de vídeo disponíveis (liga o Artlist ou guarda a chave da fal na Configuração).")
        args = video_cost.shot_args(s, sh)
        audio = video_settings(s)["audio"] if audio is None else bool(audio)
    q = await video_cost.shot_quote(args)
    cost = {"model": (model or {}).get("name"), "resolution": args["res"], "duration_s": args["seconds"],
            "credits": (q or {}).get("credits"), "cost": (q or {}).get("text") or "custo indisponível"}
    if not confirm_cost:
        return {**cost, "generated": False,
                "text": f"Não gerei. Custo do take: {cost['cost']} ({cost['model']}, {cost['resolution']}, "
                        f"{cost['duration_s']} s). Mostra ao utilizador e, se ele aceitar, chama outra vez com "
                        "confirm_cost=true."}
    with Session(engine) as s:
        p = s.get(Project, project_id)
        t = PT.get(s, p, "video", str(shot_id))
        before = s.exec(select(func.max(ImageJob.id))).one() or 0
        ok, msg = PT.enqueue(s, t, {"audio": audio})
        if not ok:
            s.rollback()
            raise ToolError(msg)
        made = list(s.exec(select(ImageJob).where(ImageJob.id > before, ImageJob.shot_id == shot_id,
                                                  ImageJob.role == "clip")).all())
        for j in made:
            j.confirm_cost = True  # o utilizador aceitou o custo
            s.add(j)
        s.commit()
        gid = "g" + "-".join(str(j.id) for j in made)
    jobs.wake()
    log.info("mcp: vídeo pedido %s (plano %s) no projeto %s", gid, shot_id, project_id)
    return {**cost, "generated": True, "generation_id": gid, "text": msg}


# ── Takes ─────────────────────────────────────────────────────

@server.tool(annotations=WRITE, structured_output=True, title="Carregar take",
             description="Carrega um take gerado fora da app (ex.: no Artlist) para um plano, como «Carregar take do "
                         "computador»: video_url (https, ex.: o link do Artlist) ou video_base64; MP4 ou MOV até 200 MB. "
                         "Fica por decidir em list_takes. prompt e model (opcionais) ficam registados com o take.")
async def upload_take(project_id: int, shot_id: int, video_url: str | None = None, video_base64: str | None = None,
                      prompt: str | None = None, model: str | None = None) -> dict[str, Any]:
    from app.media import MAX_VIDEO_BYTES, UploadError, mp4_duration_ms, video_extension

    data = await _fetch(video_url, video_base64, MAX_VIDEO_BYTES, "video")
    try:
        ext = video_extension(data, "o vídeo")
    except UploadError as exc:
        raise ToolError(str(exc)) from exc
    with Session(engine) as s:
        p = _project(s, project_id)
        sh = _shot(s, project_id, shot_id)
        m = _store(s, p, f"planos/{sh.id}/clip", ext, data, kind="video", role="clip", shot_id=sh.id,
                   duration_ms=mp4_duration_ms(data))
        m.source = "mcp"
        if prompt or model:  # o que o gerou fica com o take (list_takes mostra-o)
            s.add(ImageJob(project_id=p.id, role="clip", prompt=(prompt or "").strip(), aspect_ratio=None, inputs=[],
                           kind="upload", provider="mcp", model=(model or "").strip()[:120] or None, status="done",
                           shot_id=sh.id, result_media_id=m.id,
                           params={"model_name": (model or "").strip()[:120] or None}))
        s.add(m)
        s.commit()
        out = {"take_id": m.id, "shot": f"P{sh.number:02d}", "duration_s": round(m.duration_ms / 1000, 2) if m.duration_ms else None,
               "status": "por decidir"}
    log.info("mcp: take carregado %s no plano %s", out["take_id"], shot_id)
    return {**out, "text": f"Take {out['take_id']} carregado no {out['shot']} (por decidir)."}


@server.tool(annotations=WRITE, title="Aprovar take",
             description="Escolhe um take para o plano (o que vai para a montagem), como o botão Escolher da app: o "
                         "escolhido anterior volta a «por decidir». Também serve para um take rejeitado que ainda tem o "
                         "ficheiro. Só quando o utilizador o disser.")
def approve_take(project_id: int, shot_id: int, take_id: int) -> str:
    from app.media_service import approve_media
    from app.routes.files import kept_take

    with Session(engine) as s:
        sh, m = _take(s, project_id, shot_id, take_id)
        if m.status == "rejected" and not kept_take(m):
            raise ToolError("Este take foi rejeitado antes de os takes ficarem guardados: já não tem o ficheiro.")
        if sh.approved_clip_id == m.id and m.status == "approved":
            return f"O take {take_id} já é o escolhido do P{sh.number:02d}."
        approve_media(s, m)
        s.commit()
        log.info("mcp: take %s aprovado no plano %s", take_id, shot_id)
        return f"Escolhido: take {take_id} no P{sh.number:02d}."


@server.tool(annotations=DESTRUCTIVE, title="Rejeitar take",
             description="Rejeita um take, como o botão Rejeitar: sai de escolhido / por decidir e fica em «Rejeitados» "
                         "(o ficheiro fica guardado; restore_take repõe-no).")
def reject_take(project_id: int, shot_id: int, take_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        sh, m = _take(s, project_id, shot_id, take_id)
        if m.status == "rejected":
            return f"O take {take_id} já está rejeitado."
        reject_media(s, m)
        s.commit()
        return f"Rejeitado: take {take_id} do P{sh.number:02d} (fica em Rejeitados; restore_take repõe-no)."


@server.tool(annotations=WRITE, title="Repor take",
             description="Um take rejeitado volta a «por decidir».")
def restore_take(project_id: int, shot_id: int, take_id: int) -> str:
    from app.media_service import restore_media
    from app.routes.files import kept_take

    with Session(engine) as s:
        sh, m = _take(s, project_id, shot_id, take_id)
        if m.status != "rejected" or not kept_take(m):
            raise ToolError("Só se repõem takes rejeitados que ainda têm o ficheiro.")
        restore_media(s, m)
        s.commit()
        return f"Reposto: take {take_id} do P{sh.number:02d} (por decidir)."


# ── Vozes das personagens ─────────────────────────────────────

def _character(s: Session, project_id: int, asset_id: int) -> Asset:
    p = _project(s, project_id)
    a = s.get(Asset, asset_id)
    if a is None or a.project_id != p.id:
        raise ToolError(f"O asset {asset_id} não é deste projeto.")
    if a.kind != "character":
        raise ToolError("Só as personagens têm voz de referência.")
    return a


@server.tool(annotations=READ, structured_output=True, title="Vozes de uma personagem",
             description="A voz de referência de uma personagem (a aprovada, que segue nos pedidos de vídeo dos planos "
                         "onde ela fala) e as vozes por decidir, com o link de cada uma (2 h) e a duração.")
def list_voices(project_id: int, asset_id: int) -> dict[str, Any]:
    from app.media import media_root

    with Session(engine) as s:
        a = _character(s, project_id, asset_id)
        rows = s.exec(select(MediaFile).where(MediaFile.asset_id == a.id, MediaFile.role == "voice",
                                              MediaFile.status != "rejected").order_by(MediaFile.id.desc())).all()
        out = []
        for m in rows:
            path = media_root() / m.path
            out.append({"voice_id": m.id, "status": "aprovada" if m.id == a.voice_ref_id else "por decidir",
                        "duration_s": round(m.duration_ms / 1000, 2) if m.duration_ms else None,
                        "url": _link(path, path.suffix) if path.is_file() else None,
                        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M")})
        return {"character": a.name, "voice_note": a.voice_note, "voices": out}


@server.tool(annotations=WRITE, structured_output=True, title="Carregar voz",
             description="Carrega a voz de referência de uma personagem para a ficha dela, como «Carregar» na secção Voz: "
                         "audio_url (https) ou audio_base64; MP3, WAV, M4A, OGG ou FLAC até 25 MB (fica em MP3). Fica por "
                         "decidir; approve_voice só quando o utilizador o disser.")
async def upload_voice(project_id: int, asset_id: int, audio_url: str | None = None,
                       audio_base64: str | None = None) -> dict[str, Any]:
    from app.media import MAX_AUDIO_BYTES, UploadError, audio_extension, normalize_audio

    data = await _fetch(audio_url, audio_base64, MAX_AUDIO_BYTES, "audio")
    try:
        audio_extension(data, "o áudio")
    except UploadError as exc:
        raise ToolError(str(exc)) from exc
    data, ext, duration = normalize_audio(data)
    with Session(engine) as s:
        a = _character(s, project_id, asset_id)
        p = s.get(Project, a.project_id)
        m = _store(s, p, f"assets/{a.name}/voz", ext, data, kind="audio", role="voice", asset_id=a.id, duration_ms=duration)
        m.source = "mcp"
        s.add(m)
        s.commit()
        out = {"voice_id": m.id, "character": a.name, "duration_s": round(duration / 1000, 2) if duration else None}
    return {**out, "text": f"Voz {out['voice_id']} carregada na ficha de {out['character']} (por decidir)."}


def _voice(s: Session, project_id: int, asset_id: int, voice_id: int) -> tuple[Asset, MediaFile]:
    a = _character(s, project_id, asset_id)
    m = s.get(MediaFile, voice_id)
    if m is None or m.asset_id != a.id or m.role != "voice":
        raise ToolError(f"A voz {voice_id} não é de {a.name}. Vê list_voices.")
    if m.status == "rejected":
        raise ToolError(f"A voz {voice_id} foi rejeitada e já não existe.")
    return a, m


@server.tool(annotations=WRITE, title="Aprovar voz",
             description="Aprova uma voz como a voz de referência da personagem (a anterior volta a «por decidir»). Só "
                         "quando o utilizador o disser.")
def approve_voice(project_id: int, asset_id: int, voice_id: int) -> str:
    from app.media_service import approve_media

    with Session(engine) as s:
        a, m = _voice(s, project_id, asset_id, voice_id)
        if a.voice_ref_id == m.id:
            return f"A voz {voice_id} já é a de {a.name}."
        approve_media(s, m)
        s.commit()
        return f"Aprovada: a voz {voice_id} é agora a voz de referência de {a.name}."


@server.tool(annotations=DESTRUCTIVE, title="Rejeitar voz",
             description="Rejeita uma voz, como o botão Rejeitar: o ficheiro é apagado.")
def reject_voice(project_id: int, asset_id: int, voice_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        a, m = _voice(s, project_id, asset_id, voice_id)
        reject_media(s, m)
        s.commit()
        return f"Rejeitada e apagada: voz {voice_id} de {a.name}."
