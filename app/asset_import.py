"""Importar assets de outro projeto (ex.: uma personagem da série para um spot): uma CÓPIA independente.

Copia a ficha, as imagens aprovadas (folha, retrato), a voz de referência, o guarda-roupa (looks e folhas), e nos
locais a maquete 3D, as notas e as referências das zonas, as fotos e as vistas (câmara, prompt, render e imagem). Os
ficheiros são copiados para a pasta do projeto de destino e os códigos passam para o prefixo dele (PRJ_ANA →
OUT_ANA), também dentro dos prompts e das descrições. Nada é gerado de novo.

Cada cópia lembra-se da origem (Asset.source_asset_id) e do que já copiou (Asset.source_map), para «Atualizar a partir
do original» trazer só o que mudou, sem duplicar ficheiros.
"""

import copy
import re
import shutil
from pathlib import PurePosixPath

from sqlmodel import Session, select

from app.media import media_root, safe_segment
from app.models import Asset, CharacterLook, LocationView, MediaFile, Project

BIBLE_FIELDS = ("genre", "tone", "audience", "dialogue_language", "references", "render_style", "proportions",
                "palette_light", "style_block", "aspect_ratio", "exclusions", "project_rules", "world", "world_prompt")
ASSET_FIELDS = ("label", "group_name", "canonical_description", "profile", "voice_type", "voice_timbre", "voice",
                "key_features", "sheet_prompt", "portrait_prompt", "voice_sample")
TEXT_FIELDS = ("label", "canonical_description", "profile", "voice", "key_features", "sheet_prompt", "portrait_prompt",
               "voice_sample")


class ImportError_(ValueError):
    pass


def copy_bible(src: Project, dst: Project) -> None:
    """A bíblia visual e o mundo do projeto de origem (a história do spot é dele: não se copia)."""
    for f in BIBLE_FIELDS:
        setattr(dst, f, getattr(src, f))


def importable(session: Session, project: Project) -> list[dict]:
    """Os outros projetos e os assets de cada um, para a janela de importação (o projeto de origem primeiro)."""
    done = set(session.exec(select(Asset.source_asset_id).where(Asset.project_id == project.id,
                                                                Asset.source_asset_id.is_not(None))).all())
    out = []
    for p in session.exec(select(Project).where(Project.id != project.id).order_by(Project.name)).all():
        assets = session.exec(select(Asset).where(Asset.project_id == p.id).order_by(Asset.kind, Asset.name)).all()
        if assets:
            thumbs = {m.id: m for m in session.exec(select(MediaFile).where(
                MediaFile.id.in_([a.approved_image_id for a in assets if a.approved_image_id] or [0]))).all()}
            out.append({"project": p, "parent": p.id == project.parent_id,
                        "assets": [{"asset": a, "done": a.id in done, "thumb": thumbs.get(a.approved_image_id)}
                                   for a in assets]})
    out.sort(key=lambda r: not r["parent"])
    return out


def _new_code(session: Session, dst: Project, src: Asset, taken: set[str]) -> str:
    suffix = src.name.split("_", 1)[1] if "_" in src.name else src.name
    base = f"{dst.prefix}_{suffix}"
    taken |= set(session.exec(select(Asset.name).where(Asset.project_id == dst.id)).all())
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    taken.add(code)
    return code


def _dst_rel(src_media: MediaFile, src_project: Project, src_asset: Asset, dst_project: Project, code: str) -> str:
    old = f"{safe_segment(src_project.prefix)}/assets/{safe_segment(src_asset.name)}/"
    new = f"{safe_segment(dst_project.prefix)}/assets/{safe_segment(code)}/"
    rel = new + src_media.path[len(old):] if src_media.path.startswith(old) else new + "importados/" + PurePosixPath(src_media.path).name
    path, n = PurePosixPath(rel), 2
    while (media_root() / str(path)).exists():
        path = PurePosixPath(rel).with_stem(f"{PurePosixPath(rel).stem}_{n}")
        n += 1
    return str(path)


def _wanted_media(session: Session, src: Asset, views: list[LocationView], looks: list[CharacterLook]) -> list[MediaFile]:
    """As imagens e áudios da origem que a cópia precisa: os aprovados em uso, as fotos, os renders e as
    profundidades das vistas e as referências das zonas."""
    ids = [src.approved_image_id, src.portrait_image_id, src.voice_ref_id]
    ids += [lk.image_id for lk in looks] + [v.image_id for v in views] + [v.render_id for v in views]
    ids += [int(i) for refs in (src.zone_refs or {}).values() for i in refs if str(i).isdigit()]
    found = [m for m in (session.get(MediaFile, i) for i in dict.fromkeys(i for i in ids if i)) if m]
    found += session.exec(select(MediaFile).where(MediaFile.asset_id == src.id, MediaFile.status == "approved",
                                                  MediaFile.role.in_(("photo", "locdepth")))).all()
    out, seen = [], set()
    for m in found:
        if m.id not in seen and m.status != "rejected" and (media_root() / m.path).is_file():
            seen.add(m.id)
            out.append(m)
    return out


