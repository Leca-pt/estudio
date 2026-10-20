"""Custo estimado de um take, pedido ao fornecedor antes de gerar.

- Artlist: cotação exata (get_generation_cost, só leitura) para o modelo, a resolução, a duração e o som, em créditos.
  Demora uns segundos (passa pelo agente), por isso fica guardada (Configuração "video_quotes") e reaproveita-se.
- fal.ai: o preço por mil tokens da API de preços da fal × os tokens do clip (largura × altura × 24 × s / 1024), em USD.
- ByteDance: estimativa pela tabela (a API não cota).
Também o custo de passar um rascunho 480p do Seedance 2.5 a 1080p (Artlist, kind "draftRender").
"""

import asyncio
import logging
import time

import httpx
from sqlmodel import Session

from app.db import engine
from app.options import get_config, set_config

log = logging.getLogger("estudio.cost")
TTL = 7 * 24 * 3600
PIXELS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
_locks: dict[tuple, asyncio.Lock] = {}


def _lock(key: str) -> asyncio.Lock:
    """Um lock por pedido e por ciclo de eventos (o open_screen do conector cota noutro ciclo, numa thread)."""
    return _locks.setdefault((id(asyncio.get_running_loop()), key), asyncio.Lock())
_fal_prices: dict[str, tuple[float, float]] = {}  # endpoint → (USD por mil tokens, quando)


def _key(model: dict, res: str, seconds: int, audio: bool, frames: bool = False) -> str:
    return (f"{model.get('provider')}|{model.get('group') or model.get('key')}|{res}|{seconds}|{int(bool(audio))}"
            + ("|frames" if frames else ""))


def cached(model: dict | None, res: str, seconds: int | None, audio: bool, frames: bool = False) -> dict | None:
    """A cotação já guardada (sem pedir nada ao fornecedor), ou None."""
    if not model or not seconds:
        return None
    if model.get("provider") == "bytedance":
        return _bytedance(model, res, seconds)
    with Session(engine) as s:
        q = (get_config(s, "video_quotes", {}) or {}).get(_key(model, res, seconds, audio, frames))
    return q if q and q.get("at", 0) > time.time() - TTL else None


def _save(key: str, quote: dict) -> None:
    with Session(engine) as s:
        quotes = dict(get_config(s, "video_quotes", {}) or {})
        quotes[key] = quote
        if len(quotes) > 400:  # as mais antigas saem
            quotes = dict(sorted(quotes.items(), key=lambda kv: kv[1].get("at", 0))[-300:])
        set_config(s, "video_quotes", quotes)
        s.commit()


def _bytedance(model: dict, res: str, seconds: int) -> dict | None:
    from app import bytedance

    text = bytedance.cost_text(model.get("key") or "", res, seconds)
    return {"text": text, "credits": None, "usd": None, "source": "estimativa", "at": time.time()} if text else None


async def _fal(model: dict, res: str, seconds: int, frames: bool = False, audio: bool = True) -> dict | None:
    from app import api_keys, image_models

    m = image_models.FAL_VIDEO.get(model.get("key") or "") or {}
    if m and "per_ktok" not in m:  # cobrados por segundo (ou com/sem som): a tabela da página do modelo
        usd = image_models.fal_video_cost(model["key"], res, seconds, audio)
        return {"text": f"≈ ${usd:.2f} na fal", "credits": None, "usd": usd, "source": "tabela", "at": time.time()}
    endpoint = (m.get("i2v") if frames else m.get("refs")) or m.get("refs") or m.get("i2v")
    if not endpoint:
        return None
    price = _fal_prices.get(endpoint)
    if not price or price[1] < time.time() - 24 * 3600:
        with Session(engine) as s:
            key = api_keys.get(get_config(s, "api_keys", {}) or {}, "fal")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get("https://api.fal.ai/v1/models/pricing", params={"endpoint_id": endpoint},
                                     headers={"Authorization": f"Key {key}"})
            p = next((x for x in r.json().get("prices", []) if x.get("unit") == "1000 tokens"), None)
            if p is None:
                return None
            price = (float(p["unit_price"]), time.time())
            _fal_prices[endpoint] = price
        except (httpx.HTTPError, ValueError, KeyError):
            return None
    w, h = PIXELS.get(res, PIXELS["720p"])
    usd = round(price[0] * w * h * 24 * seconds / 1024 / 1000, 2)
    return {"text": f"≈ ${usd:.2f} na fal", "credits": None, "usd": usd, "source": "fal", "at": time.time()}


