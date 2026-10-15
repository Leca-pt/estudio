"""Guarda-roupa das personagens: os looks (roupa e acessórios) e o look de cada personagem em cada cena.

Sem look escolhido na cena, vale a folha da personagem (o look «Base»). Com look escolhido e com folha aprovada, é a
folha do look que segue para os vídeos dos planos dessa cena, no lugar da folha base (o retrato fica: é a cara).
"""

from sqlmodel import Session, select

from app.models import Asset, CharacterLook, Scene

BASE = "Base"


def looks_of(session: Session, asset: Asset) -> list[CharacterLook]:
    return list(session.exec(select(CharacterLook).where(CharacterLook.asset_id == asset.id)
                             .order_by(CharacterLook.order, CharacterLook.id)).all())


def scene_look(session: Session, scene: Scene | None, asset: Asset) -> CharacterLook | None:
    """O look escolhido para esta personagem nesta cena (None = a folha base)."""
    if scene is None or asset.kind != "character":
        return None
    look_id = (scene.looks or {}).get(str(asset.id))
    look = session.get(CharacterLook, look_id) if look_id else None
    return look if look is not None and look.asset_id == asset.id else None


def set_scene_look(scene: Scene, asset: Asset, look: CharacterLook | None) -> None:
    data = dict(scene.looks or {})
    if look is None:
        data.pop(str(asset.id), None)
    else:
        data[str(asset.id)] = look.id
    scene.looks = data or None


def ref_ids(session: Session, asset: Asset, scene: Scene | None) -> list[int]:
    """As imagens de referência da personagem nesta cena: a folha do look (ou a folha base) e o retrato."""
    look = scene_look(session, scene, asset)
    if look is not None and look.image_id:
        return [i for i in (look.image_id, asset.portrait_image_id) if i]
    return asset.ref_image_ids()


def label(look: CharacterLook | None) -> str:
    return look.name if look is not None else BASE


def context_line(session: Session, scene: Scene | None, asset: Asset) -> str:
    """Para o escritor de prompts: a roupa da personagem nesta cena."""
    look = scene_look(session, scene, asset)
    if look is None:
        return ""
    ready = "" if look.image_id else " (ainda sem folha aprovada: segue a folha base; descreve a roupa por palavras)"
    return (f"- Look nesta cena: «{look.name}»{ready}. Roupa e acessórios (EN): {look.description or '—'}. A roupa vem "
            "deste look, não da descrição canónica.")


def by_name(session: Session, asset: Asset, name: str) -> CharacterLook | None:
    key = (name or "").strip().lower()
    return next((lk for lk in looks_of(session, asset) if lk.name.strip().lower() == key), None)
