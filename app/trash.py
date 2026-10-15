"""Reciclagem: tudo o que se apaga (na app ou pelo conector MCP) — temporadas, episódios, atos, cenas, planos e assets —
fica recuperável durante 30 dias.

Apagar guarda uma fotografia completa (a cena com as linhas do guião, os assets ligados e os planos; cada plano com as
verificações, as linhas que cobre e os ficheiros) e muda os ficheiros para media/_reciclagem/<id>/ em vez de os apagar.
Depois apaga como a app (a cascata leva o resto). Recuperar recria tudo com ids novos, devolve os ficheiros aos sítios
e acerta as referências (vídeo-guia, take escolhido, renders dos momentos, versões dos prompts).
"""

import shutil
import time
from pathlib import Path, PurePosixPath

from sqlmodel import Session, select

from app.media import media_root
from app.models import (Asset, AssetSceneLink, CharacterLook, LocationView, MediaFile, Project, PromptVersion, Scene,
                        SceneLine, Shot, ShotCheck, ShotLineLink, TrashItem)

KEEP_DAYS = 30
SKIP = {"id", "created_at", "updated_at"}


def _row(obj) -> dict:
    return {k: v for k, v in obj.model_dump().items() if k not in SKIP}


def _folder(item_id: int) -> PurePosixPath:
    return PurePosixPath("_reciclagem", str(item_id))


def _move_files(media: list[MediaFile], folder: PurePosixPath, back: bool = False) -> None:
    from app.media import companions, delete_file, prune_empty_dirs

    root = media_root()
    for m in media:
        src, dst = root / m.path, root / folder / m.path
        if back:
            src, dst = dst, src
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            prune_empty_dirs(src)
        if not back:
            for extra in companions(m.path):  # derivados (refazem-se quando forem precisos)
                delete_file(extra)


def _shot_snapshot(session: Session, shot: Shot, line_order: dict[int, int]) -> dict:
    media = session.exec(select(MediaFile).where(MediaFile.shot_id == shot.id)).all()
    return {
        "id": shot.id, "row": _row(shot),
        "checks": [_row(c) for c in session.exec(select(ShotCheck).where(ShotCheck.shot_id == shot.id)).all()],
        "lines": [line_order[link.line_id] for link in session.exec(select(ShotLineLink).where(ShotLineLink.shot_id == shot.id)).all()
                  if link.line_id in line_order],
        "media": [{"id": m.id, **_row(m)} for m in media],
    }


def _scene_snapshot(session: Session, scene: Scene) -> tuple[dict, list[MediaFile]]:
    """A fotografia completa de uma cena (guião, assets ligados, ficheiros e planos) e os ficheiros a guardar."""
    lines = session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order)).all()
    order = {ln.id: ln.order for ln in lines}
    shots = session.exec(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.number)).all()
    media = session.exec(select(MediaFile).where(MediaFile.scene_id == scene.id)).all()
    data = {
        "scene": _row(scene), "old_id": scene.id,
        "lines": [_row(ln) for ln in lines],
        "assets": [link.asset_id for link in session.exec(select(AssetSceneLink).where(AssetSceneLink.scene_id == scene.id)).all()],
        "media": [{"id": m.id, **_row(m)} for m in media],
        "shots": [_shot_snapshot(session, sh, order) for sh in shots],
    }
    all_media = list(media) + [m for sh in shots for m in session.exec(select(MediaFile).where(MediaFile.shot_id == sh.id)).all()]
    return data, all_media


def trash_scene(session: Session, project: Project, scene: Scene) -> TrashItem:
    from app.routes.story import _renumber

    data, all_media = _scene_snapshot(session, scene)
    item = TrashItem(project_id=project.id, kind="scene", label=f"Cena {scene.number} «{scene.title or '—'}»", data=data,
                     expires_at=time.time() + KEEP_DAYS * 86400)
    session.add(item)
    session.flush()
    _move_files(all_media, _folder(item.id))
    siblings = session.exec(select(Scene).where(Scene.project_id == scene.project_id, Scene.episode_id == scene.episode_id,
                                                Scene.act_id == scene.act_id, Scene.id != scene.id).order_by(Scene.number)).all()
    session.delete(scene)
    session.flush()
    _renumber(session, list(siblings))
    return item


