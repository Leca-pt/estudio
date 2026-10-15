"""Modo de leitura da Sala de argumentistas: os ficheiros como se leem, sem as marcas do markdown.

Três camadas separadas:
1. Conteúdo: o ficheiro .md, intocado (nada daqui o muda).
2. Renderização: o texto é dividido em segmentos (segment) — cenas e o resto — sem perder nenhuma linha (as linhas em
   branco contam: são elas que fecham tabelas, listas e parágrafos). O que não é cena vai inteiro para o parser de
   markdown (markdown-it-py, CommonMark + tabelas GFM). Numa cena, cada bloco é classificado pelo próprio parser:
   tabelas, listas, código e citações ficam markdown; o resto é escrita de guião (ação, personagem, indicação, fala,
   transição).
3. Estilo: as classes .rd-* em app.css (tipografia, margens, disposição).

Uma cena começa num cabeçalho clássico («INT. LOCAL — NOITE», com número e negrito opcionais) ou, no formato da sala,
num «## Título» seguido de Local:/Hora:/Resumo: (ou em qualquer «## Título» de um ficheiro com destino «cenas», como
na aprovação para a app). Acaba na cena seguinte ou no próximo título markdown que não seja uma cena. Os códigos dos
assets (TST_VERA) aparecem pelo nome (Rita). Nunca passa HTML do texto: tudo é escapado."""

import re

from markupsafe import Markup, escape
from sqlmodel import Session, select

from app.models import Asset, Project, WritingFile
from app.writing import _EXT, _META


def _names(session: Session, project: Project) -> dict[str, str]:
    return {a.name: (a.label or a.name) for a in session.exec(select(Asset).where(Asset.project_id == project.id)).all()}


def _codes_re(names: dict[str, str]):
    if not names:
        return None
    return re.compile(r"@?\b(" + "|".join(re.escape(k) for k in sorted(names, key=len, reverse=True)) + r")\b")


_CLASSIC = re.compile(r"^(?:\d+[.)]\s*)?(INT\.?/EXT\.?|EXT\.?/INT\.?|I/E|INT\.|EXT\.)\s+\S", re.I)
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
MARKDOWN_BLOCKS = {"table_open", "bullet_list_open", "ordered_list_open", "fence", "code_block", "hr", "blockquote_open",
                   "heading_open"}


def _bare(line: str) -> str:
    """A linha sem as marcas de negrito/itálico à volta (para reconhecer cabeçalhos e nomes)."""
    return line.replace("**", "").replace("__", "").strip()


def _md():
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False, "breaks": True}).enable("table").enable("strikethrough")


def _room_format(f: WritingFile, text: str) -> bool:
    """O formato do guião da sala: destino «cenas», ou «## Título» logo seguido de Local:/Hora:."""
    return (f.target or "").startswith("scenes:") or bool(
        re.search(r"^## [^\n]*\n(Local|Hora|Resumo)\s*:", text, re.M | re.I))


def is_script(f: WritingFile, text: str) -> bool:
    """Um guião: destino «cenas» ou pelo menos uma cena fora de blocos de código."""
    return _room_format(f, text) or any(s["kind"] == "scene" for s in segment(text))


def render(session: Session, project: Project, f: WritingFile, text: str) -> Markup:
    return render_text(text, _names(session, project), f.target)


def render_text(text: str, names: dict[str, str] | None = None, target: str | None = None) -> Markup:
    """O mesmo percurso que a sala usa, sem base de dados (também para os testes)."""
    f = WritingFile(project_id=0, path="", target=target)
    names = names or {}
    text = str(text or "").replace("\r\n", "\n")
    if is_script(f, text):
        return _script(text, names, (target or "").startswith("scenes:"))
    return _markdown(text, names)


def _markdown(text: str, names: dict[str, str]) -> Markup:
    """Markdown → HTML. Os códigos dos assets trocam-se pelos nomes só no texto corrido (nunca dentro de código)."""
    md = _md()
    tokens = md.parse(text)
    rx = _codes_re(names)
    if rx:
        for tok in tokens:
            for child in tok.children or []:
                if child.type == "text":
                    child.content = rx.sub(lambda m: names[m.group(1)], child.content)
    return Markup(f'<div class="rd-md">{md.renderer.render(tokens, md.options, {})}</div>')