async def quote(model: dict | None, res: str, seconds: int | None, audio: bool, aspect: str | None = None,
                frames: bool = False) -> dict | None:
    """{"text", "credits", "usd", "source"} do take, pedindo ao fornecedor se ainda não está guardado."""
    if not model or not seconds:
        return None
    hit = cached(model, res, seconds, audio, frames)
    if hit:
        return hit
    provider = model.get("provider")
    if provider == "fal":
        q = await _fal(model, res, seconds, frames, audio)
        if q:
            _save(_key(model, res, seconds, audio, frames), q)
        return q
    if provider != "artlist":
        return None
    from app import artlist

    key = _key(model, res, seconds, audio, frames)
    async with _lock(key):  # o mesmo pedido de duas páginas: uma cotação só
        hit = cached(model, res, seconds, audio, frames)
        if hit:
            return hit
        group = int(model["group"])
        settings = artlist.video_settings(group, aspect, res, seconds, audio, frames=frames)
        try:
            credits = await artlist.quote({"kind": "video", "modelGroupId": group, "settings": settings, "prompt": "video"})
        except Exception as exc:  # noqa: BLE001 — sem cotação, a página continua
            log.warning("cotação do Artlist falhou: %s", exc)
            return None
        if credits is None:
            return None
        q = {"text": f"{credits} créditos", "credits": credits, "usd": None, "source": "Artlist", "at": time.time()}
        _save(key, q)
        return q


async def draft_quote(generation_id: str) -> int | None:
    """Créditos para passar um rascunho do Seedance 2.5 a 1080p (guardado por rascunho)."""
    from app import artlist

    key = f"draft|{generation_id}"
    with Session(engine) as s:
        q = (get_config(s, "video_quotes", {}) or {}).get(key)
    if q and q.get("at", 0) > time.time() - TTL:
        return q.get("credits")
    async with _lock(key):
        try:
            credits = await artlist.quote({"kind": "draftRender", "generationId": generation_id})
        except Exception as exc:  # noqa: BLE001
            log.warning("cotação do rascunho falhou: %s", exc)
            return None
        if credits is not None:
            _save(key, {"credits": credits, "at": time.time()})
        return credits


_prefetching: set[str] = set()


def prefetch(model: dict | None, res: str, seconds: int | None, audio: bool, aspect: str | None = None,
             frames: bool = False) -> None:
    """Pede a cotação em segundo plano (para quem não pode esperar, ex.: o open_screen do conector)."""
    import threading

    if not model or not seconds or cached(model, res, seconds, audio, frames):
        return
    key = _key(model, res, seconds, audio, frames)
    if key in _prefetching:
        return
    _prefetching.add(key)

    def run() -> None:
        try:
            asyncio.run(quote(model, res, seconds, audio, aspect, frames))
        finally:
            _prefetching.discard(key)

    threading.Thread(target=run, daemon=True).start()



# ── Imagens: custo por modelo e resolução ─────────────────────────────────────

async def image_quotes(pairs: list[tuple[int, str]], wait_s: int = 90) -> dict[tuple[int, str], int]:
    """{(grupo do Artlist, "1K"/"2K"/"4K"): créditos}, com as que faltam pedidas ao Artlist numa só passagem (guardadas
    7 dias). Se o Artlist demorar mais do que wait_s, devolve as que já houver (as outras ficam a ser pedidas)."""
    from app import artlist

    with Session(engine) as s:
        saved = get_config(s, "video_quotes", {}) or {}
    out, missing = {}, []
    for group, res in pairs:
        q = saved.get(f"img|{group}|{res}")
        if q and q.get("at", 0) > time.time() - TTL:
            out[(group, res)] = q["credits"]
        else:
            missing.append((group, res))
    if not missing:
        return out
    args = [{"kind": "image", "modelGroupId": g, "prompt": "image", "settings": artlist.settings_for(g, "16:9", r)}
            for g, r in missing]

    async def fetch() -> None:
        async with _lock("img-quotes"):
            for data in await artlist.quote_many(args):
                g = int(data.get("modelGroupId") or 0)
                got = data.get("resolvedSettings") or {}
                r = (artlist.RESOLUTIONS.get(g) or {})
                value = got.get(r.get("key")) if r.get("key") else None
                res = next((k for k, v in (r.get("values") or {}).items() if str(v).lower() == str(value).lower()), None)
                if res:
                    _save(f"img|{g}|{res}", {"credits": int(data["cost"]), "at": time.time()})

    task = asyncio.ensure_future(fetch())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=wait_s)
    except asyncio.TimeoutError:
        pass
    with Session(engine) as s:
        saved = get_config(s, "video_quotes", {}) or {}
    for group, res in missing:
        q = saved.get(f"img|{group}|{res}")
        if q:
            out[(group, res)] = q["credits"]
    return out



