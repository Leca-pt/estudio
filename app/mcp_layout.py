"""Ferramentas MCP das maquetes 3D: ver (view_layout, imagens tiradas com o visualizador three.js da app) e verificar
(check_layout, só lê). screen: asset:N (a maquete de um local) ou sb_scene:N (o cenário da cena, com o elenco)."""

import io
from typing import Any

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from sqlmodel import Session, select

from app.db import engine
from app.mcp_server import READ, _project, server
from app.models import Asset, LocationView, Scene

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _source(s: Session, project_id: int, screen: str):
    """(o que o visualizador desenha, a maquete do local, o local, a cena ou None)."""
    from app import scene3d

    p = _project(s, project_id)
    head, _, raw = screen.partition(":")
    if not raw.isdigit():
        raise ToolError("screen tem de ser asset:N (maquete de um local) ou sb_scene:N (cenário de uma cena).")
    if head == "asset":
        a = s.get(Asset, int(raw))
        if a is None or a.project_id != p.id:
            raise ToolError(f"O asset {raw} não é deste projeto.")
        if not a.layout3d:
            raise ToolError(f"{a.name} não tem maquete 3D.")
        return scene3d.compose(s, p.id, location=a), a.layout3d, a, None
    if head == "sb_scene":
        sc = s.get(Scene, int(raw))
        if sc is None or sc.project_id != p.id:
            raise ToolError(f"A cena {raw} não é deste projeto.")
        spec = scene3d.compose(s, p.id, scene=sc)
        if not spec:
            raise ToolError("Esta cena ainda não tem cenário 3D (escolhe o local da encenação).")
        layout, loc = scene3d.scene_set(s, sc)  # com as variações só desta cena (set_changes), se as houver
        return spec, layout or {"objects": []}, loc, sc
    raise ToolError("screen tem de ser asset:N (maquete de um local) ou sb_scene:N (cenário de uma cena).")


def _lviews(s: Session, loc: Asset | None) -> list[dict]:
    if loc is None:
        return []
    views = s.exec(select(LocationView).where(LocationView.asset_id == loc.id)
                   .order_by(LocationView.order, LocationView.id)).all()
    return [{"name": LETTERS[i], "label": (f"{v.zone} · " if v.zone else "") + (v.label or ""), "cam": v.cam, "id": v.id}
            for i, v in enumerate(views[: len(LETTERS)]) if v.cam]


def _legend(layout: dict, lviews: list[dict]) -> str:
    lines = []
    for n, o in enumerate(layout.get("objects") or []):
        what = o.get("model") or o.get("type")
        where = o.get("center") or o.get("from")
        lines.append(f"#{o.get('n', n)} {what}" + (" (só nesta cena)" if o.get("scene_only") else "")
                     + "".join(f" {k} {o[k]:g}°" for k in ("rot_x", "rot_y") if o.get(k)) + (f" «{o['label']}»" if o.get("label") else "") + (f" «{o['name']}»" if o.get("name") else "")
                     + (f" em [{', '.join(f'{x:g}' for x in where)}]" if where else ""))
    views = [f"{v['name']} = {v['label']} (vista {v['id']}: câmara em {v['cam']['pos']}, a olhar para {v['cam']['look_at']}, "
             f"{v['cam'].get('lens', 28):g} mm)" for v in lviews]
    return ("VISTAS DO LOCAL: " + ("; ".join(views) or "nenhuma") + "\nOBJETOS (#n como em «Alterar por partes»):\n"
            + "\n".join(lines))


@server.tool(annotations=READ, structured_output=False,
             description="Imagens de uma maquete 3D, tiradas com o mesmo visualizador three.js da app. screen: asset:N (maquete "
                         "de um local) ou sb_scene:N (cenário da cena, com o elenco nas posições do segundo t). views: "
                         "\"top\" (planta de cima — telhados incluídos — com grelha de 1 m — mais forte a cada 5 m —, eixos, nomes das zonas, #n de "
                         "cada objeto e seta da vista A), \"A\", \"B\"… (as vistas do local), \"eye\" (à altura dos olhos na "
                         "origem, a olhar para +Y), \"orbit\" (três quartos de cima), \"floor:<z>\" (planta de um piso, "
                         "cortando o que está acima: a vista certa para ver um interior, ex. floor:0), \"camera\" (com camera={pos, look_at, fov}). labels liga/desliga os "
                         "#n e os nomes. focus (opcional) aproxima as plantas a uma "
                         "divisão (o nome da zone, ex. \"sala\") ou a [x0, y0, x1, y1]. Devolve as imagens e a legenda #n → "
                         "tipo/label.")
