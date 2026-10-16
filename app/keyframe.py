"""Frame-chave do plano: uma imagem do take com as personagens já no sítio certo.

Com a cena 3D, parte do previs de um momento da câmara (o primeiro, ou o que o utilizador escolher: quando no início
ainda não está ninguém em plano, um momento em que já estão todos nas suas posições) (a câmara do plano, com o elenco em cápsulas a cores): o gerador
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
def moment(session: Session, shot: Shot) -> dict | None:
    """O momento da câmara de onde o frame-chave parte (com o render e a vista do local): o escolhido ou o primeiro.
    None sem cena 3D."""
    if not shot.scene3d:
        return None
    rows = moments.rows(session, shot)
    chosen = (shot.moments or {}).get("key")
    return next((r for r in rows if moments.moment_key(r) == chosen), rows[0] if rows else None)


def choose_moment(shot: Shot, n: int | None) -> None:
    """O frame-chave parte do momento n (None ou 1 = o primeiro). Fica preso ao momento (segundo e câmara)."""
    data = dict(shot.moments or {})
    m = next((k for k in moments.keys(shot) if k["n"] == n), None)
    if m is None or n == 1:
        data.pop("key", None)
    else:
        data["key"] = moments.moment_key(m)
    shot.moments = data


def render(session: Session, shot: Shot) -> MediaFile | None:
    """O previs do momento do frame-chave, se está atualizado (tirado com a câmara e a encenação atuais)."""
    r = moment(session, shot)
    return r["render"] if r else None


def _location_image(session: Session, shot: Shot) -> MediaFile | None:
    """O aspeto do sítio: a vista do local do momento do frame-chave ou, sem cena 3D, a imagem do local da cena."""
    r = moment(session, shot)
    if r is not None:
        return r["image"]
    from app.asset_refs import location_image

    scene = session.get(Scene, shot.scene_id)
    loc = session.get(Asset, scene.location_id) if scene and scene.location_id else None
    return location_image(session, loc) if loc is not None else None


@memo
def in_shot(session: Session, shot: Shot) -> list[str]:
    """Os códigos que a câmara vê no instante do frame-chave (pela ordem do elenco da cena). Quem só entra depois (ou
    já saiu) não vai para a imagem: as folhas dele seguem no pedido de vídeo, não no do frame-chave."""
    from app import scene3d

    r = moment(session, shot)
    if r is None:
        return []
    here = set(scene3d.codes_at(session, shot, r["t"]))
    return [c for c in scene3d.in_frame(session, shot) if c in here]


def absent(session: Session, shot: Shot) -> list[str]:
    """Quem aparece no plano mas não no instante do frame-chave, e quando: para o escritor não o pôr na imagem."""
    from app import scene3d

    r = moment(session, shot)
    if r is None:
        return []
    scene = session.get(Scene, shot.scene_id)
    labels = {a.name: a.label or a.name for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    here, seen = set(in_shot(session, shot)), scene3d.appearances(session, shot)
    out = []
    for code in scene3d.in_frame(session, shot):
        if code in here or code not in seen:
            continue
        first, last = seen[code]
        who = f"{labels.get(code, code)} ({code})"
        if first > r["t"]:
            out.append(f"{who} ainda não está em plano: entra aos {first:g} s")
        elif last < r["t"]:
            out.append(f"{who} já saiu de plano (até aos {last:g} s)")
        else:
            out.append(f"{who} não se vê neste instante (tapado ou fora do quadro)")
    return out


def cast(session: Session, shot: Shot) -> list[MediaFile]:
    """As folhas (do look da cena) e os retratos de quem aparece: os que a câmara vê no instante do frame-chave ou, sem
    cena 3D, as personagens da cena."""
    if shot.scene3d:
        scene = session.get(Scene, shot.scene_id)
        out: list[MediaFile] = []
        for code in in_shot(session, shot):
            a = session.exec(select(Asset).where(Asset.project_id == scene.project_id, Asset.name == code)).first()
            for i in (looks.ref_ids(session, a, scene) if a else []):
                m = moments._approved(session, i)
                if m is not None and m not in out:
                    out.append(m)
        return out
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
        r = moment(session, shot)
        return (f"Falta o previs do momento {r['n'] if r else 1}: abre o plano no Storyboard (a página tira-o sozinha; pelo conector, "
                f"render_maquete com sb_shot:{shot.id}).")
    return None


def approved(session: Session, shot: Shot) -> MediaFile | None:
    m = session.get(MediaFile, shot.key_image_id) if shot.key_image_id else None
    return m if m is not None and m.status == "approved" else None


def _params(session: Session, media: MediaFile) -> dict:
    job = session.exec(select(ImageJob).where(ImageJob.result_media_id == media.id)).first()
    return (job.params or {}) if job else {}


def made_from(session: Session, media: MediaFile) -> str | None:
    """A versão da cena 3D (moments.render_hash) com que esta imagem foi pedida."""
    return _params(session, media).get("hash")


def made_at(session: Session, media: MediaFile) -> float:
    """O segundo do plano que esta imagem mostra (0 = o início; as antigas são todas do primeiro momento)."""
    return float(_params(session, media).get("t") or 0)


def _stale(session: Session, shot: Shot, m: MediaFile) -> bool:
    if not shot.scene3d:
        return False
    was = made_from(session, m)
    return bool(was) and was != moments.render_hash(session, shot)


def stale(session: Session, shot: Shot) -> bool:
    """O frame-chave que segue no vídeo foi feito com outra câmara ou outra encenação (as posições já não batem certo)."""
    m = approved(session, shot)
    return m is not None and _stale(session, shot, m)


def _moment_of(session: Session, shot: Shot, media: MediaFile) -> str | None:
    """A identidade do momento (moments.moment_key) de que esta imagem é; as antigas eram do primeiro."""
    p = _params(session, media)
    if p.get("moment"):
        return p["moment"]
    rows = moments.keys(shot)
    return moments.moment_key(rows[0]) if rows else None


def all_approved(session: Session, shot: Shot) -> list[dict]:
    """Os frames-chave aprovados do plano, um por momento, pela ordem do tempo: {"media", "n" (None se o momento já
    não existe), "t", "stale", "video" (é o que segue no pedido de vídeo)}. São o storyboard final do plano."""
    from app.media_service import media_list

    by_key = {moments.moment_key(k): k for k in moments.keys(shot)}
    out = []
    for m in media_list(session, shot_id=shot.id, role=ROLE, status="approved"):
        k = by_key.get(_moment_of(session, shot, m))
        out.append({"media": m, "n": k["n"] if k else None, "t": k["t"] if k else made_at(session, m),
                    "stale": _stale(session, shot, m), "video": m.id == shot.key_image_id})
    return sorted(out, key=lambda x: (x["t"], x["media"].id))


def approve(session: Session, media: MediaFile) -> None:
    """Aprovar um frame-chave: fica o deste momento (o anterior do mesmo momento volta a «por decidir»). Segue no vídeo
    se ainda nenhum seguia ou se o que seguia era deste mesmo momento."""
    shot = session.get(Shot, media.shot_id)
    mine = _moment_of(session, shot, media)
    current = session.get(MediaFile, shot.key_image_id) if shot.key_image_id else None
    for m in all_approved(session, shot):
        if m["media"].id != media.id and _moment_of(session, shot, m["media"]) == mine:
            m["media"].status = "pending"
            session.add(m["media"])
    media.status = "approved"
    session.add(media)
    if current is None or current.status != "approved" or current.id == media.id or _moment_of(session, shot, current) == mine:
        shot.key_image_id = media.id
        session.add(shot)


def use_in_video(session: Session, shot: Shot, media_id: int) -> bool:
    """Este frame-chave aprovado passa a ser o que segue no pedido de vídeo."""
    m = session.get(MediaFile, media_id)
    if m is None or m.shot_id != shot.id or m.role != ROLE or m.status != "approved":
        return False
    shot.key_image_id = m.id
    session.add(shot)
    return True


def moment_view(session: Session, shot: Shot) -> MediaFile | None:
    """A vista do local do momento do frame-chave que segue no vídeo (já vai nele: não precisa de seguir à parte)."""
    key = approved(session, shot)
    if key is None:
        return None
    mine = _moment_of(session, shot, key)
    return next((r["image"] for r in moments.rows(session, shot) if moments.moment_key(r) == mine), None)


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
    for code in in_shot(session, shot):
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
    r = moment(session, shot)
    names = {a.name: a.label or a.name for a in session.exec(select(Asset).where(
        Asset.project_id == project.id, Asset.name.in_(list(scene_codes(session, shot))))).all()} if r else {}
    choices = []
    if r is not None:
        from app import scene3d

        for row in moments.rows(session, shot):
            who = [names.get(c, c) for c in scene3d.codes_at(session, shot, row["t"]) if c in names]
            choices.append({"n": row["n"], "t": row["t"], "who": who, "on": row["n"] == r["n"], "render": row["render"]})
    return {"panel": prompt_ops.panel(session, t, "Prompt do frame-chave"), "media": key, "label": label,
            "moment": r, "choices": choices, "absent": absent(session, shot), "all": all_approved(session, shot),
            "stale": stale(session, shot),
            "sent": key is not None and key.id in sent, "missing": missing(session, shot),
            "render": render(session, shot), "previous": previous(session, shot),
            "jobs": jobs_ctx(session, project, ROLE, shot_id=shot.id),
            "results": media_list(session, shot_id=shot.id, role=ROLE, status="pending"),
            "rejected": [m for m in reversed(media_list(session, shot_id=shot.id, role=ROLE, status="rejected"))
                         if (media_root() / m.path).is_file()]}


def scene_codes(session: Session, shot: Shot) -> list[str]:
    from app import scene3d

    return scene3d.in_frame(session, shot)
