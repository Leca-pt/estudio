"""ByteDance (BytePlus ModelArk): vídeo com os modelos Seedance pela API direta e registo das personagens na biblioteca
de assets.

Duas autenticações (Configuração → Chaves de API → BytePlus ModelArk):
- a chave de API (Bearer) para gerar: POST /contents/generations/tasks e GET …/tasks/<id> no endereço de dados
  (https://ark.ap-southeast.bytepluses.com/api/v3);
- a Access Key + Secret Key (assinatura HMAC-SHA256 da BytePlus OpenAPI, serviço "ark", versão 2024-01-01) para a
  biblioteca de assets: CreateAssetGroup / CreateAsset / GetAsset / DeleteAsset em ark.ap-southeast-1.byteplusapi.com.

O Seedance recusa caras realistas enviadas por URL (mesmo as personagens geradas). Registadas como assets AIGC (exige
os «Seedance Advanced Creation Rights» e a carta de autorização AIGC assinada na consola), passam a ir como
asset://<id>, que o modelo aceita. Um grupo de assets por personagem.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

log = logging.getLogger("estudio.bytedance")

DATA_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
OPENAPI_HOST = "ark.ap-southeast-1.byteplusapi.com"
REGION, SERVICE, VERSION = "ap-southeast-1", "ark", "2024-01-01"
PROJECT = "default"
ASPECTS = ("16:9", "9:16", "4:3", "3:4", "1:1", "21:9")

# Modelos Seedance da ModelArk (os ids têm de estar ativados na consola: ModelArk → Model activation).
# usd_m: preço por milhão de tokens de saída (aproximado; tokens ≈ largura × altura × 24 × segundos / 1024).
MODELS: dict[str, dict] = {
    "dreamina-seedance-2-0-260128": {
        "name": "Seedance 2.0 · ByteDance", "res": ["480p", "720p", "1080p"], "durations": list(range(4, 16)),
        "max_images": 9, "max_videos": 3, "max_audios": 3, "max_audio_s": 15, "max_files": 12, "usd_m": 7.0},
    "dreamina-seedance-2-0-fast-260128": {
        "name": "Seedance 2.0 Fast · ByteDance", "res": ["480p", "720p"], "durations": list(range(4, 16)),
        "max_images": 9, "max_videos": 3, "max_audios": 3, "max_audio_s": 15, "max_files": 12, "usd_m": None},
    "dreamina-seedance-2-0-mini-260615": {
        "name": "Seedance 2.0 Mini · ByteDance", "res": ["480p", "720p"], "durations": list(range(4, 16)),
        "max_images": 9, "max_videos": 3, "max_audios": 3, "max_audio_s": 15, "max_files": 12, "usd_m": None},
    "dreamina-seedance-2-5-260628": {
        "name": "Seedance 2.5 · ByteDance", "res": ["480p", "720p", "1080p"], "durations": list(range(4, 31)),
        "max_images": 30, "max_videos": 10, "max_audios": 10, "max_audio_s": 30, "max_files": 50, "usd_m": 10.7},
}
SIZES = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}


class ByteDanceError(Exception):
    pass


def keys(stored: dict) -> dict:
    """As credenciais guardadas, em claro (só para os pedidos): key (API), access, secret e o endereço de dados."""
    from app import api_keys

    return {"key": api_keys.get(stored, "byteplus"), "access": api_keys.get(stored, "byteplus", "access"),
            "secret": api_keys.get(stored, "byteplus", "secret"),
            "base_url": (api_keys.get(stored, "byteplus", "base_url") or DATA_URL).rstrip("/")}


def cost_text(model_id: str, res: str, seconds: int) -> str | None:
    usd_m = MODELS.get(model_id, {}).get("usd_m")
    if not usd_m:
        return None
    w, h = SIZES.get(res, SIZES["720p"])
    return f"≈ ${w * h * 24 * seconds / 1024 / 1e6 * usd_m:.2f} ({res}, {seconds} s, estimativa)"


# ── Geração (chave de API) ──────────────────────────────────

def _error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"A ByteDance respondeu {resp.status_code}."
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"{err.get('code') or resp.status_code}: {err.get('message') or ''}".strip()
    meta = (body.get("ResponseMetadata") or {}).get("Error") if isinstance(body, dict) else None
    if isinstance(meta, dict):
        return f"{meta.get('Code')}: {meta.get('Message') or ''}".strip()
    return f"A ByteDance respondeu {resp.status_code}."


async def create_task(creds: dict, body: dict) -> str:
    if not creds.get("key"):
        raise ByteDanceError("Falta a chave de API da BytePlus ModelArk (Configuração → Chaves de API).")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{creds['base_url']}/contents/generations/tasks", json=body,
                                 headers={"Authorization": f"Bearer {creds['key']}"})
    if resp.status_code >= 300:
        raise ByteDanceError(_error(resp))
    return str(resp.json()["id"])


async def get_task(creds: dict, task_id: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{creds['base_url']}/contents/generations/tasks/{task_id}",
                                headers={"Authorization": f"Bearer {creds['key']}"})
    if resp.status_code >= 300:
        raise ByteDanceError(_error(resp))
    return resp.json()


def video_body(model_id: str, prompt: str, aspect: str | None, images: list[str], videos: list[str], audios: list[str],
               frames: bool, duration: int | None, resolution: str, audio: bool) -> dict:
    """O pedido: o texto e as referências pela ordem (imagens, vídeos, áudios — o prompt chama-lhes @image1…,
    @video1, @audio1). frames: imagem inicial (e final) em vez de referências."""
    m = MODELS[model_id]
    content: list[dict] = [{"type": "text", "text": prompt}]
    for n, url in enumerate(images[: m["max_images"]]):
        role = ("first_frame" if n == 0 else "last_frame") if frames else "reference_image"
        content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
    if not frames:
        content += [{"type": "video_url", "video_url": {"url": u}, "role": "reference_video"} for u in videos[: m["max_videos"]]]
        content += [{"type": "audio_url", "audio_url": {"url": u}, "role": "reference_audio"} for u in audios[: m["max_audios"]]]
    ratio = (aspect or "16:9").split(" ")[0]
    body = {"model": model_id, "content": content, "resolution": resolution if resolution in m["res"] else m["res"][-1],
            "ratio": ratio if ratio in ASPECTS else "adaptive", "generate_audio": bool(audio), "watermark": False}
    if duration:
        body["duration"] = min(max(int(duration), m["durations"][0]), m["durations"][-1])
    return body


async def run_video(creds: dict, body: dict, timeout_s: int, task_id: str | None = None, on_task=None,
                    on_status=None) -> tuple[bytes, dict]:
    """Cria o pedido (ou retoma task_id, depois de um reinício: não se paga duas vezes), espera e descarrega.
    Devolve (mp4, a tarefa tal como acabou: usage com os tokens)."""
    if task_id is None:
        task_id = await create_task(creds, body)
        if on_task:
            on_task(task_id)
    states = {"queued": "na fila da ByteDance", "running": "a gerar na ByteDance"}
    end = time.time() + timeout_s
    while True:
        task = await get_task(creds, task_id)
        status = task.get("status")
        if status == "succeeded":
            url = (task.get("content") or {}).get("video_url")
            url = url.get("url") if isinstance(url, dict) else url
            if not url:
                raise ByteDanceError("O pedido acabou sem vídeo.")
            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                resp = await client.get(url)
            if resp.status_code >= 300 or not resp.content:
                raise ByteDanceError(f"Não consegui descarregar o vídeo ({resp.status_code}).")
            return resp.content, task
        if status in ("failed", "cancelled", "expired"):
            err = task.get("error") or {}
            raise ByteDanceError(f"O pedido {task_id} acabou como «{status}»: "
                                 f"{err.get('code') or ''} {err.get('message') or ''}".strip())
        if on_status:
            on_status(states.get(status, status or "à espera"))
        if time.time() > end:
            raise ByteDanceError(f"O vídeo não ficou pronto a tempo (pedido {task_id}: {status}). Pode ainda acabar na "
                                 "ByteDance; volta a pedir mais tarde.")
        await asyncio.sleep(8)


# ── Biblioteca de assets (Access Key + Secret Key) ──────────

def _q(value: str) -> str:
    return quote(value, safe="-_.~")


def _sign(access: str, secret: str, query: dict, payload: bytes, now: datetime) -> dict:
    """Cabeçalhos da assinatura BytePlus OpenAPI V4 (HMAC-SHA256)."""
    date, short = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    digest = hashlib.sha256(payload).hexdigest()
    head = {"content-type": "application/json", "host": OPENAPI_HOST, "x-content-sha256": digest, "x-date": date}
    names = sorted(head)
    canonical = "\n".join(["POST", "/", "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items())),
                           "".join(f"{n}:{head[n]}\n" for n in names), ";".join(names), digest])
    scope = f"{short}/{REGION}/{SERVICE}/request"
    to_sign = "\n".join(["HMAC-SHA256", date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = secret.encode()
    for part in (short, REGION, SERVICE, "request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"Host": OPENAPI_HOST, "X-Date": date, "X-Content-Sha256": digest, "Content-Type": "application/json",
            "Authorization": f"HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"}


async def openapi(creds: dict, action: str, body: dict) -> dict:
    if not creds.get("access") or not creds.get("secret"):
        raise ByteDanceError("Faltam a Access Key e a Secret Key da BytePlus (Configuração → Chaves de API): são elas "
                             "que registam as personagens.")
    query = {"Action": action, "Version": VERSION}
    payload = json.dumps({"ProjectName": PROJECT, **body}, separators=(",", ":"), ensure_ascii=False).encode()
    headers = _sign(creds["access"], creds["secret"], query, payload, datetime.now(timezone.utc))
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"https://{OPENAPI_HOST}/", params=query, content=payload, headers=headers)
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 300 or not isinstance(data, dict) or (data.get("ResponseMetadata") or {}).get("Error"):
        raise ByteDanceError(f"{action}: {_error(resp)}")
    return data.get("Result") or {}


async def create_group(creds: dict, name: str, description: str = "") -> str:
    res = await openapi(creds, "CreateAssetGroup", {"Name": name[:64], "Description": description[:200],
                                                    "GroupType": "AIGC"})
    return str(res["Id"])


async def create_asset(creds: dict, group_id: str, url: str, name: str) -> str:
    res = await openapi(creds, "CreateAsset", {"GroupId": group_id, "URL": url, "AssetType": "Image", "Name": name[:64]})
    return str(res["Id"])


async def get_asset(creds: dict, asset_id: str) -> dict:
    return await openapi(creds, "GetAsset", {"Id": asset_id})


async def delete_asset(creds: dict, asset_id: str) -> None:
    await openapi(creds, "DeleteAsset", {"Id": asset_id})


async def test(stored: dict) -> tuple[str, str]:
    """Teste só de leitura das duas autenticações: listar pedidos de vídeo (chave de API) e grupos de assets (AK/SK)."""
    creds = keys(stored)
    parts, ok = [], True
    if creds["key"]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{creds['base_url']}/contents/generations/tasks", params={"page_size": 1},
                                        headers={"Authorization": f"Bearer {creds['key']}"})
            good = resp.status_code < 300
            parts.append("chave de API válida" if good else f"chave de API recusada ({_error(resp)})")
            ok &= good
        except httpx.HTTPError as exc:
            parts.append(f"sem ligação ({exc.__class__.__name__})")
            ok = False
    else:
        parts.append("falta a chave de API (é ela que gera os vídeos)")
        ok = False
    if creds["access"] and creds["secret"]:
        try:
            await openapi(creds, "ListAssetGroups", {"Filter": {"GroupType": "AIGC"}, "MaxResults": 1})
            parts.append("Access Key / Secret Key válidas (biblioteca de personagens)")
        except (ByteDanceError, httpx.HTTPError) as exc:
            parts.append(f"Access Key / Secret Key: {exc}")
            ok = False
    else:
        parts.append("faltam a Access Key e a Secret Key (registo de personagens)")
        ok = False
    return ("ok" if ok else "fail"), "; ".join(parts) + "."
