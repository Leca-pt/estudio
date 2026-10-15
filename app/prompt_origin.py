"""De onde vêm as partes de um prompt montado (bíblia, cena) e contradições com a hora do dia da cena."""

from app.models import Project, Scene
from app.prompt_checks import word_count
from app.prompts import time_words_in

BIBLE = "/projetos/{pid}/etapa/1"


def prompt_origin(project: Project | None, text: str | None, scene: Scene | None = None) -> dict:
    """{"parts": [(rótulo, nº de palavras, href)], "warnings": [texto]} — só o que está mesmo no prompt."""
    if not project or not text:
        return {"parts": [], "warnings": []}
    parts, warnings = [], []
    href = BIBLE.format(pid=project.id)
    for label, value in (("o mundo", project.world_prompt), ("bloco de estilo", project.style_block),
                         ("exclusões", project.exclusions)):
        value = (value or "").strip().rstrip(".")
        if value and value in text:
            parts.append((label, word_count(value), f"/projetos/{project.id}/etapa/4#mundo" if label == "o mundo" else href))
    if scene is not None and scene.time_of_day:
        found = []
        for label, value in (("bloco de estilo", project.style_block), ("exclusões", project.exclusions)):
            if (value or "").strip() and value.strip() in text:
                words = time_words_in(value)
                if words:
                    found.append(f"{label} ({', '.join(words)})")
        if found:
            warnings.append(f"A cena é «{scene.time_of_day}», mas a bíblia fala de luz/hora noutras partes: "
                            + "; ".join(found) + ". A luz de cada cena deve vir só da hora do dia: tira estas "
                            "referências do bloco de estilo e das exclusões.")
    return {"parts": parts, "warnings": warnings}


def bible_warnings(project: Project) -> dict:
    """Avisos no bloco de estilo e nas exclusões da bíblia (entram em todos os prompts)."""
    from app.prompt_checks import _find

    out = {}
    for field, limit in (("style_block", 20), ("exclusions", 15), ("world_prompt", 30)):
        value = (getattr(project, field) or "").strip()
        if not value:
            continue
        w = []
        n = word_count(value)
        if n > limit:
            w.append(f"{n} palavras: entra em todos os prompts (limite de 80 por imagem); o ideal é até {limit}.")
        times = time_words_in(value)
        if times:
            w.append("Fala de luz/hora do dia (" + ", ".join(times) + "): contradiz cenas com outra hora. "
                     "A luz vem da hora de cada cena e da «Paleta e luz».")
        fmt = _find(value, ["aspect ratio", "16:9", "9:16", "1:1", "4:3", "vertical", "horizontal", "1080p", "720p", "4k"])
        if fmt:
            w.append("Formato no texto (" + ", ".join(fmt) + "): é uma definição do modelo, não entra no prompt.")
        if w:
            out[field] = w
    return out
