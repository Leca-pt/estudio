"""O que ficou para trás num plano: coisas feitas com uma câmara, uma encenação, uma maquete ou uma duração que
entretanto mudaram.

Regra: nada desatualizado segue num pedido sem o utilizador decidir. Assinalar não chega — se o pedido continuasse a
levar o conteúdo antigo, gerava-se em vão e em silêncio. Por isso o pedido fica parado (na página e no servidor, o
que vale também para o escritor de prompts e o conector MCP) até se refazer o que está para trás ou se dizer
explicitamente que fica assim. Cada problema traz as duas saídas como ações.

Os renders da maquete (previs dos momentos, vistas do local) refazem-se sozinhos; o que não se refaz sozinho e é
caro (frames-chave, vídeo-guia aprovado, imagens das vistas do local, prompts editados à mão) é o que aqui se vê.
"""

from sqlmodel import Session, select

from app.memo import memo
from app.models import ImageJob, LocationView, MediaFile, PromptVersion, Scene, Shot


def _act(label: str, op: str | None = None, href: str | None = None) -> dict:
    return {"label": label, "op": op, "href": href}


# ── Prompts editados à mão ─────────────────────────────────────

def prompt_outdated_by_hand(session: Session, t) -> bool:
    """Um prompt editado à mão (ou no chat do Assistente) foi escrito para uma versão do plano que já mudou: a câmara,
    a encenação, as imagens que seguem… O escritor não o reescreve sozinho (o texto é do utilizador), por isso fica
    parado até ser reescrito ou confirmado («Manter este texto»)."""
    from app import prompt_targets as PT

    if not (t.prompt or "").strip() or PT.state(session, t) != "manual":
        return False
    rows = session.exec(select(PromptVersion).where(PromptVersion.project_id == t.project.id,
                                                    PromptVersion.field_key == t.field_key)
                        .order_by(PromptVersion.id.desc()).limit(30)).all()
    if not rows or rows[0].text != t.prompt:
        return False
    ref = rows[0].basis or next((v.basis for v in rows if v.basis), None)  # a versão do plano para que foi escrito
    return bool(ref) and ref != PT.basis(session, t)


def keep_prompt(session: Session, t) -> None:
    """«Manter este texto»: o prompt atual passa a valer para a versão atual do plano."""
    from app import prompt_targets as PT
    from app.prompt_versions import record

    record(session, t.project.id, t.field_key, t.prompt or "", "manual", basis=PT.basis(session, t))


# ── Imagens das vistas do local ─────────────────────────────────

def _job_of(session: Session, media_id: int) -> ImageJob | None:
    return session.exec(select(ImageJob).where(ImageJob.result_media_id == media_id)).first()


def _same_picture(a: MediaFile, b: MediaFile) -> bool:
    """Dois renders mostram a mesma coisa (a maquete mudou noutro sítio, não no que esta vista vê)."""
    from PIL import Image, ImageChops

    from app.media import media_root

    try:
        x = Image.open(media_root() / a.path).convert("L").resize((192, 108))
        y = Image.open(media_root() / b.path).convert("L").resize((192, 108))
    except OSError:
        return False
    diff = ImageChops.difference(x, y).point(lambda v: 255 if v > 24 else 0)
    changed = sum(1 for v in diff.getdata() if v) / (192 * 108)
    return changed < 0.004


def view_image_outdated(session: Session, view: LocationView) -> bool:
    """A imagem aprovada da vista foi gerada a partir de um render da maquete que mostrava outra coisa: quando chega um
    render novo, compara-se com o anterior (render_replaced) — mudar a maquete noutra divisão não estraga esta imagem.
    Imagens carregadas à mão, confirmadas («Manter») ou anteriores a esta verificação (sem como comparar) não contam."""
    if not view.image_id or not view.render_id:
        return False
    job = _job_of(session, view.image_id)
    if job is None or not job.inputs:
        return False
    params = job.params or {}
    if job.inputs[0] == view.render_id or params.get("render_ok") == view.render_id:
        return False
    return bool(params.get("render_changed"))


