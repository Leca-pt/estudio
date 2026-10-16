"""Assistente pelo OpenRouter (opção à subscrição Claude): mesma resposta estruturada {reply, proposals}."""

import base64
import io
import json
import re
import time

import httpx

from app.assistant.llm import LLMError, SCHEMA

API = "https://openrouter.ai/api/v1"
_models_cache: tuple[float, list[dict]] | None = None
SKIP = ("lyria", "content-safety", "guard")  # música, moderação: não servem de assistente


def models(free_only: bool = True) -> list[dict]:
    """Modelos do OpenRouter (em cache 1 h): [{id, name, free, images, json, context}]."""
    global _models_cache
    if _models_cache and time.time() - _models_cache[0] < 3600:
        data = _models_cache[1]
    else:
        try:
            data = httpx.get(f"{API}/models", timeout=20).json().get("data", [])
            _models_cache = (time.time(), data)
        except (httpx.HTTPError, ValueError):
            data = _models_cache[1] if _models_cache else []
    out = []
    for m in data:
        pricing = m.get("pricing") or {}
        free = str(pricing.get("prompt")) == "0" and str(pricing.get("completion")) == "0"
        if (free_only and not free) or any(s in m.get("id", "") for s in SKIP):
            continue
        params = m.get("supported_parameters") or []
        out.append({"id": m["id"], "name": m.get("name") or m["id"], "free": free,
                    "images": "image" in ((m.get("architecture") or {}).get("input_modalities") or []),
                    "json": "structured_outputs" in params or "response_format" in params,
                    "context": m.get("context_length") or 0})
    return sorted(out, key=lambda x: (not x["json"], not x["images"], x["name"].lower()))


def _image_url(path) -> str | None:
    from PIL import Image

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
    except OSError:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError("O modelo não devolveu o formato esperado.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise LLMError("O modelo não devolveu o formato esperado.")
    if not isinstance(data, dict) or "reply" not in data:
        raise LLMError("O modelo não devolveu o formato esperado.")
    data.setdefault("proposals", [])
    return data


async def ask(key: str, system: str, prompt: str, model: str, timeout_s: int, images: list | None = None,
              referer: str = "") -> dict:
    info = next((m for m in models(free_only=False) if m["id"] == model), None)
    content: list = []
    if images and (info is None or info["images"]):
        for n, (path, label) in enumerate(images, start=1):
            url = _image_url(path)
            if url:
                content += [{"type": "text", "text": f"Image {n}: {label}"}, {"type": "image_url", "image_url": {"url": url}}]
    content.append({"type": "text", "text": prompt + "\n\nResponde só com um objeto JSON válido com as chaves "
                                                     "\"reply\" (texto) e \"proposals\" (lista de {field, value, reason})."})
    from app import i18n

    body = {"model": model, "messages": [{"role": "system", "content": system + i18n.assistant_note()},
                                         {"role": "user", "content": content}]}
    if info is None or info["json"]:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "assistant_reply", "strict": True, "schema": SCHEMA}}
    headers = {"Authorization": f"Bearer {key}", "X-Title": "Estudio"}  # cabeçalhos HTTP: só ASCII
    if referer:
        headers["HTTP-Referer"] = referer
    fallback_used = None
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(f"{API}/chat/completions", json=body, headers=headers)
            if resp.status_code == 400 and "response_format" in body:  # modelo sem saída estruturada: tenta sem
                body.pop("response_format")
                resp = await client.post(f"{API}/chat/completions", json=body, headers=headers)
            if resp.status_code == 429 and _upstream(resp) and model != FALLBACK:
                # O modelo grátis está sobrecarregado no fornecedor: tenta o router grátis do OpenRouter.
                fallback_used = FALLBACK
                body["model"] = FALLBACK
                resp = await client.post(f"{API}/chat/completions", json=body, headers=headers)
    except httpx.TimeoutException:
        raise LLMError(f"O OpenRouter não respondeu em {timeout_s} s.")
    except httpx.HTTPError as exc:
        raise LLMError(f"Sem ligação ao OpenRouter ({exc.__class__.__name__}).")
    if resp.status_code == 429:
        if _upstream(resp):
            raise LLMError(f"O modelo {model} está sobrecarregado no OpenRouter neste momento (acontece muito nos "
                           "modelos grátis; não é o teu limite diário). Tenta daqui a pouco, escolhe outro modelo ou "
                           "volta ao Claude na Configuração → Assistente.")
        raise LLMError("Limite diário do OpenRouter atingido (no plano grátis são 50 pedidos por dia). Volta ao "
                       "Claude na Configuração → Assistente, ou tenta amanhã.")
    if resp.status_code == 402:
        raise LLMError(f"Sem saldo no OpenRouter para o modelo {model} (é pago). Carrega saldo em openrouter.ai, "
                       "escolhe um modelo grátis ou volta ao Claude na Configuração → Assistente.")
    if resp.status_code in (401, 403):
        raise LLMError("O OpenRouter recusou a chave (Configuração → Chaves de API).")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = resp.text[:200]
        raise LLMError(f"O OpenRouter respondeu {resp.status_code}: {detail[:200]}")
    try:
        message = resp.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError):
        raise LLMError("Resposta do OpenRouter ilegível.")
    data = _parse(message.get("content") or "")
    if fallback_used:
        data["reply"] = (data.get("reply") or "") + f"\n\n(Respondido por outro modelo grátis: {model} estava sobrecarregado.)"
    return data


FALLBACK = "openrouter/free"


def _upstream(resp) -> bool:
    try:
        raw = str(resp.json().get("error", {}).get("metadata", {}).get("raw", ""))
    except ValueError:
        return False
    return "upstream" in raw or "temporarily" in raw
