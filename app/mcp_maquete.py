"""Ferramentas MCP para fazer nas maquetes o que o utilizador faz na app: vistas dos locais (criar, propor as
automáticas, mudar o nome, apagar), os renders que a página tira no navegador (vistas com a profundidade, previs dos
momentos) e o vídeo-guia (gravar, aprovar, rejeitar). Os renders e a gravação usam o próprio visualizador da app num
Chromium sem ecrã (app/maquete_render.capture) e ficam guardados como se viessem da página."""

import io
from typing import Any

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlmodel import Session, select

from app.db import engine
from app.mcp_server import READ, WRITE, _project, log, server
from app.models import Asset, LocationView, MediaFile, Project, Shot

DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)


def _location(s: Session, p: Project, asset_id: int) -> Asset:
    a = s.get(Asset, asset_id)
    if a is None or a.project_id != p.id:
        raise ToolError(f"O asset {asset_id} não é deste projeto.")
    if a.kind != "location":
        raise ToolError(f"{a.name} não é um local.")
    if not a.layout3d:
        raise ToolError(f"{a.name} ainda não tem maquete 3D (cria-a com o campo layout3d em open_screen asset:{a.id}).")
    return a


def _view(s: Session, p: Project, view_id: int) -> tuple[LocationView, Asset]:
    v = s.get(LocationView, view_id)
    a = s.get(Asset, v.asset_id) if v else None
    if a is None or a.project_id != p.id:
        raise ToolError(f"A vista {view_id} não existe neste projeto.")
    return v, a


def _shot(s: Session, p: Project, shot_id: int) -> Shot:
    from app.models import Scene

    sh = s.get(Shot, shot_id)
    sc = s.get(Scene, sh.scene_id) if sh else None
    if sc is None or sc.project_id != p.id:
        raise ToolError(f"O plano {shot_id} não é deste projeto.")
    return sh


def _thumb(data: bytes, size: int = 640) -> Image:
    from PIL import Image as PILImage

    im = PILImage.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return Image(data=buf.getvalue(), format="jpeg")


def _view_label(v: LocationView) -> str:
    return f"vista {v.id} ({v.zone or 'espaço'} · {v.label}{' · referência da divisão' if v.kind == 'ref' else ''})"


def stale_views(s: Session, p: Project, asset: Asset) -> tuple[str, list[LocationView]]:
    """(versão atual da maquete, as vistas sem render, com render de outra versão ou sem profundidade)."""
    from app import location3d, scene3d

    digest = scene3d.preview_hash(scene3d.compose(s, p.id, location=asset))
    depth = set(s.exec(select(MediaFile.locview_id).where(MediaFile.asset_id == asset.id, MediaFile.role == "locdepth")).all())
    return digest, [v for v in location3d.location_views(s, asset)
                    if v.render_id is None or v.render_hash != digest or v.id not in depth]


def stale_moments(s: Session, p: Project, shot: Shot) -> tuple[str, list[dict]]:
    """(versão atual da cena 3D do plano, os momentos a tirar de novo: todos, se a versão mudou)."""
    from app import moments, scene3d

    if not shot.scene3d:
        return "", []
    digest = scene3d.preview_hash(scene3d.compose(s, p.id, shot=shot))
    return digest, ([] if (shot.moments or {}).get("hash") == digest else moments.keys(shot))


async def _render_views(project_id: int, asset_id: int) -> list[tuple[LocationView, bytes]]:
    """Tira e guarda os renders em falta das vistas do local. Devolve [(vista, png)]."""
    from app import location3d, maquete_render, scene3d
    from app.routes.assets import save_view_render

    with Session(engine) as s:
        p = _project(s, project_id)
        a = _location(s, p, asset_id)
        digest, todo = stale_views(s, p, a)
        if not todo:
            return []
        spec = scene3d.compose(s, p.id, location=a)
        aspect = p.aspect_ratio or "16:9"
        req = [{"id": v.id, "cam": v.cam, "outdoor": bool((location3d.zone_info(a.layout3d, v.zone) or {}).get("outdoor"))}
               for v in todo]
    try:
        got = await maquete_render.capture(spec, aspect, views=req)
    except Exception as exc:  # noqa: BLE001 — o motivo vai para o cliente
        raise ToolError(f"Não consegui tirar os renders das vistas: {exc}") from exc
    out = []
    with Session(engine) as s:
        p = _project(s, project_id)
        a = s.get(Asset, asset_id)
        for vid, (png, depth) in got["views"].items():
            v = s.get(LocationView, vid)
            if v is not None:
                save_view_render(s, p, a, v, digest, png, depth)
                out.append((v, png))
        s.commit()
        for v, _png in out:
            s.refresh(v)
    return out


