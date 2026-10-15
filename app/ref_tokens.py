"""Marcadores de referência nos prompts: @CÓDIGO em vez de números fixos ("Image 2", "@image2").

O prompt guarda marcadores estáveis, ligados às imagens pelo asset/cena/plano de origem:
  @AVT_VERA            folha do asset (ou a primeira imagem enviada desse asset)
  @AVT_VERA.portrait   retrato;  @LOC_BAR.v20  vista 20 da maquete do local;  @LOC_BAR.r20  render cinzento dela
  @P02.frame1          fotograma guardado de um take
  @START / @END        início e fim do clip (modelos só com imagem inicial/final)
Só no envio, já com a lista exata das imagens que seguem para o modelo, cada marcador passa à sintaxe desse modelo
("Image 1", "@image1"…). Assim o número no texto corresponde sempre à imagem certa, mesmo que as referências mudem
depois de o prompt ser escrito ou que o modelo escolhido no Gerar seja outro.
"""

import re

from sqlmodel import Session

from app.models import Asset, MediaFile, Shot

TOKEN = re.compile(r"@([A-Z][A-Z0-9_]*)(?![a-z])(?:\.([A-Za-z][A-Za-z0-9]*))?")  # @Video1 não é um código
NUMBERED = re.compile(r"\b[Ii]mage \d+\b|@image\d+|<Picture \d+>")
FRAME_WORDS = {"START": "the start frame", "END": "the end frame"}
KIND_EN = {"character": "character sheet", "location": "location reference", "prop": "prop reference",
           "element": "world element design sheet (several variants of one design family)"}


def token_for(session: Session, media: MediaFile) -> tuple[str, str]:
    """(marcador, descrição em inglês) de uma imagem de referência."""
    if media.asset_id:
        asset = session.get(Asset, media.asset_id)
        code = asset.name if asset else "ASSET"
        who = (asset.label or code) if asset else code  # nome legível: o código pode ser trocado no envio
        role = media.role or ""
        if role == "portrait":
            return f"@{code}.portrait", f"portrait of {who}"
        if role == "voice":
            return f"@{code}.voice", f"reference voice of {who} (audio)"
        if role == "look" and media.look_id:  # folha de um look do guarda-roupa: a mesma pessoa, com esta roupa
            from app.models import CharacterLook

            lk = session.get(CharacterLook, media.look_id)
            return (f"@{code}.look{media.look_id}",
                    f"character sheet of {who} wearing the “{lk.name if lk else 'look'}” outfit (same person, this "
                    "clothing and accessories)")
        if role in ("locview", "locrender") and media.locview_id:  # cada vista da maquete tem o seu marcador
            from app.models import LocationView

            v = session.get(LocationView, media.locview_id)
            if v:
                where = f" (zone “{v.zone}”)" if v.zone else ""
                if role == "locrender":
                    return (f"@{code}.r{v.id}", f"grey 3D block-out render of the camera position of view “{v.label}”"
                                                f"{where} of {who} (the geometry to follow)")
                return f"@{code}.v{v.id}", f"location view “{v.label}”{where} of {who} (no people)"
        if role == "photo":
            n = re.search(r"/v(\d+)", media.path)
            return f"@{code}.photo{n.group(1) if n else ''}", f"reference photo of the real {who}"
        return f"@{code}", f"{KIND_EN.get(asset.kind if asset else '', 'reference')} of {who}"
    shot = session.get(Shot, media.shot_id) if media.shot_id else None
    code = f"P{shot.number:02d}" if shot else "P00"
    if media.role == "shotref":  # frame de um take guardado como referência de um plano (pelo conector MCP)
        src = re.search(r"_de(\d+)_t(\d+)", media.path)
        what = f"shot P{session.get(Shot, int(src.group(1))).number:02d}" if src and session.get(Shot, int(src.group(1))) else "another shot"
        when = "the last frame" if "_fim" in media.path else f"{int(src.group(2)) / 1000:g} s" if src else ""
        return (f"@SHOT{media.shot_id}.f{media.id}",
                f"frame taken from a take of {what} at {when}: continue from it (same set, light, characters and "
                "positions)" if src else "frame taken from a take: continue from it")
    if media.role == "key":  # frame-chave do plano (app/keyframe.py): o início do take com toda a gente no sítio
        return (f"@{code}.key",
                f"key frame of shot {code}: the opening image of this shot — the place, the light, each character's look "
                "and exactly where everyone stands and faces")
    if media.role == "frame":  # fotograma guardado de um take (continuidade entre planos)
        n = re.search(r"/v(\d+)", media.path)
        return (f"@{code}.frame{n.group(1) if n else ''}",
                f"frame taken from the chosen take of shot {code}: continue from it (same set, light, characters and "
                "positions)")
    if media.role == "render":  # render de um momento da cena 3D do plano (previs: só o escritor de prompts o vê)
        n = re.search(r"_m(\d+)", media.path)
        return (f"@{code}.m{n.group(1) if n else ''}",
                f"grey 3D previs of shot {code}, moment {n.group(1) if n else ''} (people as smooth capsule figures with a dark "
                "visor on the face side; each cast member in a tint of their colour)")
    return f"@{code}.{media.role or 'frame'}", f"{media.role or 'frame'} frame of shot {code}"


