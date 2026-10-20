"""Ferramentas MCP para a montagem fora da app: descarregar o que está aprovado (takes escolhidos por ordem, frames-chave,
imagens e vozes dos assets, áudio das falas) e carregar áudio de falas (ex.: a narração de um documentário gerada no
ElevenLabs), que fica ligado a uma ou mais linhas seguidas do guião (app/line_audio.py)."""

from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlmodel import Session, select

from app.db import engine
from app.mcp_server import PUBLIC, READ, WRITE, _project, log, server
from app.models import Asset, CharacterLook, LocationView, MediaFile, Project, Scene, Shot

LINK_TTL = 6 * 3600  # dá tempo para descarregar tudo para uma montagem
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
EXT_ROUTE = {".png", ".mp4", ".webm", ".mp3", ".wav", ".m4a", ".ogg"}  # as extensões que /pub/ref serve


def _link(m: MediaFile) -> str | None:
    from app.artlist import share
    from app.media import media_root

    path = media_root() / m.path
    if not path.is_file():
        return None
    ext = path.suffix.lower()
    return f"{PUBLIC}/pub/ref/{share(path, ttl_s=LINK_TTL)}{ext if ext in EXT_ROUTE else '.png'}"


def _item(m: MediaFile, name: str, **extra) -> dict[str, Any]:
    url = _link(m)
    out = {"media_id": m.id, "kind": m.kind, "role": m.role, "file_name": name,
           "duration_s": round(m.duration_ms / 1000, 2) if m.duration_ms else None, "url": url}
    if url is None:
        out["missing_file"] = True
    return out | {k: v for k, v in extra.items() if v is not None}


def _scene_items(s: Session, p: Project, scene: Scene, kinds: set[str], counter: list[int]) -> tuple[list, list]:
    from app import keyframe, line_audio
    from app.media_service import scene_code
    from app.routes.production import scene_shots

    code = scene_code(s, scene)
    items, gaps = [], []
    for sh in scene_shots(s, scene):
        ref = {"scene": code, "scene_id": scene.id, "shot": f"P{sh.number:02d}", "shot_id": sh.id}
        clip = s.get(MediaFile, sh.approved_clip_id) if sh.approved_clip_id else None
        if "video" in kinds:
            if clip is not None and clip.status == "approved":
                counter[0] += 1
                ext = PurePosixPath(clip.path).suffix
                items.append(_item(clip, f"{counter[0]:02d}_{p.prefix}_{code}_P{sh.number:02d}{ext}", order=counter[0],
                                   **ref, what="take escolhido"))
            else:
                gaps.append({**ref, "missing": "sem take escolhido"})
        if "image" in kinds:
            for k in keyframe.all_approved(s, sh):
                m = k["media"]
                n = f"_m{k['n']}" if k.get("n") else ""
                items.append(_item(m, f"{p.prefix}_{code}_P{sh.number:02d}_frame-chave{n}{PurePosixPath(m.path).suffix}",
                                   **ref, what="frame-chave aprovado", outdated=True if k.get("stale") else None))
    if "audio" in kinds:
        for c in line_audio.clips(s, scene):
            m = c["media"]
            items.append(_item(m, line_audio.file_name(p, code, c, PurePosixPath(m.path).suffix), scene=code,
                               scene_id=scene.id, what="áudio de falas", lines=f"{c['first']}–{c['last']}" if c["first"] else None,
                               line_ids=c["ids"], outdated=c["problem"]))
    return items, gaps


