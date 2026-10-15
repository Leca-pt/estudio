"""Momentos de um plano (docs/DECISOES.md, ponto 5): as posições da câmara da cena 3D marcadas com "moment".

Em cada momento:
- o render da cena 3D do plano (com o elenco a cores) — o previs, para o utilizador e o escritor de prompts;
- a vista do local que vai para o vídeo: a escolhida à mão ou, sem escolha, a vista da maquete mais parecida com a
  câmara nesse momento. Se nenhuma serve, cria-se uma vista nova do local a partir da câmara do momento (fica guardada
  na maquete do local e serve para outros planos).

O vídeo recebe as imagens dessas vistas (uma por momento, sem repetir), as folhas do elenco e o vídeo-guia.
"""

import math

from sqlmodel import Session, select

from app import looks
from app.models import Asset, LocationView, MediaFile, Scene, Shot
from app.memo import memo

GOOD, CLOSE = 0.8, 1.6  # parecença das vistas (ver score): até GOOD é parecida; até CLOSE serve; acima, criar uma nova
MAX_MOMENTS = 6


def keys(shot: Shot) -> list[dict]:
    """Os momentos: as posições de câmara marcadas com "moment" (ou "panel", nas cenas antigas); sem nenhuma marcada,
    todas (até 6). Cada um: {"n", "t", "cam": {pos, look_at, lens}}."""
    cams = (shot.scene3d or {}).get("camera") or []
    marked = [k for k in cams if k.get("moment") or k.get("panel")]
    chosen = (marked or cams)[:MAX_MOMENTS]
    return [{"n": n, "t": float(k["t"]), "cam": {"pos": k["pos"], "look_at": k["look_at"], "lens": k.get("lens", 28)}}
            for n, k in enumerate(chosen, 1)]


def moment_key(m: dict) -> str:
    """A identidade de um momento para as escolhas à mão: o segundo e a câmara (arredondados). Juntar ou apagar outras
    posições não mexe nela; mudar a câmara deste momento desfaz a escolha (a vista pode já não servir)."""
    c = m["cam"]
    r = lambda v: ",".join(f"{x:.1f}" for x in v)  # noqa: E731
    return f"{float(m['t']):.1f}|{r(c['pos'])}|{r(c['look_at'])}|{float(c.get('lens') or 28):.0f}"


def _manual(data: dict, m: dict):
    """A vista escolhida à mão para este momento."""
    v = (data.get("views") or {}).get(moment_key(m))
    return int(v) if str(v or "").isdigit() else None


@memo
def location(session: Session, shot: Shot) -> Asset | None:
    """O local (com maquete) onde o plano se passa: o da encenação da cena."""
    scene = session.get(Scene, shot.scene_id)
    loc = session.get(Asset, scene.set3d_asset_id) if scene is not None and scene.set3d_asset_id else None
    return loc if loc is not None and loc.layout3d else None


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


def _angle(a: list[float], b: list[float]) -> float:
    a, b = _unit(a), _unit(b)
    return math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))


def score(view_cam: dict, cam: dict, same_zone: bool = True) -> float:
    """Diferença entre o que a câmara do plano vê e o que a vista mostra (0 = igual). O que conta é o ponto para onde
    o plano olha (look_at):
    - a vista tem de o ter no enquadramento (ângulo entre a direção da vista e o ponto, para lá do centro);
    - tem de o ver do mesmo lado (ângulo entre as duas linhas de visão até ao ponto);
    - a uma distância parecida (de perto ou de longe);
    - e de preferência da mesma zona (uma vista da sala não mostra a rua).
    Assim uma vista mais atrás na mesma linha de visão é parecida, e uma de outra divisão não é."""
    target, pos, vpos = cam["look_at"], cam["pos"], view_cam["pos"]
    to_t = [target[k] - vpos[k] for k in range(3)]
    in_frame = max(0.0, _angle([view_cam["look_at"][k] - vpos[k] for k in range(3)], to_t) - 0.12)
    same_side = _angle([target[k] - pos[k] for k in range(3)], to_t)
    d_view, d_cam = math.dist(vpos, target), math.dist(pos, target)
    scale = abs(math.log2(max(d_view, 0.3) / max(d_cam, 0.3)))
    return in_frame / 0.5 + same_side / 0.6 + 0.4 * scale + (0.0 if same_zone else 0.8)


def fit_label(s: float | None) -> str:
    if s is None:
        return ""
    return "parecida" if s <= GOOD else "aproximada" if s <= CLOSE else "diferente"


def _approved(session: Session, media_id: int | None) -> MediaFile | None:
    m = session.get(MediaFile, media_id) if media_id else None
    return m if m is not None and m.status == "approved" else None


