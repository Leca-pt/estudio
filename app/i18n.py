"""Idioma da interface: português (o da app e das fontes) ou inglês.

A app continua a gerar as páginas em português. Com o inglês escolhido (Configuração → Idioma), o texto visível de
cada página HTML é traduzido à saída (TranslateMiddleware) por um dicionário português → inglês (app/i18n/en.json):
frases exatas ("strings") e frases com partes variáveis ("patterns": expressão regular → tradução com \\1, \\2…).
Assim o código, as comparações de texto e os valores gravados ficam iguais; o que muda é só o que se vê.

Não se traduz: o conteúdo dos projetos (só frases que estão no dicionário mudam, inteiras), o que está dentro de
<textarea>, <script>, <style> e de elementos com translate="no", nem os valores dos campos. O texto que o navegador
escreve sozinho (a maquete, avisos) traduz-se no navegador com o mesmo dicionário (static/js/i18n.js).

I18N_COLLECT=<ficheiro>: guarda as frases que ainda não têm tradução (para completar o dicionário).
"""

import html
import json
import os
import re
import time
from pathlib import Path

LANGS = {"pt": "Português", "en": "English"}
DIR = Path(__file__).parent / "i18n"
CONFIG_KEY = "ui_language"

_lang = {"at": 0.0, "value": "pt"}
_data: dict = {}
_missing: set[str] = set()


def current() -> str:
    """O idioma escolhido (lido da Configuração; guardado 2 s para não ir à base de dados em cada pedido)."""
    now = time.monotonic()
    if now - _lang["at"] > 2:
        from sqlmodel import Session

        from app.db import engine
        from app.options import get_config

        try:
            with Session(engine) as session:
                value = get_config(session, CONFIG_KEY, "pt")
        except Exception:  # noqa: BLE001 — sem base de dados (arranque): português
            value = "pt"
        _lang.update(at=now, value=value if value in LANGS else "pt")
    return _lang["value"]


def assistant_note() -> str:
    """Para o Assistente e o escritor de prompts: em que língua responde ao utilizador."""
    if current() != "en":
        return ""
    return ("\n\nINTERFACE LANGUAGE: English. Write \"reply\" and every \"reason\" in English (the user reads the app "
            "in English). The VALUES of the fields keep following each field's own rules (e.g. a field that asks for "
            "Portuguese stays in Portuguese; prompts for the image and video models stay in English).")


def set_language(session, code: str) -> None:
    from app.options import set_config

    set_config(session, CONFIG_KEY, code if code in LANGS else "pt")
    _lang["at"] = 0.0


def _load() -> dict:
    """O dicionário, lido uma vez (só muda quando se publica uma versão nova da app, que reinicia)."""
    if _data:
        return _data
    path = DIR / "en.json"
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    strings = {_norm(k): v for k, v in (raw.get("strings") or {}).items()}
    patterns = []
    for item in raw.get("patterns") or []:
        rx, repl, anchor, frags = (list(item) + ["", []])[:4]
        try:
            patterns.append((re.compile(rx + r"\Z", re.S), repl, anchor, tuple(frags or ())))
        except re.error:
            continue
    _data.update(stamp=path.stat().st_mtime if path.exists() else 0, strings=strings, patterns=patterns, memo={},
                 words=raw.get("words") or {})
    return _data


def version() -> str:
    """Muda quando o dicionário muda (o navegador guarda-o com esta versão)."""
    return str(int(_load().get("stamp") or 0))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def lookup(text: str) -> str | None:
    """A tradução de uma frase inteira (sem os espaços à volta), ou None."""
    d = _load()
    key = _norm(text)
    if not key:
        return None
    hit = d["strings"].get(key)
    if hit is not None:
        return hit
    memo = d["memo"]
    if key in memo:
        return memo[key]
    out = None
    for rx, repl, anchor, frags in d["patterns"]:  # do mais específico para o mais geral
        if anchor and anchor not in key or not all(f in key for f in frags):
            continue  # falta-lhe texto fixo: não pode casar (evita tentar a expressão, que é o caro)
        m = rx.match(key)
        if m:
            got = [lookup(g) if g else None for g in m.groups()]
            if any(t is None and _sentence_like(g) for g, t in zip(m.groups(), got)):
                continue  # a parte variável é uma frase por traduzir (conteúdo do projeto): este padrão não serve
            parts = [(t or g or "") for g, t in zip(m.groups(), got)]  # as partes também se traduzem
            out = re.sub(r"\\(\d+)", lambda x: parts[int(x.group(1)) - 1] if int(x.group(1)) <= len(parts) else "", repl)
            break
    if out is None:
        out = _composite(key, d)
    if out is None:  # separadores agarrados às pontas («· Curta», «Base:»): traduz o meio
        m = re.fullmatch(r"([·—–|:,\s]*)(.*?)([·—–|:,.\s]*)", key, re.S)
        if m and (m.group(1) or m.group(3)) and m.group(2) and m.group(2) != key:
            mid = lookup(m.group(2))
            if mid is not None:
                out = m.group(1) + mid + m.group(3)
    if len(memo) > 50000:
        memo.clear()
    memo[key] = out
    return out


