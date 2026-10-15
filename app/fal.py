"""fal.ai (chave de API): gerar e editar imagens pela fila da fal (queue.fal.run).

Pedido: POST https://queue.fal.run/<modelo> com o JSON do modelo e o cabeçalho "Authorization: Key <chave>"; a
resposta traz status_url e response_url, que se consultam até o estado ser COMPLETED. As imagens de entrada vão como
data URI (base64) nos campos *_url — a fal aceita-as em todos os modelos, e a app não precisa de endereço público.
"""

import asyncio
import base64
import io
import time
from pathlib import Path

import httpx

QUEUE = "https://queue.fal.run"
MAX_SIDE = 2048  # as entradas vão reduzidas (JPEG): pedidos mais leves, e nenhum modelo precisa de mais


class FalError(RuntimeError):
    pass


def data_uri(path: Path) -> str:
    """Imagem do disco → data URI JPEG (lado maior até 2048 px)."""
    from PIL import Image

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
    except OSError as exc:
        raise FalError(f"Não foi possível ler a imagem de entrada ({path.name}).") from exc
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list) and detail:  # erros de validação do modelo (422): o 1.º, com a entrada em causa
        d = detail[0]
        kind = d.get("type") or ""
        where = ".".join(str(x) for x in d.get("loc", [])[1:])
        msg = str(d.get("msg") or "").strip()
        if kind == "content_policy_violation" or "policy" in msg.lower() or "sensitive" in msg.lower():
            return (f"O modelo recusou o pedido (política de conteúdo){' em ' + where if where else ''}"
                    f"{': ' + msg if msg else ''}.")
        return f"{msg or kind}{' (' + where + ')' if where else ''}".strip()
    if isinstance(detail, str):
        return detail
    return str(data.get("error") or data)[:300]


QUEUE_LIMIT = 300  # s à espera na fila da fal sem começar: desiste (a fal está cheia)


async def upload(key: str, path: Path, content_type: str) -> str:
    """Sobe um ficheiro para o armazenamento da fal (ex.: o vídeo-guia) e devolve o endereço público."""
    async with httpx.AsyncClient(timeout=300) as client:
        tok = await client.post("https://rest.fal.ai/storage/auth/token", params={"storage_type": "fal-cdn-v3"},
                                headers={"Authorization": f"Key {key}"}, json={})
        if tok.status_code >= 400:
            raise FalError(f"Não foi possível enviar o ficheiro para a fal: {_error(tok)}")
        t = tok.json()
        up = await client.post(f"{t['base_url']}/files/upload", content=path.read_bytes(),
                               headers={"Authorization": f"{t['token_type']} {t['token']}", "Content-Type": content_type})
        if up.status_code >= 400:
            raise FalError(f"Não foi possível enviar o ficheiro para a fal: {_error(up)}")
        return up.json()["access_url"]


async def run(key: str, model: str, args: dict, timeout_s: int = 300, on_status=None, output: str = "image") -> bytes:
    """Corre um modelo da fal e devolve o resultado (bytes): a primeira imagem, ou o vídeo (output="video").
    on_status(texto): em que ponto está."""
    headers = {"Authorization": f"Key {key}"}
    started = time.monotonic()
    deadline = started + timeout_s
    began = None  # quando saiu da fila e começou a gerar
    note = on_status or (lambda _t: None)
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(f"{QUEUE}/{model}", json=args, headers=headers)
        except httpx.HTTPError as exc:
            raise FalError(f"Sem ligação à fal.ai ({exc.__class__.__name__}).") from exc
        if resp.status_code == 401:
            raise FalError("A chave da fal.ai foi recusada (Configuração → Chaves de API).")
        if resp.status_code >= 400:
            raise FalError(f"A fal.ai recusou o pedido: {_error(resp)}")
        sub = resp.json()
        status_url, response_url = sub.get("status_url"), sub.get("response_url")
        if not status_url or not response_url:
            raise FalError("A fal.ai não devolveu o pedido na fila.")
        while True:
            if time.monotonic() > deadline:
                if sub.get("cancel_url"):
                    try:
                        await client.put(sub["cancel_url"], headers=headers)
                    except httpx.HTTPError:
                        pass
                raise FalError(f"A fal.ai não terminou em {timeout_s} s.")
            await asyncio.sleep(2)
            try:
                st = await client.get(status_url, headers=headers)
            except httpx.HTTPError:
                continue  # falha de rede passageira: tenta outra vez
            if st.status_code >= 400:
                raise FalError(f"Erro ao consultar a fal.ai: {_error(st)}")
            data = st.json()
            if data.get("error"):
                raise FalError(f"A fal.ai falhou: {data.get('error')}")
            status = data.get("status")
            if status == "COMPLETED":
                break
            if status == "IN_QUEUE":
                pos = data.get("queue_position")
                note("na fila da fal" + (f" (posição {pos + 1})" if isinstance(pos, int) else ""))
                if began is None and time.monotonic() - started > QUEUE_LIMIT:
                    if sub.get("cancel_url"):
                        try:
                            await client.put(sub["cancel_url"], headers=headers)
                        except httpx.HTTPError:
                            pass
                    raise FalError(f"A fila da fal não andou em {QUEUE_LIMIT // 60} min (está cheia). Tenta mais tarde "
                                   "ou outro modelo.")
            elif status == "IN_PROGRESS":
                if began is None:
                    began = time.monotonic()
                note(f"a gerar na fal ({int(time.monotonic() - began)} s)")
        out = await client.get(response_url, headers=headers)
        if out.status_code >= 400:
            raise FalError(f"A fal.ai falhou: {_error(out)}")
        result = out.json()
        if output == "audio":
            url = (result.get("audio") or result.get("audio_file") or {}).get("url")
            if not url:
                raise FalError("A fal.ai não devolveu o áudio.")
            aud = await client.get(url, timeout=120)
            if aud.status_code >= 400:
                raise FalError("Não foi possível descarregar o áudio gerado pela fal.ai.")
            return aud.content
        if output == "video":
            url = (result.get("video") or {}).get("url")
            if not url:
                raise FalError("A fal.ai não devolveu o vídeo.")
            vid = await client.get(url, timeout=600)
            if vid.status_code >= 400:
                raise FalError("Não foi possível descarregar o vídeo gerado pela fal.ai.")
            return vid.content
        images = result.get("images") or ([result["image"]] if isinstance(result.get("image"), dict) else [])
        if not images or not images[0].get("url"):
            raise FalError("A fal.ai não devolveu nenhuma imagem.")
        url = images[0]["url"]
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        img = await client.get(url, timeout=120)
        if img.status_code >= 400:
            raise FalError("Não foi possível descarregar a imagem gerada pela fal.ai.")
        return img.content


async def prices(key: str, endpoints: list[str]) -> dict[str, dict]:
    """Preço atual de cada modelo ({id: {"unit_price", "unit", "currency"}}); vazio se a fal não responder."""
    params = [("endpoint_id", e) for e in endpoints[:50]]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://api.fal.ai/v1/models/pricing", params=params,
                                    headers={"Authorization": f"Key {key}"})
        return {p["endpoint_id"]: p for p in resp.json().get("prices", [])} if resp.status_code == 200 else {}
    except (httpx.HTTPError, ValueError, KeyError):
        return {}