def _sync(session: Session, src: Asset, dst: Asset, dst_project: Project) -> dict:
    """Copia (ou atualiza) tudo o que vem da origem para a cópia. Devolve o mapa de ids {media, views, looks}."""
    src_project = session.get(Project, src.project_id)
    smap = copy.deepcopy(dst.source_map or {})
    mmap, vmap, lmap = (smap.setdefault(k, {}) for k in ("media", "views", "looks"))

    for f in ASSET_FIELDS:
        setattr(dst, f, getattr(src, f))
    dst.kind = src.kind
    dst.layout3d = copy.deepcopy(src.layout3d)
    dst.zone_notes = copy.deepcopy(src.zone_notes)

    # Vistas da maquete (locais) e looks (personagens): as que já foram copiadas atualizam-se, as novas juntam-se.
    src_views = list(session.exec(select(LocationView).where(LocationView.asset_id == src.id)).all())
    for v in src_views:
        mine = session.get(LocationView, vmap.get(str(v.id), 0))
        if mine is None or mine.asset_id != dst.id:
            mine = LocationView(asset_id=dst.id, cam=copy.deepcopy(v.cam))
        mine.zone, mine.kind, mine.order, mine.label = v.zone, v.kind, v.order, v.label
        mine.cam, mine.render_hash, mine.prompt = copy.deepcopy(v.cam), v.render_hash, v.prompt
        session.add(mine)
        session.flush()
        vmap[str(v.id)] = mine.id
    src_looks = list(session.exec(select(CharacterLook).where(CharacterLook.asset_id == src.id)).all())
    for lk in src_looks:
        mine = session.get(CharacterLook, lmap.get(str(lk.id), 0))
        if mine is None or mine.asset_id != dst.id:
            mine = CharacterLook(project_id=dst_project.id, asset_id=dst.id)
        mine.name, mine.description, mine.prompt, mine.order = lk.name, lk.description, lk.prompt, lk.order
        session.add(mine)
        session.flush()
        lmap[str(lk.id)] = mine.id

    # Ficheiros: só os que ainda não foram copiados (ou cuja cópia já não existe)
    for m in _wanted_media(session, src, src_views, src_looks):
        have = session.get(MediaFile, mmap.get(str(m.id), 0))
        if have is not None and have.status != "rejected" and (media_root() / have.path).is_file():
            continue
        if m.locview_id and str(m.locview_id) not in vmap:
            continue
        rel = _dst_rel(m, src_project, src, dst_project, dst.name)
        target = media_root() / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(media_root() / m.path, target)
        new = MediaFile(project_id=dst_project.id, path=rel, kind=m.kind, role=m.role, source=m.source,
                        status="approved", duration_ms=m.duration_ms, asset_id=dst.id,
                        locview_id=vmap.get(str(m.locview_id)) if m.locview_id else None,
                        look_id=lmap.get(str(m.look_id)) if m.look_id else None)
        session.add(new)
        session.flush()
        mmap[str(m.id)] = new.id

    def mapped(i):
        return mmap.get(str(i)) if i else None

    def use(current, src_id):  # o aprovado da cópia passa a ser a cópia do aprovado da origem; o anterior volta a «por decidir»
        new_id = mapped(src_id)
        if new_id is None:
            return current
        if current and current != new_id:
            old = session.get(MediaFile, current)
            if old is not None and old.status == "approved":
                old.status = "pending"
                session.add(old)
        return new_id

    dst.approved_image_id = use(dst.approved_image_id, src.approved_image_id)
    dst.portrait_image_id = use(dst.portrait_image_id, src.portrait_image_id)
    dst.voice_ref_id = use(dst.voice_ref_id, src.voice_ref_id)
    for v in src_views:
        mine = session.get(LocationView, vmap[str(v.id)])
        mine.image_id = use(mine.image_id, v.image_id)
        mine.render_id = mapped(v.render_id) or mine.render_id
        session.add(mine)
    for lk in src_looks:
        mine = session.get(CharacterLook, lmap[str(lk.id)])
        mine.image_id = use(mine.image_id, lk.image_id)
        session.add(mine)
    dst.zone_refs = ({z: [mapped(i) for i in ids if mapped(i)] for z, ids in (src.zone_refs or {}).items()}
                     if src.zone_refs else None)
    dst.source_asset_id, dst.source_map = src.id, smap
    session.add(dst)
    return smap