def trash_container(session: Session, project: Project, obj) -> TrashItem:
    """Temporada, episódio ou ato, com as cenas (e os episódios da temporada) — tudo recuperável."""
    from app.models import Act, Episode, Season
    from app.routes.story import _renumber

    def scenes_of(where) -> list[Scene]:
        return list(session.exec(select(Scene).where(where).order_by(Scene.number)).all())

    media: list[MediaFile] = []

    def snap_scenes(scenes: list[Scene]) -> list[dict]:
        out = []
        for sc in scenes:
            d, m = _scene_snapshot(session, sc)
            out.append(d)
            media.extend(m)
        return out

    if isinstance(obj, Season):
        eps = list(session.exec(select(Episode).where(Episode.season_id == obj.id).order_by(Episode.number)).all())
        data = {"type": "season", "row": _row(obj), "old_id": obj.id,
                "episodes": [{"row": _row(e), "old_id": e.id, "scenes": snap_scenes(scenes_of(Scene.episode_id == e.id))}
                             for e in eps]}
        label = f"Temporada {obj.number}" + (f" «{obj.title}»" if obj.title else "") + f" ({len(eps)} episódio(s))"
        siblings = session.exec(select(Season).where(Season.project_id == obj.project_id, Season.id != obj.id)
                                .order_by(Season.number)).all()
    elif isinstance(obj, Episode):
        season = session.get(Season, obj.season_id)
        data = {"type": "episode", "row": _row(obj), "old_id": obj.id,
                "scenes": snap_scenes(scenes_of(Scene.episode_id == obj.id))}
        label = f"T{season.number if season else '?'} E{obj.number:02d}" + (f" «{obj.title}»" if obj.title else "")
        siblings = session.exec(select(Episode).where(Episode.season_id == obj.season_id, Episode.id != obj.id)
                                .order_by(Episode.number)).all()
    elif isinstance(obj, Act):
        data = {"type": "act", "row": _row(obj), "old_id": obj.id, "scenes": snap_scenes(scenes_of(Scene.act_id == obj.id))}
        label = f"Ato {obj.number}" + (f" «{obj.title}»" if obj.title else "")
        siblings = session.exec(select(Act).where(Act.project_id == obj.project_id, Act.id != obj.id)
                                .order_by(Act.number)).all()
    else:
        raise ValueError("Não se pode mandar isto para a Reciclagem.")
    item = TrashItem(project_id=project.id, kind=data["type"], label=label, data=data,
                     expires_at=time.time() + KEEP_DAYS * 86400)
    session.add(item)
    session.flush()
    _move_files(media, _folder(item.id))
    session.delete(obj)  # a cascata leva as cenas (já fotografadas e com os ficheiros guardados)
    session.flush()
    _renumber(session, list(siblings))
    return item


def trash_shot(session: Session, project: Project, shot: Shot) -> TrashItem:
    from app.routes.story import _renumber

    scene = session.get(Scene, shot.scene_id)
    order = {ln.id: ln.order for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)).all()}
    snap = _shot_snapshot(session, shot, order)
    item = TrashItem(project_id=project.id, kind="shot", label=f"Cena {scene.number} · P{shot.number:02d} «{shot.title or '—'}»",
                     data={"scene_id": scene.id, "shot": snap}, expires_at=time.time() + KEEP_DAYS * 86400)
    session.add(item)
    session.flush()
    _move_files(session.exec(select(MediaFile).where(MediaFile.shot_id == shot.id)).all(), _folder(item.id))
    siblings = session.exec(select(Shot).where(Shot.scene_id == scene.id, Shot.id != shot.id).order_by(Shot.number)).all()
    session.delete(shot)
    session.flush()
    _renumber(session, list(siblings))
    return item


MEDIA_FKS = {Asset: ("approved_image_id", "portrait_image_id", "voice_ref_id"), CharacterLook: ("image_id",),
             LocationView: ("render_id", "image_id")}


def usage(session: Session, asset: Asset) -> dict:
    """Onde o asset é usado: o que a reciclagem tem de repor (e o que o Claude deve dizer antes de apagar)."""
    scenes = session.exec(select(Scene).where(Scene.project_id == asset.project_id)).all()
    return {
        "scenes": list(session.exec(select(AssetSceneLink.scene_id).where(AssetSceneLink.asset_id == asset.id)).all()),
        "location_of": [sc.id for sc in scenes if sc.location_id == asset.id],
        "set3d_of": [sc.id for sc in scenes if sc.set3d_asset_id == asset.id],
        "lines": list(session.exec(select(SceneLine.id).where(SceneLine.character_id == asset.id)).all()),
        "dialogue_of": list(session.exec(select(Shot.id).where(Shot.dialogue_character_id == asset.id)).all()),
        "sheet_ref_of": [a.id for a in session.exec(select(Asset).where(Asset.project_id == asset.project_id)).all()
                         if asset.id in (a.sheet_refs or [])],
        "looks_in": {str(sc.id): sc.looks[str(asset.id)] for sc in scenes if sc.looks and str(asset.id) in sc.looks},
        "source_of": list(session.exec(select(Asset.id).where(Asset.source_asset_id == asset.id)).all()),
        "in_stage": [sc.id for sc in scenes if sc.stage3d and any(a.get("code") == asset.name for a in sc.stage3d.get("actors") or [])],
    }