_SEPS = (" · ", " › ", " — ")
_LOWER_WORD = re.compile(r"(?<![\w@])[a-zà-ÿ]{2,}(?![\w])")


def _sentence_like(text: str | None) -> bool:
    """Parece uma frase de conteúdo (várias palavras em minúsculas), e não um nome, número ou código."""
    return bool(text) and len(_LOWER_WORD.findall(text)) >= 3


def _composite(key: str, d: dict) -> str | None:
    """Linhas feitas de partes («Plano de pormenor · Estática · linhas 3 · 4 s») traduzem-se parte a parte; datas
    («Sáb 3 jun 2028», «16 out.») palavra a palavra. None se nada muda."""
    for sep in _SEPS:
        if sep in key:
            parts = key.split(sep)
            got = [lookup(p) for p in parts]
            if any(g is not None for g in got):
                return sep.join(g if g is not None else p for g, p in zip(got, parts))
    sentences = re.split(r"(?<=[.!?:])\s+(?=[A-ZÀ-Ý«“(])", key)  # várias frases: todas traduzidas, ou nenhuma
    if len(sentences) > 1:
        got = [lookup(x) for x in sentences]
        if all(g is not None for g in got):
            return " ".join(got)
    words = d.get("words") or {}
    tokens = re.split(r"([\s,]+)", key)
    if words and any(t in words for t in tokens) and all(t in words or not t.strip(" ,") or re.fullmatch(r"[\d:./-]+", t)
                                                         for t in tokens):
        out = "".join(words.get(t, t) for t in tokens)
        return re.sub(r"\s{2,}", " ", out).strip()
    return None


_WORDY = re.compile(r"[A-Za-zÀ-ÿ]{2,}")


def tr(text: str) -> str:
    """Traduz mantendo os espaços à volta; o que não está no dicionário fica como está."""
    hit = lookup(text)
    if hit is None:
        if os.environ.get("I18N_COLLECT") and _WORDY.search(text):
            _missing.add(_norm(text))
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + hit + trail


# Zonas que nunca se traduzem: scripts, estilos, caixas de texto e o que está marcado translate="no"
_PROTECTED = re.compile(r"<(script|style|textarea)\b[^>]*>.*?</\1\s*>|<(\w+)\b[^>]*\btranslate=\"no\"[^>]*>.*?</\2\s*>",
                        re.S | re.I)
_TEXT = re.compile(r">([^<>]+)<")
_ATTR = re.compile(r"(\s(?:title|placeholder|aria-label|alt|data-tip|data-confirm|data-confirm-text|data-confirm-ok|"
                   r"data-busy-label|data-busy-text|data-asst-ask|label)=\")([^\"]*)(\")")


def _text_sub(m: re.Match) -> str:
    raw = m.group(1)
    if not _WORDY.search(raw):
        return m.group(0)
    plain = html.unescape(raw)
    out = tr(plain)
    return m.group(0) if out == plain else ">" + html.escape(out, quote=False) + "<"


def _attr_sub(m: re.Match) -> str:
    raw = m.group(2)
    if not _WORDY.search(raw):
        return m.group(0)
    plain = html.unescape(raw)
    out = tr(plain)
    return m.group(0) if out == plain else m.group(1) + html.escape(out, quote=True) + m.group(3)


def translate_html(body: str) -> str:
    out, pos = [], 0
    for m in _PROTECTED.finditer(body):
        out.append(_translate_chunk(body[pos:m.start()]))
        out.append(_translate_open_tag(m.group(0)))
        pos = m.end()
    out.append(_translate_chunk(body[pos:]))
    return "".join(out).replace('<html lang="pt-PT">', '<html lang="en">', 1)


def _translate_chunk(chunk: str) -> str:
    return _ATTR.sub(_attr_sub, _TEXT.sub(_text_sub, chunk))


def _translate_open_tag(block: str) -> str:
    """Numa zona protegida traduzem-se só os atributos da etiqueta de abertura (ex.: o placeholder de uma caixa de
    texto), nunca o conteúdo."""
    end = block.find(">")
    return _ATTR.sub(_attr_sub, block[: end + 1]) + block[end + 1:] if end >= 0 else block


def dump_missing() -> None:
    path = os.environ.get("I18N_COLLECT")
    if not path:
        return
    old = set()
    if os.path.exists(path):
        old = set(json.loads(open(path, encoding="utf-8").read()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(old | _missing), f, ensure_ascii=False, indent=0)


class TranslateMiddleware:
    """Traduz as respostas HTML quando a interface está em inglês (o resto passa como está)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or current() == "pt":
            await self.app(scope, receive, send)
            return
        start: dict | None = None
        body: list[bytes] = []
        passthrough = False

        async def wrapped(message):
            nonlocal start, passthrough
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers") or []}
                if b"text/html" in headers.get(b"content-type", b"") and b"content-encoding" not in headers:
                    start = message
                    return
                passthrough = True
                await send(message)
                return
            if message["type"] == "http.response.body" and start is not None and not passthrough:
                body.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                data = translate_html(b"".join(body).decode("utf-8", "replace")).encode("utf-8")
                headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(data)).encode()))
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": data})
                return
            await send(message)

        await self.app(scope, receive, wrapped)
