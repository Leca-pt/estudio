"""Locais: a maquete 3D e as vistas ligadas a posições de câmara na maquete, por zona (divisão ou espaço exterior).

Fluxo: a descrição e as fotos do local → a maquete (o Assistente monta-a) → em cada zona, uma imagem de referência (a
"folha" da zona) e as vistas, cada uma uma posição de câmara (automáticas, guardadas na vista livre ou criadas a partir
da câmara de um plano). O navegador tira o render de cada posição; a imagem gera-se a partir do render (a geometria),
da referência da zona (os materiais e a luz) e das folhas escolhidas para a zona, com o prompt do escritor de prompts.
"""

import math

from sqlmodel import Session, select

from app.models import Asset, LocationView, MediaFile

EYE = 1.6  # altura dos olhos


def zones(layout: dict | None) -> list[dict]:
    """Divisões da maquete; sem divisões, o espaço todo é uma (sem nome)."""
    objs = (layout or {}).get("objects", [])
    out = [{"name": o["name"], "from": o["from"], "to": o["to"], "z": float(o.get("z") or 0), "outdoor": _outdoor(o),
            "height": _height(o)} for o in objs if o.get("type") == "zone"]
    if out:
        return out
    xs, ys = [], []
    for o in objs:
        for k in ("center", "from", "to"):
            if k in o:
                xs.append(o[k][0])
                ys.append(o[k][1])
    if not xs:
        return [{"name": None, "from": [-5, -5], "to": [5, 5], "z": 0.0, "outdoor": False, "height": 50.0}]
    return [{"name": None, "from": [min(xs), min(ys)], "to": [max(xs), max(ys)], "z": 0.0, "outdoor": False, "height": 50.0}]


def _outdoor(zone: dict) -> bool:
    """Zona ao ar livre (rua, pátio): sem teto. A mesma regra do navegador: "ceiling" 0, ou sem "ceiling" e área ≥ 400 m²."""
    if zone.get("ceiling") is not None:
        return not zone["ceiling"]
    (x0, y0), (x1, y1) = zone["from"], zone["to"]
    return (x1 - x0) * (y1 - y0) >= 400


def _height(zone: dict) -> float:
    """Até onde vai a divisão acima do seu chão (para saber em que piso está cada coisa): o teto, ou 50 m ao ar livre."""
    return 50.0 if _outdoor(zone) else float(zone.get("ceiling") or 2.9) + 0.05


def zone_info(layout: dict | None, zone: str | None) -> dict | None:
    return next((z for z in zones(layout) if z["name"] == zone), None)


def zone_at(layout: dict | None, x: float, y: float, h: float | None = None) -> str | None:
    """Divisão onde está o ponto; com a altura h, a do piso certo (o chão mais alto que fica abaixo de h)."""
    found = [z for z in zones(layout) if z["from"][0] <= x <= z["to"][0] and z["from"][1] <= y <= z["to"][1]]
    if h is not None:
        below = [z for z in found if z["z"] <= h + 0.3]
        found = sorted(below, key=lambda z: -z["z"]) or found
    return found[0]["name"] if found else None


def nearest_outdoor(layout: dict | None, x: float, y: float, h: float | None = None, limit: float = 60.0) -> str | None:
    """Fora de todas as zonas (ex.: a rua continua para lá da zona marcada): a zona ao ar livre mais perto, no mesmo piso."""
    best, dist = None, limit
    for z in zones(layout):
        if not z["outdoor"] or not z["name"] or (h is not None and not z["z"] - 0.3 <= h):
            continue
        dx = max(z["from"][0] - x, 0, x - z["to"][0])
        dy = max(z["from"][1] - y, 0, y - z["to"][1])
        d = math.hypot(dx, dy)
        if d < dist:
            best, dist = z["name"], d
    return best


