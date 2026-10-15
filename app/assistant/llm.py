"""Chamada ao Claude Code CLI em modo não interativo (`claude -p`).

Sem ferramentas (--tools ""), sem definições nem MCP de terceiros, sem sessões gravadas,
com timeout, numa pasta temporária vazia e com o utilizador sem privilégios do contentor.
A resposta é JSON validado por --json-schema."""

import asyncio
import json
import os
import shutil
import tempfile

from app.config import settings


class LLMError(RuntimeError):
    pass


SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Resposta curta ao utilizador, em português europeu."},
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "description": "Chave exata de um dos campos permitidos."},
                    "value": {"type": "string", "description": "Valor completo proposto para o campo."},
                    "reason": {"type": "string", "description": "Porquê, numa frase, em português europeu."},
                },
                "required": ["field", "value"],
            },
        },
    },
    "required": ["reply", "proposals"],
}


def cli_path() -> str | None:
    return shutil.which(settings.claude_bin) or (settings.claude_bin if os.path.isfile(settings.claude_bin) else None)


def configured() -> bool:
    """Há algum motor disponível: o Claude Code (subscrição) ou uma chave do OpenRouter."""
    if cli_path() and (settings.claude_code_oauth_token or settings.anthropic_api_key):
        return True
    try:
        from sqlmodel import Session

        from app import api_keys
        from app.db import engine
        from app.options import get_config

        with Session(engine) as session:
            return bool(api_keys.get(get_config(session, "api_keys", {}) or {}, "openrouter"))
    except Exception:
        return False


ALIASES = [("sonnet", "Sonnet · sempre o mais recente", "equilibrado, recomendado"),
           ("opus", "Opus · sempre o mais recente", "o mais capaz, mais lento"),
           ("haiku", "Haiku · sempre o mais recente", "rápido e leve")]
_models_cache: tuple[float, list[dict]] | None = None


def models() -> list[dict]:
    """Modelos Claude a que a subscrição (ou a chave) tem acesso, do mais recente para o mais antigo: [{id, name}].
    Pedido à Anthropic e guardado 1 h; sem ligação, devolve a última lista (ou nenhuma: ficam só os atalhos)."""
    import time

    import httpx

    global _models_cache
    if _models_cache and time.time() - _models_cache[0] < 3600:
        return _models_cache[1]
    headers = {"anthropic-version": "2023-06-01"}
    if settings.claude_code_oauth_token:
        headers |= {"Authorization": f"Bearer {settings.claude_code_oauth_token}", "anthropic-beta": "oauth-2025-04-20"}
    elif settings.anthropic_api_key:
        headers["x-api-key"] = settings.anthropic_api_key
    else:
        return []
    try:
        data = httpx.get("https://api.anthropic.com/v1/models?limit=100", headers=headers, timeout=15).json()["data"]
        out = [{"id": m["id"], "name": m.get("display_name") or m["id"]} for m in data if m.get("id", "").startswith("claude")]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return _models_cache[1] if _models_cache else []
    _models_cache = (time.time(), out)
    return out


def valid_model(model: str) -> bool:
    import re

    return model in {a[0] for a in ALIASES} or bool(re.fullmatch(r"claude-[a-z0-9.-]{1,60}", model or ""))


MAX_IMAGE_SIDE = 1024


def _image_block(path) -> dict | None:
    """Imagem reduzida (JPEG, lado maior 1024) para o Claude ver; None se o ficheiro não existir."""
    import base64
    import io

    from PIL import Image

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
    except OSError:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


async def ask(system: str, prompt: str, model: str, timeout_s: int, images: list | None = None) -> dict:
    """images: [(caminho, rótulo)] — imagens aprovadas do ecrã, que o Assistente vê (só leitura)."""
    binary = cli_path()
    if not binary:
        raise LLMError("O Claude Code CLI não está instalado no contentor.")
    if not (settings.claude_code_oauth_token or settings.anthropic_api_key):
        raise LLMError("Falta a autenticação do Claude (CLAUDE_CODE_OAUTH_TOKEN no .env).")
    workdir = tempfile.mkdtemp(prefix="llm-")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:" + os.path.dirname(binary),
        "HOME": workdir,  # sem acesso a configurações de ninguém
        "LANG": "C.UTF-8",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if settings.claude_code_oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token
    else:
        env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    args = [
        binary, "-p",
        "--tools", "",
        "--restricted",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format", "stream-json" if images else "json",
        *(["--input-format", "stream-json", "--verbose"] if images else []),
        "--json-schema", json.dumps(SCHEMA),
        "--system-prompt", system,
        "--model", model,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=workdir, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            if images:
                content = []
                for n, (path, label) in enumerate(images, start=1):
                    block = _image_block(path)
                    if block:
                        content += [{"type": "text", "text": f"Image {n}: {label}"}, block]
                content.append({"type": "text", "text": prompt})
                stdin = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
            else:
                stdin = prompt
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise LLMError(f"O Assistente não respondeu em {timeout_s} s.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if proc.returncode != 0 and not out:
        raise LLMError("O Claude Code falhou: " + (err.decode(errors="replace").strip()[-300:] or f"código {proc.returncode}"))
    try:
        if images:  # stream-json: o envelope final é o evento "result"
            events = [json.loads(line) for line in out.decode().splitlines() if line.strip().startswith("{")]
            envelope = next(e for e in reversed(events) if e.get("type") == "result")
        else:
            envelope = json.loads(out.decode())
    except (json.JSONDecodeError, StopIteration):
        raise LLMError("Resposta do Claude Code ilegível.")
    if envelope.get("is_error"):
        raise LLMError("O Claude Code devolveu um erro: " + str(envelope.get("result") or envelope.get("subtype"))[:300])
    data = envelope.get("structured_output")
    if data is None:
        try:
            data = json.loads(envelope.get("result") or "")
        except json.JSONDecodeError:
            raise LLMError("O Assistente não devolveu o formato esperado.")
    if not isinstance(data, dict) or "reply" not in data:
        raise LLMError("O Assistente não devolveu o formato esperado.")
    data.setdefault("proposals", [])
    return data
