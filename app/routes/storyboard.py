"""Etapa 5 — Storyboard, por cena e por plano. Cada plano passa-se na maquete 3D de um local da cena: o Assistente monta
a cena 3D (câmara, elenco, figurantes); em cada momento da câmara, a vista do local mais parecida (ou uma criada a
partir dessa câmara) dá o aspeto do sítio; grava-se o vídeo-guia. O vídeo do plano parte disto (app/moments.py)."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import moments
from app.db import get_session
from app.forms import text
from app.media import media_root
from app.media_service import media_list, reject_media, scene_code
from app.models import ASSET_KINDS, CHECK_CATEGORIES, Asset, AssetSceneLink, LocationView, MediaFile, Project, Scene, Shot, ShotCheck
from app.routes.assets import gpt_jobs
from app.routes.files import flash, flash_info
from app.routes.production import (
    approved_media, durations, get_scene, get_shot, next_shot_number, ordered_scenes, scene_assets, scene_shots,
)
from app.security import csrf_protect, require_login
from app.stages import get_project, render_stage, shot_board_state

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])

STAGE = 5


# ── Vista geral da etapa ─────────────────────────────────────

def overview_page(request: Request, session: Session, project: Project):
    from app.stages import current_episode

    ep = current_episode(request, session, project) if project.type == "series" else None
    groups = []
    for label, scenes in ordered_scenes(session, project):
        if ep is not None:  # série: só o episódio atual
            scenes = [s for s in scenes if s.episode_id == ep.id]
            if not scenes:
                continue
        rows = []
        for s in scenes:
            shots = scene_shots(session, s)
            states = [shot_board_state(session, sh) for sh in shots]
            loc = session.get(Asset, s.location_id) if s.location_id else None
            rows.append({"scene": s, "location": loc, "shots": len(shots), "ready": states.count("ok"),
                         "has_maquete": bool(loc and loc.layout3d)})
        groups.append((label, rows))
    return render_stage(request, session, project, STAGE, "storyboard/overview.html",
                        {"groups": groups, "assistant_key": "storyboard"})


# ── Cena ─────────────────────────────────────────────────────

def scene_locations(session: Session, scene: Scene) -> list[Asset]:
    """Locais da cena: o local principal primeiro e depois os outros marcados na cena."""
    linked = list(session.exec(select(Asset).join(AssetSceneLink, AssetSceneLink.asset_id == Asset.id)
                               .where(AssetSceneLink.scene_id == scene.id, Asset.kind == "location")
                               .order_by(Asset.name)).all())
    main = session.get(Asset, scene.location_id) if scene.location_id else None
    out = [main] if main is not None and main.kind == "location" else []
    return out + [a for a in linked if not out or a.id != out[0].id]


def _timeline(shots: list, stage_d: float) -> dict:
    """Os troços dos planos na encenação, em faixas sem sobreposição (um troço por faixa de cada vez), e a escala."""
    from app import scene3d as S3

    windows = [{"shot": sh, "start": S3.shot_window(sh)[0], "end": sum(S3.shot_window(sh)), "camera": bool(sh.scene3d)}
               for sh in shots]
    lanes: list[float] = []
    for w in sorted(windows, key=lambda w: (w["start"], w["shot"].number)):
        lane = next((i for i, end in enumerate(lanes) if end <= w["start"] + 1e-6), None)
        if lane is None:
            lanes.append(0.0)
            lane = len(lanes) - 1
        lanes[lane] = w["end"]
        w["lane"] = lane
    span = max([stage_d] + [w["end"] for w in windows]) or 1
    step = next(s for s in (1, 2, 5, 10, 15, 30, 60, 120) if span / s <= 12)
    return {"windows": windows, "tl_lanes": max(1, len(lanes)), "tl_span": span,
            "tl_ticks": [i * step for i in range(int(span // step) + 1)]}


def _scene_view(request: Request, session: Session, project: Project, scene: Scene):
    from app.shot_script import script_coverage

    assets = scene_assets(session, scene)
    from app import keyframe

    shots = scene_shots(session, scene)
    script = script_coverage(session, scene)
    lines_of = {sh.id: [r["n"] for r in script if sh in r["shots"]] for sh in shots}
    location = session.get(Asset, scene.location_id) if scene.location_id else None
    from app import outdated

    rows = []
    for sh in shots:
        ms = moments.rows(session, sh)
        rows.append({"shot": sh, "state": shot_board_state(session, sh), "moments": ms, "lines": lines_of.get(sh.id, []),
                     "key": keyframe.approved(session, sh), "outdated": outdated.summary(session, sh),
                     "stills": [a["media"] for a in keyframe.all_approved(session, sh) if a["n"] is not None]
                     or [m for m in [keyframe.approved(session, sh)] if m is not None]})
    from app import looks as L

    wardrobe = {}
    for a in assets:
        if a.kind == "character":
            options = L.looks_of(session, a)
            if options:
                wardrobe[a.id] = {"options": options, "current": L.scene_look(session, scene, a)}
    sheets = approved_media(session, [i for a in assets for i in L.ref_ids(session, a, scene)]
                           + [i for a in assets for i in a.ref_image_ids()])
    from app import scene3d as S3

    stage_spec = S3.compose(session, project.id, scene=scene)
    base, set_loc = S3.scene_set(session, scene)
    spans = S3.line_spans(scene)
    stage_subs = [{"from": spans[r["n"]][0], "to": spans[r["n"]][1], "text": r["line"].text,
                   "who": (r["who"].label or r["who"].name) if r["who"] else ""}
                  for r in script if r["line"].kind == "dialogue" and r["n"] in spans and r["line"].text.strip()]
    ctx = {
        "stage_spec": stage_spec, "stage_subs": stage_subs, "set_var": S3.variation_info(session, scene), "stage_hash": S3.preview_hash(stage_spec),
        "stage_have": S3.preview_have(session, scene_id=scene.id), "set_loc": set_loc,
        "stage_d": float((scene.stage3d or {}).get("duration") or 0),
        **_timeline(shots, float((scene.stage3d or {}).get("duration") or 0)),
        **_cast_ctx(session, project, scene),
        "kinds": ASSET_KINDS,
        "wardrobe": wardrobe,
        "scene": scene, "scene_code": scene_code(session, scene), "location": location, "assets": assets,
        "sheets": sheets, "locations": scene_locations(session, scene), "shots": rows, "script": script,
        "ready_n": sum(1 for r in rows if r["state"] == "ok"), "durations": durations(session, [scene.id]),
        "main_form": "scene-form", "assistant_key": f"sb_scene:{scene.id}",
    }
    from app import scene_nav

    ctx["nav"] = scene_nav.build(session, project, scene, "storyboard")
    return render_stage(request, session, project, STAGE, "storyboard/scene.html", ctx, current=("scene", scene.id))


@router.get("/storyboard/cenas/{scene_id}")
def scene_page(request: Request, project_id: int, scene_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    return _scene_view(request, session, project, get_scene(session, project, scene_id))


def _default_video_settings(session: Session) -> dict:
    from app.routes.video import video_models

    models = video_models(session)
    model = next((m for m in models if m.get("default")), models[0] if models else None)
    if not model:
        return {}
    durations_ = model.get("durations_s") or []
    return {"model": model["name"], "duration_s": durations_[0] if durations_ else None}


def create_shot(session: Session, scene: Scene) -> Shot:
    """Plano novo: uma câmara sobre a encenação da cena, a começar onde acaba o plano anterior; a continuidade parte da
    do plano anterior. Se a cena ainda não tem local da maquete, fica o primeiro local da cena que a tem."""
    from app.scene3d import shot_window

    previous = scene_shots(session, scene)
    if not scene.set3d_asset_id:
        loc = next((a for a in scene_locations(session, scene) if a.layout3d), None)
        scene.set3d_asset_id = loc.id if loc else None
        session.add(scene)
    shot = Shot(scene_id=scene.id, number=next_shot_number(session, scene), **_default_video_settings(session))
    if previous:
        start, dur = shot_window(previous[-1])
        shot.stage_start = round(start + dur, 2)
    session.add(shot)
    session.flush()
    if previous:
        last = previous[-1]
        for c in session.exec(select(ShotCheck).where(ShotCheck.shot_id == last.id).order_by(ShotCheck.order)).all():
            session.add(ShotCheck(shot_id=shot.id, order=c.order, category=c.category, text=c.text,
                                  note=c.note, checked=False))
    return shot


def _renumber(session: Session, items: list) -> None:
    for n, item in enumerate(items, start=1):
        if item.number != n:
            item.number = n
            session.add(item)


def delete_shot(session: Session, shot: Shot) -> None:
    """Apaga o plano para a Reciclagem (com a câmara, os takes e os ficheiros; recupera-se durante 30 dias)."""
    from app import trash
    from app.models import Project

    scene = session.get(Scene, shot.scene_id)
    trash.trash_shot(session, session.get(Project, scene.project_id), shot)


@router.post("/storyboard/cenas/{scene_id}")
async def scene_save(request: Request, project_id: int, scene_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    scene = get_scene(session, project, scene_id)
    form = await request.form()
    op = text(form, "op") or ""
    verb, _, arg = op.partition(":")
    url = f"/projetos/{project.id}/storyboard/cenas/{scene.id}"
    if "blocking" in form:
        scene.blocking = text(form, "blocking")
    if "set3d_asset" in form:  # o local da maquete onde a encenação se passa
        set_location(session, project, scene, text(form, "set3d_asset") or "")
    if "cast3d_sent" in form or any(k.startswith("cast3d_color:") for k in form.keys()):
        _save_cast3d(session, project, scene, form)
    looks_changed = False
    for key in form.keys():  # guarda-roupa: o look de cada personagem nesta cena ("" = a folha base)
        if key.startswith("look:") and key[5:].isdigit():
            from app import looks as L
            from app.models import CharacterLook

            asset = session.get(Asset, int(key[5:]))
            if asset is None or asset.project_id != project.id:
                continue
            raw = text(form, key) or ""
            look = session.get(CharacterLook, int(raw)) if raw.isdigit() else None
            if look is not None and look.asset_id != asset.id:
                continue
            if L.scene_look(session, scene, asset) != look:
                L.set_scene_look(scene, asset, look)
                looks_changed = True
    session.add(scene)
    shots = scene_shots(session, scene)
    if "cover_sent" in form or verb == "all_lines":  # guião marcado: que planos cobrem cada linha
        from app.models import SceneLine
        from app.shot_script import set_shot_lines

        line_ids = [ln.id for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)
                                                 .order_by(SceneLine.order)).all()]
        wanted = {sh.id: [] for sh in shots}
        for raw in form.getlist("cover"):
            lid, _, sid = str(raw).partition(":")
            if lid.isdigit() and sid.isdigit() and int(sid) in wanted and int(lid) in line_ids:
                wanted[int(sid)].append(int(lid))
        if verb == "all_lines" and arg.isdigit() and int(arg) in wanted:
            wanted[int(arg)] = list(line_ids)
        for sh in shots:
            set_shot_lines(session, sh, wanted[sh.id])
    if verb == "all_lines":
        session.commit()
        flash_info(request, "O plano passa a cobrir todas as linhas do guião da cena.")
        return RedirectResponse(url + "#guiao", status_code=303)
    if verb == "add_shot":
        shot = create_shot(session, scene)
        session.commit()
        return RedirectResponse(f"/projetos/{project.id}/storyboard/planos/{shot.id}", status_code=303)
    if verb == "delete_shot" and arg.isdigit():
        target = next((sh for sh in shots if sh.id == int(arg)), None)
        if target is None:
            raise HTTPException(status_code=404)
        delete_shot(session, target)  # os outros renumeram-se ao gravar, pela ordem na encenação (models._renumber_shots)
        session.commit()
        flash_info(request, "Foi para a Reciclagem (no topo da página): podes recuperá-lo durante 30 dias.")
        return RedirectResponse(url + "#planos", status_code=303)
    session.commit()
    if looks_changed:
        flash_info(request, "Guarda-roupa da cena guardado. Os vídeos dos planos desta cena passam a levar a folha do "
                            "look escolhido; os prompts reescrevem-se ao gerar.")
    return RedirectResponse(url + ("" if op else "?guardado=1") + ("#planos" if op else ""), status_code=303)


# ── Plano ────────────────────────────────────────────────────

def _neighbours(session: Session, scene: Scene, shot: Shot) -> tuple[Shot | None, Shot | None]:
    shots = scene_shots(session, scene)
    idx = next(i for i, s in enumerate(shots) if s.id == shot.id)
    return (shots[idx - 1] if idx > 0 else None), (shots[idx + 1] if idx + 1 < len(shots) else None)


def _moments_ctx(session: Session, project: Project, shot: Shot) -> dict:
    """Os momentos da câmara, cada um com a vista do local, as opções de vista e o que falta para a imagem."""
    from app import location3d, prompt_writer

    loc = moments.location(session, shot)
    views = location3d.location_views(session, loc) if loc else []
    found = approved_media(session, [v.image_id for v in views])
    options = [(v.id, (f"{v.zone} · " if v.zone else "") + v.label + ("" if v.image_id in found else " (sem imagem)"))
               for v in views]
    rows = []
    for r in moments.rows(session, shot):
        v = r["view"]
        rows.append(r | {
            "results": [m for m in media_list(session, locview_id=v.id, role="locview", status="pending")] if v else [],
            "jobs": gpt_jobs(session, project, "locview", locview_id=v.id) if v else [],
            "task": prompt_writer.task_for(session, project.id, "vista", str(v.id)) if v else None,
            "render_ok": bool(v and v.render_id), "ready": moments.ready(r),
            "look_ref": location3d.look_reference(session, loc, v) if v is not None and loc is not None and v.kind == "view" else None,
        })
    pending_views = [{"id": v.id, "zone": v.zone, "cam": v.cam,
                      "outdoor": bool((location3d.zone_info(loc.layout3d, v.zone) or {}).get("outdoor"))}
                     for v in views if v.render_id is None] if loc else []
    return {"moments": rows, "view_options": options, "loc_pending": pending_views,
            "loc_hash": _location_hash(session, project, loc) if loc else ""}


def _location_hash(session: Session, project: Project, loc: Asset) -> str | None:
    from app import scene3d

    return scene3d.preview_hash(scene3d.compose(session, project.id, location=loc))


def _shot_view(request: Request, session: Session, project: Project, shot: Shot, scene: Scene):
    from app.shot_script import script_coverage, shot_line_ids

    checks = list(session.exec(select(ShotCheck).where(ShotCheck.shot_id == shot.id).order_by(ShotCheck.order, ShotCheck.id)).all())
    prev_shot, next_shot = _neighbours(session, scene, shot)
    script = script_coverage(session, scene)
    from app import scene3d as _s3

    ctx = {
        "set_var": _s3.variation_info(session, scene),
        "script": script, "shot_lines": set(shot_line_ids(session, shot)),
        "line_groups": [("Guião da cena", [(r["line"].id, None, f"{r['n']} · " + (
            f"{r['who'].name if r['who'] else '—'}: «{r['line'].text}»" if r["line"].kind == "dialogue" else r["line"].text)
            + (" — " + ", ".join(f"P{o.number:02d}" for o in r["shots"] if o.id != shot.id) if any(o.id != shot.id for o in r["shots"]) else ""))
            for r in script])] if script else [],
        "shot": shot, "scene": scene, "scene_code": scene_code(session, scene),
        "checks": checks, "check_categories": CHECK_CATEGORIES, "state": shot_board_state(session, shot),
        "prev_shot": prev_shot, "next_shot": next_shot, "main_form": "shot-form", "assistant_key": f"sb_shot:{shot.id}",
        "locations": scene_locations(session, scene), "location": moments.location(session, shot),
    } | _maquete_ctx(session, project, shot) | _moments_ctx(session, project, shot)
    from app import scene_nav

    from app import keyframe

    ctx["key"] = keyframe.block(session, project, shot)
    ctx["nav"] = scene_nav.build(session, project, scene, "storyboard", shot)
    return render_stage(request, session, project, STAGE, "storyboard/shot.html", ctx, current=("shot", shot.id))


def _save_cast3d(session: Session, project: Project, scene: Scene, form) -> bool:
    """Elenco da encenação: quem entra (modal de seleção) e a cor de cada um, sem repetir cores na cena. Quem entra de
    novo fica parado no meio do local (o Assistente põe-no no sítio); quem sai deixa a encenação."""
    from app import scene3d

    assets = {a.name: a for a in session.exec(select(Asset).where(
        Asset.project_id == project.id, Asset.kind.in_(("character", "prop", "element")))).all()}
    old = scene3d.scene_cast(session, scene)
    if "cast3d_sent" in form:
        by_id = {str(a.id): a.name for a in assets.values()}
        chosen = [by_id[v] for v in form.getlist("cast3d") if v in by_id]
    else:
        chosen = list(old)
    cast: dict[str, str] = {}
    for code in chosen:  # primeiro quem já estava (mantém a cor), depois os novos (a de outra cena, ou uma livre)
        if code in old:
            cast[code] = old[code]
    for code in chosen:
        if code not in cast:
            cast[code] = scene3d.suggest_color(session, scene, code, set(cast.values()))
    for key in form.keys():  # cor escolhida: se já era de outro, trocam
        code = key.split(":", 1)[1] if key.startswith("cast3d_color:") else None
        color = text(form, key) if code else None
        if code in cast and color in scene3d.CAST_ORDER and color != cast[code]:
            other = next((c for c, col in cast.items() if col == color and c != code), None)
            if other:
                cast[other] = cast[code]
            cast[code] = color
    changed = cast != old
    scene.cast3d = cast or None
    stage = dict(scene.stage3d or {})
    if stage or cast:
        actors = [a for a in stage.get("actors", []) if not a.get("code") or a["code"] in cast]
        present = {a.get("code") for a in actors}
        n = 0
        for code in cast:
            if code not in present:  # lado a lado no meio do local, à espera de ser encenado
                kind = {"element": "person"}.get(assets[code].kind, assets[code].kind)
                actors.append({"kind": kind, "code": code, "keys": [{"t": 0, "pos": [round((n - 0.5) * 1.2, 2), 0, 0]}]})
                n += 1
        stage["actors"] = actors
        stage.setdefault("duration", 10)
        scene.stage3d = scene3d.validate_stage(stage, session, project.id)
    session.add(scene)
    return changed


def shot_durations(session: Session, shot: Shot) -> list[int]:
    """Durações que o modelo de vídeo do plano consegue gerar (a animação 3D tem de ter uma destas)."""
    from app.routes.video import shot_model

    model = shot_model(session, shot)
    return (model or {}).get("durations_s") or list(range(3, 16))


def set_shot_duration(session: Session, shot: Shot, seconds: int) -> None:
    """Uma só duração para o plano: o vídeo e o troço da encenação que ele mostra (a câmara faz o mesmo percurso no
    tempo novo; a encenação não muda)."""
    from app import scene3d

    old = float(shot.duration_s or seconds)
    shot.duration_s = seconds
    if shot.scene3d and abs(old - seconds) > 1e-6:
        shot.scene3d = scene3d.retime_camera(shot.scene3d, old, float(seconds))
    session.add(shot)


def fmt_durations(allowed: list[int]) -> str:
    """«4–15 s» quando é um intervalo seguido; senão a lista."""
    if allowed and allowed == list(range(allowed[0], allowed[-1] + 1)):
        return f"{allowed[0]}–{allowed[-1]} s"
    return ", ".join(str(d) for d in allowed) + " s"


def change_duration(session: Session, shot: Shot, seconds: int) -> list[str]:
    """Muda a duração do plano e refaz os troços da cena: os planos seguintes começam mais cedo ou mais tarde na
    encenação pela diferença. ValueError (com as opções) se o modelo não aceita a duração ou se o fim passa a
    encenação da cena. Devolve as notas (o vídeo-guia e os previs que ficam por refazer)."""
    from app import scene3d

    allowed = shot_durations(session, shot)
    if seconds not in allowed:
        raise ValueError(f"O modelo de vídeo do plano não gera {seconds} s. Durações aceites: {fmt_durations(allowed)}.")
    old = int(shot.duration_s or seconds)
    if seconds == old:
        return []
    delta = seconds - old
    scene = session.get(Scene, shot.scene_id)
    shots = scene_shots(session, scene)
    after = [sh for sh in shots if sh.number > shot.number]
    stage_d = float((scene.stage3d or {}).get("duration") or 0)
    if scene.stage3d and stage_d:
        ends = [scene3d.shot_window(sh)[0] + (delta if sh in after else 0) + (seconds if sh.id == shot.id else float(sh.duration_s or 5))
                for sh in shots]
        if max(ends) > stage_d + 1e-6:
            raise ValueError(f"Os planos da cena passam a acabar aos {max(ends):g} s, e a encenação tem {stage_d:g} s: faltam "
                             f"{max(ends) - stage_d:g} s. Alonga primeiro a encenação (campo scene_duration:{scene.id} no "
                             f"ecrã sb_scene:{scene.id}).")
    set_shot_duration(session, shot, seconds)
    for sh in after:  # os troços seguintes andam com a diferença
        sh.stage_start = round(max(0.0, float(sh.stage_start or 0) + delta), 3)
        session.add(sh)
    notes = []
    if shot.guide_video_id:
        notes.append("o vídeo-guia deste plano ficou desatualizado (tem a duração antiga): grava-o de novo")
    if shot.scene3d:
        notes.append("os previs dos momentos ficam por refazer (render_maquete)")
    moved = [f"P{sh.number:02d}" for sh in after if sh.scene3d]
    if moved:
        notes.append(f"os troços de {', '.join(moved)} mudaram: os vídeos-guia e previs deles também ficam por refazer")
    return notes


def set_location(session: Session, project: Project, scene: Scene, value: str) -> None:
    """O local (com maquete) onde a encenação da cena se passa (todos os planos dela)."""
    loc = session.get(Asset, int(value)) if value.isdigit() else None
    if loc is not None and loc.project_id == project.id and loc.kind == "location" and loc.layout3d:
        if loc.id != scene.set3d_asset_id:
            for sh in scene_shots(session, scene):  # as vistas escolhidas eram do outro local
                sh.moments = None
                session.add(sh)
        scene.set3d_asset_id = loc.id
        session.add(scene)


def _maquete_ctx(session: Session, project: Project, shot: Shot) -> dict:
    """Cena 3D do plano: o que o visualizador desenha, os momentos (para os renders) e o vídeo-guia."""
    from app import scene3d

    guides = media_list(session, shot_id=shot.id, role="guide")
    approved = next((m for m in guides if m.id == shot.guide_video_id), None)
    # um guia acabado de gravar (por aprovar) passa à frente do aprovado, senão não se via nem se podia aprovar
    # (só os mais recentes: ao aprovar um, o anterior volta a «por decidir» e não deve reaparecer)
    pending = next((m for m in reversed(guides) if m.status == "pending" and m.id > (shot.guide_video_id or 0)), None)
    guide = pending or approved
    spec = scene3d.compose(session, project.id, shot=shot)
    digest = scene3d.preview_hash(spec)
    data = shot.moments or {}
    return {"scene3d": spec, "preview_hash": digest,
            "preview_have": scene3d.preview_have(session, shot_id=shot.id),
            "m3d_moments": [{"n": k["n"], "t": k["t"]} for k in moments.keys(shot)],
            "renders_stale": bool(shot.scene3d) and data.get("hash") != digest,
            "guide": guide, "guide_ok": guide is not None and guide.id == shot.guide_video_id,
            "guide_stale": guide is not None and bool(guide.duration_ms) and shot.duration_s is not None
            and abs(guide.duration_ms - shot.duration_s * 1000) > 50,
            "guide_old": guide is not None and moments.guide_hash(shot, guide.id) not in (None, digest),
            "guide_replaces": approved is not None and guide is not approved,
            # os outros guias gravados (por decidir ou o aprovado), para voltar a um anterior sem gravar de novo
            "guide_others": [g for g in reversed(guides) if g is not guide and g.status != "rejected"
                             and (media_root() / g.path).is_file()],
            "m3d_durations": shot_durations(session, shot), **_window_ctx(session, shot)}


def _window_ctx(session: Session, shot: Shot) -> dict:
    """O troço da encenação que o plano mostra e quem a câmara vê nele."""
    from app import scene3d

    scene = session.get(Scene, shot.scene_id)
    stage = scene.stage3d or {}
    start, dur = scene3d.shot_window(shot)
    labels = {a.name: a for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    cast = scene3d.scene_cast(session, scene)
    return {"stage": stage, "stage_d": float(stage.get("duration") or 0), "win_start": start, "win_end": start + dur,
            "in_frame": [{"asset": labels[c], "hex": scene3d.hex_color(cast.get(c, "white"))}
                         for c in scene3d.in_frame(session, shot) if c in labels],
            "window_over": bool(stage) and start + dur > float(stage.get("duration") or 0) + 0.05,
            "cam_warnings": scene3d.turn_warnings(shot),
            "crowd": scene3d.crowd(session, shot) if shot.scene3d else None}


def _cast_ctx(session: Session, project: Project, scene: Scene) -> dict:
    """Elenco da encenação: os escolhidos com a cor de cada um e o modal de seleção (personagens e adereços)."""
    from app import scene3d

    cast = scene3d.scene_cast(session, scene)
    assets = session.exec(select(Asset).where(Asset.project_id == project.id,
                                              Asset.kind.in_(("character", "prop", "element")))
                          .order_by(Asset.kind, Asset.name)).all()
    media = approved_media(session, [a.portrait_image_id or a.approved_image_id for a in assets])
    by_code = {a.name: a for a in assets}
    return {
        "cast3d": [{"asset": by_code[c], "color": col, "hex": scene3d.hex_color(col)} for c, col in cast.items() if c in by_code],
        "cast3d_ids": [by_code[c].id for c in cast if c in by_code],
        "cast3d_groups": [(label, [(a.id, media.get(a.portrait_image_id or a.approved_image_id), a.name) for a in assets if a.kind == k])
                          for k, label in (("character", "Personagens"), ("prop", "Adereços"),
                                           ("element", "Elementos do mundo (robots, veículos…)"))],
        "cast3d_colors": [(c, scene3d.CAST_NAMES[c], scene3d.hex_color(c)) for c in scene3d.CAST_ORDER],
    }


@router.get("/storyboard/planos/{shot_id}")
def shot_page(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    shot, scene = get_shot(session, project, shot_id)
    return _shot_view(request, session, project, shot, scene)


@router.post("/storyboard/planos/{shot_id}")
async def shot_save(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    from app.routes import prompt_ops
    from app.shot_script import set_shot_lines

    project = get_project(session, project_id)
    shot, scene = get_shot(session, project, shot_id)
    form = await request.form()
    op = text(form, "op") or ""
    verb, _, arg = op.partition(":")
    url = f"/projetos/{project.id}/storyboard/planos/{shot.id}"

    if verb == "delete_shot":
        delete_shot(session, shot)
        session.commit()
        flash_info(request, "Foi para a Reciclagem (no topo da página): podes recuperá-lo durante 30 dias.")
        return RedirectResponse(f"/projetos/{project.id}/storyboard/cenas/{scene.id}#planos", status_code=303)

    for field in ("title", "description", "shot_type", "camera_angle", "camera_movement"):
        if field in form:
            setattr(shot, field, text(form, field))
    raw_start = str(form.get("stage_start") or "").strip().replace(",", ".")
    try:  # o segundo da encenação onde o plano começa
        if raw_start and float(raw_start) != shot.stage_start:
            shot.stage_start = round(max(0.0, min(600.0, float(raw_start))), 2)
    except ValueError:
        flash(request, "O início do plano tem de ser um número de segundos (ex.: 6 ou 6,5).")
    if "lines_sent" in form:  # guião marcado: as linhas que o plano cobre
        set_shot_lines(session, shot, [int(v) for v in form.getlist("line") if str(v).isdigit()])
    if verb == "all_lines":
        from app.models import SceneLine

        set_shot_lines(session, shot, list(session.exec(select(SceneLine.id).where(SceneLine.scene_id == scene.id)).all()))
    raw_d = str(form.get("m3d_duration") or "").strip()
    if raw_d.isdigit() and int(raw_d) in shot_durations(session, shot) and int(raw_d) != shot.duration_s:
        set_shot_duration(session, shot, int(raw_d))
    for key in form.keys():  # vista do local em cada momento: escolhida à mão, ou "" = a automática
        if key.startswith("mview:") and key[6:].isdigit():
            value = text(form, key) or ""
            moments.choose_view(shot, int(key[6:]), int(value) if value.isdigit() else None)

    # Continuidade
    checked = {int(v) for v in form.getlist("check") if v.isdigit()}
    checks = list(session.exec(select(ShotCheck).where(ShotCheck.shot_id == shot.id).order_by(ShotCheck.order, ShotCheck.id)).all())
    if "checks_present" in form:
        for c in checks:
            c.checked = c.id in checked
            session.add(c)
    if verb == "add_check":
        category = text(form, "new_check_category")
        body = text(form, "new_check_text")
        if category in CHECK_CATEGORIES and body:
            session.add(ShotCheck(shot_id=shot.id, order=len(checks), category=category, text=body,
                                  note=text(form, "new_check_note")))
        else:
            flash(request, "Escolhe a categoria e escreve a verificação.")
    elif verb == "del_check" and arg.isdigit():
        c = next((c for c in checks if c.id == int(arg)), None)
        if c:
            session.delete(c)
    from app import outdated

    done = outdated.apply_op(session, shot, op)  # «manter» do «Antes de gerar» do frame-chave
    if done:
        flash_info(request, done)
    if "key_moment" in form:  # de que momento se gera o frame-chave
        from app import keyframe

        raw = text(form, "key_moment") or ""
        keyframe.choose_moment(shot, int(raw) if raw.isdigit() else None)
    if verb == "key_video" and arg.isdigit():  # este frame-chave aprovado segue no pedido de vídeo
        from app import keyframe

        if keyframe.use_in_video(session, shot, int(arg)):
            flash_info(request, "Este frame-chave passa a seguir no pedido de vídeo.")
    if verb == "mview_new" and arg.isdigit():  # vista nova do local a partir da câmara deste momento
        try:
            view = moments.create_view(session, shot, int(arg))
            flash_info(request, f"Vista «{view.label}» criada na maquete do local. O render tira-se agora; depois carrega "
                                "em Gerar para criar a imagem.")
        except ValueError as exc:
            flash(request, str(exc))
    session.add(shot)
    prompt_ops.save(session, project, form, op)
    session.commit()
    if prompt_ops.run(request, session, project, form, op):
        session.commit()
        return RedirectResponse(url + ("#frame-chave" if ":chave:" in op else "#momentos"), status_code=303)
    anchor = {"add_check": "#continuidade", "del_check": "#continuidade", "mview_new": "#momentos",
              "key_video": "#frame-chave"}.get(verb, "#frame-chave" if "key_moment" in form else "")
    return RedirectResponse(url + ("" if op else "?guardado=1") + anchor, status_code=303)


# Cena 3D: o navegador envia o render de cada momento, o das vistas novas do local e o vídeo-guia

def _store_upload(session: Session, project: Project, shot: Shot, role: str, data: bytes, ext: str, name: str) -> MediaFile:
    from pathlib import PurePosixPath

    from app.media import safe_segment, write_file

    folder = PurePosixPath(safe_segment(project.prefix), "planos", str(shot.id), "maquete")
    n = len(media_list(session, shot_id=shot.id, role=role)) + 1
    rel = folder / f"{name}_v{n}.{ext}"
    while (media_root() / rel).exists():
        n += 1
        rel = folder / f"{name}_v{n}.{ext}"
    write_file(str(rel), data)
    media = MediaFile(project_id=project.id, path=str(rel), kind="video" if role == "guide" else "image", role=role,
                      status="approved" if role == "render" else "pending", shot_id=shot.id, source="maquete")
    session.add(media)
    session.flush()
    return media


@router.post("/storyboard/planos/{shot_id}/maquete/render/{n}")
async def moment_render(request: Request, project_id: int, shot_id: int, n: int, session: Session = Depends(get_session)):
    """Render de um momento (a câmara do plano nesse segundo, com o elenco): substitui o anterior desse momento."""
    from app.media import MAX_IMAGE_BYTES, UploadError, image_extension, read_limited

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    form = await request.form()
    digest = str(form.get("hash") or "")
    if n not in {k["n"] for k in moments.keys(shot)} or not re.fullmatch(r"[0-9a-f]{12}", digest):
        return JSONResponse({"ok": False, "error": "Momento desconhecido."}, status_code=400)
    try:
        data = await read_limited(form.get("file"), MAX_IMAGE_BYTES)
        ext = image_extension(data, "render.png")
    except (UploadError, AttributeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc) or "Ficheiro em falta."}, status_code=400)
    media = save_moment_render(session, project, shot, n, digest, data, ext)
    session.commit()
    return JSONResponse({"ok": True, "id": media.id})


def save_moment_render(session: Session, project: Project, shot: Shot, n: int, digest: str, data: bytes, ext: str) -> MediaFile:
    """O previs do momento n (substitui o anterior), com a versão da cena 3D em que foi tirado."""
    info = dict(shot.moments or {})
    renders = dict(info.get("renders") or {})
    old = session.get(MediaFile, renders.get(str(n))) if renders.get(str(n)) else None
    media = _store_upload(session, project, shot, "render", data, ext, f"render_m{n}")
    renders[str(n)] = media.id
    info["renders"], info["hash"] = renders, digest
    shot.moments = info
    session.add(shot)
    if old is not None:
        reject_media(session, old)
    session.flush()
    return media


@router.post("/storyboard/planos/{shot_id}/camera")
async def shot_camera_save(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    """Câmara editada à mão no visualizador (as posições-chave). A versão anterior fica guardada para «Desfazer»;
    {"undo": true} volta a ela."""
    from app import scene3d

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "Pedido inválido."}, status_code=400)
    data = dict(shot.moments or {})
    if body.get("undo"):
        prev = data.get("camera_prev")
        if not prev:
            return JSONResponse({"ok": False, "error": "Não há versão anterior da câmara."}, status_code=400)
        data["camera_prev"] = (shot.scene3d or {}).get("camera")
        shot.scene3d = {"camera": prev}
    else:
        try:
            clean = scene3d.validate_camera({"camera": body.get("camera")})
        except scene3d.SceneError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        if shot.duration_s and any(k["t"] > shot.duration_s + 0.01 for k in clean["camera"]):
            return JSONResponse({"ok": False, "error": f"Há posições depois do fim do plano ({shot.duration_s} s)."},
                                status_code=400)
        data["camera_prev"] = (shot.scene3d or {}).get("camera")
        shot.scene3d = {"camera": clean["camera"]}
    shot.moments = data
    session.add(shot)
    session.commit()
    return JSONResponse({"ok": True})


@router.post("/storyboard/cenas/{scene_id}/encenacao/previas")
async def stage_previews(request: Request, project_id: int, scene_id: int, session: Session = Depends(get_session)):
    """Vistas da encenação da cena tiradas pelo navegador, para o Assistente ver o que construiu."""
    from app import scene3d

    project = get_project(session, project_id)
    scene = get_scene(session, project, scene_id)
    await scene3d.save_previews(request, session, project, f"cenas/{scene.id}/encenacao", scene_id=scene.id)
    return JSONResponse({"ok": True})


@router.post("/storyboard/planos/{shot_id}/maquete/previas")
async def maquete_previews(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    """Vistas da cena 3D do plano tiradas pelo navegador, para o Assistente ver o que construiu."""
    from app import scene3d

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    await scene3d.save_previews(request, session, project, f"planos/{shot.id}/maquete", shot_id=shot.id)
    return JSONResponse({"ok": True})


@router.post("/storyboard/planos/{shot_id}/maquete/guia")
async def maquete_guide(request: Request, project_id: int, shot_id: int, session: Session = Depends(get_session)):
    """Vídeo-guia gravado no visualizador (WebM ou MP4): fica por aprovar."""
    from app.media import MAX_VIDEO_BYTES, UploadError, read_limited

    project = get_project(session, project_id)
    shot, _scene = get_shot(session, project, shot_id)
    upload = (await request.form()).get("file")
    try:
        data = await read_limited(upload, MAX_VIDEO_BYTES)
    except (UploadError, AttributeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc) or "Ficheiro em falta."}, status_code=400)
    try:
        media = save_guide(session, project, shot, data)
    except UploadError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    session.commit()
    return JSONResponse({"ok": True, "id": media.id})


def save_guide(session: Session, project: Project, shot: Shot, data: bytes) -> MediaFile:
    """Guarda um vídeo-guia gravado (WebM ou MP4) como por aprovar, normalizado para a duração do plano. Usado pelo botão
    «Gravar vídeo-guia» e pela gravação do conector MCP. UploadError com o motivo se não serve."""
    from app import scene3d
    from app.media import UploadError, normalize_guide

    if data[:4] == b"\x1a\x45\xdf\xa3":
        ext = "webm"
    elif len(data) > 12 and data[4:8] == b"ftyp":
        ext = "mp4"
    else:
        raise UploadError("O vídeo tem de ser WebM ou MP4.")
    want = float(shot.duration_s or 0)
    data, measured = normalize_guide(data, ext, want or None)
    if measured and want and measured < want * 1000 - 500:
        raise UploadError(f"A gravação ficou com {measured / 1000:.1f} s e o plano tem {want:g} s "
                          "(o navegador parou de desenhar a meio). Mantém esta página à vista e grava outra vez.")
    media = _store_upload(session, project, shot, "guide", data, "mp4", "guide")
    media.duration_ms = int(want * 1000) or measured  # a duração do plano quando foi gravado: para saber se ficou desatualizado
    session.add(media)
    data_m = dict(shot.moments or {})  # a versão da encenação e da câmara gravada em cada guia (vale quando o aprovam)
    hashes = {k: v for k, v in (data_m.get("guide_hashes") or {}).items()
              if int(k) in {m.id for m in media_list(session, shot_id=shot.id, role="guide") if m.status != "rejected"}}
    hashes[str(media.id)] = scene3d.preview_hash(scene3d.compose(session, project.id, shot=shot))
    data_m["guide_hashes"] = hashes
    shot.moments = data_m
    session.add(shot)
    session.flush()
    return media


def view_of_location(session: Session, project: Project, view_id: int) -> LocationView:
    view = session.get(LocationView, view_id)
    asset = session.get(Asset, view.asset_id) if view else None
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404)
    return view