def outside_zones(layout: dict | None) -> list[str]:
    """Coisas com nome que ficam fora de todas as zonas (sinal de que a rua ou o pátio cresceu e a zona não)."""
    if not any(o.get("type") == "zone" for o in (layout or {}).get("objects", [])):
        return []
    return [o["label"] for o in (layout or {}).get("objects", [])
            if o.get("label") and "center" in o and zone_at(layout, o["center"][0], o["center"][1]) is None]


def location_views(session: Session, asset: Asset) -> list[LocationView]:
    return list(session.exec(select(LocationView).where(LocationView.asset_id == asset.id)
                             .order_by(LocationView.zone, LocationView.kind, LocationView.order, LocationView.id)).all())


def auto_views(session: Session, asset: Asset) -> int:
    """Propõe as vistas de cada divisão: a referência (de um canto para o canto oposto, grande angular) e 4 vistas a
    partir do centro, à altura dos olhos, a rodar 90°. Só junta as que faltam (não mexe nas que já existem)."""
    from app import terrain

    existing = {(v.zone, v.label) for v in location_views(session, asset)}
    added = 0
    ground = terrain.ground_fn([asset.layout3d or {}])
    # com relevo, cada câmara fica à altura dos olhos acima do chão por baixo dela (e olha para o chão do outro ponto)
    on = (lambda c: {**c, "pos": [c["pos"][0], c["pos"][1], c["pos"][2] + ground(c["pos"][0], c["pos"][1])],
                     "look_at": [c["look_at"][0], c["look_at"][1], c["look_at"][2] + ground(c["look_at"][0], c["look_at"][1])]}
          ) if ground else (lambda c: c)
    for z in zones(asset.layout3d):
        (x0, y0), (x1, y1) = z["from"], z["to"]
        cx, cy, fz = (x0 + x1) / 2, (y0 + y1) / 2, z["z"]  # fz: chão do piso desta divisão
        inset_x, inset_y = min(0.6, (x1 - x0) / 4), min(0.6, (y1 - y0) / 4)
        big = max(x1 - x0, y1 - y0) > 40  # espaço aberto grande (rua, praça): vistas mais altas e mais longe
        side = facade_side(asset.layout3d, z) if z["outdoor"] else None
        if side:  # rua ou pátio colado ao edifício: a referência fica do outro lado, virada para a fachada
            (fx, fy), (bx, by) = side
            ref_cam = {"pos": [bx, by, fz + 1.7], "look_at": [fx, fy, fz + 2.2], "lens": 16}
        else:
            ref_cam = {"pos": [x0 + inset_x, y0 + inset_y, fz + (2.2 if not big else 12)],
                       "look_at": [x1 - inset_x, y1 - inset_y, fz + 1.0], "lens": 16}
        specs = [("ref", "referência", ref_cam)]
        for label, (dx, dy) in (("frente", (0, 1)), ("direita", (1, 0)), ("trás", (0, -1)), ("esquerda", (-1, 0))):
            specs.append(("view", label, {"pos": [cx, cy, fz + EYE], "look_at": [cx + dx * 5, cy + dy * 5, fz + EYE], "lens": 18}))
        for n, (kind, label, cam) in enumerate(specs):
            if (z["name"], label) in existing:
                continue
            cam = on(cam)
            session.add(LocationView(asset_id=asset.id, zone=z["name"], kind=kind, order=n, label=label,
                                     cam={k: [round(c, 2) for c in v] if isinstance(v, list) else v for k, v in cam.items()}))
            added += 1
    session.flush()
    return added