def render_replaced(session: Session, view: LocationView, old: MediaFile, new: MediaFile) -> None:
    """Chegou um render novo da vista (o antigo ainda existe): se a imagem valia para o antigo, continua a valer se os
    dois mostram o mesmo; senão fica marcada como desatualizada."""
    job = _job_of(session, view.image_id) if view.image_id else None
    if job is None or not job.inputs:
        return
    params = dict(job.params or {})
    if job.inputs[0] != old.id and params.get("render_ok") != old.id:
        return  # já estava desatualizada (ou é de antes desta verificação)
    if _same_picture(old, new):
        params["render_ok"] = new.id
    else:
        params["render_changed"] = new.id
    job.params = params
    session.add(job)


def keep_view_image(session: Session, view: LocationView) -> None:
    """«Manter a imagem»: vale para o render atual da vista (a diferença não importa)."""
    job = _job_of(session, view.image_id) if view.image_id else None
    if job is not None:
        job.params = {**(job.params or {}), "render_ok": view.render_id}
        session.add(job)


# ── O plano ─────────────────────────────────────────────────────

def _key_problem(session: Session, shot: Shot) -> str | None:
    from app import keyframe, moments

    key = keyframe.approved(session, shot)
    if key is None or not shot.scene3d:
        return None
    live = {moments.moment_key(k) for k in moments.keys(shot)}
    if keyframe._moment_of(session, shot, key) not in live:
        return "O frame-chave que segue no vídeo é de uma câmara antiga (a câmara do plano mudou depois)."
    if keyframe._stale(session, shot, key):
        return "O frame-chave que segue no vídeo foi feito antes de a câmara ou a encenação mudarem."
    return None


def _guide_problem(session: Session, shot: Shot) -> str | None:
    from app import moments, scene3d

    guide = session.get(MediaFile, shot.guide_video_id) if shot.guide_video_id else None
    if guide is None or guide.status != "approved" or not shot.scene3d:
        return None
    if guide.duration_ms and shot.duration_s and abs(guide.duration_ms - shot.duration_s * 1000) > 50:
        return f"O vídeo-guia aprovado tem {guide.duration_ms / 1000:.1f} s e o plano tem agora {shot.duration_s} s."
    gh = moments.guide_hash(shot, guide.id)
    scene = session.get(Scene, shot.scene_id)
    if gh and gh != scene3d.preview_hash(scene3d.compose(session, scene.project_id, shot=shot)):
        return "O vídeo-guia aprovado foi gravado antes de a câmara ou a encenação mudarem."
    return None


@memo
def summary(session: Session, shot: Shot) -> list[str]:
    """Para as listas (cena, Montagem): o que está para trás neste plano, em poucas palavras (só o que se sabe sem
    montar os prompts)."""
    out = []
    if _key_problem(session, shot):
        out.append("frame-chave")
    if _guide_problem(session, shot):
        out.append("vídeo-guia")
    return out


