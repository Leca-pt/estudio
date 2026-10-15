"""Regras dos ficheiros de resultado: aprovar, rejeitar, nomes e códigos."""

from pathlib import PurePosixPath

from sqlmodel import Session, select, update

from app.models import Act, Asset, Episode, MediaFile, Project, Scene, Season, Shot

SHOT_ROLE_FIELDS = {"clip": "approved_clip_id", "guide": "guide_video_id", "key": "key_image_id"}


def scene_code(session: Session, scene: Scene) -> str:
    """C01, T1E02_C01 (séries) ou A1_C01 (filmes)."""
    code = f"C{scene.number:02d}"
    if scene.episode_id:
        episode = session.get(Episode, scene.episode_id)
        season = session.get(Season, episode.season_id) if episode else None
        if episode and season:
            return f"T{season.number}E{episode.number:02d}_{code}"
    if scene.act_id:
        act = session.get(Act, scene.act_id)
        if act:
            return f"A{act.number}_{code}"
    return code


def shot_code(session: Session, shot: Shot, scene: Scene | None = None) -> str:
    scene = scene or session.get(Scene, shot.scene_id)
    return f"{scene_code(session, scene)}_P{shot.number:02d}"


def approval_target(session: Session, media: MediaFile):
    """(objeto, campo) onde fica a aprovação deste ficheiro."""
    if media.look_id:  # folha de um look do guarda-roupa
        from app.models import CharacterLook

        return session.get(CharacterLook, media.look_id), "image_id"
    if media.role == "locview" and media.locview_id:
        from app.models import LocationView

        return session.get(LocationView, media.locview_id), "image_id"
    if media.asset_id:
        field = {"portrait": "portrait_image_id", "voice": "voice_ref_id"}.get(media.role, "approved_image_id")
        return session.get(Asset, media.asset_id), field
    if media.shot_id and media.role in SHOT_ROLE_FIELDS:
        return session.get(Shot, media.shot_id), SHOT_ROLE_FIELDS[media.role]
    if media.role == "cover":
        return session.get(Project, media.project_id), "cover_media_id"
    return None, None


def restore_media(session: Session, media: MediaFile) -> None:
    """Um rejeitado (take, imagem, voz…) volta a «por decidir» (o ficheiro ficou guardado)."""
    media.status = "pending"
    session.add(media)


def approve_media(session: Session, media: MediaFile) -> None:
    """Aprovar substitui o aprovado atual (que volta a 'por decidir')."""
    target, field = approval_target(session, media)
    if target is None:
        return
    previous_id = getattr(target, field)
    if previous_id and previous_id != media.id:
        previous = session.get(MediaFile, previous_id)
        if previous is not None and previous.status == "approved":
            previous.status = "pending"
            session.add(previous)
    media.status = "approved"
    setattr(target, field, media.id)
    session.add(media)
    session.add(target)


# O que o utilizador gerou ou carregou fica guardado ao rejeitar (em «Rejeitados», de onde se repõe); os ficheiros que a
# app refaz sozinha (vídeo-guia, renders da maquete, profundidade) apagam-se.
KEEP_ON_REJECT = {"clip", "sheet", "portrait", "look", "locview", "cover", "voice", "photo", "shotref", "frame", "key"}


def reject_media(session: Session, media: MediaFile) -> None:
    """Rejeitar: retira-o de onde estava aprovado. Takes, imagens, vozes e fotos ficam guardados em «Rejeitados» (podem
    ser repostos); os ficheiros técnicos que a app refaz apagam-se.

    As ligações como referência de planos ficam, para o plano mostrar o aviso
    de que uma referência escolhida foi rejeitada."""
    from app.media import delete_file

    if media.role not in KEEP_ON_REJECT:
        delete_file(media.path)
    media.status = "rejected"
    session.add(media)
    session.exec(update(Asset).where(Asset.approved_image_id == media.id).values(approved_image_id=None))
    session.exec(update(Asset).where(Asset.portrait_image_id == media.id).values(portrait_image_id=None))
    session.exec(update(Asset).where(Asset.voice_ref_id == media.id).values(voice_ref_id=None))
    for column in SHOT_ROLE_FIELDS.values():
        session.exec(update(Shot).where(getattr(Shot, column) == media.id).values({column: None}))
    session.exec(update(Project).where(Project.cover_media_id == media.id).values(cover_media_id=None))
    from app.models import CharacterLook

    session.exec(update(CharacterLook).where(CharacterLook.image_id == media.id).values(image_id=None))
    from app.models import LocationView

    session.exec(update(LocationView).where(LocationView.image_id == media.id).values(image_id=None))