def facade_side(layout: dict | None, zone: dict):
    """Numa zona ao ar livre colada a divisões interiores: (meio da fachada, ponto do lado oposto da zona), em [x, y].
    A fachada é o lado da zona que mais encosta às divisões interiores; None se não encosta a nenhuma."""
    (x0, y0), (x1, y1) = zone["from"], zone["to"]
    sides = {"y0": 0.0, "y1": 0.0, "x0": 0.0, "x1": 0.0}
    for z in zones(layout):
        if z["outdoor"] or z is zone or abs(z["z"] - zone["z"]) > 0.5:  # só as divisões do mesmo piso
            continue
        (a0, b0), (a1, b1) = z["from"], z["to"]
        over_x = max(0.0, min(x1, a1) - max(x0, a0))  # quanto se sobrepõem ao longo de X
        over_y = max(0.0, min(y1, b1) - max(y0, b0))
        if abs(b1 - y0) < 0.6 or (b0 < y0 < b1):
            sides["y0"] += over_x
        if abs(b0 - y1) < 0.6 or (b0 < y1 < b1):
            sides["y1"] += over_x
        if abs(a1 - x0) < 0.6 or (a0 < x0 < a1):
            sides["x0"] += over_y
        if abs(a0 - x1) < 0.6 or (a0 < x1 < a1):
            sides["x1"] += over_y
    best = max(sides, key=sides.get)
    if sides[best] < 0.5:
        return None
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    inset = 0.6
    return {"y0": ((cx, y0), (cx, y1 - inset)), "y1": ((cx, y1), (cx, y0 + inset)),
            "x0": ((x0, cy), (x1 - inset, cy)), "x1": ((x1, cy), (x0 + inset, cy))}[best]


def clean_cam(cam) -> dict:
    def vec(v):
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise ValueError("posição inválida")
        out = [round(float(c), 2) for c in v]
        if any(not math.isfinite(c) or abs(c) > 5000 for c in out):
            raise ValueError("posição fora dos limites")
        return out

    lens = float(cam.get("lens") or 24)
    return {"pos": vec(cam.get("pos")), "look_at": vec(cam.get("look_at")), "lens": round(max(8.0, min(300.0, lens)), 1)}


def zone_reference(session: Session, asset: Asset, zone: str | None) -> MediaFile | None:
    """Imagem aprovada da referência desta divisão (se já existe)."""
    ref = session.exec(select(LocationView).where(LocationView.asset_id == asset.id, LocationView.kind == "ref",
                                                  LocationView.zone == zone if zone is not None
                                                  else LocationView.zone.is_(None))).first()
    if ref and ref.image_id:
        media = session.get(MediaFile, ref.image_id)
        if media and media.status == "approved":
            return media
    return None


PIECE_EN = {
    "table_rect": "rectangular table", "table_round": "round table", "chair": "chair", "bar_stool": "bar stool",
    "sofa": "sofa", "armchair": "armchair", "booth": "booth bench", "bench": "bench", "counter": "counter",
    "shelf": "tall shelf", "fridge": "fridge", "register": "cash register", "stall": "market stall",
    "kitchen_counter": "kitchen counter", "stove": "stove", "sink": "sink", "bed": "bed", "wardrobe": "wardrobe",
    "desk": "desk", "tv": "TV screen", "plant": "potted plant", "door": "door", "window": "window",
    "stairs": "staircase", "column": "column", "ceiling_lamp": "ceiling lamp", "street_lamp": "street lamp",
    "tree": "tree", "car": "car", "motorbike": "motorbike", "bin": "bin", "crate": "crate",
}
MAX_SEEN = 12  # coisas diferentes na linha "o que esta vista mostra" (um prompt de imagem quer-se curto)
_NUM = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _zone_box(layout: dict | None, zone: str | None) -> tuple[list[float], list[float], float, float] | None:
    """(from, to, chão, altura) da divisão."""
    for z in zones(layout):
        if z["name"] == zone:
            return z["from"], z["to"], z["z"], z["height"]
    return None