def trash_asset(session: Session, project: Project, asset: Asset) -> TrashItem:
    looks = session.exec(select(CharacterLook).where(CharacterLook.asset_id == asset.id)).all()
    views = session.exec(select(LocationView).where(LocationView.asset_id == asset.id)).all()
    media = session.exec(select(MediaFile).where(MediaFile.asset_id == asset.id)).all()
    media += [m for m in session.exec(select(MediaFile).where(MediaFile.look_id.in_([lk.id for lk in looks] or [0]))).all() if m not in media]
    media += [m for m in session.exec(select(MediaFile).where(MediaFile.locview_id.in_([v.id for v in views] or [0]))).all() if m not in media]
    data = {"asset": {"id": asset.id, **_row(asset)}, "looks": [{"id": x.id, **_row(x)} for x in looks],
            "views": [{"id": x.id, **_row(x)} for x in views], "media": [{"id": m.id, **_row(m)} for m in media],
            "usage": usage(session, asset)}
    kind = {"character": "Personagem", "location": "Local", "prop": "Adereço", "element": "Elemento"}.get(asset.kind, "Asset")
    item = TrashItem(project_id=project.id, kind="asset", label=f"{kind} {asset.label or asset.name} ({asset.name})", data=data,
                     expires_at=time.time() + KEEP_DAYS * 86400)
    session.add(item)
    session.flush()
    _move_files(media, _folder(item.id))
    for sc in session.exec(select(Scene).where(Scene.project_id == project.id)).all():
        if sc.looks and str(asset.id) in sc.looks:
            sc.looks = {k: v for k, v in sc.looks.items() if k != str(asset.id)}
            session.add(sc)
    for a in session.exec(select(Asset).where(Asset.project_id == project.id)).all():
        if asset.id in (a.sheet_refs or []):
            a.sheet_refs = [i for i in a.sheet_refs if i != asset.id] or None
            session.add(a)
    for obj in (asset, *looks, *views):  # as ligações aos ficheiros saem antes de os registos sumirem
        for f in MEDIA_FKS.get(type(obj), ()):
            setattr(obj, f, None)
        session.add(obj)
    session.flush()
    for m in media:
        session.delete(m)
    session.flush()
    session.delete(asset)  # looks, vistas e ligações às cenas em cascata; cabeçalhos e falas ficam sem ele
    return item


def _restore_asset(session: Session, item: TrashItem) -> str:
    d = item.data
    a = d["asset"]
    if session.get(Asset, a["id"]) is not None:
        raise ValueError("Já existe outro asset com o mesmo id; não é possível recuperar este.")
    if session.exec(select(Asset).where(Asset.project_id == a["project_id"], Asset.name == a["name"])).first():
        raise ValueError(f"Já existe outro asset com o código {a['name']}: muda-lhe o código e volta a tentar.")
    held = {}
    row = dict(a)
    for f in MEDIA_FKS[Asset]:
        held[("asset", f)] = row.pop(f, None)
    session.add(Asset(**row))
    session.flush()
    looks_held, views_held = [], []
    for x in d["looks"]:
        r = dict(x); looks_held.append((r["id"], r.pop("image_id", None)))
        session.add(CharacterLook(**r))
    for x in d["views"]:
        r = dict(x); views_held.append((r["id"], r.pop("render_id", None), r.pop("image_id", None)))
        session.add(LocationView(**r))
    session.flush()
    for r in d["media"]:
        session.add(MediaFile(**r))
    session.flush()
    _move_files([MediaFile(path=r["path"]) for r in d["media"]], _folder(item.id), back=True)
    asset = session.get(Asset, a["id"])
    for f in MEDIA_FKS[Asset]:
        setattr(asset, f, held[("asset", f)])
    for lid, img in looks_held:
        lk = session.get(CharacterLook, lid); lk.image_id = img; session.add(lk)
    for vid, render, img in views_held:
        v = session.get(LocationView, vid); v.render_id, v.image_id = render, img; session.add(v)
    u = d["usage"]
    for sid in u["scenes"]:
        if session.get(Scene, sid) and not session.get(AssetSceneLink, (asset.id, sid)):
            session.add(AssetSceneLink(asset_id=asset.id, scene_id=sid))
    for model, ids, attr in ((Scene, u["location_of"], "location_id"), (Scene, u["set3d_of"], "set3d_asset_id"),
                             (SceneLine, u["lines"], "character_id"), (Shot, u["dialogue_of"], "dialogue_character_id"),
                             (Asset, u["source_of"], "source_asset_id")):
        for i in ids:
            obj = session.get(model, i)
            if obj is not None and getattr(obj, attr) is None:
                setattr(obj, attr, asset.id)
                session.add(obj)
    for i in u["sheet_ref_of"]:
        other = session.get(Asset, i)
        if other is not None and asset.id not in (other.sheet_refs or []):
            other.sheet_refs = [*(other.sheet_refs or []), asset.id]
            session.add(other)
    for sid, look in u["looks_in"].items():
        sc = session.get(Scene, int(sid))
        if sc is not None:
            sc.looks = {**(sc.looks or {}), str(asset.id): look}
            session.add(sc)
    session.add(asset)
    return item.label


