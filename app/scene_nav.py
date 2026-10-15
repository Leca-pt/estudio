"""Barra de navegação da cena, igual no guião, no storyboard e no vídeo: a cena anterior e a seguinte (no mesmo
episódio, ato ou curta), os três passos da mesma cena (Guião · Storyboard · Vídeo) e os planos dela, cada um com o
estado e a ligação para o mesmo passo. Assim anda-se entre cenas, planos e passos sem voltar às etapas."""

from sqlmodel import Session, select

from app.media_service import scene_code
from app.models import MediaFile, Project, Scene, Shot

MODES = ("script", "storyboard", "video")


def _shots(session: Session, scene: Scene) -> list[Shot]:
    return list(session.exec(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.number, Shot.id)).all())


def _scene_url(project: Project, scene: Scene, mode: str, shots: list[Shot] | None = None) -> str:
    base = f"/projetos/{project.id}"
    if mode == "script":
        return f"{base}/historia/cenas/{scene.id}"
    if mode == "video":  # o primeiro plano; sem planos, a cena na etapa Vídeo (no âmbito dela)
        return f"{base}/video/planos/{shots[0].id}" if shots else f"{base}/etapa/6?ambito=cena-{scene.id}"
    return f"{base}/storyboard/cenas/{scene.id}"


def _shot_url(project: Project, shot: Shot, mode: str) -> str:
    return f"/projetos/{project.id}/{'video' if mode == 'video' else 'storyboard'}/planos/{shot.id}"


def build(session: Session, project: Project, scene: Scene, mode: str, shot: Shot | None = None) -> dict:
    from app.stages import OK, PEND, WIP, shot_board_state

    siblings = list(session.exec(select(Scene).where(
        Scene.project_id == project.id, Scene.episode_id == scene.episode_id, Scene.act_id == scene.act_id)
        .order_by(Scene.number, Scene.id)).all())
    i = next((n for n, s in enumerate(siblings) if s.id == scene.id), 0)
    shots = _shots(session, scene)
    clips = set(session.exec(select(MediaFile.shot_id).where(
        MediaFile.shot_id.in_([s.id for s in shots]), MediaFile.role == "clip", MediaFile.status != "rejected")).all()) if shots else set()

    def state(sh: Shot) -> str:
        if mode == "video":
            return OK if sh.approved_clip_id else WIP if sh.id in clips else PEND
        return shot_board_state(session, sh)

    def neighbour(s: Scene | None, prefix: str = "") -> dict | None:
        if s is None:
            return None
        return {"code": prefix + f"C{s.number:02d}", "title": s.title or "",
                "url": _scene_url(project, s, mode, _shots(session, s) if mode == "video" else None)}

    def across(step: int) -> dict | None:
        """Na primeira ou na última cena: a última do episódio (ou ato) anterior ou a primeira do seguinte."""
        from app.models import Act, Episode, Season

        if scene.episode_id:
            eps = list(session.exec(select(Episode).join(Season, Season.id == Episode.season_id)
                                    .where(Season.project_id == project.id).order_by(Season.number, Episode.number)).all())
            idx = next((n for n, e in enumerate(eps) if e.id == scene.episode_id), None)
            units, col, tag = eps, Scene.episode_id, lambda u: f"E{u.number:02d} · "
        elif scene.act_id:
            units = list(session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all())
            idx = next((n for n, a in enumerate(units) if a.id == scene.act_id), None)
            col, tag = Scene.act_id, lambda u: f"A{u.number} · "
        else:
            return None
        j = (idx if idx is not None else -99) + step
        while 0 <= j < len(units):
            sc = list(session.exec(select(Scene).where(col == units[j].id).order_by(Scene.number, Scene.id)).all())
            if sc:
                return neighbour(sc[0] if step > 0 else sc[-1], tag(units[j]))
            j += step
        return None

    tabs = [("script", "Guião", _scene_url(project, scene, "script")),
            ("storyboard", "Storyboard", _shot_url(project, shot, "storyboard") if shot else _scene_url(project, scene, "storyboard")),
            ("video", "Vídeo", _shot_url(project, shot, "video") if shot else _scene_url(project, scene, "video"))]
    return {
        "code": scene_code(session, scene), "number": scene.number, "title": scene.title or f"Cena {scene.number}",
        "prev": neighbour(siblings[i - 1]) if i > 0 else across(-1),
        "next": neighbour(siblings[i + 1]) if i + 1 < len(siblings) else across(1),
        "tabs": [{"key": k, "label": label, "url": url, "on": k == mode} for k, label, url in tabs],
        "scene_url": _scene_url(project, scene, "storyboard" if mode == "script" else mode),
        "scene_on": shot is None and mode != "script",
        "shots": [{"code": f"P{s.number:02d}", "title": s.title or s.shot_type or "", "state": state(s),
                   "url": _shot_url(project, s, mode), "on": shot is not None and s.id == shot.id} for s in shots],
        "mode": mode,
    }