def _crosses(p, q, center, size, rot_z=0.0) -> bool:
    """O segmento p→q (no chão) atravessa o retângulo (centro, tamanho, rodado rot_z graus)?"""
    c, s = math.cos(math.radians(-rot_z)), math.sin(math.radians(-rot_z))

    def local(pt):
        dx, dy = pt[0] - center[0], pt[1] - center[1]
        return dx * c - dy * s, dx * s + dy * c

    (x0, y0), (x1, y1) = local(p), local(q)
    hx, hy = size[0] / 2, size[1] / 2
    t0, t1 = 0.0, 1.0
    for d, lo, hi, a in ((x1 - x0, -hx, hx, x0), (y1 - y0, -hy, hy, y0)):  # Liang–Barsky
        if abs(d) < 1e-9:
            if not lo <= a <= hi:
                return False
            continue
        ta, tb = (lo - a) / d, (hi - a) / d
        t0, t1 = max(t0, min(ta, tb)), min(t1, max(ta, tb))
        if t0 > t1:
            return False
    return True


def _inside(pt, center, size, rot_z=0.0, pad=0.0) -> bool:
    c, s = math.cos(math.radians(-rot_z)), math.sin(math.radians(-rot_z))
    dx, dy = pt[0] - center[0], pt[1] - center[1]
    return abs(dx * c - dy * s) <= size[0] / 2 + pad and abs(dx * s + dy * c) <= size[1] / 2 + pad


def _occluders(layout: dict | None, eye_z: float) -> list[tuple]:
    """O que tapa a vista ao nível dos olhos, numa rua: prédios e paredes altos e o interior dos edifícios."""
    out = []
    for o in (layout or {}).get("objects", []):
        if o.get("type") == "box" and "center" in o:
            sx, sy, sz = o["size"]
            if sz >= 2.0 and o["center"][2] - sz / 2 <= eye_z <= o["center"][2] + sz / 2 + 0.5:
                out.append((o["center"], (sx, sy), float(o.get("rot_z") or 0)))
    for z in zones(layout):
        if not z["outdoor"] and z["name"]:
            (x0, y0), (x1, y1) = z["from"], z["to"]
            out.append(([(x0 + x1) / 2, (y0 + y1) / 2], (x1 - x0, y1 - y0), 0.0))
    return out


_DIGESTS: dict[int, tuple[dict, str]] = {}  # id(maquete carregada) → (ela, impressão digital do conteúdo)
_SEEN: dict[str, dict] = {}  # impressão digital → {(zona, câmara): texto}; as últimas 8 versões de maquete


def _digest(layout: dict) -> str:
    from app.scene3d import layout_digest

    hit = _DIGESTS.get(id(layout))
    if hit is None or hit[0] is not layout:
        if len(_DIGESTS) >= 16:
            _DIGESTS.pop(next(iter(_DIGESTS)))
        hit = _DIGESTS[id(layout)] = (layout, layout_digest(layout))
    return hit[1]


def seen_from(layout: dict | None, view: LocationView) -> str:
    """O que a câmara desta vista vê na maquete (EN): o que fica à esquerda, ao centro e à direita, perto e ao fundo.
    É o corpo do prompt: diz ao modelo o que está nesta divisão, para não desenhar outra. Guardado em memória pela
    versão da maquete: as páginas com muitas vistas não o recalculam a cada pedido."""
    import json

    if layout is None:
        return ""
    digest = _digest(layout)
    cache = _SEEN.get(digest)
    if cache is None:
        if len(_SEEN) >= 8:
            _SEEN.pop(next(iter(_SEEN)))
        cache = _SEEN[digest] = {}
    key = (view.zone, json.dumps(view.cam, sort_keys=True))
    if key not in cache:
        cache[key] = _seen_from(layout, view)
    return cache[key]