async def view_layout(project_id: int, screen: str, views: list[str] | None = None, camera: dict | None = None,
                      labels: bool = True, size: int = 1024, t: float = 0, focus: Any = None) -> list[Any]:
    from PIL import Image as PILImage

    from app import maquete_render

    views = views or ["top", "A"]
    with Session(engine) as s:
        spec, layout, loc, _scene = _source(s, project_id, screen)
        lviews = _lviews(s, loc)
    if camera is not None:
        if not isinstance(camera, dict) or len(camera.get("pos") or []) != 3 or len(camera.get("look_at") or []) != 3:
            raise ToolError('camera = {"pos": [x, y, z], "look_at": [x, y, z], "fov": 50}.')
        if "camera" not in views:
            views = [*views, "camera"]
    names = {v["name"] for v in lviews}
    bad = [v for v in views if v not in ("top", "eye", "orbit", "camera") and not v.startswith("floor:") and v not in names]
    if bad:
        raise ToolError(f"Vista(s) desconhecida(s): {', '.join(bad)}. Este local tem as vistas {', '.join(sorted(names)) or 'nenhuma'}.")
    try:
        shots = await maquete_render.render(spec, {"views": views, "camera": camera, "labels": bool(labels),
                                                   "size": int(size), "lviews": lviews, "t": float(t), "focus": focus})
    except Exception as exc:  # noqa: BLE001 — o motivo vai para o cliente
        raise ToolError(f"Não consegui desenhar a maquete: {exc}") from exc
    out: list[Any] = [_variations(s_note) if (s_note := _changes_note(project_id, screen)) else "", _legend(layout, lviews)]
    out = [x for x in out if x]
    for r in shots:
        if r.get("error"):
            out.append(f"{r['view']}: {r['error']}")
            continue
        im = PILImage.open(io.BytesIO(r["png"])).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        label = {"top": "planta (de cima)", "eye": "à altura dos olhos", "orbit": "três quartos", "camera": "câmara livre"}.get(r["view"])
        if r["view"].startswith("floor:"):
            label = f"planta do piso z = {r['view'].split(':')[1]} m"
        if label is None:
            label = "vista " + r["view"] + "".join(f" — {v['label']}" for v in lviews if v["name"] == r["view"])
        out += [label, Image(data=buf.getvalue(), format="jpeg")]
    return out


@server.tool(annotations=READ,
             description="Verifica uma maquete 3D (só lê) e devolve os avisos: objetos sobrepostos (sobretudo mobília dentro "
                         "de paredes), portas sem vão ou janelas fora de paredes, peças fora das divisões e divisões sem "
                         "chão, objetos a flutuar ou enterrados, assentos virados para o lado errado e medidas estranhas. "
                         "screen: asset:N ou sb_scene:N (verifica a maquete do local da cena).")
def check_layout(project_id: int, screen: str) -> str:
    from app import layout_check

    with Session(engine) as s:
        _spec, layout, loc, scene = _source(s, project_id, screen)
    name = loc.name if loc else "cenário"
    note = _changes_note(project_id, screen)
    return (f"Verificação da maquete {name}" + (" com as variações desta cena" if note else "") + ":\n"
            + (_variations(note) + "\n" if note else "") + "\n".join(layout_check.check(layout)))


def _changes_note(project_id: int, screen: str) -> str:
    """sb_scene:N com variações da maquete: o resumo delas (ou o motivo de não se aplicarem)."""
    from app import scene3d

    head, _, raw = screen.partition(":")
    if head != "sb_scene" or not raw.isdigit():
        return ""
    with Session(engine) as s:
        sc = s.get(Scene, int(raw))
        if sc is None or sc.project_id != project_id:
            return ""
        changes = (sc.stage3d or {}).get("set_changes")
        if not changes:
            return ""
        problem = scene3d.set_changes_problem(s, sc)
        if problem:
            return f"!{problem}"
        base, _loc = scene3d.scene_base(s, sc)
        return f"{scene3d.set_changes_count(changes)} mudança(s): {scene3d.set_changes_summary(changes, base)}"


def _variations(note: str) -> str:
    if note.startswith("!"):
        return ("ATENÇÃO: as variações desta cena (set_changes) NÃO se aplicam e o que se vê é a maquete do local: "
                + note[1:] + " Corrige o set_changes da encenação.")
    return ("VARIAÇÕES SÓ DESTA CENA (set_changes da encenação; a maquete do local não muda): " + note
            + ". Os #n são os da maquete do local; os objetos juntados continuam a contagem.")