def _inline(text: str, names: dict[str, str], rx) -> str:
    """Escapa e troca os códigos pelos nomes; **negrito** e *itálico* simples."""
    out = str(escape(text))
    if rx:
        out = rx.sub(lambda m: f'<span class="rd-ref">{escape(names[m.group(1)])}</span>', out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", out)
    return out


def _notes(text: str) -> str:
    """[[notas]] do guião em itálico."""
    return re.sub(r"\[\[(.*?)\]\]", lambda m: f"*{m.group(1).strip()}*", text, flags=re.S)


def _cue(line: str, names: dict[str, str], labels: dict[str, str]) -> tuple[str, str, str] | None:
    """«RITA», «**RITA** *(no rádio)*», «SUPERVISOR (V.O.)»… → (nome, extensão, indicação); None se não for um nome."""
    t = _bare(line).lstrip("@").replace("*", "").replace("_", " ").strip().rstrip(":").strip()
    m = re.match(r"^([^()]{1,40}?)\s*((?:\([^)]*\)\s*)*)$", t)
    if not m:
        return None
    name, rest = m.group(1).strip(), m.group(2)
    if not name or name != name.upper() or not any(c.isalpha() for c in name) or name.endswith("."):
        return None
    ext, par = [], []
    for g in re.findall(r"\(([^)]*)\)", rest):
        (ext if _EXT.fullmatch(f" ({g})") else par).append(g.strip())
    return name, " ".join(f"({e})" for e in ext), "; ".join(par)




# ── Guiões: segmentar e compor ───────────────────────────────

def _scene_start(lines: list[str], i: int, any_h2: bool) -> dict | None:
    """Se a linha i começa uma cena: {"title", "classic", "num", "meta", "next"} (next = a primeira linha do corpo)."""
    line = lines[i].strip()
    bare = _bare(line)
    head = bare.lstrip("#").strip() if bare.startswith("#") else bare
    title = classic = None
    if _CLASSIC.match(head):
        classic = head
    elif re.match(r"^##\s+\S", line) and not line.startswith("###"):
        nxt = _bare(lines[i + 1]) if i + 1 < len(lines) else ""
        if any_h2 or _META.match(nxt):
            title = line[2:].strip()
    if title is None and classic is None:
        return None
    num = None
    if classic:
        m = re.match(r"^(\d+)[.)]\s*(.*)$", classic)
        if m:
            num, classic = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    k = i + 1
    while k < len(lines):
        m = _META.match(_bare(lines[k]))
        if not m:
            break
        key = m.group(1).lower()
        k += 1
        if key == "resumo":  # pode continuar nas linhas seguintes, até uma linha vazia
            more = []
            while k < len(lines) and lines[k].strip() and not _META.match(_bare(lines[k])):
                more.append(lines[k].strip())
                k += 1
            meta[key] = " ".join([m.group(2).strip()] + more).strip()
        else:
            meta[key] = m.group(2).strip()
    return {"title": title, "classic": classic, "num": num, "meta": meta, "next": k}


def segment(text: str, any_h2: bool = False) -> list[dict]:
    """Divide o guião em segmentos, por ordem, sem perder linhas: {"kind": "md", "lines"} (tudo o que não é cena) e
    {"kind": "scene", "title", "classic", "num", "meta", "lines"}. Dentro de blocos de código (```) não há cenas."""
    lines = text.split("\n")
    out: list[dict] = [{"kind": "md", "lines": []}]
    fence = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if fence:
            out[-1]["lines"].append(ln)
            if ln.strip().startswith(fence):
                fence = None
            i += 1
            continue
        m = _FENCE.match(ln)
        if m:
            fence = m.group(1)
            out[-1]["lines"].append(ln)
            i += 1
            continue
        start = _scene_start(lines, i, any_h2)
        if start:
            out.append({"kind": "scene", **{k: v for k, v in start.items() if k != "next"}, "lines": []})
            i = start["next"]
            continue
        if out[-1]["kind"] == "scene" and _MD_HEADING.match(ln):  # um título que não é cena fecha a cena
            out.append({"kind": "md", "lines": []})
        out[-1]["lines"].append(ln)
        i += 1
    return [s for s in out if s["kind"] == "scene" or any(x.strip() for x in s["lines"])]


def _blocks(lines: list[str]) -> list[list[str]]:
    """Os blocos de uma cena: parágrafos separados por linhas vazias; um bloco de código (```) fica inteiro."""
    out, cur, fence = [], [], None
    for ln in lines:
        if fence:
            cur.append(ln)
            if ln.strip().startswith(fence):
                fence = None
            continue
        m = _FENCE.match(ln)
        if m:
            fence = m.group(1)
            cur.append(ln)
            continue
        if ln.strip():
            cur.append(ln)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _script(text: str, names: dict[str, str], any_h2: bool) -> Markup:
    rx = _codes_re(names)
    labels = {re.sub(r"[^A-Z0-9]+", "", (lbl or "").upper()): lbl for lbl in names.values()}
    md = _md()
    html: list[str] = ['<div class="rd-script">']
    n_scene = 0

    def shown_name(name: str) -> str:
        code = name.upper().replace(" ", "_")
        return (names.get(code) or names.get(name) or name).upper()

    last = {"cue": None}  # quem falou por último na cena (uma fala solta «> …» depois de uma ação continua a dele)

    def dialogue(cue: tuple[str, str, str], lines: list[str]) -> str:
        name, ext, par = cue
        if name and not ext.endswith("(cont.)"):
            last["cue"] = (name, ext)
        parts = [f'<div class="rd-cue">{escape(shown_name(name))}'
                 + (f' <span class="rd-ext">{escape(ext)}</span>' if ext else "") + "</div>"] if name else []
        if par:
            parts.append(f'<div class="rd-par">({_inline(par, names, rx)})</div>')
        for ln in lines:
            ln = ln.strip().rstrip("\\").strip()
            if not ln:
                continue
            bare = ln.replace("*", "").strip()
            if bare.startswith("(") and bare.endswith(")"):
                parts.append(f'<div class="rd-par">{_inline(bare, names, rx)}</div>')
            else:
                parts.append(f'<div class="rd-line">{_inline(ln, names, rx)}</div>')
        return '<div class="rd-dlg">' + "".join(parts) + "</div>"

    for seg in segment(text.replace("&#32;", " "), any_h2):
        if seg["kind"] == "md":
            html.append(str(_markdown(_notes("\n".join(seg["lines"])), names)))
            continue
        n_scene += 1
        meta, title, classic = seg["meta"], seg["title"], seg["classic"]
        slug = classic or " — ".join(x for x in (meta.get("local"), meta.get("hora")) if x)
        html.append(f'<div class="rd-scene"><div class="rd-n">{escape(seg["num"] or f"C{n_scene:02d}")}</div>'
                    f'<div class="rd-h">{_inline(title or slug or f"Cena {n_scene}", names, rx)}</div>'
                    + (f'<div class="rd-slug">{_inline(slug, names, rx)}</div>' if title and slug else "")
                    + (f'<p class="rd-sum">{_inline(meta["resumo"], names, rx)}</p>' if meta.get("resumo") else "")
                    + "</div>")
        blocks = _blocks(seg["lines"])
        last["cue"] = None
        b = 0
        while b < len(blocks):
            block = [x.strip() for x in blocks[b]]
            first = block[0]
            if len(block) == 1 and re.fullmatch(r"\[\[.*\]\]", first):
                html.append(f'<p class="rd-note">{_inline(first[2:-2], names, rx)}</p>')
                b += 1
                continue
            if first.startswith("!"):  # ação forçada
                html.append('<p class="rd-act">' + "<br>".join(_inline(x.lstrip("!"), names, rx) for x in block) + "</p>")
                b += 1
                continue
            raw = "\n".join(blocks[b])
            tokens = md.parse(raw)
            if tokens and tokens[0].type in MARKDOWN_BLOCKS - {"blockquote_open"}:  # tabela, lista, código…: markdown
                html.append(str(_markdown(raw, names)))
                b += 1
                continue
            cue = _cue(first, names, labels)
            if cue and len(block) > 1:  # NOME e a fala logo a seguir
                html.append(dialogue(cue, block[1:]))
                b += 1
                continue
            if cue and len(block) == 1:
                if b + 1 < len(blocks) and blocks[b + 1][0].strip().startswith(">"):  # NOME e a fala como citação
                    html.append(dialogue(cue, [x.strip().lstrip(">") for x in blocks[b + 1]]))
                    b += 2
                    continue
                if _bare(first).endswith(":"):  # transição (CORTA PARA:)
                    html.append(f'<div class="rd-trans">{escape(_bare(first))}</div>')
                    b += 1
                    continue
            one = re.match(r"^@?([^:()]{1,40}?)\s*(\([^)]*\))?\s*:\s+(\S.*)$", _bare(first))
            if one and (one.group(1).upper().replace(" ", "_") in names
                        or re.sub(r"[^A-Z0-9]+", "", one.group(1).upper()) in labels):
                html.append(dialogue((one.group(1).strip(), "", (one.group(2) or "").strip("() ")), [one.group(3)] + block[1:]))
                b += 1
                continue
            if tokens and tokens[0].type == "blockquote_open":  # fala solta: continua a de quem falou por último
                if last["cue"]:
                    name, ext = last["cue"]
                    html.append(dialogue((name, (ext + " (cont.)").strip(), ""), [x.strip().lstrip(">") for x in blocks[b]]))
                else:
                    html.append(dialogue(("", "", ""), [x.strip().lstrip(">") for x in blocks[b]]))
            else:
                html.append('<p class="rd-act">' + "<br>".join(_inline(x, names, rx) for x in block) + "</p>")
            b += 1
    html.append("</div>")
    return Markup("".join(html))
