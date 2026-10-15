"""Registo das personagens na biblioteca de assets da ByteDance (BytePlus ModelArk).

O Seedance recusa caras realistas enviadas por URL; as imagens registadas como assets AIGC vão como asset://<id>.
Regista-se cada imagem aprovada de uma personagem (folha, retrato, folhas dos looks): à mão, na página da personagem,
ou sozinho quando um vídeo da ByteDance as leva (o registo é gratuito; a geração continua a ser o único passo pago).
Um grupo de assets por personagem; uma imagem nova é um asset novo (a anterior fica até ser rejeitada).
"""

import asyncio
import logging
import time

from sqlmodel import Session, select

from app import bytedance
from app.config import settings
from app.db import engine
from app.media import media_root
from app.models import Asset, CharacterLook, CharacterRegistration, MediaFile, Project

log = logging.getLogger("estudio.registry")
_locks: dict[int, asyncio.Lock] = {}  # uma personagem de cada vez (o grupo cria-se uma só vez)


def character_of(session: Session, media: MediaFile) -> Asset | None:
    """A personagem desta imagem (folha, retrato ou folha de um look), ou None se não é de uma personagem."""
    if media.kind != "image" or media.role not in ("sheet", "portrait", "look", ""):
        return None
    asset_id = media.asset_id
    if media.look_id:
        look = session.get(CharacterLook, media.look_id)
        asset_id = look.asset_id if look else asset_id
    asset = session.get(Asset, asset_id) if asset_id else None
    return asset if asset is not None and asset.kind == "character" else None


def images_of(session: Session, asset: Asset) -> list[tuple[MediaFile, str]]:
    """As imagens aprovadas da personagem que vão para os vídeos: folha, retrato e folhas dos looks."""
    out = []
    for mid, label in ((asset.approved_image_id, "Folha"), (asset.portrait_image_id, "Retrato")):
        m = session.get(MediaFile, mid) if mid else None
        if m is not None and m.status == "approved":
            out.append((m, label))
    for lk in session.exec(select(CharacterLook).where(CharacterLook.asset_id == asset.id)
                           .order_by(CharacterLook.order, CharacterLook.id)).all():
        m = session.get(MediaFile, lk.image_id) if lk.image_id else None
        if m is not None and m.status == "approved":
            out.append((m, f"Look «{lk.name}»"))
    return out


def registration(session: Session, media_id: int) -> CharacterRegistration | None:
    return session.exec(select(CharacterRegistration).where(CharacterRegistration.media_id == media_id)
                        .order_by(CharacterRegistration.id.desc())).first()


def status_rows(session: Session, asset: Asset) -> list[dict]:
    """Para a página da personagem: cada imagem aprovada e o estado do registo."""
    rows = []
    for m, label in images_of(session, asset):
        r = registration(session, m.id)
        rows.append({"media": m, "label": label, "status": r.status if r else "none", "error": r.error if r else None,
                     "remote_id": r.remote_id if r else ""})
    return rows


def _creds() -> dict:
    from app.options import get_config

    with Session(engine) as s:
        return bytedance.keys(get_config(s, "api_keys", {}) or {})


_next_create = [0.0]  # o escalão gratuito (Entry) aceita 3 CreateAsset por minuto


async def _spaced() -> None:
    wait = _next_create[0] - time.time()
    _next_create[0] = max(time.time(), _next_create[0]) + 21
    if wait > 0:
        await asyncio.sleep(wait)


async def cleanup(asset_id: int, creds: dict) -> int:
    """Apaga na ByteDance os assets de imagens que já não estão aprovadas nesta personagem (o escalão gratuito só tem
    50 assets no total). Devolve quantos apagou."""
    with Session(engine) as s:
        asset = s.get(Asset, asset_id)
        keep = {m.id for m, _l in images_of(s, asset)} if asset else set()
        old = [(r.id, r.remote_id) for r in s.exec(select(CharacterRegistration).where(
            CharacterRegistration.asset_id == asset_id)).all() if r.media_id not in keep and r.remote_id]
    n = 0
    for row_id, remote in old:
        try:
            await bytedance.delete_asset(creds, remote)
        except bytedance.ByteDanceError as exc:
            if "NotFound" not in str(exc):
                log.warning("não apaguei o asset %s: %s", remote, exc)
                continue
        with Session(engine) as s:
            r = s.get(CharacterRegistration, row_id)
            if r is not None:
                s.delete(r)
                s.commit()
        n += 1
    return n


