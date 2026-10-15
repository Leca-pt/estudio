"""Utilitários partilhados pelo storyboard, vídeo e montagem."""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlmodel import Session, func, select

from app.media_service import scene_code
from app.models import Act, Asset, AssetSceneLink, Episode, MediaFile, Project, Scene, Season, Shot
from app.options import get_config


def get_scene(session: Session, project: Project, scene_id: int) -> Scene:
    scene = session.get(Scene, scene_id)
    if scene is None or scene.project_id != project.id:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    return scene


def get_shot(session: Session, project: Project, shot_id: int) -> tuple[Shot, Scene]:
    shot = session.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    return shot, get_scene(session, project, shot.scene_id)


def scene_shots(session: Session, scene: Scene) -> list[Shot]:
    return list(session.exec(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.number, Shot.id)).all())


def scene_assets(session: Session, scene: Scene) -> list[Asset]:
    order = {"character": 0, "prop": 1, "element": 2, "location": 3}
    rows = session.exec(
        select(Asset).join(AssetSceneLink, AssetSceneLink.asset_id == Asset.id).where(AssetSceneLink.scene_id == scene.id)
    ).all()
    return sorted(rows, key=lambda a: (order.get(a.kind, 9), a.name))


def approved_media(session: Session, ids) -> dict[int, MediaFile]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = session.exec(select(MediaFile).where(MediaFile.id.in_(ids), MediaFile.status == "approved")).all()
    return {m.id: m for m in rows}


def image_model_tag(session: Session, project: Project) -> str:
    """Etiqueta dos blocos de prompt de imagem: o modelo por omissão (Configuração → Imagens) e o formato."""
    from app.routes.gpt import generator_label

    aspect = (project.aspect_ratio or "").split(" ")[0] if project.aspect_ratio else None
    return " · ".join(x for x in [generator_label(session), aspect] if x)


def prompt_limits(session: Session) -> dict:
    return {"max_chars": get_config(session, "prompt_max_chars", 2000),
            "warn_enabled": get_config(session, "prompt_warn_enabled", True)}


def ordered_scenes(session: Session, project: Project) -> list[tuple[str | None, list[Scene]]]:
    """Cenas pela ordem do projeto, agrupadas por episódio (séries) ou ato (filmes)."""
    if project.type == "series":
        rows = session.exec(
            select(Episode, Season).join(Season, Season.id == Episode.season_id)
            .where(Season.project_id == project.id).order_by(Season.number, Episode.number)
        ).all()
        return [(f"T{s.number} · E{e.number:02d}" + (f" · {e.title}" if e.title else ""),
                 list(session.exec(select(Scene).where(Scene.episode_id == e.id).order_by(Scene.number)).all()))
                for e, s in rows]
    if project.type == "film":
        acts = session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all()
        return [(f"Ato {a.number}" + (f" · {a.title}" if a.title else ""),
                 list(session.exec(select(Scene).where(Scene.act_id == a.id).order_by(Scene.number)).all()))
                for a in acts]
    return [(None, list(session.exec(select(Scene).where(Scene.project_id == project.id).order_by(Scene.number)).all()))]


@dataclass
class Durations:
    planned_s: int = 0  # soma das durações escolhidas nos planos
    approved_ms: int = 0  # soma dos clips aprovados
    shots: int = 0
    approved: int = 0


def durations(session: Session, scene_ids: list[int]) -> Durations:
    d = Durations()
    if not scene_ids:
        return d
    shots = session.exec(select(Shot).where(Shot.scene_id.in_(scene_ids))).all()
    d.shots = len(shots)
    d.planned_s = sum(s.duration_s or 0 for s in shots)
    clips = approved_media(session, [s.approved_clip_id for s in shots])
    d.approved = len(clips)
    d.approved_ms = sum(m.duration_ms or 0 for m in clips.values())
    return d


def clock(seconds: float | int | None) -> str:
    seconds = int(round(seconds or 0))
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def scene_label(session: Session, scene: Scene) -> str:
    return scene_code(session, scene)


def next_shot_number(session: Session, scene: Scene) -> int:
    return (session.exec(select(func.max(Shot.number)).where(Shot.scene_id == scene.id)).one() or 0) + 1
