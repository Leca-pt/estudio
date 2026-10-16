"""Etapa 6 (vista geral) e etapa 7 — Montagem: clips aprovados por ordem, lacunas e exportação em zip."""

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.db import get_session
from app.media_service import scene_code
from app.models import Episode, Project, Scene
from app.routes.files import media_available, zip_response
from app.routes.production import approved_media, clock, durations, ordered_scenes, scene_shots
from app.security import csrf_protect, require_login
from app.stages import get_project, pending_roles_by_shot, render_stage, shot_clip_state

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])


def scopes(session: Session, project: Project) -> list[tuple[str, str]]:
    groups = ordered_scenes(session, project)
    n = sum(len(sc) for _, sc in groups)
    out = [("projeto", f"Projeto inteiro · {n} cena(s)")]
    for label, scs in groups:
        if label is None:
            continue
        first = scs[0] if scs else None
        if project.type == "series" and first and first.episode_id:
            out.append((f"ep-{first.episode_id}", f"{label} · {len(scs)} cena(s)"))
        elif project.type == "film" and first and first.act_id:
            out.append((f"ato-{first.act_id}", f"{label} · {len(scs)} cena(s)"))
    for _label, scs in groups:
        for s in scs:
            out.append((f"cena-{s.id}", f"{scene_code(session, s)} · {s.title or 'Sem título'}"))
    return out


def _scope_scenes(session: Session, project: Project, scope: str) -> tuple[list[tuple[str | None, list[Scene]]], int | None]:
    """(grupos de cenas no âmbito, duração-alvo do âmbito em segundos)."""
    groups = ordered_scenes(session, project)
    kind, _, raw = scope.partition("-")
    if kind == "ep" and raw.isdigit():
        groups = [(lbl, [s for s in sc if s.episode_id == int(raw)]) for lbl, sc in groups]
        episode = session.get(Episode, int(raw))
        target = episode.target_duration_s if episode else None
    elif kind == "ato" and raw.isdigit():
        groups = [(lbl, [s for s in sc if s.act_id == int(raw)]) for lbl, sc in groups]
        target = None
    elif kind == "cena" and raw.isdigit():
        groups = [(lbl, [s for s in sc if s.id == int(raw)]) for lbl, sc in groups]
        target = None
    else:
        target = project.target_duration_s if project.type != "series" else None
    return [(lbl, sc) for lbl, sc in groups if sc], target


def _rows(session: Session, project: Project, groups) -> list[dict]:
    pending = pending_roles_by_shot(session, project.id)
    rows = []
    for label, scenes in groups:
        for s in scenes:
            shots = scene_shots(session, s)
            clips = approved_media(session, [sh.approved_clip_id for sh in shots])
            keys = approved_media(session, [sh.key_image_id for sh in shots])
            tiles = []
            for sh in shots:
                roles = pending.get(sh.id, set())
                clip = clips.get(sh.approved_clip_id)
                if clip:
                    gap = None
                elif "clip" in roles:
                    gap = "Take por escolher"
                elif sh.scene3d:
                    gap = "Sem takes"
                else:
                    gap = "Storyboard por fazer"
                # sem take, os frames-chave aprovados fazem de imagens do plano (animatic: «Ver seguido» mostra cada um
                # do segundo do momento dele até ao seguinte; um só, o tempo do plano todo)
                stills = [] if clip else _stills(session, sh, keys.get(sh.key_image_id))
                tiles.append({"shot": sh, "clip": clip, "key": stills[0]["media"] if stills else None,
                              "hold": stills[0]["hold"] if stills else None, "more": stills[1:],
                              "gap": gap, "state": shot_clip_state(sh, roles),
                              "wip_gap": gap in ("Take por escolher", "Sem takes")})
            d = durations(session, [s.id])
            rows.append({"group": label, "scene": s, "code": scene_code(session, s), "tiles": tiles, "durations": d})
    return rows


def _stills(session: Session, shot, video_key) -> list[dict]:
    """Os frames-chave aprovados do plano para o animatic: [{"media", "hold" (segundos)}], pela ordem dos momentos; os
    tempos somam a duração do plano."""
    from app import keyframe

    dur = float(shot.duration_s or 4)
    found = [a for a in keyframe.all_approved(session, shot) if a["n"] is not None] if shot.scene3d else []
    if not found:
        return [{"media": video_key, "hold": dur}] if video_key is not None else []
    # cada imagem fica a meio caminho entre o momento dela e os vizinhos (a primeira desde o início, a última até ao fim)
    ts = [min(dur, max(0.0, a["t"])) for a in found]
    starts = [0.0] + [(x + y) / 2 for x, y in zip(ts, ts[1:])]
    ends = starts[1:] + [dur]
    return [{"media": a["media"], "hold": round(max(0.5, e - b), 2)} for a, b, e in zip(found, starts, ends)]


def montage_page(request: Request, session: Session, project: Project, stage: int):
    scope = request.query_params.get("ambito")
    if not scope and project.type == "series":  # série: por omissão, o episódio atual
        from app.stages import current_episode

        ep = current_episode(request, session, project)
        scope = f"ep-{ep.id}" if ep else None
    scope = scope or "projeto"
    valid = scopes(session, project)
    if scope not in {k for k, _ in valid}:
        scope = "projeto"
    groups, target = _scope_scenes(session, project, scope)
    rows = _rows(session, project, groups)
    d = durations(session, [r["scene"].id for r in rows])
    ctx = {
        "rows": rows, "scope": scope, "scopes": valid, "target": target,
        "stills": sum(1 for r in rows for t in r["tiles"] if t["key"]),
        "summary": {"approved": d.approved, "shots": d.shots, "approved_clock": clock(d.approved_ms / 1000),
                    "planned_clock": clock(d.planned_s), "gaps": d.shots - d.approved,
                    "target_clock": clock(target) if target else None,
                    "over": bool(target) and d.planned_s > target},
    }
    if stage == 6:  # o modelo e a resolução padrão do projeto (cada plano pode escolher outro)
        from app.routes.video import RESOLUTIONS, project_model, project_resolution, video_models

        ctx.update({"models": video_models(session), "proj_model": project_model(session, project),
                    "proj_res": project_resolution(session, project), "resolutions": RESOLUTIONS})
    template = "montage/stage7.html" if stage == 7 else "montage/stage6.html"
    return render_stage(request, session, project, stage, template, ctx)


@router.get("/montagem/exportar.zip")
def export_zip(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    scope = request.query_params.get("ambito") or "projeto"
    groups, _target = _scope_scenes(session, project, scope)
    entries, n = [], 0
    for _label, scenes in groups:
        for s in scenes:
            code = scene_code(session, s)
            shots = scene_shots(session, s)
            clips = approved_media(session, [sh.approved_clip_id for sh in shots])
            for sh in shots:
                clip = clips.get(sh.approved_clip_id)
                if clip and media_available(clip):
                    n += 1
                    ext = PurePosixPath(clip.path).suffix
                    entries.append((f"{n:02d}_{project.prefix}_{code}_P{sh.number:02d}{ext}", clip))
    if not entries:
        raise HTTPException(status_code=404, detail="Ainda não há clips aprovados neste âmbito.")
    suffix = "" if scope == "projeto" else f"_{scope}"
    return zip_response(f"{project.prefix}_clips{suffix}.zip", entries)