def _seen_from(layout: dict | None, view: LocationView) -> str:
    cam = view.cam
    px, py = cam["pos"][0], cam["pos"][1]
    fx, fy = cam["look_at"][0] - px, cam["look_at"][1] - py
    n = math.hypot(fx, fy) or 1.0
    fx, fy = fx / n, fy / n
    rx, ry = fy, -fx  # direita, com Z para cima
    tan_h = 18 / float(cam.get("lens") or 24)  # meia largura do campo de visão a 1 m (sensor de 36 mm)
    box = _zone_box(layout, view.zone)
    outdoor = bool((zone_info(layout, view.zone) or {}).get("outdoor"))
    indoor = [z for z in zones(layout) if not z["outdoor"] and z["name"]]
    blockers = _occluders(layout, cam["pos"][2]) if outdoor else []
    blockers = [b for b in blockers if not _inside((px, py), *b)]  # o que está à volta da câmara não tapa
    def in_building(x, y):
        """Dentro do edifício: dentro de uma divisão, ou na parede entre duas divisões. O que está fora das divisões ou
        na linha de uma parede que dá para a rua (portas, janelas, o letreiro) é fachada e vê-se."""
        edge = 0.08
        touching = 0
        for z in indoor:
            if z["from"][0] + edge < x < z["to"][0] - edge and z["from"][1] + edge < y < z["to"][1] - edge:
                return True
            if z["from"][0] - edge <= x <= z["to"][0] + edge and z["from"][1] - edge <= y <= z["to"][1] + edge:
                touching += 1
        return touching >= 2

    found: list[tuple[float, str, str, str]] = []  # (profundidade, lado, distância, nome)
    for o in (layout or {}).get("objects", []):
        if o.get("type") not in ("piece", "box", "cylinder") or "center" not in o:
            continue
        if o.get("type") != "piece" and not o.get("label"):
            continue  # paredes, chão: não são "coisas"
        if o.get("type") == "box" and (o.get("size") or [0, 0, 1])[2] < 0.3:
            continue  # marcas no chão (passeios, passadeiras, linhas): o render já as mostra
        x, y = o["center"][0], o["center"][1]
        if outdoor:  # na rua vê-se para lá da zona marcada; só não se vê o que está dentro dos edifícios
            if in_building(x, y):
                continue  # dentro do edifício (o que está na fachada, como a porta, vê-se)
            if any(_crosses((px, py), (x, y), *b) and not _inside((x, y), *b, pad=0.3) for b in blockers):
                continue  # atrás de um prédio ou do próprio edifício
        elif box and not (box[0][0] - 0.3 <= x <= box[1][0] + 0.3 and box[0][1] - 0.3 <= y <= box[1][1] + 0.3):
            continue  # noutra divisão
        base = o["center"][2] if o.get("type") == "piece" else o["center"][2] - (o.get("size") or [0, 0, 0])[2] / 2 \
            if o.get("type") == "box" else o["center"][2] - float(o.get("height") or 0) / 2
        if box and not (box[2] - 0.3 <= base < box[2] + box[3] - 0.3):
            continue  # noutro piso
        dx, dy = x - px, y - py
        depth = dx * fx + dy * fy
        if depth < 0.4 or (outdoor and depth > 35):
            continue  # atrás da câmara, ou demasiado longe para contar
        side = (dx * rx + dy * ry) / depth
        if abs(side) > tan_h * 1.1:
            continue  # fora do enquadramento
        h = "on the left" if side < -tan_h * 0.33 else "on the right" if side > tan_h * 0.33 else "in the centre"
        d = "close to the camera" if depth < 2.5 else "at the back" if depth > 6 else ""
        name = f"the “{o['label']}”" if o.get("label") else PIECE_EN.get(o.get("model", ""), o.get("model", "object"))
        found.append((depth + abs(side) * depth * 0.5, h, d, name))
    # Só o que conta: as coisas mais perto e mais ao centro (o render mostra o resto), sem repetir nomes.
    spots: dict[tuple[str, str], list[str]] = {}
    kept: set[str] = set()
    for _score, h, d, name in sorted(found):
        if name.startswith("the ") and name in kept:
            continue
        if len(kept) >= MAX_SEEN and name not in kept:
            continue
        kept.add(name)
        spots.setdefault((h, d), []).append(name)
    if not spots:
        return ""
    parts = []
    for h in ("on the left", "in the centre", "on the right"):
        for d in ("close to the camera", "", "at the back"):
            names = spots.get((h, d))
            if not names:
                continue
            counted = []
            for name in dict.fromkeys(names):
                k = names.count(name)
                counted.append(name if name.startswith("the ") else (f"{_NUM.get(k, k)} {name}s" if k > 1 else f"a {name}"))
            parts.append(f"{h}{', ' + d if d else ''}: {', '.join(counted)}")
    return "What this view shows (from the 3D model): " + "; ".join(parts) + "."


