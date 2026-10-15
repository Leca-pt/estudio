"""GPT Image através do Codex CLI (subscrição ChatGPT), em modo não interativo.

Corre com o utilizador sem privilégios, num diretório temporário, com um ambiente mínimo
(sem os segredos da app) e sem funcionalidades extra. A shell do Codex fica bloqueada pela
sandbox (falha fechada). A imagem gerada é lida de $CODEX_HOME/generated_images/<sessão>/."""

import asyncio
import io
import json
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image

CODEX_BIN = "codex"
MAX_INPUT_SIDE = 1536


class GPTImageError(RuntimeError):
    pass


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "/data/codex"))


def configured() -> bool:
    return bool(shutil.which(CODEX_BIN)) and (codex_home() / "auth.json").is_file()


def orientation(aspect: str | None) -> str:
    try:
        w, h = (float(x) for x in (aspect or "").split(":"))
    except ValueError:
        return ""
    return "portrait" if h > w else "landscape" if w > h else "square"


def _prepare_inputs(paths: list[Path], workdir: Path) -> list[Path]:
    """O Codex aceita PNG/JPG: converte para PNG e reduz o lado maior (poupa limite)."""
    out = []
    for n, src in enumerate(paths, start=1):
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE))
            dest = workdir / f"image_{n}.png"
            im.save(dest, "PNG")
        out.append(dest)
    return out


BASE_NOTE = ("Image 1 (attached) is the BASE of the result: a rough grey block-out of the scene (a low-detail 3D "
             "mock-up). Turn it into a real photograph: keep exactly its framing, camera position and height, lens, "
             "perspective and horizon, the layout of buildings, walls and openings, and where each thing stands and how "
             "big it is. Every simple block stands for a real object (a box with wheels is a real car, a block on the "
             "pavement is a real bollard, planter or bench…): draw each one as the real, fully detailed object, never "
             "as a box. Do not move the camera, do not change the viewpoint and do not invent a different composition. "
             "The other attached images (Image 2 onwards) are only references for materials, colours, light, style and "
             "the design of things; never copy their composition, viewpoint or layout.")


def instruction(prompt: str, aspect: str | None, input_labels: list[str], edit: bool, base: bool = False) -> str:
    lines = [
        "Generate exactly one image with your image generation tool, then stop.",
        "Do not run shell commands and do not create, save or copy files: the app collects the generated image by itself.",
    ]
    if base and input_labels:  # a 1.ª imagem é a geometria (render da maquete), não uma referência de aspeto
        lines.append(BASE_NOTE)
    elif edit:
        lines.append("Use Image 1 (attached) as the input image to edit. Apply only the change described below and "
                     "keep everything else in the image exactly as it is.")
    elif input_labels:
        lines.append("Attached reference images, in order: " + "; ".join(
            f"Image {n}: {label}" for n, label in enumerate(input_labels, start=1))
            + ". Use them only to keep the identity and look of these characters, places and objects consistent.")
    if aspect:
        lines.append(f"Aspect ratio: {aspect} ({orientation(aspect)}).")
    lines.append("Image prompt:" if not edit else "Change:")
    lines.append(prompt.strip())
    return "\n".join(lines)


