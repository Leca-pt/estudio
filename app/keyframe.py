"""Frame-chave do plano: uma imagem do início do take com as personagens já no sítio certo.

Com a cena 3D, parte do previs do primeiro momento (a câmara do plano, com o elenco em cápsulas a cores): o gerador
de imagem recebe esse render como geometria, a vista do local desse momento (o aspeto do sítio) e as folhas de quem a
câmara vê. Sem cena 3D, o escritor compõe a imagem pela descrição do plano, com as mesmas folhas e a imagem do local.

É o último passo do Storyboard do plano (gera-se e aprova-se lá; a página Vídeo e a Montagem só o mostram). Aprovado,
segue no pedido de vídeo como referência (a primeira imagem): o modelo vê logo quem está onde, sem o texto ter de o
explicar. Não substitui o vídeo-guia (que continua a ser escolha do plano, em «O que segue no pedido») nem é a
frame inicial (só se o utilizador a escolher assim).
"""

from sqlmodel import Session, select

from app import looks, moments
from app.memo import memo
from app.models import Asset, ImageJob, MediaFile, Scene, Shot

ROLE = "key"


@memo
def first_moment(session: Session, shot: Shot) -> dict | None:
    """O primeiro momento da câmara (com o render e a vista do local), ou None sem cena 3D."""
    if not shot.scene3d:
        return None
    rows = moments.rows(session, shot)
    return rows[0] if rows else None


def render(session: Session, shot: Shot) -> MediaFile | None:
    """O previs do primeiro momento, se está atualizado (tirado com a câmara e a encenação atuais)."""
    r = first_moment(session, shot)
    return r["render"] if r else None


def _location_image(session: Session, shot: Shot) -> MediaFile | None:
    """O aspeto do sítio: a vista do local do primeiro momento ou, sem cena 3D, a imagem do local da cena."""
    r = first_moment(session, shot)
    if r is not None:
        return r["image"]
    from app.asset_refs import location_image

    scene = session.get(Scene, shot.scene_id)
    loc = session.get(Asset, scene.location_id) if scene and scene.location_id else None
    return location_image(session, loc) if loc is not None else None


def cast(session: Session, shot: Shot) -> list[MediaFile]:
    """As folhas (do look da cena) e os retratos de quem aparece: os que a câmara vê ou, sem cena 3D, as personagens da
    cena."""
    if shot.scene3d:
        return moments.cast_images(session, shot)
    from app.routes.production import approved_media, scene_assets

    scene = session.get(Scene, shot.scene_id)
    ids = [i for a in scene_assets(session, scene) if a.kind == "character" for i in looks.ref_ids(session, a, scene)]
    found = approved_media(session, ids)
    return [found[i] for i in dict.fromkeys(ids) if i in found]


@memo
def inputs(session: Session, shot: Shot) -> list[MediaFile]:
    """O que segue para o gerador, por ordem: o previs (a geometria), a vista do local e as folhas do elenco."""
    out = [m for m in (render(session, shot), _location_image(session, shot)) if m is not None]
    for m in cast(session, shot):
        if m not in out:
            out.append(m)
    return out


def missing(session: Session, shot: Shot) -> str | None:
    """Porque ainda não se pode gerar (None = pode)."""
    if shot.scene3d and render(session, shot) is None:
        return ("Falta o previs do primeiro momento: abre o plano no Storyboard (a página tira-o sozinha; pelo conector, "
                f"render_maquete com sb_shot:{shot.id}).")
    return None


def approved(session: Session, shot: Shot) -> MediaFile | None:
    m = session.get(MediaFile, shot.key_image_id) if shot.key_image_id else None
    return m if m is not None and m.status == "approved" else None


def made_from(session: Session, media: MediaFile) -> str | None:
    """A versão da cena 3D (moments.render_hash) com que esta imagem foi pedida."""
    job = session.exec(select(ImageJob).where(ImageJob.result_media_id == media.id)).first()
    return (job.params or {}).get("hash") if job else None


def stale(session: Session, shot: Shot) -> bool:
    """O frame-chave aprovado foi feito com outra câmara ou outra encenação (as posições já não batem certo)."""
    m = approved(session, shot)
    if m is None or not shot.scene3d:
        return False
    was = made_from(session, m)
    return bool(was) and was != moments.render_hash(session, shot)


def previous(session: Session, shot: Shot) -> MediaFile | None:
    """O frame-chave aprovado do plano anterior da cena (para o escritor manter a luz, a roupa e o sítio)."""
    prev = session.exec(select(Shot).where(Shot.scene_id == shot.scene_id, Shot.number < shot.number)
                        .order_by(Shot.number.desc())).first()
    return approved(session, prev) if prev is not None else None


def cast_colours(session: Session, shot: Shot) -> list[str]:
    """Para o escritor: a cor da cápsula de cada personagem no previs."""
    from app import scene3d

    scene = session.get(Scene, shot.scene_id)
    cast = scene3d.scene_cast(session, scene)
    out = []
    for code in scene3d.in_frame(session, shot):
        a = session.exec(select(Asset).where(Asset.project_id == scene.project_id, Asset.name == code)).first()
        if a is not None and code in cast:
            out.append(f"@{code} ({a.label or code}) = a cápsula {scene3d.CAST_NAMES.get(cast[code], cast[code])} "
                       f"(\"the {scene3d.CAST_EN.get(cast[code], cast[code])} figure\")")
    return out


def block(session: Session, project, shot: Shot) -> dict:
    """O bloco «Frame-chave» do plano no Storyboard: o aprovado (e se envelheceu), o prompt, os pedidos e as candidatas."""
    from app import prompt_targets as PT
    from app.media import media_root
    from app.media_service import media_list
    from app.routes import prompt_ops
    from app.routes.gpt import jobs_ctx
    from app.routes.video import request_plan, shot_model

    t = PT.get(session, project, "chave", str(shot.id))
    key = approved(session, shot)
    sent = {m.id for m in request_plan(session, shot, shot_model(session, shot))["images"]}
    from app import ref_tokens

    label = " · ".join(ref_tokens.token_for(session, key)) if key is not None else None
    return {"panel": prompt_ops.panel(session, t, "Prompt do frame-chave"), "media": key, "label": label,
            "stale": stale(session, shot),
            "sent": key is not None and key.id in sent, "missing": missing(session, shot),
            "render": render(session, shot), "previous": previous(session, shot),
            "jobs": jobs_ctx(session, project, ROLE, shot_id=shot.id),
            "results": media_list(session, shot_id=shot.id, role=ROLE, status="pending"),
            "rejected": [m for m in reversed(media_list(session, shot_id=shot.id, role=ROLE, status="rejected"))
                         if (media_root() / m.path).is_file()]}
