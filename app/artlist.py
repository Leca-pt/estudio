"""Artlist (imagens com os créditos do plano Artlist) através do servidor MCP oficial.

O Artlist só aceita clientes MCP conhecidos (Claude, ChatGPT, VS Code), por isso a app usa o Claude Code
que já corre no contentor como cliente: `claude mcp login --no-browser` para ligar a conta (uma vez, a partir
da Configuração) e `claude -p` só com as ferramentas do Artlist para cada geração. O acesso fica em
$ARTLIST_HOME/home (0700, fora dos backups). As referências chegam ao Artlist por links temporários."""

import asyncio
import json
import logging
import os
import pty
import re
import secrets
import select
import shutil
import tempfile
import time
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger("estudio.artlist")

MCP_URL = "https://mcp.artlist.io/mcp"
SERVER = "artlist"
# Modelos de imagem do Artlist que aceitam referências. Cada um chama à resolução de forma diferente
# (quality/resolution, 1k/1K/medium); "cost" = créditos a 1K, cotados pelo Artlist em 2026-09-29.
MODELS = {
    513: {"name": "Seedream 5.0 Pro", "key": "quality", "1k": "1k", "2k": "2k", "cost": 75,
          "aspects": ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")},
    613: {"name": "Seedream 5.0 Flash", "key": "quality", "1k": "1k", "2k": "2k", "cost": 50,
          "aspects": ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")},
    414: {"name": "Nano Banana 2 Lite", "key": "quality", "1k": "1k", "2k": "1k", "cost": 50,
          "aspects": ("1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")},
    102: {"name": "Flux 2.0 Pro", "key": "resolution", "1k": "1K", "2k": "2K", "cost": 80,
          "aspects": ("1:1", "3:4", "4:3", "16:9", "9:16")},
    363: {"name": "Nano Banana 2", "key": "resolution", "1k": "1k", "2k": "2k", "cost": 90,
          "aspects": ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")},
    602: {"name": "GPT Image 2.5", "key": "resolution", "1k": "medium", "2k": "high", "cost": 100,
          "aspects": ("1:1", "16:9", "9:16", "4:3", "3:4")},
    117: {"name": "Nano Banana Pro", "key": "quality", "1k": "1K", "2k": "2K", "cost": 160,
          "aspects": ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")},
}
# Mais modelos de imagem (cotados em 2026-09-29). "needs_refs": só geram com imagens de referência (edição/composição).
MODELS.update({
    360: {"name": "Seedream 5.0 (2K)", "key": "resolution", "1k": "2k", "2k": "3k", "cost": 100,
          "aspects": ("1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9")},
    313: {"name": "Seedream 4.5 (2K)", "key": "resolution", "1k": "2k", "2k": "4k", "cost": 100,
          "aspects": ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9")},
    354: {"name": "Kling O3 Image", "key": "resolution", "1k": "1K", "2k": "2K", "cost": 100,
          "aspects": ("1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9")},
    521: {"name": "Qwen Image 3", "key": None, "cost": 100, "aspects": ("1:1", "3:4", "9:16", "4:3", "16:9")},
    582: {"name": "Grok Imagine 2.0", "key": "resolution", "1k": "1k", "2k": "2k", "cost": 120,
          "aspects": ("16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "1:1")},
    380: {"name": "GPT Image 2.0", "key": "resolution", "1k": "medium", "2k": "high", "cost": None, "needs_refs": True,
          "aspects": ("1:1", "16:9", "9:16", "3:4", "4:3")},
    610: {"name": "Meta Muse Image", "key": None, "cost": None, "needs_refs": True,
          "aspects": ("16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "1:1", "21:9")},
    383: {"name": "Wan 2.7 Pro", "key": None, "cost": None, "needs_refs": True,
          "aspects": ("16:9", "9:16", "1:1", "4:3", "3:4"),
          "aspect_values": {"16:9": "landscape_16_9", "9:16": "portrait_16_9", "1:1": "square_hd",
                            "4:3": "landscape_4_3", "3:4": "portrait_4_3"}},
})
DEFAULT_MODEL = 513

# Modelos de vídeo (imagem → vídeo). res: [(rótulo, valor)] da mais barata para a mais cara; durations: valores aceites
# (tipo original: int ou texto); refs: aceita várias imagens de referência; audio: tem som gerado. Custos a 480p/5 s.
VIDEO_MODELS = {
    416: {"name": "Seedance 2.0 Mini", "res": [("480p", "480p"), ("720p", "720p")], "durations": list(range(4, 16)),
          "aspects": ("16:9", "9:16", "4:3", "3:4", "1:1", "21:9"), "refs": True, "audio": True, "cost": 250,
          "video_refs": True, "audio_refs": True, "mode_key": ("generation_mode", "endFrame", "references"),
          "max_images": 9, "max_videos": 3, "max_audios": 3, "max_audio_s": 15, "max_files": 12},
    581: {"name": "Wan 3.0", "res": [("480p", "480p"), ("720p", "720p"), ("1080p", "1080p")], "durations": list(range(2, 16)),
          "aspects": ("16:9", "9:16", "1:1", "4:3", "3:4"), "refs": True, "audio": True, "cost": 375,
          "max_images": 9, "max_videos": 0, "max_audios": 0},
    358: {"name": "Seedance 2.0", "video_refs": True, "audio_refs": True, "res": [("480p", "480p"), ("720p", "720p"), ("1080p", "1080p")],
          "durations": [str(d) for d in range(4, 16)], "aspects": ("16:9", "9:16", "4:3", "3:4", "1:1", "21:9"),
          "refs": True, "audio": True, "cost": 275, "max_images": 9, "max_videos": 3, "max_audios": 3, "max_audio_s": 15,
          "max_files": 12},
    515: {"name": "Seedance 2.5", "video_refs": True, "audio_refs": True, "res": [("480p", "480p"), ("720p", "720p"), ("1080p", "1080p")],
          "durations": [str(d) for d in range(4, 31)], "aspects": ("16:9", "9:16", "4:3", "3:4", "1:1", "21:9"),
          "refs": True, "audio": True, "cost": 1000, "max_images": 30, "max_videos": 10, "max_audios": 10,
          "max_audio_s": 30, "max_files": 50},
    376: {"name": "Veo 3.1 Lite", "res": [("720p", "720p"), ("1080p", "1080p")], "durations": ["4", "6", "8"],
          "aspects": ("16:9", "9:16"), "refs": False, "audio": True, "cost": 252},
    115: {"name": "Veo 3.1 Fast", "res": [("720p", "720p"), ("1080p", "1080p")], "durations": [4, 6, 8],
          "aspects": ("16:9", "9:16"), "refs": False, "audio": True, "cost": 800},
    349: {"name": "Kling v3", "res": [("720p", "standard"), ("1080p", "pro")], "durations": list(range(3, 16)),
          "aspects": ("16:9", "9:16", "1:1"), "refs": False, "audio": True, "cost": 600},
    # o mesmo Kling v3, preso à variante mais barata: 720p e sem som (400 cr./5 s contra 600 com som e 750 a 1080p)
    3143: {"name": "Kling v3 Standard 720p sem som", "group": 349, "res": [("720p", "standard")],
           "durations": list(range(3, 16)), "aspects": ("16:9", "9:16", "1:1"), "refs": False, "audio": False,
           "audio_off": True, "cost": 400},
    # avatar que fala (o VEED Fabric da fal): a 1.ª imagem do plano e o áudio das falas; dura o que dura o áudio
    370: {"name": "Fabric 1.0 720p", "res": [("720p", "720p")], "durations": list(range(1, 61)),
          "aspects": ("16:9", "9:16", "1:1"), "refs": False, "audio": False, "end": False, "avatar": True,
          "settings": {"quality": "premium"}, "cost": None},
    347: {"name": "Kling O3", "res": [("720p", "standard"), ("1080p", "pro")], "durations": list(range(3, 16)),
          "aspects": ("1:1", "16:9", "9:16"), "refs": False, "audio": True, "cost": None},
    599: {"name": "MiniMax H3 Max", "res": [("480p", "480P"), ("768p", "768P")], "durations": list(range(5, 16)),
          "aspects": ("16:9", "9:16", "1:1", "4:3", "21:9", "3:4"), "refs": False, "audio": False, "audio_always": True,
          "cost": 350},  # sem interruptor de som no Artlist, mas gera voz e ambiente (teste real de 29/09/2026)
    584: {"name": "LTX 2.5 Pro", "res": [("720p", "720p"), ("1080p", "1080p")], "durations": ["6", "8", "10"],
          "aspects": ("16:9", "9:16"), "refs": False, "audio": True, "cost": 1080},
    399: {"name": "Grok Imagine Video 1.5", "res": [("480p", "480p"), ("720p", "720p"), ("1080p", "1080p")],
          "durations": [str(d) for d in range(1, 16)], "aspects": ("16:9", "4:3", "3:2", "1:1", "2:3", "3:4", "9:16"),
          "refs": False, "audio": False, "end": False, "cost": 1000},
    523: {"name": "Luma Ray 3.2", "res": [("720p", "720p"), ("1080p", "1080p")], "durations": ["5", "10"],
          "aspects": ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"), "refs": False, "audio": False, "cost": 1850},
}
DEFAULT_VIDEO_MODEL = 416


def video_group(key: int) -> int:
    """O modelGroupId do Artlist (uma entrada pode ser uma variante presa de outro grupo, ex.: Kling v3 720p sem som)."""
    return int((VIDEO_MODELS.get(int(key)) or {}).get("group") or key)


def video_model_by_name(name: str | None) -> tuple[int, dict] | None:
    return next(((g, m) for g, m in VIDEO_MODELS.items() if m["name"] == name), None)


def video_settings(group: int, aspect_ratio: str | None, quality: str, duration: int | None, audio: bool,
                   frames: bool) -> dict:
    """Definições do pedido de vídeo no formato de cada modelo (resolução, duração mais próxima, formato, som)."""
    m = VIDEO_MODELS[group]
    labels = [lab for lab, _v in m["res"]]
    wanted = quality if quality in labels else labels[0]
    res_value = dict(m["res"])[wanted]
    if m.get("avatar"):  # sem duração nem formato: o vídeo dura o áudio e segue a imagem
        return {"resolution": res_value, **m.get("settings", {})}
    target = duration or 5
    dur = min(m["durations"], key=lambda d: (abs(int(d) - target), int(d)))
    out = {"resolution": res_value, "duration": dur, "aspect_ratio": closest_aspect(aspect_ratio, m["aspects"])}
    if m.get("audio"):
        out["generate_audio"] = bool(audio)
    elif m.get("audio_off"):
        out["generate_audio"] = False
    if m.get("mode_key"):
        key, frames_value, refs_value = m["mode_key"]
        out[key] = frames_value if frames else refs_value
    return out
ASPECTS = MODELS[DEFAULT_MODEL]["aspects"]


def model(group_id) -> dict:
    try:
        return MODELS.get(int(group_id)) or MODELS[DEFAULT_MODEL]
    except (TypeError, ValueError):
        return MODELS[DEFAULT_MODEL]


def closest_aspect(value: str | None, allowed) -> str:
    """O aspect ratio pedido, ou o mais próximo que o modelo aceita."""
    if value in allowed:
        return value
    try:
        w, h = (float(x) for x in (value or "16:9").split(":"))
    except ValueError:
        return "16:9" if "16:9" in allowed else allowed[0]
    return min(allowed, key=lambda a: abs(float(a.split(":")[0]) / float(a.split(":")[1]) - w / h))


# A resolução de cada modelo de imagem, tal como o get_model_config do Artlist a dá (lida em 2026-10-08): a chave e o
# valor mudam de modelo para modelo. "values": 1K/2K/4K → o valor a mandar; "extra": definições fixas (ex.: a
# qualidade do GPT Image). Sem "values": o modelo não tem escolha de tamanho.
RESOLUTIONS: dict[int, dict] = {
    117: {"key": "quality", "values": {"1K": "1K", "2K": "2K", "4K": "4K"}, "default": "2K"},      # Nano Banana Pro
    363: {"key": "resolution", "values": {"1K": "1k", "2K": "2k", "4K": "4k"}, "default": "2K"},   # Nano Banana 2
    414: {"key": "quality", "values": {"1K": "1k"}, "default": "1K"},                               # Nano Banana 2 Lite
    513: {"key": "quality", "values": {"1K": "1k", "2K": "2k"}, "default": "1K"},                   # Seedream 5.0 Pro
    613: {"key": "quality", "values": {"1K": "1k", "2K": "2k"}, "default": "2K"},                   # Seedream 5.0 Flash
    102: {"key": "resolution", "values": {"1K": "1K", "2K": "2K"}, "default": "1K"},                # Flux 2.0 Pro
    602: {"key": "output_resolution", "values": {"1K": "1K", "2K": "2K", "4K": "4K"}, "default": "1K",
          "extra": {"resolution": "medium"}},                                                       # GPT Image 2.5
    360: {"key": "resolution", "values": {"2K": "2k", "4K": "4k"}, "default": "2K"},               # Seedream 5.0 (2K)
    313: {"key": "resolution", "values": {"2K": "2k", "4K": "4k"}, "default": "2K"},               # Seedream 4.5
    354: {"key": "resolution", "values": {"1K": "1K", "2K": "2K", "4K": "4K"}, "default": "2K"},   # Kling O3 Image
    582: {"key": "resolution", "values": {"1K": "1k", "2K": "2k"}, "default": "1K"},               # Grok Imagine 2.0
    380: {"extra": {"resolution": "medium"}},                                                       # GPT Image 2.0 (qualidade)
    521: {}, 610: {}, 383: {},                                                                      # Qwen, Muse, Wan: sem escolha
}


def settings_for(group_id, aspect_ratio: str | None, quality: str | None) -> dict:
    """As definições do pedido: formato, uma imagem e a resolução no campo e no formato deste modelo. quality:
    1K / 2K / 4K (a mais próxima que o modelo aceita; sem ela, a do modelo por omissão)."""
    from app.image_models import fit_resolution

    m = model(group_id)
    aspect = closest_aspect(aspect_ratio, m["aspects"])
    out = {"aspect_ratio": (m.get("aspect_values") or {}).get(aspect, aspect), "num_images": 1}
    r = RESOLUTIONS.get(int(group_id or DEFAULT_MODEL)) or {}
    out.update(r.get("extra") or {})
    if r.get("values"):
        res = fit_resolution(str(quality or "").upper() or r.get("default"), list(r["values"]))
        out[r["key"]] = r["values"][res]
    return out
TOOLS = ["upload_image", "upload_video", "upload_audio", "generate_image", "generate_video", "render_draft", "generate_voiceover",
         "list_voices", "get_generation_status", "get_balance"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\]8;;[^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b\]8;;")


class ArtlistError(RuntimeError):
    pass


class CostConfirmationRequired(ArtlistError):
    """O Artlist pede confirmação do custo antes de gerar (nada foi gasto)."""


def home() -> Path:
    return Path(os.environ.get("ARTLIST_HOME", "/data/artlist"))


def cli_home() -> Path:
    return home() / "home"


def _marker() -> Path:
    return home() / "connected"


def connected() -> bool:
    return _marker().is_file()


def _env(with_claude_token: bool = False) -> dict:
    env = {"HOME": str(cli_home()), "PATH": "/home/app/.local/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "TERM": "xterm", "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1"}
    if with_claude_token:
        if settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token
        elif settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    return env


def _claude() -> str:
    binary = shutil.which(settings.claude_bin, path=_env()["PATH"])
    if not binary:
        raise ArtlistError("O Claude Code CLI não está instalado no contentor.")
    return binary


def _ensure_server() -> None:
    cli_home().mkdir(parents=True, exist_ok=True)
    os.chmod(home(), 0o700)
    os.chmod(cli_home(), 0o700)
    cfg = cli_home() / ".claude.json"
    if cfg.is_file() and SERVER in (json.loads(cfg.read_text() or "{}").get("mcpServers") or {}):
        return
    import subprocess

    subprocess.run([_claude(), "mcp", "add", "--transport", "http", "--scope", "user", SERVER, MCP_URL],
                   env=_env(), cwd=str(cli_home()), capture_output=True, timeout=60, check=False)


# ── Ligar a conta (claude mcp login --no-browser, num pseudo-terminal) ──

_login: dict = {}  # {"pid", "fd", "started", "output"}


def _read(fd: int, until, timeout: float) -> str:
    out, end = "", time.monotonic() + timeout
    while time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk.decode(errors="replace")
        if until(ANSI.sub("", out)):
            break
    return ANSI.sub("", out)


def _stop_login() -> None:
    if _login.get("pid"):
        try:
            os.kill(_login["pid"], 9)
            os.waitpid(_login["pid"], 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        try:
            os.close(_login["fd"])
        except OSError:
            pass
    _login.clear()


def start_login() -> str:
    """Começa o login e devolve o endereço do Artlist para o utilizador abrir."""
    _stop_login()
    _ensure_server()
    binary = _claude()
    pid, fd = pty.fork()
    if pid == 0:  # processo filho: o Claude Code num terminal
        os.chdir(str(cli_home()))
        os.execve(binary, [binary, "mcp", "login", "--no-browser", SERVER], _env())
    _login.update({"pid": pid, "fd": fd, "started": time.monotonic()})
    text = _read(fd, lambda t: "paste the redirect URL" in t or "Couldn't" in t, 40)
    url = next(iter(re.findall(r"https://auth\.artlist\.io/authorize\?[^\s\]]+?(?=https://|\s|$)", text)), None)
    if not url:
        _stop_login()
        raise ArtlistError("O Claude Code não devolveu o endereço de login do Artlist: " + text.strip()[-200:])
    return url


def finish_login(redirect_url: str) -> None:
    """Recebe o endereço localhost…/callback?code=… para onde o browser foi e conclui o login."""
    if not _login.get("pid") or time.monotonic() - _login["started"] > 15 * 60:
        _stop_login()
        raise ArtlistError("O pedido de login expirou. Carrega outra vez em “Ligar ao Artlist”.")
    redirect_url = redirect_url.strip()
    if not re.match(r"^http://(localhost|127\.0\.0\.1):\d+/callback\?", redirect_url):
        raise ArtlistError("Cola o endereço completo da barra do browser, que começa por http://localhost:…/callback?code=")
    fd = _login["fd"]
    os.write(fd, redirect_url.encode() + b"\r")
    text = _read(fd, lambda t: re.search(r"(?i)success|authenticated|couldn.t|failed|error", t.split(redirect_url)[-1]), 60)
    tail = text.split(redirect_url)[-1]
    _stop_login()
    if re.search(r"(?i)couldn.t|failed|error", tail) or not re.search(r"(?i)success|authenticated", tail):
        raise ArtlistError("O Artlist não confirmou o login: " + tail.strip()[-200:])
    _marker().write_text(time.strftime("%Y-%m-%d %H:%M"))


def disconnect_marker() -> None:
    _marker().unlink(missing_ok=True)


def disconnect() -> None:
    import subprocess

    _marker().unlink(missing_ok=True)
    try:
        subprocess.run([_claude(), "mcp", "logout", SERVER], env=_env(), cwd=str(cli_home()), capture_output=True,
                       timeout=60, check=False)
    except Exception:
        pass


# ── Links temporários para as referências ────────────────────

_public: dict[str, tuple[Path, float]] = {}


def _share_dir() -> Path:
    d = Path(os.environ.get("DATA_DIR", "/data")) / "cache" / "shares"
    d.mkdir(parents=True, exist_ok=True)
    return d


def share(path: Path, ttl_s: int = 15 * 60) -> str:
    """Link temporário e aleatório para um ficheiro (para o fornecedor o descarregar). Fica também em disco: serve
    noutro processo da app (ex.: um trabalho de fundo) e depois de um reinício."""
    now = time.time()
    for token in [t for t, (_p, exp) in _public.items() if exp < now]:
        _public.pop(token, None)
    token = secrets.token_urlsafe(24)
    _public[token] = (path, now + ttl_s)
    try:
        d = _share_dir()
        (d / f"{token}.json").write_text(json.dumps({"path": str(path), "exp": now + ttl_s}))
        for old in d.glob("*.json"):  # os expirados saem
            try:
                if json.loads(old.read_text()).get("exp", 0) < now:
                    old.unlink(missing_ok=True)
            except (OSError, ValueError):
                old.unlink(missing_ok=True)
    except OSError:
        pass
    return token


def shared_file(token: str) -> Path | None:
    item = _public.get(token)
    if item is None and re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        try:
            data = json.loads((_share_dir() / f"{token}.json").read_text())
            item = (Path(data["path"]), float(data["exp"]))
        except (OSError, ValueError, KeyError):
            return None
    if not item or item[1] < time.time() or not item[0].is_file():
        return None
    from app.media import media_root

    if not item[0].resolve().is_relative_to(media_root().resolve()):  # só ficheiros da pasta de media
        return None
    return item[0]


# ── Geração (claude -p só com as ferramentas do Artlist) ─────

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "confirmation_required", "failed"]},
        "image_url": {"type": "string", "description": "https URL of the generated image file (when done)"},
        "cost": {"type": "number", "description": "credits charged or quoted, if the tools said"},
        "message": {"type": "string", "description": "short error or note"},
    },
    "required": ["status"],
}
VIDEO_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "confirmation_required", "failed"]},
        "video_url": {"type": "string", "description": "https URL of the generated video file (when done)"},
        "generation_id": {"type": "string", "description": "the generationId of the video generation"},
        "cost": {"type": "number", "description": "credits charged or quoted, if the tools said"},
        "message": {"type": "string", "description": "short error or note"},
    },
    "required": ["status"],
}


def aspect(value: str | None) -> str:
    return value if value in ASPECTS else "16:9"


def _instruction(prompt: str, refs: list[tuple[str, str]], args: dict, confirm_cost: bool) -> str:
    steps = []
    if refs:
        steps.append("1. For each reference below, in this order, call upload_image with exactly that imageUrl and "
                     "fileName, and keep the returned assetId:\n"
                     + "\n".join(f"   - imageUrl: {u}\n     fileName: {n}" for u, n in refs))
    input_note = ("Add \"input\": the list of the assetIds from step 1, in the same order, as objects {\"assetId\": ...}."
                  if refs else "Do not pass any input.")
    steps.append("2. Call generate_image with exactly these arguments (JSON), using the prompt string unchanged:\n"
                 + json.dumps(args, ensure_ascii=False) + f"\n   {input_note}"
                 + ("\n   Also pass \"confirmCost\": true — the user has already approved the cost." if confirm_cost
                    else "\n   Never pass confirmCost."))
    steps.append("3. If generate_image returns status \"confirmation_required\", stop and answer "
                 "{\"status\": \"confirmation_required\", \"cost\": <the quoted cost>}.")
    steps.append("4. Otherwise call get_generation_status with the generationId, again and again (no waiting between calls) "
                 "until it is final.")
    steps.append("5. Answer with the JSON result: status \"done\" and image_url = the https URL of the generated image "
                 "file (plus cost if known), or status \"failed\" with a short message.")
    return ("You are an automation step inside an app. Use only the Artlist tools, do exactly the steps below and "
            "nothing else: no other generations, no variations, no questions.\n\n" + "\n".join(steps))


async def generate(prompt: str, aspect_ratio: str | None, inputs: list[tuple[Path, str]], edit: bool,
                   timeout_s: int, model_group_id: int, quality: str, confirm_cost: bool = False,
                   public_base: str = "", agent_model: str = "haiku") -> tuple[bytes, int | None]:
    """Gera uma imagem; devolve (bytes, créditos gastos se o Artlist os indicar)."""
    if not connected():
        raise ArtlistError("A app não está ligada ao Artlist (Configuração → Ligações → Artlist).")
    if inputs and not public_base:
        raise ArtlistError("Falta o endereço público da app para enviar as referências.")
    text = prompt.strip()
    if inputs and not edit:
        text = ("Reference images: " + "; ".join(f"Image {n}: {label}" for n, (_p, label) in enumerate(inputs, start=1))
                + ". Keep these characters, places and objects consistent with them.\n" + text)
    if edit:
        text = "Edit Image 1. " + text + " Keep everything else exactly as it is."
    refs = [(f"{public_base}/pub/ref/{share(path)}.png", re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80] + ".png")
            for path, label in inputs]
    group = int(model_group_id) if str(model_group_id).isdigit() and int(model_group_id) in MODELS else DEFAULT_MODEL
    if MODELS[group].get("needs_refs") and not inputs:
        raise ArtlistError(f"O {MODELS[group]['name']} só gera a partir de imagens de referência; escolhe outro modelo "
                           "ou gera primeiro com referências.")
    args = {"prompt": text, "modelGroupId": group, "settings": settings_for(group, aspect_ratio, quality)}

    workdir = tempfile.mkdtemp(prefix="artlist-")
    allowed = ",".join(f"mcp__{SERVER}__{t}" for t in TOOLS)
    cmd = [_claude(), "-p", "--tools", "", "--allowedTools", allowed, "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
           "--model", agent_model]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=workdir, env=_env(with_claude_token=True),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(_instruction(text, refs, args, confirm_cost).encode()),
                                              timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ArtlistError(f"O Artlist não terminou em {timeout_s} s (a imagem pode aparecer mais tarde no Artlist).")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    try:
        envelope = json.loads(out.decode())
    except json.JSONDecodeError:
        raise ArtlistError("Resposta ilegível do Claude Code: " + (err.decode(errors="replace").strip()[-200:] or "vazia"))
    data = envelope.get("structured_output")
    if data is None:
        try:
            data = json.loads(envelope.get("result") or "")
        except json.JSONDecodeError:
            data = {"status": "failed", "message": str(envelope.get("result") or "")[:300]}
    if envelope.get("is_error") and data.get("status") != "done":
        raise ArtlistError("O Claude Code falhou: " + str(envelope.get("result") or envelope.get("subtype"))[:300])
    status = data.get("status")
    if status == "confirmation_required":
        raise CostConfirmationRequired(
            f"O Artlist pede confirmação: esta imagem custa {data.get('cost', '?')} créditos. "
            "Carrega em Repetir para confirmar e gerar.")
    if status != "done" or not str(data.get("image_url") or "").startswith("https://"):
        message = str(data.get("message") or "sem imagem")
        if re.search(r"(?i)auth|unauthori[sz]ed|401|login", message):
            _marker().unlink(missing_ok=True)
            message += " — volta a ligar a app ao Artlist na Configuração."
        raise ArtlistError("A geração no Artlist falhou: " + message[:300])
    async with httpx.AsyncClient() as client:
        resp = await client.get(data["image_url"], timeout=120, follow_redirects=True)
    if resp.status_code >= 400 or not resp.headers.get("content-type", "").startswith("image/"):
        raise ArtlistError(f"Não foi possível descarregar a imagem do Artlist ({resp.status_code}).")
    cost = data.get("cost")
    return resp.content, (int(cost) if isinstance(cost, (int, float)) else None)


async def _run_agent(instruction: str, schema: dict, timeout_s: int, agent_model: str) -> dict:
    workdir = tempfile.mkdtemp(prefix="artlist-")
    allowed = ",".join(f"mcp__{SERVER}__{t}" for t in TOOLS)
    cmd = [_claude(), "-p", "--tools", "", "--allowedTools", allowed, "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(schema),
           "--model", agent_model]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=workdir, env=_env(with_claude_token=True),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(instruction.encode()), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ArtlistError(f"O Artlist não terminou em {timeout_s} s (o resultado pode aparecer mais tarde no Artlist).")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    envelope, calls = None, []  # calls: as ferramentas do Artlist que o agente chamou, com os argumentos exatos
    for line in out.decode(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            envelope = ev
        elif ev.get("type") == "assistant":
            calls += [(c["name"].rsplit("__", 1)[-1], c.get("input") or {}) for c in (ev.get("message") or {}).get("content") or []
                      if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name", "").startswith("mcp__")]
    if envelope is None:
        raise ArtlistError("Resposta ilegível do Claude Code: " + (err.decode(errors="replace").strip()[-200:] or "vazia"))
    data = envelope.get("structured_output")
    if data is None:
        try:
            data = json.loads(envelope.get("result") or "")
        except json.JSONDecodeError:
            data = {"status": "failed", "message": str(envelope.get("result") or "")[:300]}
    if envelope.get("is_error") and data.get("status") != "done":
        raise ArtlistError("O Claude Code falhou: " + str(envelope.get("result") or envelope.get("subtype"))[:300])
    return {**data, "_calls": calls}


async def generate_video(prompt: str, aspect_ratio: str | None, frames: list[tuple[Path, str]],
                         refs: list[tuple[Path, str]], group: int, quality: str, duration: int | None, audio: bool,
                         timeout_s: int, confirm_cost: bool = False, public_base: str = "",
                         agent_model: str = "haiku", videos: list[tuple[Path, str]] | None = None,
                         audios: list[tuple[Path, str]] | None = None) -> tuple[bytes, int | None, str | None]:
    """Gera um clip: frames = [Start Frame, End Frame?] (modo Frames) ou refs = imagens de referência (modo Referências).
    videos: vídeos de referência (ex.: o vídeo-guia da maquete), só nos modelos que os aceitam ("video_refs")."""
    if not connected():
        raise ArtlistError("A app não está ligada ao Artlist (Configuração → Ligações).")
    if group not in VIDEO_MODELS:
        raise ArtlistError("Modelo de vídeo desconhecido.")
    m = VIDEO_MODELS[group]
    if m.get("avatar"):  # avatar que fala: uma imagem e o áudio das falas (já num só ficheiro), como lista de inputs
        refs, frames = (frames or refs)[:1], []
        if not audios:
            raise ArtlistError("Falta o áudio das falas do plano (carrega-o no guião da cena).")
    elif refs and not m.get("refs"):  # modelo sem referências múltiplas: usa só a primeira como Start Frame
        frames, refs = refs[:1], []
    if len(frames) > 1 and m.get("end") is False:
        frames = frames[:1]
    if not frames and not refs:
        raise ArtlistError("Falta o Start Frame (ou as imagens de referência) do plano.")
    if not public_base:
        raise ArtlistError("Falta o endereço público da app para enviar as imagens.")
    items = frames or refs
    uploads = [(f"{public_base}/pub/ref/{share(path)}.png", re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80] + ".png")
               for path, label in items]
    videos = (videos or []) if (refs and m.get("video_refs")) else []
    vids = [(f"{public_base}/pub/ref/{share(path)}{path.suffix.lower() if path.suffix.lower() in ('.mp4', '.webm') else '.mp4'}",
             re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80] + (path.suffix.lower() or ".mp4")) for path, label in videos]
    audios = (audios or []) if (refs and (m.get("audio_refs") or m.get("avatar"))) else []
    auds = [(f"{public_base}/pub/ref/{share(path)}{path.suffix.lower()}",
             re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80] + path.suffix.lower()) for path, label in audios]
    settings_ = video_settings(group, aspect_ratio, quality, duration, audio, frames=bool(frames))
    args = {"prompt": prompt.strip(), "modelGroupId": video_group(group), "settings": settings_}
    if frames:
        how = ("Add \"input\": {\"startFrame\": {\"assetId\": <first assetId>}"
               + (", \"endFrame\": {\"assetId\": <second assetId>}}" if len(frames) > 1 else "}") + ".")
    else:
        n_all = len(uploads) + len(vids) + len(auds)
        how = ("Add \"input\": the list of the assetIds from step 1 (images first, in the same order, then the videos, "
               f"then the audios), as objects {{\"assetId\": ...}} — exactly {n_all} items, none left out.")
    steps = [
        "1. For each image below, in this order, call upload_image with exactly that imageUrl and fileName, and keep "
        "the returned assetId:\n" + "\n".join(f"   - imageUrl: {u}\n     fileName: {n}" for u, n in uploads)
        + ("\n   Then, for each video below, call upload_video with exactly that videoUrl and fileName, and keep the "
           "returned assetId:\n" + "\n".join(f"   - videoUrl: {u}\n     fileName: {n}" for u, n in vids) if vids else "")
        + ("\n   Then, for each audio below, call upload_audio with exactly that audioUrl and fileName, and keep the "
           "returned assetId:\n" + "\n".join(f"   - audioUrl: {u}\n     fileName: {n}" for u, n in auds) if auds else ""),
        "2. Call generate_video with exactly these arguments (JSON), using the prompt string unchanged and the "
        "settings exactly as given (keep \"duration\": never drop it, never use Auto, never change it):\n"
        + json.dumps(args, ensure_ascii=False) + f"\n   {how}"
        + ("\n   Also pass \"confirmCost\": true — the user has already approved the cost." if confirm_cost
           else "\n   Never pass confirmCost."),
        "3. If generate_video returns status \"confirmation_required\", stop and answer "
        "{\"status\": \"confirmation_required\", \"cost\": <the quoted cost>}.",
        "4. Otherwise call get_generation_status with the generationId, again and again (no waiting between calls) "
        "until it is final.",
        "5. Answer with the JSON result: status \"done\", video_url = the https URL of the generated video file, "
        "generation_id = the generationId (plus cost if known), or status \"failed\" with a short message.",
    ]
    instruction = ("You are an automation step inside an app. Use only the Artlist tools, do exactly the steps below "
                   "and nothing else: no other generations, no variations, no questions.\n\n" + "\n".join(steps))
    data = await _run_agent(instruction, VIDEO_SCHEMA, timeout_s, agent_model)
    sent = [a for name, a in data["_calls"] if name == "generate_video"]
    uploaded = [f"{name.removeprefix('upload_')}:{a.get('fileName')}" for name, a in data["_calls"]
                if name.startswith("upload_")]
    expected = (len(frames) if frames else len(uploads)) + len(vids) + len(auds)
    for a in sent:  # o que o agente pediu de facto (o Seedance já devolveu clips mais longos do que o plano)
        got = a.get("settings") or {}
        n_in = len(a["input"]) if isinstance(a.get("input"), list) else len([v for v in (a.get("input") or {}).values() if v])
        log.info("artlist generate_video settings=%s inputs=%d/%d uploads=%s", json.dumps(got), n_in, expected,
                 json.dumps(uploaded, ensure_ascii=False))
        if n_in < expected:
            log.warning("artlist: o agente enviou %d de %d ficheiros ao modelo", n_in, expected)
        if str(got.get("duration")) != str(settings_["duration"]):
            log.warning("artlist: o agente mudou a duração (%s em vez de %s)", got.get("duration"), settings_["duration"])
    if data.get("status") == "confirmation_required":
        raise CostConfirmationRequired(
            f"O Artlist pede confirmação: este clip custa {data.get('cost', '?')} créditos. "
            "Carrega em Repetir para confirmar e gerar.")
    if data.get("status") != "done" or not str(data.get("video_url") or "").startswith("https://"):
        message = str(data.get("message") or "sem vídeo")
        if re.search(r"(?i)auth|unauthori[sz]ed|401|login", message):
            _marker().unlink(missing_ok=True)
            message += " — volta a ligar a app ao Artlist na Configuração."
        raise ArtlistError("A geração do vídeo falhou: " + message[:300])
    async with httpx.AsyncClient() as client:
        resp = await client.get(data["video_url"], timeout=300, follow_redirects=True)
    if resp.status_code >= 400 or not resp.content[4:8] == b"ftyp":
        raise ArtlistError(f"Não foi possível descarregar o vídeo do Artlist ({resp.status_code}).")
    cost = data.get("cost")
    gid = str(data.get("generation_id") or "")
    return resp.content, (int(cost) if isinstance(cost, (int, float)) else None), (gid if re.fullmatch(r"[0-9a-f-]{36}", gid) else None)


async def quote(args: dict, timeout_s: int = 180, agent_model: str = "haiku") -> int | None:
    """Cotação exata do Artlist (get_generation_cost, só leitura: não gera nada). args: kind, modelGroupId, settings…
    (ou kind "draftRender" + generationId). Devolve os créditos, ou None se o Artlist não cotou."""
    if not connected():
        return None
    instruction = ("Call the Artlist tool get_generation_cost exactly once with exactly these arguments (JSON), then "
                   "answer just \"ok\":\n" + json.dumps(args, ensure_ascii=False))
    for text_ in await _tool_results(instruction, ["get_generation_cost"], timeout_s, agent_model):
        try:
            data = json.loads(text_)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("cost"), (int, float)):
            return int(data["cost"])
    return None


async def quote_request(group: int, settings_: dict, images: list[tuple[Path, str]], videos: list[tuple[Path, str]],
                        audios: list[tuple[Path, str]], frames: bool, public_base: str, timeout_s: int = 300,
                        agent_model: str = "haiku") -> dict:
    """Cotação exata de um pedido de vídeo com os mesmos ficheiros que o pedido real (o Artlist escolhe a variante e o
    preço pelos inputs: com vídeo de referência é outro preço). Sobe os ficheiros como o generate_video e chama
    get_generation_cost com eles e, havendo vídeo, também sem ele (o que o vídeo-guia custa). Não gera nada.
    Devolve {"total", "without_video" (ou None), "feature"}."""
    if not connected():
        raise ArtlistError("A app não está ligada ao Artlist (Configuração → Ligações).")
    def link(path: Path, ext: str) -> str:
        return f"{public_base}/pub/ref/{share(path)}{ext}"

    ups = [("upload_image", "imageUrl", link(p_, ".png"), re.sub(r"[^A-Za-z0-9_.-]+", "_", lab)[:80] + ".png") for p_, lab in images]
    ups += [("upload_video", "videoUrl", link(p_, p_.suffix.lower() if p_.suffix.lower() in (".mp4", ".webm") else ".mp4"),
             re.sub(r"[^A-Za-z0-9_.-]+", "_", lab)[:80] + (p_.suffix.lower() or ".mp4")) for p_, lab in videos]
    ups += [("upload_audio", "audioUrl", link(p_, p_.suffix.lower()), re.sub(r"[^A-Za-z0-9_.-]+", "_", lab)[:80]
             + p_.suffix.lower()) for p_, lab in audios]
    if (VIDEO_MODELS.get(group) or {}).get("avatar"):
        frames = False  # imagem e áudio vão juntos na lista de inputs
    args = {"kind": "video", "modelGroupId": video_group(group), "settings": settings_, "prompt": "video"}
    n_img, n_vid = len(images), len(videos)
    if frames:
        how = ('"input": {"startFrame": {"assetId": <first assetId>}' + (', "endFrame": {"assetId": <second assetId>}}' if n_img > 1 else "}"))
    else:
        how = '"input": the list of ALL the assetIds from step 1, in the same order, as objects {"assetId": ...}'
    steps = ["1. Upload each file below in this order, with exactly that URL and fileName, and keep each returned assetId:\n"
             + "\n".join(f"   - {tool} {key}: {url} fileName: {name}" for tool, key, url, name in ups),
             "2. Call get_generation_cost with exactly these arguments (JSON) plus " + how + ":\n" + json.dumps(args)]
    if n_vid and not frames:
        steps.append("3. Call get_generation_cost again with the same arguments, but with \"input\" WITHOUT the video "
                     "assetIds (only the image and audio assetIds, same order).")
    steps.append(f"{len(steps) + 1}. Answer just \"ok\". Never call generate_video.")
    instruction = ("You are an automation step inside an app. Use only the Artlist tools, do exactly the steps below and "
                   "nothing else.\n\n" + "\n".join(steps))
    tools = ["upload_image", "upload_video", "upload_audio", "get_generation_cost"]
    quotes = []
    for text_ in await _tool_results(instruction, tools, timeout_s, agent_model):
        try:
            data = json.loads(text_)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("cost"), (int, float)) and "feature" in data:
            quotes.append(data)
    if not quotes:
        raise ArtlistError("O Artlist não deu a cotação.")
    return {"total": int(quotes[0]["cost"]), "without_video": int(quotes[1]["cost"]) if len(quotes) > 1 else None,
            "feature": quotes[0].get("feature")}