async def generate(prompt: str, aspect: str | None, inputs: list[tuple[Path, str]], edit: bool,
                   timeout_s: int, base: bool = False) -> bytes:
    if not shutil.which(CODEX_BIN):
        raise GPTImageError("O Codex CLI não está instalado no contentor.")
    if not (codex_home() / "auth.json").is_file():
        raise GPTImageError("O Codex ainda não tem login (ver README: codex login --device-auth).")
    workdir = Path(tempfile.mkdtemp(prefix="gptimg-"))
    thread_id = None
    try:
        files = _prepare_inputs([p for p, _ in inputs], workdir)
        text = instruction(prompt, aspect, [label for _, label in inputs], edit, base)
        args = [CODEX_BIN, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                "--sandbox", "workspace-write", "--disable", "browser_use", "--disable", "computer_use",
                "--disable", "apps", "-C", str(workdir), "--json"]
        for f in files:
            args += ["-i", str(f)]
        args.append("-")  # instruções pelo stdin (o -i aceita vários ficheiros)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workdir), "CODEX_HOME": str(codex_home()),
               "LANG": "C.UTF-8"}
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=workdir, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(text.encode()), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise GPTImageError(f"O GPT Image não respondeu em {timeout_s} s.")
        messages, errors = [], []
        for line in out.decode(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                messages.append(item.get("text") or "")
            if event.get("type") in ("error", "turn.failed"):
                errors.append(str(event.get("message") or (event.get("error") or {}).get("message") or event))
        folder = codex_home() / "generated_images" / (thread_id or "-")
        images = sorted(folder.glob("*.png"), key=lambda p: p.stat().st_mtime) if thread_id else []
        if not images:
            detail = "; ".join(errors) or (messages[-1] if messages else "") or err.decode(errors="replace").strip()[-300:]
            raise GPTImageError("O GPT Image não devolveu nenhuma imagem" + (f": {detail[:300]}" if detail else "."))
        data = images[-1].read_bytes()
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        return data
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if thread_id:
            shutil.rmtree(codex_home() / "generated_images" / thread_id, ignore_errors=True)


# ── Login do Codex a partir da Configuração (device code, num pseudo-terminal) ──

import pty as _pty
import re as _re
import select as _select
import time as _time

_login: dict = {}  # {"pid", "fd", "started", "url", "code", "output"}
_ANSI = _re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _login_env() -> dict:
    return {"HOME": "/home/app", "CODEX_HOME": str(codex_home()), "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "TERM": "xterm"}


def _drain(timeout: float = 0.2) -> None:
    fd = _login.get("fd")
    while fd is not None:
        ready, _, _ = _select.select([fd], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            return
        if not chunk:
            return
        _login["output"] = _login.get("output", "") + _ANSI.sub("", chunk.decode(errors="replace"))


def _stop_login() -> None:
    if _login.get("pid"):
        try:
            os.kill(_login["pid"], 9)
            os.waitpid(_login["pid"], 0)
        except (ProcessLookupError, ChildProcessError):
            pass
    if _login.get("fd") is not None:
        try:
            os.close(_login["fd"])
        except OSError:
            pass
    _login.clear()


def _backup() -> Path:
    return codex_home() / "auth.json.pre-login"


def restore_previous() -> bool:
    """O login do Codex apaga o acesso atual logo ao começar: se não foi concluído, repõe a cópia anterior."""
    backup, auth = _backup(), codex_home() / "auth.json"
    if backup.is_file() and not auth.is_file():
        backup.replace(auth)
        return True
    backup.unlink(missing_ok=True)
    return False


def start_login() -> tuple[str, str]:
    """Começa o login por código: devolve (endereço, código) para o utilizador autorizar no browser."""
    _stop_login()
    restore_previous()
    if not shutil.which(CODEX_BIN):
        raise GPTImageError("O Codex CLI não está instalado no contentor.")
    codex_home().mkdir(parents=True, exist_ok=True)
    auth = codex_home() / "auth.json"
    if auth.is_file():
        shutil.copy2(auth, _backup())
        os.chmod(_backup(), 0o600)
    pid, fd = _pty.fork()
    if pid == 0:  # processo filho: o Codex num terminal
        os.execvpe(CODEX_BIN, [CODEX_BIN, "login", "--device-auth"], _login_env())
    _login.update({"pid": pid, "fd": fd, "started": _time.monotonic(), "output": ""})
    end = _time.monotonic() + 30
    while _time.monotonic() < end and not _re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b", _login["output"]):
        _drain(1)
    url = next(iter(_re.findall(r"https://auth\.openai\.com/\S+", _login["output"])), None)
    code = next(iter(_re.findall(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b", _login["output"])), None)
    if not url or not code:
        text = _login.get("output", "")
        _stop_login()
        restore_previous()
        raise GPTImageError("O Codex não mostrou o código de login: " + text.strip()[-200:])
    _login.update({"url": url, "code": code})
    return url, code


def login_state() -> dict:
    """{"state": "idle"|"waiting"|"done"|"failed", "url", "code", "message"}."""
    if not _login.get("pid"):
        restore_previous()  # ex.: a app reiniciou a meio de um login
        return {"state": "idle"}
    _drain()
    try:
        done_pid, status = os.waitpid(_login["pid"], os.WNOHANG)
    except ChildProcessError:
        done_pid, status = _login["pid"], 0
    if done_pid == 0:
        if _time.monotonic() - _login["started"] > 15 * 60:
            _stop_login()
            restored = restore_previous()
            return {"state": "failed", "message": "O código expirou (15 minutos)."
                    + (" O login anterior foi reposto." if restored else " Começa de novo.")}
        return {"state": "waiting", "url": _login.get("url"), "code": _login.get("code")}
    ok = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0 and (codex_home() / "auth.json").is_file()
    message = _login.get("output", "").strip().splitlines()[-1:] or [""]
    _login["pid"] = None
    _stop_login()
    if ok:
        _backup().unlink(missing_ok=True)
        return {"state": "done"}
    restored = restore_previous()
    return {"state": "failed", "message": "O login não foi concluído: " + message[0][:200]
            + (" O login anterior foi reposto." if restored else "")}


def cancel_login() -> bool:
    """Cancela um login a meio; devolve True se o login anterior foi reposto."""
    _stop_login()
    return restore_previous()


def logout() -> None:
    import subprocess

    _stop_login()
    _backup().unlink(missing_ok=True)
    subprocess.run([CODEX_BIN, "logout"], env=_login_env(), capture_output=True, timeout=30, check=False)


def account() -> dict:
    """Conta ChatGPT ligada (e-mail e plano), lidos do auth.json do Codex, se existirem."""
    import base64

    try:
        data = json.loads((codex_home() / "auth.json").read_text())
        token = (data.get("tokens") or {}).get("id_token") or ""
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (OSError, ValueError, IndexError, json.JSONDecodeError):
        return {}
    auth = claims.get("https://api.openai.com/auth") or {}
    return {"email": claims.get("email"), "plan": auth.get("chatgpt_plan_type")}