def _asset_items(s: Session, p: Project, a: Asset, kinds: set[str]) -> list[dict]:
    from app.asset_refs import asset_photos
    from app.media_service import download_name

    items = []

    def add(mid: int | None, what: str) -> None:
        m = s.get(MediaFile, mid) if mid else None
        if m is not None and m.status == "approved" and m.kind in kinds:
            items.append(_item(m, download_name(s, p, m), asset=a.name, what=what))

    add(a.approved_image_id, "folha aprovada")
    add(a.portrait_image_id, "retrato aprovado")
    for lk in s.exec(select(CharacterLook).where(CharacterLook.asset_id == a.id)).all():
        add(lk.image_id, f"look «{lk.name}»")
    for v in s.exec(select(LocationView).where(LocationView.asset_id == a.id)).all():
        add(v.image_id, f"vista «{v.label}»")
    for m in asset_photos(s, a):
        add(m.id, "foto de referência")
    add(a.voice_ref_id, "voz de referência")
    return items


@server.tool(annotations=READ, description=(
    "Links para descarregar o que está aprovado, para montar fora da app (links de 6 h). scope: projeto, ep-N (episódio), "
    "ato-N, cena-N (pela ordem do guião e dos planos, como a Montagem da app), plano-N (um plano) ou asset-N (as imagens, "
    "fotos e voz aprovadas de um asset). kinds: video (o take escolhido de cada plano, numerado pela ordem da montagem), "
    "audio (o áudio das falas carregado no guião, com as linhas que cobre), image (os frames-chave aprovados; num asset, "
    "as imagens). Por omissão video e audio. gaps = planos sem take escolhido; outdated = o áudio ou a imagem já não "
    "bate com o guião ou a câmara (avisa o utilizador antes de o usar)."))
def export_media(project_id: int, scope: str = "projeto", kinds: list[str] | None = None) -> dict[str, Any]:
    from app.routes.montage import _scope_scenes

    want = set(kinds or ["video", "audio"])
    bad = want - {"video", "audio", "image"}
    if bad:
        raise ToolError(f"kinds desconhecidos: {', '.join(sorted(bad))} (usa video, audio, image).")
    with Session(engine) as s:
        p = _project(s, project_id)
        head, _, raw = scope.partition("-")
        if head == "asset" and raw.isdigit():
            a = s.get(Asset, int(raw))
            if a is None or a.project_id != p.id:
                raise ToolError(f"Não existe o asset {raw} neste projeto.")
            items = _asset_items(s, p, a, {"image", "audio"} if kinds is None else want)
            return {"scope": scope, "items": items, "gaps": []}
        counter, items, gaps = [0], [], []
        if head == "plano" and raw.isdigit():
            sh = s.get(Shot, int(raw))
            sc = s.get(Scene, sh.scene_id) if sh else None
            if sc is None or sc.project_id != p.id:
                raise ToolError(f"Não existe o plano {raw} neste projeto.")
            got, gaps = _scene_items(s, p, sc, want, counter)
            items = [i for i in got if i.get("shot_id") == sh.id or (i.get("line_ids") and _covers(s, sh, i["line_ids"]))]
            gaps = [g for g in gaps if g["shot_id"] == sh.id]
            return {"scope": scope, "items": items, "gaps": gaps}
        if head not in ("projeto", "ep", "ato", "cena"):
            raise ToolError("scope: projeto, ep-N, ato-N, cena-N, plano-N ou asset-N.")
        groups, _target = _scope_scenes(s, p, scope)
        for _label, scenes in groups:
            for sc in scenes:
                got, g = _scene_items(s, p, sc, want, counter)
                items += got
                gaps += g
        if not items and not gaps:
            raise ToolError(f"Nada neste âmbito ({scope}).")
        log.info("mcp: export_media %s %s (%d itens)", project_id, scope, len(items))
        return {"scope": scope, "items": items, "gaps": gaps}


def _covers(s: Session, sh: Shot, line_ids: list[int]) -> bool:
    from app.models import ShotLineLink

    mine = set(s.exec(select(ShotLineLink.line_id).where(ShotLineLink.shot_id == sh.id)).all())
    return bool(mine & set(line_ids))


# ── Áudio das falas ───────────────────────────────────────────────────────────