def _index(session: Session, medias: list[MediaFile]) -> tuple[dict[str, int], dict[str, int], dict[int, str]]:
    exact, first, desc = {}, {}, {}
    for n, media in enumerate(medias, start=1):
        token, label = token_for(session, media)
        exact.setdefault(token[1:], n)
        first.setdefault(token[1:].split(".")[0], n)
        desc[n] = label
    return exact, first, desc


def resolve(session: Session, text: str, medias: list[MediaFile], fmt: str | None,
            frames: bool = False, bare: bool = False) -> tuple[str, list[str]]:
    """Troca os marcadores pela sintaxe do modelo, pela ordem de envio (medias).

    fmt: "Image {n}", "@image{n}"… ou None (modelo sem referências numeradas: fica o código, sem @).
    bare: também os códigos escritos sem @ (ex.: "AVT_VERA walks") dos assets que seguem como referência.
    Devolve (texto para enviar, problemas)."""
    if not text:
        return text, []
    exact, first, _desc = _index(session, medias)
    problems = []

    def swap(m: re.Match) -> str:
        code, suffix = m.group(1), m.group(2)
        key = f"{code}.{suffix}" if suffix else code
        if suffix == "voice":  # vozes de referência: trocadas à parte (routes/video.final_prompt), ficam como estão
            return m.group(0)
        if code in FRAME_WORDS and not suffix:
            if frames:
                return FRAME_WORDS[code]
            problems.append(f"@{code} só existe nos modelos que recebem só a imagem inicial (e final).")
            return FRAME_WORDS[code]
        n = exact.get(key) or (first.get(code) if not suffix else None)
        if n is None:
            if not frames:  # no modo Frames os assets entram pelo nome (as imagens são o Start/End Frame)
                problems.append(f"@{key} está no prompt mas essa imagem não vai nas referências.")
            return code
        return fmt.format(n=n) if fmt else code

    out = TOKEN.sub(swap, text)
    if bare and fmt:
        for code, n in sorted(first.items(), key=lambda kv: -len(kv[0])):
            if "_" in code:  # só códigos de asset (AVT_VERA); C01/P03 ficam como estão
                out = re.sub(r"(?<![@\w.])" + re.escape(code) + r"(?![\w])", fmt.format(n=n), out)
    if bare:  # códigos de assets sem imagem nas referências: o modelo de vídeo não os conhece — vai o nome
        out = _codes_to_names(session, out, medias)
    return out, list(dict.fromkeys(problems))


def _codes_to_names(session: Session, text: str, medias: list[MediaFile]) -> str:
    from sqlmodel import select

    from app.models import Asset

    project_id = next((m.project_id for m in medias if m), None)
    codes = set(re.findall(r"(?<![@\w.])([A-Z][A-Z0-9]*_[A-Z0-9_]+)(?![\w])", text))
    if not codes or project_id is None:
        return text
    names = {a.name: a.label for a in session.exec(select(Asset).where(Asset.project_id == project_id,
                                                                      Asset.name.in_(codes))).all()}
    for code in sorted(names, key=len, reverse=True):
        text = re.sub(r"(?<![@\w.])" + re.escape(code) + r"(?![\w])", names[code], text)
    return text


def unused(session: Session, text: str, medias: list[MediaFile]) -> list[str]:
    """Marcadores das imagens que seguem mas que o prompt não refere (nem pelo marcador, nem pelo código do asset)."""
    if not medias:
        return []
    marked, _p = resolve(session, text or "", medias, "\x00{n}\x00", bare=True)
    used = {int(n) for n in re.findall(r"\x00(\d+)\x00", marked)}
    return [token_for(session, m)[0] for n, m in enumerate(medias, start=1) if n not in used]


def check(session: Session, text: str, medias: list[MediaFile], frames: bool = False) -> list[str]:
    """Avisos para o ecrã: marcadores sem imagem e números fixos que podem apontar para a imagem errada."""
    if not text:
        return []
    _out, problems = resolve(session, text, medias, "Image {n}", frames)
    fixed = sorted(set(NUMBERED.findall(TOKEN.sub("", text))))
    if fixed:
        problems.append("Números fixos de imagem (" + ", ".join(fixed) + "): usa @CÓDIGO (ex.: @AVT_VERA) para a app "
                        "ligar cada menção à imagem certa ao gerar.")
    return problems


def medias(session: Session, ids: list[int]) -> list[MediaFile]:
    """As imagens que de facto seguem num pedido (o mesmo filtro do envio em app/jobs.py)."""
    from app.media import media_root

    out = []
    for mid in ids:
        media = session.get(MediaFile, mid)
        if media and media.status != "rejected" and (media_root() / media.path).is_file():
            out.append(media)
    return out


def legend(session: Session, medias: list[MediaFile], fmt: str | None) -> list[dict]:
    """Tabela para o ecrã: marcador → como segue para o modelo → descrição."""
    out = []
    for n, media in enumerate(medias, start=1):
        token, label = token_for(session, media)
        out.append({"token": token, "sent": fmt.format(n=n) if fmt else token[1:], "label": label})
    return out


def mentions(session: Session, medias: list[MediaFile]) -> list[dict]:
    """Opções da lista @ nas caixas de prompt (mesmo formato das menções do Assistente)."""
    out = []
    for media in medias:
        token, label = token_for(session, media)
        out.append({"code": token[1:], "kind": "", "label": label})
    return out