async def quote_many(args_list: list[dict], timeout_s: int = 240, agent_model: str = "haiku") -> list[dict]:
    """Várias cotações numa só passagem do agente (get_generation_cost, só leitura). Devolve os resultados tal como o
    Artlist os deu (com modelGroupId, cost e resolvedSettings), pela ordem em que chegaram."""
    if not connected() or not args_list:
        return []
    instruction = ("Call the Artlist tool get_generation_cost once for each of the argument sets below (JSON, one per "
                   "line), exactly as given, then answer just \"ok\":\n"
                   + "\n".join(json.dumps(a, ensure_ascii=False) for a in args_list))
    out = []
    for text_ in await _tool_results(instruction, ["get_generation_cost"], timeout_s, agent_model):
        try:
            data = json.loads(text_)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("cost"), (int, float)):
            out.append(data)
    return out


async def render_draft(generation_id: str, timeout_s: int, confirm_cost: bool = False,
                       agent_model: str = "haiku") -> tuple[bytes, int | None, str | None]:
    """O mesmo take de um rascunho 480p do Seedance 2.5, em 1080p (render_draft). Devolve (vídeo, custo, id novo)."""
    if not connected():
        raise ArtlistError("A app não está ligada ao Artlist (Configuração → Ligações).")
    steps = [
        f"1. Call render_draft with generationId \"{generation_id}\""
        + (" and confirmCost true — the user has already approved the cost." if confirm_cost else ". Never pass confirmCost."),
        "2. If it returns status \"confirmation_required\", stop and answer {\"status\": \"confirmation_required\", "
        "\"cost\": <the quoted cost>}. If it returns draft_unavailable, reference_expired or render_failed, answer "
        "status \"failed\" with that reason as message.",
        "3. Otherwise call get_generation_status with the NEW generationId, again and again until it is final.",
        "4. Answer with the JSON result: status \"done\", video_url = the https URL of the 1080p video file, "
        "generation_id = the new generationId (plus cost if known), or status \"failed\" with a short message.",
    ]
    instruction = ("You are an automation step inside an app. Use only the Artlist tools, do exactly the steps below "
                   "and nothing else.\n\n" + "\n".join(steps))
    data = await _run_agent(instruction, VIDEO_SCHEMA, timeout_s, agent_model)
    if data.get("status") == "confirmation_required":
        raise CostConfirmationRequired(
            f"O Artlist pede confirmação: passar a 1080p custa {data.get('cost', '?')} créditos. Carrega em Repetir para "
            "confirmar.")
    if data.get("status") != "done" or not str(data.get("video_url") or "").startswith("https://"):
        raise ArtlistError("Passar a 1080p falhou: " + str(data.get("message") or "sem vídeo")[:300])
    async with httpx.AsyncClient() as client:
        resp = await client.get(data["video_url"], timeout=300, follow_redirects=True)
    if resp.status_code >= 400 or not resp.content[4:8] == b"ftyp":
        raise ArtlistError(f"Não foi possível descarregar o vídeo do Artlist ({resp.status_code}).")
    cost = data.get("cost")
    gid = str(data.get("generation_id") or "")
    return resp.content, (int(cost) if isinstance(cost, (int, float)) else None), (gid if re.fullmatch(r"[0-9a-f-]{36}", gid) else None)