def _recode(session: Session, dst_project: Project, pairs: list[tuple[Asset, Asset]]) -> None:
    """Nos textos das cópias: os códigos da origem passam aos da cópia, e os marcadores de vistas e looks
    (@CÓDIGO.v12, @CÓDIGO.r12, @CÓDIGO.look3) apontam para os ids copiados."""
    codes = {s.name: d.name for s, d in pairs}
    for d in session.exec(select(Asset).where(Asset.project_id == dst_project.id,
                                              Asset.source_asset_id.is_not(None))).all():
        s = session.get(Asset, d.source_asset_id)
        if s is not None:
            codes.setdefault(s.name, d.name)
    maps = {d.name: (d.source_map or {}) for _s, d in pairs}

    def fix(text: str | None, own: Asset) -> str | None:
        if not text:
            return text

        def marker(m: re.Match) -> str:
            old, kind, num = m.group(1), m.group(2), m.group(3)
            new = codes.get(old, old)
            table = (maps.get(new) or {}).get("looks" if kind == "look" else "views", {})
            return f"@{new}.{kind}{table.get(num, num)}"

        text = re.sub(r"@([A-Z][A-Z0-9_]*)\.(look|v|r)(\d+)\b", marker, text)
        for old in sorted(codes, key=len, reverse=True):
            text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", codes[old], text)
        return text

    for _s, d in pairs:
        for f in TEXT_FIELDS:
            setattr(d, f, fix(getattr(d, f), d))
        if d.zone_notes:
            d.zone_notes = {z: fix(t, d) for z, t in d.zone_notes.items()}
        session.add(d)
        for lk in session.exec(select(CharacterLook).where(CharacterLook.asset_id == d.id)).all():
            lk.description, lk.prompt = fix(lk.description, d), fix(lk.prompt, d)
            session.add(lk)
        for v in session.exec(select(LocationView).where(LocationView.asset_id == d.id)).all():
            v.prompt = fix(v.prompt, d)
            session.add(v)


def _sheet_refs(session: Session, dst_project: Project, pairs: list[tuple[Asset, Asset]]) -> None:
    """As referências da folha apontam para as cópias (as que não foram importadas ficam de fora)."""
    copies = {d.source_asset_id: d.id for d in session.exec(select(Asset).where(
        Asset.project_id == dst_project.id, Asset.source_asset_id.is_not(None))).all()}
    for s, d in pairs:
        keep = [i for i in (d.sheet_refs or []) if session.get(Asset, i) and session.get(Asset, i).project_id == dst_project.id]
        d.sheet_refs = list(dict.fromkeys(keep + [copies[i] for i in (s.sheet_refs or []) if i in copies]))
        session.add(d)


def import_assets(session: Session, dst_project: Project, src_ids: list[int]) -> list[Asset]:
    """Copia os assets escolhidos para o projeto. Os que já foram importados antes não se repetem."""
    already = set(session.exec(select(Asset.source_asset_id).where(Asset.project_id == dst_project.id,
                                                                   Asset.source_asset_id.is_not(None))).all())
    sources = [a for a in (session.get(Asset, i) for i in dict.fromkeys(src_ids))
               if a is not None and a.project_id != dst_project.id and a.id not in already]
    taken: set[str] = set()
    pairs = []
    for s in sources:
        d = Asset(project_id=dst_project.id, kind=s.kind, name=_new_code(session, dst_project, s, taken), label=s.label)
        session.add(d)
        session.flush()
        pairs.append((s, d))
    for s, d in pairs:
        _sync(session, s, d, dst_project)
    _recode(session, dst_project, pairs)
    _sheet_refs(session, dst_project, pairs)
    return [d for _s, d in pairs]


def missing_refs(session: Session, dst_project: Project, made: list[Asset]) -> list[str]:
    """Códigos de outros projetos que ficaram nos textos das cópias (assets referidos que não foram importados)."""
    others = {a.name for a in session.exec(select(Asset).where(Asset.project_id != dst_project.id)).all()}
    found: set[str] = set()
    for d in made:
        texts = [getattr(d, f) for f in TEXT_FIELDS] + list((d.zone_notes or {}).values())
        texts += [lk.prompt for lk in session.exec(select(CharacterLook).where(CharacterLook.asset_id == d.id)).all()]
        texts += [v.prompt for v in session.exec(select(LocationView).where(LocationView.asset_id == d.id)).all()]
        for t in texts:
            found |= {c for c in re.findall(r"[A-Z][A-Z0-9]*_[A-Z0-9_]+", t or "") if c in others}
    return sorted(found)


def refresh(session: Session, asset: Asset) -> Asset:
    """«Atualizar a partir do original»: traz as mudanças da origem (texto, imagens aprovadas, voz, looks, maquete e
    vistas). O que só existe na cópia (looks, vistas e imagens feitos aqui) fica."""
    src = session.get(Asset, asset.source_asset_id) if asset.source_asset_id else None
    if src is None:
        raise ImportError_("O asset de origem já não existe.")
    project = session.get(Project, asset.project_id)
    _sync(session, src, asset, project)
    _recode(session, project, [(src, asset)])
    _sheet_refs(session, project, [(src, asset)])
    return asset
