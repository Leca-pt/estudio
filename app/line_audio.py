"""Áudio de falas do guião: ficheiros carregados pelo utilizador (ex.: a narração de um documentário gerada no
ElevenLabs) que cobrem uma ou mais linhas seguidas de uma cena. Ficam para a montagem (export e conector MCP).

Regras:
- Um ficheiro cobre um intervalo de linhas seguidas da cena (da linha X à Y, pela ordem do guião). Uma fala só está num
  áudio de cada vez: carregar um novo que apanhe linhas de outro manda o outro para «Rejeitados».
- Guarda-se o texto das linhas no momento do carregamento. Se o guião mudar depois (texto alterado, linha apagada ou
  posta noutro sítio), o áudio fica desatualizado e diz-se porquê: nunca segue em silêncio. Saídas: carregar outro ou
  «Manter» (o áudio passa a valer para o texto atual — só quando as linhas continuam todas lá e seguidas).
"""

import hashlib

from sqlmodel import Session, select

from app.models import MediaFile, Project, Scene, SceneLine

ROLE = "line_audio"


def scene_lines(session: Session, scene: Scene) -> list[SceneLine]:
    return list(session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)
                             .order_by(SceneLine.order, SceneLine.id)).all())


def basis(lines: list[SceneLine]) -> str:
    text = "\n".join(f"{ln.id}:{(ln.text or '').strip()}" for ln in lines)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def active(session: Session, scene: Scene) -> list[MediaFile]:
    return list(session.exec(select(MediaFile).where(MediaFile.scene_id == scene.id, MediaFile.role == ROLE,
                                                     MediaFile.status == "approved").order_by(MediaFile.id)).all())


def problem(m: MediaFile, lines: list[SceneLine]) -> str | None:
    """Porque é que o áudio já não bate com o guião (None se bate)."""
    pos = {ln.id: n for n, ln in enumerate(lines)}
    ids = [int(i) for i in (m.lines or [])]
    if not ids:
        return "O áudio não está ligado a nenhuma linha."
    if any(i not in pos for i in ids):
        return "Uma das linhas deste áudio foi apagada do guião."
    idx = [pos[i] for i in ids]
    if idx != list(range(idx[0], idx[0] + len(idx))):
        return "As linhas deste áudio já não estão seguidas no guião (mudaram de sítio)."
    if m.basis != basis([lines[i] for i in idx]):
        return "O texto das linhas mudou depois de o áudio ser carregado."
    return None


def clips(session: Session, scene: Scene, lines: list[SceneLine] | None = None) -> list[dict]:
    """Os áudios da cena, pela ordem do guião: {media, ids, first, last (números das linhas, 1…), problem}."""
    lines = lines if lines is not None else scene_lines(session, scene)
    pos = {ln.id: n for n, ln in enumerate(lines, 1)}
    out = []
    for m in active(session, scene):
        nums = [pos[int(i)] for i in (m.lines or []) if int(i) in pos]
        out.append({"media": m, "ids": [int(i) for i in (m.lines or [])], "first": min(nums) if nums else None,
                    "last": max(nums) if nums else None, "problem": problem(m, lines)})
    out.sort(key=lambda c: (c["first"] is None, c["first"] or 0))
    return out


def by_line(session: Session, scene: Scene, lines: list[SceneLine]) -> dict[int, dict]:
    """Para o guião: linha → o áudio que a cobre (com "starts" na primeira linha do intervalo)."""
    out = {}
    for c in clips(session, scene, lines):
        for n, i in enumerate(c["ids"]):
            out[i] = {**c, "starts": n == 0}
    return out


def line_range(lines: list[SceneLine], first_id: int, last_id: int | None) -> list[SceneLine]:
    """As linhas da primeira à última (inclusive), pela ordem do guião; ValueError se não forem desta cena."""
    pos = {ln.id: n for n, ln in enumerate(lines)}
    last_id = last_id or first_id
    if first_id not in pos or last_id not in pos:
        raise ValueError("Essas linhas não são desta cena.")
    a, b = pos[first_id], pos[last_id]
    if b < a:
        a, b = b, a
    return lines[a:b + 1]


def attach(session: Session, project: Project, scene: Scene, first_id: int, last_id: int | None, data: bytes,
           source: str = "upload") -> tuple[MediaFile, list[MediaFile]]:
    """Grava o áudio (convertido como os outros áudios da app) para as linhas first…last e tira os áudios que
    apanhavam alguma dessas linhas. Devolve (novo, substituídos). UploadError/ValueError se não servir."""
    from app.media import audio_extension, normalize_audio
    from app.media_service import reject_media
    from app.routes.files import store_bytes
    from app.media_service import scene_code

    lines = scene_lines(session, scene)
    chosen = line_range(lines, first_id, last_id)
    audio_extension(data, "áudio")
    data, ext, duration = normalize_audio(data)
    ids = [ln.id for ln in chosen]
    replaced = [m for m in active(session, scene) if set(int(i) for i in (m.lines or [])) & set(ids)]
    for old in replaced:
        reject_media(session, old)
    m = store_bytes(session, project, f"cenas/{scene_code(session, scene)}/audio", "audio", ROLE, data, ext,
                    status="approved", duration_ms=duration, scene_id=scene.id)
    m.lines, m.basis, m.source = ids, basis(chosen), source
    session.add(m)
    return m, replaced


def keep(session: Session, scene: Scene, m: MediaFile) -> str | None:
    """«Manter»: o áudio passa a valer para o texto atual das mesmas linhas. Devolve o motivo se não der."""
    lines = scene_lines(session, scene)
    reason = problem(m, lines)
    if reason is None:
        return None
    if "texto" not in reason:
        return reason + " Carrega o áudio outra vez para as linhas certas."
    pos = {ln.id: ln for ln in lines}
    m.basis = basis([pos[int(i)] for i in m.lines])
    session.add(m)
    return None


def file_name(project: Project, code: str, c: dict, ext: str) -> str:
    rng = f"L{c['first']:02d}" + (f"-{c['last']:02d}" if c["last"] != c["first"] else "") if c["first"] else "L"
    return f"{project.prefix}_{code}_{rng}_audio{ext}"


def rejected(session: Session, scene: Scene) -> list[MediaFile]:
    return list(session.exec(select(MediaFile).where(MediaFile.scene_id == scene.id, MediaFile.role == ROLE,
                                                     MediaFile.status == "rejected").order_by(MediaFile.id.desc())).all())


def restore(session: Session, scene: Scene, m: MediaFile) -> str | None:
    """Repor um áudio tirado: volta para as mesmas linhas (se ainda existem) e tira os que as apanham agora."""
    from app.media_service import reject_media

    lines = scene_lines(session, scene)
    have = {ln.id for ln in lines}
    ids = [int(i) for i in (m.lines or [])]
    if not ids or any(i not in have for i in ids):
        return "As linhas deste áudio já não existem no guião: carrega-o outra vez para as linhas certas."
    for old in active(session, scene):
        if old.id != m.id and set(int(i) for i in (old.lines or [])) & set(ids):
            reject_media(session, old)
    m.status = "approved"
    session.add(m)
    return None