async def _register(media_id: int, creds: dict) -> None:
    """Cria o asset (e o grupo da personagem, a primeira vez). Fica «processing» até a ByteDance o aceitar."""
    from app.artlist import share

    with Session(engine) as s:
        m = s.get(MediaFile, media_id)
        asset = character_of(s, m) if m else None
        if asset is None:
            raise bytedance.ByteDanceError("Essa imagem não é de uma personagem.")
        project = s.get(Project, asset.project_id)
        group = s.exec(select(CharacterRegistration.group_id).where(
            CharacterRegistration.asset_id == asset.id, CharacterRegistration.group_id != "")).first()
        path, name, asset_id, project_id = media_root() / m.path, f"{asset.name}_{m.role or 'sheet'}_{m.id}", asset.id, project.id
        group_name, desc = asset.name, f"Estúdio · {project.name} · {asset.label or asset.name}"
    if not path.is_file():
        raise bytedance.ByteDanceError("O ficheiro da imagem já não existe.")
    await cleanup(asset_id, creds)  # liberta a quota antes de ocupar mais
    if not group:
        group = await bytedance.create_group(creds, group_name, desc)
    await _spaced()
    url = f"{settings.public_url.rstrip('/')}/pub/ref/{share(path, ttl_s=3600)}.png"
    remote = await bytedance.create_asset(creds, group, url, name)
    with Session(engine) as s:
        s.add(CharacterRegistration(project_id=project_id, asset_id=asset_id, media_id=media_id, group_id=group,
                                    remote_id=remote, status="processing"))
        s.commit()
    log.info("personagem %s: imagem %s registada como %s (grupo %s)", asset_id, media_id, remote, group)


async def _refresh(row_id: int, creds: dict) -> str:
    with Session(engine) as s:
        r = s.get(CharacterRegistration, row_id)
        remote = r.remote_id
    try:
        info = await bytedance.get_asset(creds, remote)
        status = str(info.get("Status") or "").lower()
        error = None
        if status == "failed":
            mod = info.get("Moderation") or {}
            error = str(mod.get("Reason") or mod.get("Message") or info.get("FailReason") or mod or "recusada pela ByteDance")[:300]
    except bytedance.ByteDanceError as exc:
        status, error = "failed", str(exc)[:300]
    with Session(engine) as s:
        r = s.get(CharacterRegistration, row_id)
        r.status = "active" if status == "active" else "failed" if status == "failed" else "processing"
        r.error = error
        s.add(r)
        s.commit()
        return r.status


async def ensure(media_ids: list[int], wait_s: int = 600, on_status=None) -> dict[int, str]:
    """Garante que estas imagens estão registadas e ativas. Devolve {media_id: asset_id} das que são de personagens
    (as outras seguem por URL). Erro se alguma é recusada ou não fica ativa a tempo."""
    creds = _creds()
    with Session(engine) as s:
        chars = {}
        for mid in media_ids:
            m = s.get(MediaFile, mid)
            a = character_of(s, m) if m else None
            if a is not None:
                chars[mid] = a.id
    if not chars:
        return {}
    for mid, aid in chars.items():
        async with _locks.setdefault(aid, asyncio.Lock()):
            with Session(engine) as s:
                r = registration(s, mid)
                todo = r is None or r.status == "failed"
            if todo:
                if on_status:
                    on_status("a registar as personagens na ByteDance")
                await _register(mid, creds)
    end = time.time() + wait_s
    while True:
        with Session(engine) as s:
            rows = {mid: registration(s, mid) for mid in chars}
        pending = [r for r in rows.values() if r.status == "processing"]
        for r in pending:
            await _refresh(r.id, creds)
        with Session(engine) as s:
            rows = {mid: registration(s, mid) for mid in chars}
            names = {mid: (s.get(Asset, aid).name if s.get(Asset, aid) else str(aid)) for mid, aid in chars.items()}
        failed = [(mid, r) for mid, r in rows.items() if r.status == "failed"]
        if failed:
            raise bytedance.ByteDanceError("A ByteDance recusou o registo de " + ", ".join(
                f"{names[mid]} (imagem {mid}: {r.error})" for mid, r in failed) + ". O vídeo não foi pedido.")
        if all(r.status == "active" for r in rows.values()):
            return {mid: r.remote_id for mid, r in rows.items()}
        if time.time() > end:
            raise bytedance.ByteDanceError("O registo das personagens na ByteDance ainda não acabou: tenta daqui a "
                                           "uns minutos (o vídeo não foi pedido).")
        if on_status:
            on_status("à espera que a ByteDance aceite as personagens")
        await asyncio.sleep(10)


async def register_character(asset_id: int) -> str:
    """O botão «Registar na ByteDance» da personagem: todas as imagens aprovadas (as que já estão ficam)."""
    with Session(engine) as s:
        asset = s.get(Asset, asset_id)
        ids = [m.id for m, _l in images_of(s, asset)] if asset else []
    if not ids:
        return "Esta personagem ainda não tem imagens aprovadas."
    try:
        await ensure(ids, wait_s=240)
    except bytedance.ByteDanceError as exc:
        return str(exc)
    return f"{len(ids)} imagem(ns) registada(s) e ativa(s) na ByteDance."


_tasks: set[asyncio.Task] = set()


def start_registration(asset_id: int) -> None:
    """Regista em segundo plano (a página mostra o estado; o registo pode demorar alguns minutos)."""
    task = asyncio.get_event_loop().create_task(register_character(asset_id))
    _tasks.add(task)
    task.add_done_callback(lambda t: (_tasks.discard(t), log.info("registo da personagem %s: %s", asset_id,
                                                                  t.result() if not t.exception() else t.exception())))
