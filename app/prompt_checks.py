"""Validações dos prompts (docs/PROMPT_RULES.md, secção 5). Só avisam; nenhuma bloqueia."""

import re

NUMBER_WORDS = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
TIMED = re.compile(r"\b(in the first|seconds?|then|until the end|by the end)\b|\[\d", re.I)


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9][\w'’-]*", text or ""))


def _find(text: str, words: list[str]) -> list[str]:
    """Termos encontrados (palavras inteiras, sem repetir sobreposições)."""
    found, masked = [], text.lower()
    for w in sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True):
        pattern = r"(?<![\w-])" + re.escape(w) + r"(?![\w-])"
        if re.search(pattern, masked):
            found.append(w)
            masked = re.sub(pattern, " ", masked)
    return found


def check(text: str, kind: str, cfg: dict, *, people: int = 0, dialogue: str | None = None,
          mode: str | None = None) -> list[str]:
    """kind: image (capa), reference (folha, retrato, vista: limite de palavras próprio) ou video."""
    if not text:
        return []
    warnings = []
    words = word_count(text)
    reference = kind == "reference"
    if reference:
        kind = "image"  # as mesmas regras de imagem; só o limite de palavras muda
        limit = int(cfg.get("reference_max_words", 120))
        if words > limit:
            warnings.append(f"{words} palavras: acima de {limit} (imagens de referência: folha, retrato, vista"
                            + (f"; {cfg['image_profile']})." if cfg.get("image_profile") else ")."))
    elif kind == "image" and words > int(cfg.get("image_max_words", 80)):
        warnings.append(f"{words} palavras: acima de {cfg.get('image_max_words', 80)}"
                        + (f" ({cfg['image_profile']})." if cfg.get("image_profile") else "."))
    if kind == "video":
        timed = bool(TIMED.search(text))
        limit = int(cfg.get("video_max_words_timed", 150) if timed else cfg.get("video_max_words", 100))
        if words > limit:
            warnings.append(f"{words} palavras: acima de {limit}{' com estrutura temporal' if timed else ''} "
                            + (f"({cfg['video_profile']})." if cfg.get("video_profile") else "(alvo 50–70)."))
    if kind == "image":
        motion = _find(text, cfg.get("motion_words", []))
        if motion:
            warnings.append("Movimento numa imagem (deve ser um instante parado): " + ", ".join(f"“{m}”" for m in motion) + ".")
    if kind == "video":
        cams = _find(text, cfg.get("camera_words", []))
        guided = "@Video1" in text  # com vídeo-guia, a câmara descrita passo a passo segue o previs
        if len(cams) > 1 and not guided and not cfg.get("video_timeline"):
            warnings.append("Mais de um movimento de câmara: " + ", ".join(f"“{c}”" for c in cams) + ". Usa um só por plano.")
        fast = _find(text, cfg.get("fast_words", []))
        if fast:
            warnings.append("Evitar " + ", ".join(f"“{f}”" for f in fast) + ": degrada a imagem. Se é preciso ritmo, só um elemento é rápido.")
    empty = _find(text, cfg.get("empty_adjectives", []))
    if empty:
        warnings.append("Adjetivos vazios: " + ", ".join(f"“{e}”" for e in empty) + ". Troca por luz, materiais e composição.")
    fmt = _find(text, cfg.get("format_words", []))
    if fmt:
        warnings.append("Formato ou resolução no texto: " + ", ".join(f"“{f}”" for f in fmt) + ". São definições do modelo, não do prompt.")
    if people and not re.search(r"\bexactly " + NUMBER_WORDS + r" (?:[a-z]+ )?(people|persons?|characters?)", text, re.I):
        warnings.append(f"Falta “exactly {people} {'person' if people == 1 else 'people'}”: há personagens visíveis.")
    if kind == "video":
        line = (dialogue or "").strip().strip('"“”«»').strip()
        quoted = r'["“\'‘]' + re.escape(line) + r'["”\'’]|<d>[^<]*' + re.escape(line)  # aspas, 'Wan' ou <d>MiniMax</d>
        if line and line in text and not re.search(quoted, text):
            warnings.append("A fala está sem aspas (ou sem a marca de fala do modelo).")
        if mode == "references" and "start frame" in text.lower():
            warnings.append("Modos misturados: o plano usa imagens de referência, mas o prompt fala do start frame.")
        if mode == "frames" and re.search(r"\bImage \d+\b|@image\d+", text, re.I):
            warnings.append("Modos misturados: este modelo só recebe a imagem inicial (e final), mas o prompt refere imagens numeradas.")
    return warnings


def quote_dialogue(text: str, dialogue: str | None) -> str:
    """Correção automática: põe a fala entre aspas duplas se aparecer sem elas."""
    line = (dialogue or "").strip().strip('"“”«»').strip()
    if not text or not line or line not in text:
        return text
    if re.search(r'["“\'‘]' + re.escape(line) + r'["”\'’]|<d>[^<]*' + re.escape(line), text):
        return text
    return text.replace(line, f'"{line}"', 1)