def _remap_versions(session: Session, project_id: int, entity: str, old: int, new: int) -> None:
    for v in session.exec(select(PromptVersion).where(PromptVersion.project_id == project_id,
                                                      PromptVersion.field_key.startswith(f"{entity}:{old}."))).all():
        v.field_key = f"{entity}:{new}." + v.field_key.split(".", 1)[1]
        session.add(v)


def _restore_media(session: Session, rows: list[dict], **fk) -> dict[int, int]:
    ids = {}
    for r in rows:
        old = r["id"]
        m = MediaFile(**{**{k: v for k, v in r.items() if k != "id"}, **fk})
        session.add(m)
        session.flush()
        ids[old] = m.id
    return ids


def _restore_shot(session: Session, project_id: int, scene: Scene, snap: dict, lines_by_order: dict[int, int],
                  folder: PurePosixPath, number: int | None = None) -> Shot:
    row = dict(snap["row"], scene_id=scene.id)
    if number is not None:
        row["number"] = number
    old_guide, old_clip = row.pop("guide_video_id", None), row.pop("approved_clip_id", None)  # ficheiros ainda por recriar
    shot = Shot(**row)
    session.add(shot)
    session.flush()
    for c in snap["checks"]:
        session.add(ShotCheck(**dict(c, shot_id=shot.id)))
    for o in snap["lines"]:
        if o in lines_by_order:
            session.add(ShotLineLink(shot_id=shot.id, line_id=lines_by_order[o]))
    ids = _restore_media(session, snap["media"], shot_id=shot.id, scene_id=None)
    _move_files([MediaFile(path=r["path"]) for r in snap["media"]], folder, back=True)
    shot.guide_video_id = ids.get(old_guide) if old_guide else None
    shot.approved_clip_id = ids.get(old_clip) if old_clip else None
    if shot.video_refs:
        shot.video_refs = [ids.get(x, x) for x in shot.video_refs]
    if shot.moments:
        mom = dict(shot.moments)
        if mom.get("renders"):
            mom["renders"] = {k: ids.get(v, v) for k, v in mom["renders"].items()}
        for key in ("guide_hashes",):
            if mom.get(key):
                mom[key] = {str(ids.get(int(k), k)): v for k, v in mom[key].items()}
        shot.moments = mom
    session.add(shot)
    _remap_versions(session, project_id, "shot", snap["id"], shot.id)
    return shot


def _restore_scene(session: Session, project_id: int, d: dict, folder: PurePosixPath, reorder: bool = True,
                   **parent) -> Scene:
    from app.routes.story import _renumber

    scene = Scene(**{**d["scene"], **parent})
    session.add(scene)
    session.flush()
    by_order = {}
    for ln in d["lines"]:
        new = SceneLine(**dict(ln, scene_id=scene.id))
        session.add(new)
        session.flush()
        by_order[ln["order"]] = new.id
    for aid in d["assets"]:
        if session.get(Asset, aid):
            session.add(AssetSceneLink(asset_id=aid, scene_id=scene.id))
    _restore_media(session, d["media"], scene_id=scene.id, shot_id=None)
    _move_files([MediaFile(path=r["path"]) for r in d["media"]], folder, back=True)
    for snap in d["shots"]:
        _restore_shot(session, project_id, scene, snap, by_order, folder)
    _remap_versions(session, project_id, "scene", d["old_id"], scene.id)
    if reorder:  # volta ao lugar que tinha (as seguintes andam uma para a frente)
        siblings = session.exec(select(Scene).where(Scene.project_id == scene.project_id, Scene.episode_id == scene.episode_id,
                                                    Scene.act_id == scene.act_id, Scene.id != scene.id).order_by(Scene.number)).all()
        ordered = list(siblings)
        ordered.insert(min(max(0, (d["scene"]["number"] or 1) - 1), len(ordered)), scene)
        _renumber(session, ordered)
    return scene