MAX_ZONE_REFS = 4  # cada imagem leva até 6: o render, a referência da zona e 4 escolhidas


def zone_refs(asset: Asset, zone: str | None) -> list[int]:
    """Imagens de referência escolhidas para esta zona (ids), pela ordem em que foram escolhidas."""
    return [int(i) for i in (asset.zone_refs or {}).get(zone or "", []) if str(i).isdigit()]


def ref_options(session: Session, asset: Asset) -> list[dict]:
    """O que se pode escolher como referência de uma zona: as fotos do local, a imagem geral aprovada e a referência
    aprovada de cada zona (ex.: a da rua da frente para as traseiras)."""
    from app.asset_refs import asset_photos

    out = [{"id": m.id, "path": m.path, "label": f"Foto {n}", "group": "Fotos do local"}
           for n, m in enumerate(asset_photos(session, asset), 1)]
    general = session.get(MediaFile, asset.approved_image_id) if asset.approved_image_id else None
    if general and general.status == "approved":
        out.append({"id": general.id, "path": general.path, "label": "Imagem geral", "group": "Imagem geral"})
    for v in location_views(session, asset):
        media = session.get(MediaFile, v.image_id) if v.image_id else None
        if v.kind == "ref" and media and media.status == "approved":
            out.append({"id": media.id, "path": media.path, "label": f"Referência · {v.zone or 'espaço'}",
                        "group": "Referências das zonas"})
    for el in session.exec(select(Asset).where(Asset.project_id == asset.project_id, Asset.kind == "element",
                                               Asset.approved_image_id.is_not(None)).order_by(Asset.name)).all():
        media = session.get(MediaFile, el.approved_image_id)
        if media and media.status == "approved":  # elementos do mundo: o design dos carros, ecrãs, drones…
            out.append({"id": media.id, "path": media.path, "label": el.label, "group": "Elementos do mundo"})
    for o in out:
        o["item"] = (o["id"], o, o["label"])  # (valor, imagem, nome) para o modal de seleção
    return out


def view_inputs(session: Session, asset: Asset, view: LocationView) -> list[int]:
    """Render desta posição + referência da zona (ou a imagem geral antiga do local, se existir) + as referências
    escolhidas para a zona (folhas; as fotografias reais ficam para o escritor de prompts)."""
    from app.routes.production import approved_media

    ids = [view.render_id]
    room = zone_reference(session, asset, view.zone) if view.kind == "view" else None
    ids.append(room.id if room else asset.approved_image_id)  # sem nenhuma das duas, segue só o render
    # Para o gerador só vão as folhas dos elementos e as imagens aprovadas (as fotografias reais ficam para o escritor
    # de prompts, que as descreve no texto): o limite conta só com essas.
    design = [i for i in design_refs(session, asset, view.zone) if i not in ids]  # o corte é o do modelo, ao gerar
    found = approved_media(session, ids + design)
    return [i for i in ids + design if i in found]


def design_refs(session: Session, asset: Asset, zone: str | None) -> list[int]:
    """As referências escolhidas da zona que vão para o gerador (não são fotografias reais), pela ordem escolhida."""
    out = []
    for i in zone_refs(asset, zone):
        m = session.get(MediaFile, i)
        if m and m.role != "photo":
            out.append(i)
    return out