def back_url(project: Project, media: MediaFile) -> str:
    if media.role in ("locview", "locrender", "locdepth") and media.asset_id:
        return f"/projetos/{project.id}/assets/{media.asset_id}#vista3d-{media.locview_id}"
    if media.asset_id:
        anchor = {"portrait": "retrato", "photo": "fotos", "voice": "voz", "look": "looks"}.get(media.role, "resultados")
        return f"/projetos/{project.id}/assets/{media.asset_id}#{anchor}"
    if media.role == "clip":
        return f"/projetos/{project.id}/video/planos/{media.shot_id}#" + ("take-escolhido" if media.status == "approved" else "resultados")
    if media.role == "cover":
        return f"/projetos/{project.id}/etapa/1#capa"
    if media.role == "key":
        return f"/projetos/{project.id}/storyboard/planos/{media.shot_id}#frame-chave"
    if media.role in ("guide", "render"):
        return f"/projetos/{project.id}/storyboard/planos/{media.shot_id}#maquete"
    return f"/projetos/{project.id}/storyboard/planos/{media.shot_id}"


def download_name(session: Session, project: Project, media: MediaFile) -> str:
    name = PurePosixPath(media.path).name  # vN.ext
    ext = PurePosixPath(media.path).suffix
    approved = media.status == "approved"
    if media.asset_id:
        asset = session.get(Asset, media.asset_id)
        if media.role == "portrait":
            return f"{asset.name}_portrait{ext}" if approved else f"{asset.name}_portrait_{name}"
        if media.role == "photo":
            return f"{asset.name}_photo_{name}"
        if media.role == "voice":
            return f"{asset.name}_voice{ext}" if approved else f"{asset.name}_voice_{name}"
        if media.role == "look":
            return f"{asset.name}_look{media.look_id}{ext}" if approved else f"{asset.name}_look{media.look_id}_{name}"
        if media.role in ("locview", "locrender", "locdepth"):
            from app.models import LocationView

            lv = session.get(LocationView, media.locview_id) if media.locview_id else None
            tag = "_".join(x for x in [(lv.zone or "geral") if lv else "vista", (lv.label if lv else "")] if x).replace(" ", "-")
            return f"{asset.name}_{tag}{ext}" if approved else f"{asset.name}_{tag}_{name}"
        return f"{asset.name}_sheet{ext}" if approved else f"{asset.name}_{name}"
    if media.role == "cover":
        return f"{project.prefix}_cover{ext}" if approved else f"{project.prefix}_cover_{name}"
    shot = session.get(Shot, media.shot_id)
    code = f"{project.prefix}_{shot_code(session, shot)}"
    if media.role in ("clip", "frame"):
        return f"{code}_{'frame_' if media.role == 'frame' else ''}{name}"
    return f"{code}_{media.role}{ext}" if approved else f"{code}_{media.role}_{name}"


def media_list(session: Session, **where) -> list[MediaFile]:
    stmt = select(MediaFile)
    for field, value in where.items():
        stmt = stmt.where(getattr(MediaFile, field) == value)
    return list(session.exec(stmt.order_by(MediaFile.created_at, MediaFile.id)).all())


def purge_scene_files(session: Session, scene_ids: list[int]) -> None:
    """Antes de apagar cenas: apaga do disco os ficheiros da cena e dos planos (a cascata só apaga os registos)."""
    from app.media import delete_file

    if not scene_ids:
        return
    shot_ids = list(session.exec(select(Shot.id).where(Shot.scene_id.in_(scene_ids))).all())
    stmt = select(MediaFile).where(MediaFile.scene_id.in_(scene_ids))
    rows = list(session.exec(stmt).all())
    if shot_ids:
        rows += list(session.exec(select(MediaFile).where(MediaFile.shot_id.in_(shot_ids))).all())
    for m in rows:
        delete_file(m.path)