def _restore_container(session: Session, item: TrashItem, folder: PurePosixPath) -> str:
    from app.models import Act, Episode, Season
    from app.routes.story import _renumber

    d = item.data

    def put_back(obj, siblings, number):
        ordered = list(siblings)
        ordered.insert(min(max(0, (number or 1) - 1), len(ordered)), obj)
        _renumber(session, ordered)

    if d["type"] == "season":
        season = Season(**d["row"])
        session.add(season)
        session.flush()
        for e in d["episodes"]:
            ep = Episode(**dict(e["row"], season_id=season.id))
            session.add(ep)
            session.flush()
            for sd in e["scenes"]:
                _restore_scene(session, item.project_id, sd, folder, reorder=False, episode_id=ep.id)
        put_back(season, session.exec(select(Season).where(Season.project_id == season.project_id, Season.id != season.id)
                                      .order_by(Season.number)).all(), d["row"].get("number"))
    elif d["type"] == "episode":
        if session.get(Season, d["row"]["season_id"]) is None:
            raise ValueError("A temporada deste episódio já não existe: recupera primeiro a temporada.")
        ep = Episode(**d["row"])
        session.add(ep)
        session.flush()
        for sd in d["scenes"]:
            _restore_scene(session, item.project_id, sd, folder, reorder=False, episode_id=ep.id)
        put_back(ep, session.exec(select(Episode).where(Episode.season_id == ep.season_id, Episode.id != ep.id)
                                  .order_by(Episode.number)).all(), d["row"].get("number"))
    else:
        act = Act(**d["row"])
        session.add(act)
        session.flush()
        for sd in d["scenes"]:
            _restore_scene(session, item.project_id, sd, folder, reorder=False, act_id=act.id)
        put_back(act, session.exec(select(Act).where(Act.project_id == act.project_id, Act.id != act.id)
                                   .order_by(Act.number)).all(), d["row"].get("number"))
    return item.label


def restore(session: Session, item: TrashItem) -> str:
    from app.routes.story import _renumber

    folder = _folder(item.id)
    if item.kind == "asset":
        label = _restore_asset(session, item)
        shutil.rmtree(media_root() / folder, ignore_errors=True)
        session.delete(item)
        return label
    if item.kind in ("season", "episode", "act"):
        label = _restore_container(session, item, folder)
    elif item.kind == "scene":
        _restore_scene(session, item.project_id, item.data, folder)
        label = item.label
    else:
        scene = session.get(Scene, item.data["scene_id"])
        if scene is None:
            raise ValueError("A cena deste plano já não existe: recupera primeiro a cena.")
        by_order = {ln.order: ln.id for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)).all()}
        shot = _restore_shot(session, item.project_id, scene, item.data["shot"], by_order, folder)
        siblings = session.exec(select(Shot).where(Shot.scene_id == scene.id, Shot.id != shot.id).order_by(Shot.number)).all()
        ordered = list(siblings)
        ordered.insert(min(max(0, (item.data["shot"]["row"]["number"] or 1) - 1), len(ordered)), shot)
        _renumber(session, ordered)
        label = item.label
    shutil.rmtree(media_root() / folder, ignore_errors=True)
    session.delete(item)
    return label


def purge_expired(session: Session) -> int:
    """Apaga de vez o que passou dos 30 dias (os ficheiros e a fotografia)."""
    gone = session.exec(select(TrashItem).where(TrashItem.expires_at < time.time())).all()
    for item in gone:
        shutil.rmtree(media_root() / _folder(item.id), ignore_errors=True)
        session.delete(item)
    return len(gone)


def items(session: Session, project_id: int) -> list[TrashItem]:
    return list(session.exec(select(TrashItem).where(TrashItem.project_id == project_id)
                             .order_by(TrashItem.id.desc())).all())


def path_of(item_id: int) -> Path:
    return media_root() / _folder(item_id)