def _scene(s: Session, project_id: int, scene_id: int) -> tuple[Project, Scene]:
    p = _project(s, project_id)
    sc = s.get(Scene, scene_id)
    if sc is None or sc.project_id != p.id:
        raise ToolError(f"Não existe a cena {scene_id} neste projeto.")
    return p, sc


def _audio(s: Session, project_id: int, audio_id: int) -> tuple[Project, Scene, MediaFile]:
    m = s.get(MediaFile, audio_id)
    if m is None or m.role != "line_audio" or not m.scene_id:
        raise ToolError(f"O áudio {audio_id} não existe (vê os [audio:N] em open_screen da cena).")
    p, sc = _scene(s, project_id, m.scene_id)
    return p, sc, m


@server.tool(annotations=WRITE, title="Carregar áudio de falas", description=(
    "Carrega um áudio para uma ou mais linhas SEGUIDAS do guião de uma cena (ex.: a narração gerada no ElevenLabs, que "
    "pode cobrir várias falas partidas): first_line_id e last_line_id são os [line:N] de open_screen(scene:N) (só "
    "first_line_id = só essa linha). audio_url (https) ou audio_base64; MP3, WAV, M4A, OGG ou FLAC até 25 MB (os outros "
    "ficam em MP3). Os áudios que já cobriam alguma dessas linhas vão para «Rejeitados». Fica logo no guião."))
async def upload_line_audio(project_id: int, scene_id: int, first_line_id: int, last_line_id: int | None = None,
                            audio_url: str | None = None, audio_base64: str | None = None) -> str:
    from app import line_audio
    from app.mcp_video import _fetch
    from app.media import MAX_AUDIO_BYTES, UploadError

    data = await _fetch(audio_url, audio_base64, MAX_AUDIO_BYTES, "audio")
    with Session(engine) as s:
        p, sc = _scene(s, project_id, scene_id)
        try:
            m, replaced = line_audio.attach(s, p, sc, first_line_id, last_line_id, data, source="mcp")
        except (UploadError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        s.commit()
        c = next(c for c in line_audio.clips(s, sc) if c["media"].id == m.id)
        rng = f"linha {c['first']}" if c["first"] == c["last"] else f"linhas {c['first']}–{c['last']}"
        log.info("mcp: áudio de falas %s na cena %s (%s)", m.id, scene_id, rng)
        return (f"Áudio guardado: audio_id {m.id}, {rng}"
                + (f", {m.duration_ms / 1000:.1f} s" if m.duration_ms else "") + "."
                + (f" {len(replaced)} áudio(s) que cobriam as mesmas linhas foram para «Rejeitados»." if replaced else ""))


@server.tool(annotations=DESTRUCTIVE, title="Tirar áudio de falas", description=(
    "Tira um áudio de falas do guião, como o botão «Tirar» da app: fica em «Rejeitados» (o utilizador pode repô-lo)."))
def remove_line_audio(project_id: int, audio_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        _p, _sc, m = _audio(s, project_id, audio_id)
        if m.status == "rejected":
            return f"O áudio {audio_id} já tinha sido tirado."
        reject_media(s, m)
        s.commit()
        return f"Áudio {audio_id} tirado (fica em «Rejeitados»)."


@server.tool(annotations=WRITE, title="Manter áudio de falas", description=(
    "Um áudio marcado DESATUALIZADO porque o texto das linhas mudou passa a valer para o texto atual (o «Manter» da "
    "app). Só quando o utilizador disser que o áudio continua certo; se as linhas foram apagadas ou mudaram de sítio, "
    "não dá: carrega-se outro com upload_line_audio."))
def keep_line_audio(project_id: int, audio_id: int) -> str:
    from app import line_audio

    with Session(engine) as s:
        _p, sc, m = _audio(s, project_id, audio_id)
        reason = line_audio.keep(s, sc, m)
        if reason:
            raise ToolError(reason)
        s.commit()
        return f"O áudio {audio_id} fica a valer para o texto atual das linhas."