async def _render_moments(project_id: int, shot_id: int) -> list[tuple[int, bytes]]:
    from app import maquete_render, scene3d
    from app.media import image_extension
    from app.routes.storyboard import save_moment_render

    with Session(engine) as s:
        p = _project(s, project_id)
        sh = _shot(s, p, shot_id)
        digest, todo = stale_moments(s, p, sh)
        if not todo:
            return []
        spec = scene3d.compose(s, p.id, shot=sh)
        aspect = p.aspect_ratio or "16:9"
    try:
        got = await maquete_render.capture(spec, aspect, moments=[k["t"] for k in todo])
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"Não consegui tirar os previs dos momentos: {exc}") from exc
    with Session(engine) as s:
        p = _project(s, project_id)
        sh = s.get(Shot, shot_id)
        for k, png in zip(todo, got["moments"]):
            save_moment_render(s, p, sh, k["n"], digest, png, image_extension(png, "render.png"))
        s.commit()
    return [(k["n"], png) for k, png in zip(todo, got["moments"])]


@server.tool(structured_output=False, annotations=WRITE, title="Tirar os renders da maquete",
             description="Tira os renders que a página da app tiraria ao abri-la, com o mesmo visualizador: screen asset:N "
                         "(as vistas do local sem render ou com a maquete mudada desde então, com o mapa de profundidade — "
                         "precisas deles para gerar as imagens das vistas) ou sb_shot:N (os previs dos momentos da câmara do "
                         "plano e as vistas novas do local dele). Chama depois de mudar uma maquete, uma encenação ou uma "
                         "câmara, antes de gerar imagens.")