def blockers(session: Session, shot: Shot, kind: str) -> list[dict]:
    """O que impede gerar (kind "video": um take; "chave": um frame-chave): [{"text", "actions": [...]}]. Vazio = pode."""
    from app import prompt_targets as PT
    from app.routes.video import request_plan, shot_model

    scene = session.get(Scene, shot.scene_id)
    project_id = scene.project_id
    sb = f"/projetos/{project_id}/storyboard/planos/{shot.id}"
    out = []
    from app.routes.gpt import FORMAT_MISSING, project_aspect

    if project_aspect(_project(session, project_id)) is None:  # sem formato não se adivinha a proporção
        out.append({"text": FORMAT_MISSING, "actions": [_act("Escolher o formato", href=f"/projetos/{project_id}/etapa/1")]})
    if kind == "video":
        from app import video_inputs as VI

        plan = request_plan(session, shot, shot_model(session, shot))
        sent = {m.id for m in plan["images"]}
        problem = _key_problem(session, shot)
        if problem and shot.key_image_id in sent:
            acts = [_act("Gerar o frame-chave de novo", href=sb + "#frame-chave"), _act("Seguir sem frame-chave", op="key_drop")]
            if "câmara antiga" not in problem:  # a encenação ou a maquete mudou: pode não lhe dizer respeito
                acts.append(_act("Manter (a mudança não o afeta)", op="key_ok"))
            out.append({"text": problem + " Seguia no pedido com o enquadramento e as posições antigas.", "actions": acts})
        problem = _guide_problem(session, shot)
        if problem and VI.config(shot)["guide"]:
            out.append({"text": problem + " O vídeo seguiria a câmara e as posições antigas.",
                        "actions": [_act("Gravar o vídeo-guia de novo", href=sb + "#guia"),
                                    _act("Seguir sem vídeo-guia", op="guide_off")]
                        + ([_act("Manter (a mudança não o afeta)", op="guide_ok")] if "gravado antes" in problem else [])})
        from app import moments

        for r in moments.rows(session, shot, renders=False):
            v, img = r.get("view"), r.get("image")
            if v is not None and img is not None and img.id in sent and view_image_outdated(session, v):
                out.append({"text": f"A imagem da vista «{v.label}» (momento {r['n']}) foi feita antes de a maquete "
                                    "do local mudar no que essa vista mostra.",
                            "actions": [_act("Refazer a imagem da vista", href=sb + "#momentos"),
                                        _act("Manter a imagem", op=f"vimg_ok:{v.id}")]})
    t = PT.get(session, _project(session, project_id), kind, str(shot.id))
    if prompt_outdated_by_hand(session, t):
        what = "de vídeo" if kind == "video" else "do frame-chave"
        out.append({"text": f"O prompt {what} foi editado à mão para uma versão anterior do plano (a câmara, a "
                            "encenação ou as imagens que seguem mudaram depois).",
                    "actions": [_act("Reescrever", op=f"pwrite:{kind}:{shot.id}"),
                                _act("Manter este texto", op=f"pkeep:{kind}:{shot.id}")]})
    return out


def _project(session: Session, project_id: int):
    from app.models import Project

    return session.get(Project, project_id)


def check(session: Session, shot: Shot, kind: str) -> str | None:
    """Para quem pede (o botão, o escritor, o conector MCP): None se pode gerar, senão porque não."""
    found = blockers(session, shot, kind)
    if not found:
        return None
    return ("Não gerado: " + " ".join(b["text"] for b in found)
            + " Resolve em «Antes de gerar» (refazer ou manter assim).")


def apply_op(session: Session, shot: Shot, op: str) -> str | None:
    """As ações «manter assim / seguir sem»: devolve a mensagem para o utilizador, ou None se a op não é daqui."""
    from app import keyframe
    from app.media_service import reject_media

    verb, _, arg = (op or "").partition(":")
    if verb == "key_drop":
        key = keyframe.approved(session, shot)
        if key is not None:
            reject_media(session, key)
            return "O frame-chave antigo foi para «Rejeitados»: o pedido segue sem frame-chave."
    elif verb == "key_ok":
        key = keyframe.approved(session, shot)
        if key is not None:
            keyframe.keep(session, shot, key)
            return "O frame-chave fica como está e segue no pedido."
    elif verb == "guide_ok":
        from app import scene3d

        guide = session.get(MediaFile, shot.guide_video_id) if shot.guide_video_id else None
        if guide is not None:
            data = dict(shot.moments or {})
            data["guide_hashes"] = {**(data.get("guide_hashes") or {}), str(guide.id): scene3d.preview_hash(
                scene3d.compose(session, session.get(Scene, shot.scene_id).project_id, shot=shot))}
            shot.moments = data
            session.add(shot)
            return "O vídeo-guia fica como está e segue no pedido."
    elif verb == "guide_off":
        shot.video_inputs = {**(shot.video_inputs or {}), "guide": False}
        session.add(shot)
        return "O vídeo-guia deixou de seguir neste plano (podes voltar a ligá-lo em «O que segue no pedido»)."
    elif verb == "vimg_ok" and arg.isdigit():
        view = session.get(LocationView, int(arg))
        if view is not None:
            keep_view_image(session, view)
            return f"A imagem da vista «{view.label}» fica como está."
    return None