# ── Vozes (voz de referência das personagens) ────────────────



async def _tool_results(instruction: str, tools: list[str], timeout_s: int, agent_model: str) -> list[str]:
    """Corre o agente e devolve o texto de cada resultado das ferramentas, tal como o Artlist o deu (lido do registo da
    sessão, não da resposta do modelo: listas grandes não passam pela memória dele)."""
    workdir = tempfile.mkdtemp(prefix="artlist-")
    allowed = ",".join(f"mcp__{SERVER}__{t}" for t in tools)
    cmd = [_claude(), "-p", "--tools", "", "--allowedTools", allowed, "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--model", agent_model]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=workdir, env=_env(with_claude_token=True),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _err = await asyncio.wait_for(proc.communicate(instruction.encode()), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ArtlistError(f"O Artlist não respondeu em {timeout_s} s.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    texts = []
    for line in out.decode(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "user":
            continue
        for item in (ev.get("message") or {}).get("content") or []:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                content = item.get("content")
                if isinstance(content, str):
                    texts.append(content)
                else:
                    texts += [c.get("text", "") for c in content or [] if isinstance(c, dict)]
    return texts


async def list_voices(timeout_s: int = 300, agent_model: str = "haiku") -> list[dict]:
    """O catálogo de vozes do Artlist (todas as páginas): [{voiceId, name, gender, age, accents, languages, previewUrl}]."""
    if not connected():
        raise ArtlistError("A app não está ligada ao Artlist (Configuração → Ligações).")
    instruction = ("Call the Artlist tool list_voices with perPage 20 and page 1. Read \"total\" in the result and keep "
                   "calling it with perPage 20 and page 2, 3… until page × 20 ≥ total. Do not summarise the voices; when "
                   "all pages are fetched, answer just \"ok\".")
    voices: dict[int, dict] = {}
    for text_ in await _tool_results(instruction, ["list_voices"], timeout_s, agent_model):
        try:
            data = json.loads(text_)
        except json.JSONDecodeError:
            continue
        for v in data.get("voices") or []:
            if not isinstance(v, dict) or not str(v.get("voiceId", "")).isdigit():
                continue
            langs = v.get("languages") or []
            voices[int(v["voiceId"])] = {
                "voiceId": int(v["voiceId"]), "name": str(v.get("name") or ""), "gender": str(v.get("gender") or ""),
                "age": str(v.get("age") or "").replace("_", " "),
                "accents": ", ".join(v.get("accents") or []) if isinstance(v.get("accents"), list) else str(v.get("accents") or ""),
                "languages": (f"{len(langs)} línguas" if isinstance(langs, list) and len(langs) > 4
                              else ", ".join(langs) if isinstance(langs, list) else str(langs)),
                "portuguese": "Portuguese" in langs if isinstance(langs, list) else False,
                "previewUrl": str(v.get("previewUrl") or "")}
    if not voices:
        raise ArtlistError("O Artlist não devolveu vozes.")
    return sorted(voices.values(), key=lambda v: (v["gender"], v["name"]))


def _find_urls(node, out: list) -> None:
    if isinstance(node, dict):
        for v in node.values():
            _find_urls(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_urls(v, out)
    elif isinstance(node, str) and node.startswith("https://"):
        out.append(node)


async def generate_voice(text: str, voice_id: int, timeout_s: int, confirm_cost: bool = False,
                         agent_model: str = "haiku") -> tuple[bytes, int | None]:
    """Fala o texto na voz escolhida do catálogo do Artlist; devolve (áudio, créditos gastos se indicados). O link do
    áudio lê-se do resultado da ferramenta (não da resposta do agente)."""
    if not connected():
        raise ArtlistError("A app não está ligada ao Artlist (Configuração → Ligações).")
    args = {"voiceId": int(voice_id), "text": text.strip()[:5000]}
    instruction = (
        "You are an automation step inside an app. Use only the Artlist tools.\n"
        "1. Call generate_voiceover with exactly these arguments (JSON), using the text unchanged:\n"
        + json.dumps(args, ensure_ascii=False)
        + ("\n   Also pass \"confirmCost\": true — the user has already approved the cost." if confirm_cost
           else "\n   Never pass confirmCost.")
        + "\n2. If it returns status \"confirmation_required\", stop and answer \"confirmation\".\n"
          "3. Otherwise call get_generation_status with the generationId, again and again, until it is final. "
          "Then answer \"ok\".")
    results = await _tool_results(instruction, ["generate_voiceover", "get_generation_status"], timeout_s, agent_model)
    parsed = []
    for text_ in results:
        try:
            parsed.append(json.loads(text_))
        except json.JSONDecodeError:
            parsed.append({"raw": text_})
    first = parsed[0] if parsed else {}
    if isinstance(first, dict) and first.get("status") == "confirmation_required":
        cost = first.get("cost") or first.get("credits") or "?"
        raise CostConfirmationRequired(f"O Artlist pede confirmação: esta voz custa {cost} créditos. "
                                       "Carrega em Repetir para confirmar e gerar.")
    last = parsed[-1] if parsed else {}
    urls: list = []
    _find_urls(last, urls)
    audio = [u for u in urls if re.search(r"\.(mp3|wav|aac|m4a|ogg|flac)(\?|$)", u, re.I)] or urls
    if not audio:
        message = (last.get("message") or last.get("error") or last.get("raw") or "sem áudio") if isinstance(last, dict) else "sem áudio"
        raise ArtlistError("A geração da voz falhou: " + str(message)[:300])
    async with httpx.AsyncClient() as client:
        resp = await client.get(audio[0], timeout=120, follow_redirects=True)
    if resp.status_code >= 400 or not resp.content:
        raise ArtlistError(f"Não foi possível descarregar a voz do Artlist ({resp.status_code}).")
    cost = last.get("cost") or last.get("credits") if isinstance(last, dict) else None
    return resp.content, (int(cost) if isinstance(cost, (int, float)) else None)


async def balance() -> dict | None:
    """Saldo de créditos (pelo Claude Code, só com a ferramenta get_balance)."""
    if not connected():
        return None
    schema = {"type": "object", "properties": {"creditsLeft": {"type": "number"}, "planCredits": {"type": "number"},
                                                "planName": {"type": "string"}, "renewsOn": {"type": "string"}}}
    cmd = [_claude(), "-p", "--tools", "", "--allowedTools", f"mcp__{SERVER}__get_balance", "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(schema), "--model", "haiku"]
    workdir = tempfile.mkdtemp(prefix="artlist-")
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=workdir, env=_env(with_claude_token=True),
                                                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, _err = await asyncio.wait_for(proc.communicate(
            b"Call get_balance once and answer with its values (creditsLeft, planCredits, planName, renewsOn)."), timeout=90)
        return json.loads(out.decode()).get("structured_output")
    except Exception:
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