async def render_maquete(project_id: int, screen: str) -> list[Any]:
    from app import moments

    head, _, raw = screen.partition(":")
    if not raw.isdigit() or head not in ("asset", "sb_shot"):
        raise ToolError("screen tem de ser asset:N (vistas de um local) ou sb_shot:N (previs dos momentos de um plano).")
    out: list[Any] = []
    if head == "sb_shot":
        with Session(engine) as s:
            p = _project(s, project_id)
            sh = _shot(s, p, int(raw))
            loc = moments.location(s, sh)
            loc_id = loc.id if loc is not None and loc.layout3d else None
        for n, png in await _render_moments(project_id, int(raw)):
            out += [f"previs do momento {n}", _thumb(png)]
        if loc_id:
            for v, png in await _render_views(project_id, loc_id):
                out += [f"render da {_view_label(v)} do local", _thumb(png)]
    else:
        for v, png in await _render_views(project_id, int(raw)):
            out += [f"render da {_view_label(v)}", _thumb(png)]
    log.info("mcp: renders da maquete %s no projeto %s (%s)", screen, project_id, len(out) // 2)
    return [f"Tirei {len(out) // 2} render(s)." if out else "Nada a tirar: os renders já estão em dia."] + out


@server.tool(structured_output=False, annotations=WRITE, title="Criar vista do local",
             description="Cria uma vista na maquete de um local (como «Guardar vista aqui» na app) e tira o render dela. "
                         "cam = {\"pos\": [x, y, z], \"look_at\": [x, y, z], \"lens\": mm} em metros (z para cima; olhos "
                         "≈ chão + 1.6; 16–18 mm para mostrar uma divisão, 28–35 normal). A divisão é a da posição da câmara "
                         "(fora das zonas fica na zona ao ar livre mais perto). kind \"ref\" = a imagem de referência da "
                         "divisão (uma por divisão: de um canto para o canto oposto, grande angular); \"view\" = uma vista. "
                         "Vê antes com view_layout (camera=…) para acertar o enquadramento.")
async def add_view(project_id: int, asset_id: int, cam: dict, label: str = "", kind: str = "view") -> list[Any]:
    from app import location3d

    if kind not in ("view", "ref"):
        raise ToolError('kind é "view" ou "ref".')
    with Session(engine) as s:
        p = _project(s, project_id)
        a = _location(s, p, asset_id)
        try:
            clean = location3d.clean_cam(cam or {})
        except (TypeError, ValueError, AttributeError) as exc:
            raise ToolError(f'cam = {{"pos": [x, y, z], "look_at": [x, y, z], "lens": 24}}: {exc}') from exc
        note = ""
        zone = location3d.zone_at(a.layout3d, *clean["pos"])
        if zone is None and any(z["name"] for z in location3d.zones(a.layout3d)):
            zone = location3d.nearest_outdoor(a.layout3d, *clean["pos"])
            note = (f" A câmara estava fora das zonas marcadas: a vista ficou em «{zone}», a zona ao ar livre mais perto "
                    "(alarga essa zona na maquete para ficar certo)." if zone else
                    " A câmara estava fora de todas as zonas: a vista ficou sem zona.")
        siblings = [v for v in location3d.location_views(s, a) if v.zone == zone]
        if kind == "ref" and any(v.kind == "ref" for v in siblings):
            raise ToolError(f"A divisão «{zone or 'espaço'}» já tem referência: apaga-a primeiro (delete_view) ou cria uma vista.")
        v = LocationView(asset_id=a.id, zone=zone, kind=kind, order=len(siblings),
                         label=(str(label or "").strip() or f"vista {len(siblings) + 1}")[:40], cam=clean)
        s.add(v)
        s.commit()
        s.refresh(v)
        vid, text = v.id, _view_label(v)
    log.info("mcp: vista %s criada no local %s (projeto %s)", vid, asset_id, project_id)
    out: list[Any] = [f"Criada a {text}.{note} Gera a imagem com write_prompt/generate_image (kind view, view_id {vid})."]
    for rv, png in await _render_views(project_id, asset_id):
        out += [f"render da {_view_label(rv)}", _thumb(png)]
    return out


@server.tool(structured_output=False, annotations=WRITE, title="Propor as vistas automáticas",
             description="Como o botão da app: em cada divisão da maquete, a referência (de um canto para o canto oposto) e "
                         "4 vistas a partir do centro (frente, direita, trás, esquerda). Só junta as que faltam e tira os "
                         "renders.")
async def auto_views(project_id: int, asset_id: int) -> list[Any]:
    from app import location3d

    with Session(engine) as s:
        p = _project(s, project_id)
        a = _location(s, p, asset_id)
        n = location3d.auto_views(s, a)
        s.commit()
    log.info("mcp: %s vista(s) automáticas no local %s (projeto %s)", n, asset_id, project_id)
    out: list[Any] = [f"{n} vista(s) proposta(s)." if n else "Já existem as vistas automáticas de todas as divisões."]
    rendered = await _render_views(project_id, asset_id)
    out[0] += f" Tirei {len(rendered)} render(s)."
    for rv, png in rendered[:12]:
        out += [f"render da {_view_label(rv)}", _thumb(png, 480)]
    return out


@server.tool(annotations=WRITE, title="Mudar o nome de uma vista", description="Muda o nome de uma vista de um local.")
def rename_view(project_id: int, view_id: int, label: str) -> str:
    label = str(label or "").strip()[:40]
    if not label:
        raise ToolError("O nome não pode ficar vazio.")
    with Session(engine) as s:
        p = _project(s, project_id)
        v, _a = _view(s, p, view_id)
        if v.label == label:
            return "Sem alterações: a vista já tem esse nome."
        v.label = label
        s.add(v)
        s.commit()
    return f"A vista {view_id} chama-se agora «{label}»."


@server.tool(annotations=DESTRUCTIVE, title="Apagar uma vista",
             description="Apaga uma vista de um local, como o botão da app: a vista, o render e as imagens dela (aprovada e "
                         "candidatas) deixam de existir. Os momentos dos planos que a usavam voltam à escolha automática.")
def delete_view(project_id: int, view_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        p = _project(s, project_id)
        v, a = _view(s, p, view_id)
        text, name = _view_label(v), a.name
        for m in s.exec(select(MediaFile).where(MediaFile.locview_id == v.id)).all():
            reject_media(s, m)
        s.delete(v)
        s.commit()
    log.info("mcp: vista %s apagada (projeto %s)", view_id, project_id)
    return f"Apagada a {text} de {name}."


RECORDING: dict[int, dict] = {}  # plano → {"done", "total", "error", "guide_id", "task"} (gravações em segundo plano)


@server.tool(annotations=WRITE, title="Gravar o vídeo-guia",
             description="Grava o vídeo-guia de um plano, como o botão «Gravar vídeo-guia» da app: a câmara do plano do "
                         "início ao fim, com a encenação, 1280×720 a 24 imagens/s (quadro a quadro, sem falhas). Corre em "
                         "segundo plano (1–5 minutos, conforme a maquete): devolve logo; acompanha com list_guides, que "
                         "mostra o progresso e, no fim, a tira de imagens do guia novo. Fica por aprovar: mostra-o ao "
                         "utilizador e aprova-o com approve_guide só quando ele disser.")
async def record_guide(project_id: int, shot_id: int) -> str:
    import asyncio

    from app import scene3d

    with Session(engine) as s:
        p = _project(s, project_id)
        sh = _shot(s, p, shot_id)
        if not sh.scene3d:
            raise ToolError("Este plano ainda não tem câmara na cena 3D (define-a no ecrã sb_shot:N).")
        spec = scene3d.compose(s, p.id, shot=sh)
        aspect = p.aspect_ratio or "16:9"
        crowd = scene3d.crowd(s, sh)
    run = RECORDING.get(shot_id)
    if run and not run["finished"]:
        return f"Já está a gravar o vídeo-guia deste plano ({run['done']}/{run['total'] or '?'} quadros): vê list_guides."
    state = {"done": 0, "total": 0, "error": None, "guide_id": None, "finished": False}
    RECORDING[shot_id] = state
    state["task"] = asyncio.create_task(_record(project_id, shot_id, spec, aspect, state))
    msg = "A gravar o vídeo-guia em segundo plano: vê o progresso e o resultado com list_guides daqui a 1–2 minutos."
    if crowd:
        msg += (f" Aviso: a câmara vê {crowd.get('n')} figuras além das personagens principais ({crowd.get('detail')}); "
                "com mais de 3 o vídeo sai pior.")
    return msg


async def _record(project_id: int, shot_id: int, spec: dict, aspect: str, state: dict) -> None:
    from app import maquete_render
    from app.media import UploadError
    from app.routes.storyboard import save_guide

    def progress(done: int, total: int) -> None:
        state["done"], state["total"] = done, total

    try:
        got = await maquete_render.capture(spec, aspect, fps=24, progress=progress)
        with Session(engine) as s:
            p = _project(s, project_id)
            sh = s.get(Shot, shot_id)
            try:
                media = save_guide(s, p, sh, got["video"])
            except UploadError as exc:
                s.rollback()
                raise RuntimeError(f"o vídeo-guia não serve: {exc}") from exc
            s.commit()
            state["guide_id"] = media.id
        log.info("mcp: vídeo-guia %s gravado no plano %s (projeto %s)", state["guide_id"], shot_id, project_id)
    except Exception as exc:  # noqa: BLE001 — fica no estado, para o list_guides
        state["error"] = str(exc) or exc.__class__.__name__
        log.warning("mcp: falhou a gravação do vídeo-guia do plano %s: %s", shot_id, state["error"])
    finally:
        state["finished"] = True


def _guide(s: Session, p: Project, guide_id: int) -> tuple[MediaFile, Shot]:
    m = s.get(MediaFile, guide_id)
    if m is None or m.project_id != p.id or m.role != "guide" or not m.shot_id:
        raise ToolError(f"O vídeo-guia {guide_id} não existe neste projeto.")
    if m.status == "rejected":
        raise ToolError(f"O vídeo-guia {guide_id} foi rejeitado e já não existe.")
    return m, s.get(Shot, m.shot_id)


@server.tool(annotations=READ, structured_output=True, title="Vídeos-guia de um plano",
             description="Os vídeos-guia de um plano, em dados: para cada um o guide_id, o estado (aprovado / por aprovar), "
                         "a duração, a data, se está desatualizado (e porquê), e links (2 h) para o vídeo e para a tira de "
                         "imagens dele; e a gravação em curso (record_guide), se houver.")
def list_guides(project_id: int, shot_id: int) -> dict[str, Any]:
    from app import moments, scene3d
    from app.artlist import share
    from app.mcp_server import PUBLIC
    from app.media import guide_sheet, media_root
    from app.media_service import media_list

    with Session(engine) as s:
        p = _project(s, project_id)
        sh = _shot(s, p, shot_id)
        digest = scene3d.preview_hash(scene3d.compose(s, p.id, shot=sh)) if sh.scene3d else None
        out = []
        for m in media_list(s, shot_id=sh.id, role="guide"):
            if m.status == "rejected":
                continue
            stale = None
            if m.duration_ms and sh.duration_s and abs(m.duration_ms - sh.duration_s * 1000) > 50:
                stale = f"gravado com {m.duration_ms / 1000:g} s; o plano tem {sh.duration_s} s"
            elif digest and moments.guide_hash(sh, m.id) not in (None, digest):
                stale = "a encenação, a câmara ou o troço mudaram depois de o gravar"
            path = media_root() / m.path
            sheet = guide_sheet(m.path) if path.is_file() else None
            out.append({"guide_id": m.id, "status": "aprovado" if m.id == sh.guide_video_id else "por aprovar",
                        "duration_s": m.duration_ms / 1000 if m.duration_ms else None,
                        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M"), "stale": bool(stale), "stale_reason": stale,
                        "video_url": f"{PUBLIC}/pub/ref/{share(path, ttl_s=7200)}.mp4" if path.is_file() else None,
                        "sheet_url": f"{PUBLIC}/pub/ref/{share(media_root() / sheet[0], ttl_s=7200)}.png" if sheet else None,
                        "sheet_seconds": sheet[1] if sheet else None})
        run = RECORDING.get(sh.id)
        rec = None
        if run:
            rec = {"finished": run["finished"], "frames_done": run["done"], "frames_total": run["total"],
                   "error": run["error"], "guide_id": run["guide_id"]}
        return {"shot_id": sh.id, "shot_duration_s": sh.duration_s, "approved_guide_id": sh.guide_video_id,
                "recording": rec, "guides": list(reversed(out))}


@server.tool(annotations=WRITE, title="Aprovar vídeo-guia",
             description="Aprova um vídeo-guia, como o botão da app: passa a ser o guia do vídeo do plano (o anterior volta a "
                         "«por decidir»). Só quando o utilizador o pedir.")
def approve_guide(project_id: int, guide_id: int) -> str:
    from app.media_service import approve_media

    with Session(engine) as s:
        p = _project(s, project_id)
        m, sh = _guide(s, p, guide_id)
        if sh.guide_video_id == m.id:
            return f"O vídeo-guia {guide_id} já é o aprovado."
        shot_id = sh.id
        approve_media(s, m)
        s.commit()
    log.info("mcp: vídeo-guia %s aprovado (projeto %s)", guide_id, project_id)
    return f"Aprovado: o vídeo-guia {guide_id} é agora o guia do plano {shot_id}."


@server.tool(annotations=DESTRUCTIVE, title="Rejeitar vídeo-guia",
             description="Rejeita um vídeo-guia, como o botão da app: o ficheiro é apagado.")
def reject_guide(project_id: int, guide_id: int) -> str:
    from app.media_service import reject_media

    with Session(engine) as s:
        p = _project(s, project_id)
        m, sh = _guide(s, p, guide_id)
        reject_media(s, m)
        s.commit()
    log.info("mcp: vídeo-guia %s rejeitado (projeto %s)", guide_id, project_id)
    return f"Rejeitado e apagado: vídeo-guia {guide_id}."