@memo
def rows(session: Session, shot: Shot, renders: bool = True) -> list[dict]:
    """Cada momento com o render, a vista escolhida (à mão ou automática), a parecença e a imagem aprovada dela.
    renders=False: sem os previs (não monta a cena 3D para ver se estão atualizados: é o mais demorado)."""
    loc = location(session, shot)
    views = list(session.exec(select(LocationView).where(LocationView.asset_id == loc.id)).all()) if loc else []
    by_id = {v.id: v for v in views}
    data = shot.moments or {}
    taken = data.get("renders") or {}
    fresh = renders and data.get("hash") == render_hash(session, shot)  # previs tirados com outra versão da câmara ou da encenação: não servem
    out = []
    for m in keys(shot):
        n = str(m["n"])
        zone = scene3d_zone(loc, m["cam"]) if loc else None
        scores = {v.id: score(v.cam, m["cam"], same_zone=not (zone and v.zone and v.zone != zone)) for v in views}
        sc = lambda v: scores[v.id]  # noqa: E731 — cada vista pontuada uma só vez
        chosen = _manual(data, m)
        view = by_id.get(chosen) if chosen else None
        auto = view is None
        if auto and views:
            view = min(views, key=sc)
        s = sc(view) if view else None
        render = session.get(MediaFile, taken.get(n)) if taken.get(n) and fresh else None
        out.append(m | {"view": view, "auto": auto, "score": s, "fit": fit_label(s),
                        "image": _approved(session, view.image_id) if view else None,
                        "render": render, "render_fresh": render is not None,
                        "zone": zone, "candidates": sorted(views, key=sc)[:4] if renders else []})
    return out


def scene3d_zone(loc: Asset, cam: dict) -> str | None:
    from app import location3d

    x, y, z = cam["pos"]
    return location3d.zone_at(loc.layout3d, x, y, z) or location3d.nearest_outdoor(loc.layout3d, x, y, z)


@memo
def render_hash(session: Session, shot: Shot) -> str | None:
    """Versão da cena 3D do plano (os renders dos momentos tirados com outra versão estão desatualizados)."""
    from app import scene3d

    scene = session.get(Scene, shot.scene_id)
    return scene3d.preview_hash(scene3d.compose(session, scene.project_id, shot=shot))


def ready(r: dict) -> bool:
    """Um momento está pronto com uma vista com imagem aprovada que serve: escolhida à mão (decisão do utilizador) ou,
    na automática, pelo menos «aproximada»."""
    return r["image"] is not None and (not r["auto"] or (r["score"] is not None and r["score"] <= CLOSE))


def state(session: Session, shot: Shot) -> tuple[int, int]:
    """(momentos, momentos prontos) — o storyboard do plano está pronto quando são iguais."""
    rs = rows(session, shot, renders=False)  # o estado não depende dos previs
    return len(rs), sum(1 for r in rs if ready(r))


@memo
def view_images(session: Session, shot: Shot) -> list[MediaFile]:
    """As imagens das vistas do local, uma por momento e sem repetir, pela ordem dos momentos."""
    out: list[MediaFile] = []
    for r in rows(session, shot):
        if r["image"] is not None and r["image"] not in out:
            out.append(r["image"])
    return out


@memo
def cast_images(session: Session, shot: Shot) -> list[MediaFile]:
    """As folhas (e retratos) de quem a câmara do plano vê, pela ordem do elenco da cena."""
    from app.scene3d import in_frame

    scene = session.get(Scene, shot.scene_id)
    out: list[MediaFile] = []
    for code in in_frame(session, shot):
        a = session.exec(select(Asset).where(Asset.project_id == scene.project_id, Asset.name == code)).first()
        for i in (looks.ref_ids(session, a, scene) if a else []):
            m = _approved(session, i)
            if m is not None and m not in out:
                out.append(m)
    return out


def create_view(session: Session, shot: Shot, n: int) -> LocationView:
    """Vista nova do local a partir da câmara do momento n (na zona onde a câmara está)."""
    from app import location3d

    loc = location(session, shot)
    m = next((k for k in keys(shot) if k["n"] == n), None)
    if loc is None or m is None:
        raise ValueError("Este plano não tem esse momento, ou o local não tem maquete.")
    cam = location3d.clean_cam(m["cam"])
    zone = scene3d_zone(loc, cam)
    count = len([v for v in location3d.location_views(session, loc) if v.zone == zone])
    view = LocationView(asset_id=loc.id, zone=zone, kind="view", order=count, cam=cam,
                        label=f"P{shot.number:02d} · {m['t']:g} s"[:40])
    session.add(view)
    session.flush()
    choose_view(shot, n, view.id)
    session.add(shot)
    return view


def choose_view(shot: Shot, n: int, view_id: int | None) -> None:
    """Escolha à mão da vista do momento n (None = volta à automática). Fica presa ao momento (segundo e câmara), não
    ao número dele."""
    m = next((k for k in keys(shot) if k["n"] == n), None)
    if m is None:
        return
    data = dict(shot.moments or {})
    views = dict(data.get("views") or {})
    if view_id:
        views[moment_key(m)] = view_id
    else:
        views.pop(moment_key(m), None)
    data["views"] = views
    shot.moments = data


def guide_hash(shot, media_id: int) -> str | None:
    """A versão da encenação e da câmara com que esse vídeo-guia foi gravado (None se não se sabe). Os guias antigos só
    tinham um valor para o plano, o do último gravado."""
    data = shot.moments or {}
    return (data.get("guide_hashes") or {}).get(str(media_id)) or (None if data.get("guide_hashes") else data.get("guide_hash"))
