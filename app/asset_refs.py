"""Imagens de referência de um asset: fotos carregadas, outros assets escolhidos para a folha, e a imagem que
representa um local (a referência aprovada de uma zona da maquete)."""

from sqlmodel import Session, select

from app.models import Asset, LocationView, MediaFile

MAX_REFS = 6


def asset_photos(session: Session, asset: Asset) -> list[MediaFile]:
    """Fotos de referência carregadas pelo utilizador (ex.: o sítio real em que o local se baseia)."""
    return list(session.exec(select(MediaFile).where(MediaFile.asset_id == asset.id, MediaFile.role == "photo",
                                                     MediaFile.status == "approved").order_by(MediaFile.id)).all())


def sheet_ref_assets(session: Session, asset: Asset) -> list[dict]:
    """Assets escolhidos como referência da folha, com as imagens aprovadas deles."""
    from app.routes.production import approved_media

    out = []
    for ref_id in asset.sheet_refs or []:
        ref = session.get(Asset, ref_id)
        if ref is None or ref.project_id != asset.project_id or ref.id == asset.id:
            continue
        ids = ref.ref_image_ids() or ([key.id] if (key := location_image(session, ref)) else [])
        media = approved_media(session, ids)
        out.append({"asset": ref, "images": [media[i] for i in ids if i in media]})
    return out


def sheet_ref_inputs(session: Session, asset: Asset) -> list[int]:
    """Referências para gerar a folha: as fotos de referência e depois os assets escolhidos."""
    photos = [m.id for m in asset_photos(session, asset)]
    return (photos + [m.id for r in sheet_ref_assets(session, asset) for m in r["images"]])[:MAX_REFS]


def location_image(session: Session, loc: Asset) -> MediaFile | None:
    """A imagem que representa um local: a referência aprovada da primeira zona que a tem; senão uma vista aprovada;
    senão a imagem geral antiga (de antes da maquete)."""
    if loc.kind != "location":
        return None
    views = session.exec(select(LocationView).where(LocationView.asset_id == loc.id, LocationView.image_id.is_not(None))
                         .order_by(LocationView.kind != "ref", LocationView.zone, LocationView.order)).all()
    for v in views:
        m = session.get(MediaFile, v.image_id)
        if m is not None and m.status == "approved":
            return m
    m = session.get(MediaFile, loc.approved_image_id) if loc.approved_image_id else None
    return m if m is not None and m.status == "approved" else None