# ── O custo de um plano, com os mesmos inputs do pedido real ─────────────────

def shot_args(session: Session, shot) -> dict:
    """Tudo o que muda o preço do próximo take do plano: o modelo, a resolução, a duração, o som, o tipo de pedido e os
    ficheiros que seguem (as imagens, o vídeo-guia e as vozes ligados em video_inputs). "sig" resume-os (chave da
    cotação: mudar qualquer um pede uma cotação nova)."""
    from app.media import media_root
    from app.models import Project, Scene
    from app.options import video_settings
    from app.routes.video import request_plan, shot_model, shot_resolution

    model = shot_model(session, shot)
    plan = request_plan(session, shot, model)
    project = session.get(Project, session.get(Scene, shot.scene_id).project_id)
    frames = plan["mode"] == "frames"
    images = [(media_root() / m.path, f"img{m.id}") for m in plan["images"]]
    videos = [(media_root() / plan["guide"].path, "guia", plan["guide"].duration_ms or 0)] if plan["guide"] else []
    audios = [(media_root() / m.path, f"voz{m.id}", m.duration_ms or 0) for m in plan["voices"]]
    sig = f"{'frames' if frames else 'refs'}|i{len(images)}|v" + ",".join(str(round(d / 1000)) for _p, _l, d in videos) \
          + "|a" + ",".join(str(round(d / 1000)) for _p, _l, d in audios)
    return {"model": model, "res": shot_resolution(session, shot, model), "seconds": shot.duration_s,
            "audio": video_settings(session)["audio"], "aspect": (project.aspect_ratio or "9:16").split(" ")[0],
            "frames": frames, "images": images, "videos": videos, "audios": audios, "sig": sig}


def _skey(a: dict) -> str:
    return _key(a["model"], a["res"], a["seconds"], a["audio"], a["frames"]) + "|" + a["sig"]


def shot_cached(a: dict) -> dict | None:
    """A cotação já guardada para exatamente este pedido (None = «por cotar»)."""
    if not a["model"] or not a["seconds"]:
        return None
    if a["model"].get("provider") != "artlist":
        return cached(a["model"], a["res"], a["seconds"], a["audio"], a["frames"])
    with Session(engine) as s:
        q = (get_config(s, "video_quotes", {}) or {}).get(_skey(a))
    return q if q and q.get("at", 0) > time.time() - TTL else None


def _text(total: int, base: int | None) -> str:
    if base is not None and base != total:
        return f"{total} créditos ({base} + vídeo-guia {total - base})"
    return f"{total} créditos"


async def shot_quote(a: dict) -> dict | None:
    """A cotação do próximo take, pedida ao fornecedor com os mesmos inputs do pedido real (Artlist: sobe os ficheiros e
    cota com eles, e sem o vídeo-guia para mostrar o que ele custa)."""
    if not a["model"] or not a["seconds"]:
        return None
    if a["model"].get("provider") != "artlist":
        return await quote(a["model"], a["res"], a["seconds"], a["audio"], a["aspect"], a["frames"])
    hit = shot_cached(a)
    if hit:
        return hit
    from app import artlist
    from app.config import settings as app_settings

    key = _skey(a)
    async with _lock(key):
        hit = shot_cached(a)
        if hit:
            return hit
        group = int(a["model"]["group"])
        st = artlist.video_settings(group, a["aspect"], a["res"], a["seconds"], a["audio"], frames=a["frames"])
        try:
            r = await artlist.quote_request(group, st, a["images"], [(p, lab) for p, lab, _d in a["videos"]],
                                            [(p, lab) for p, lab, _d in a["audios"]], a["frames"],
                                            app_settings.public_url.rstrip("/"))
        except Exception as exc:  # noqa: BLE001 — sem cotação, a página continua («por cotar»)
            log.warning("cotação do pedido falhou: %s", exc)
            return None
        q = {"text": _text(r["total"], r["without_video"]), "credits": r["total"], "base": r["without_video"],
             "usd": None, "source": "Artlist", "feature": r.get("feature"), "at": time.time()}
        _save(key, q)
        return q


def shot_prefetch(a: dict) -> None:
    """Pede a cotação do plano em segundo plano (para o open_screen do conector, que não pode esperar)."""
    import threading

    if not a["model"] or not a["seconds"] or shot_cached(a):
        return
    key = "shot|" + _skey(a)
    if key in _prefetching:
        return
    _prefetching.add(key)

    def run() -> None:
        try:
            asyncio.run(shot_quote(a))
        finally:
            _prefetching.discard(key)

    threading.Thread(target=run, daemon=True).start()
