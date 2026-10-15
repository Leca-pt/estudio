"""Verificação de uma maquete 3D (só lê): sobreposições, portas sem vão, peças fora das divisões, objetos a flutuar ou
enterrados, assentos virados para o lado errado e medidas estranhas. Os #n são os de «Alterar por partes» (a posição na
lista de objetos). Segue as convenções da app: as portas ficam num vão da parede (a parede partida em dois troços);
as janelas ficam dentro de uma parede inteira; peças assentam pela base (center z = chão), caixas pelo meio."""

import math

from app.scene3d import PIECES

SEATS = {"chair", "armchair", "sofa", "bench", "booth", "bar_stool"}
DIRECTED_SEATS = SEATS - {"bar_stool"}  # os bancos redondos não têm frente
WALL_MOUNTED = {"tv", "window", "ceiling_lamp", "street_lamp", "shelf"}
TARGETS = {"table_rect", "table_round", "tv", "counter", "desk", "kitchen_counter", "register", "stall"}
OUTDOOR = {"car", "street_lamp", "tree", "bin", "motorbike", "bench", "plant", "crate", "stall", "bus_stop", "traffic_light",
           "fountain", "kiosk", "barrier", "bollard"}
WALL_MIN_H = 1.2


def _rect(o: dict):
    """(cx, cy, hx, hy, ângulo, z0, z1) da pegada de uma caixa, peça ou cilindro; None para o resto."""
    t = o.get("type")
    if t == "box" and "center" in o:
        c, s = o["center"], o["size"]
        return c[0], c[1], s[0] / 2, s[1] / 2, math.radians(o.get("rot_z", 0)), c[2] - s[2] / 2, c[2] + s[2] / 2
    if t == "piece" and "center" in o:
        s = o.get("size") or list((PIECES.get(o.get("model")) or ((0.5, 0.5, 0.5),))[0])
        c = o["center"]
        return c[0], c[1], s[0] / 2, s[1] / 2, math.radians(o.get("rot_z", 0)), c[2], c[2] + s[2]
    if t == "cylinder" and "center" in o:
        c, r, h = o["center"], o.get("radius", 0.5), o.get("height", 1)
        return c[0], c[1], r, r, 0.0, c[2] - h / 2, c[2] + h / 2
    return None


def _corners(r):
    cx, cy, hx, hy, a, _z0, _z1 = r
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]


def _overlap_2d(r1, r2, shrink: float = 0.02) -> float:
    """Sobreposição das pegadas (separating axis), em metros de penetração no eixo mais fraco; 0 se não tocam."""
    p1, p2 = _corners(r1), _corners(r2)
    best = math.inf
    for poly in (p1, p2):
        for i in range(4):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % 4]
            nx, ny = y1 - y0, x0 - x1
            n = math.hypot(nx, ny) or 1
            nx, ny = nx / n, ny / n
            a = [x * nx + y * ny for x, y in p1]
            b = [x * nx + y * ny for x, y in p2]
            pen = min(max(a), max(b)) - max(min(a), min(b))
            if pen <= shrink:
                return 0.0
            best = min(best, pen)
    return best


def _z_overlap(r1, r2) -> float:
    return min(r1[6], r2[6]) - max(r1[5], r2[5])


def _name(n: int, o: dict) -> str:
    what = o.get("model") or o.get("type")
    return f"#{n} {what}" + (f" «{o['label']}»" if o.get("label") else "")


def _zones(objs):
    return [(n, o) for n, o in enumerate(objs) if o.get("type") == "zone"]


def _zone_at(zones, x, y, z):
    inside = [o for _n, o in zones if min(o["from"][0], o["to"][0]) - 0.05 <= x <= max(o["from"][0], o["to"][0]) + 0.05
              and min(o["from"][1], o["to"][1]) - 0.05 <= y <= max(o["from"][1], o["to"][1]) + 0.05
              and (o.get("z") or 0) - 0.5 <= z <= (o.get("z") or 0) + 15.0]  # do chão ao teto (naves altas incluídas)
    below = [o for o in inside if (o.get("z") or 0) <= z + 0.5]
    return max(below or inside, key=lambda o: o.get("z") or 0) if inside else None


def _indoor(zones, x, y, z) -> bool:
    """Dentro de uma divisão com teto (as portas da rua ficam em fachadas que a verificação não vê)."""
    zone = _zone_at(zones, x, y, z)
    if zone is None:
        return False
    area = abs((zone["to"][0] - zone["from"][0]) * (zone["to"][1] - zone["from"][1]))
    return bool(zone.get("ceiling", 2.9 if area < 400 else 0))


def check(layout: dict | None, max_items: int = 30) -> list[str]:
    objs = (layout or {}).get("objects") or []
    if not objs:
        return ["A maquete está vazia."]
    rects = {n: r for n, o in enumerate(objs) if (r := _rect(o))}
    tall = {n: r for n, r in rects.items() if objs[n].get("type") == "box" and (r[6] - r[5]) > WALL_MIN_H}
    walls = {n: r for n, r in tall.items() if min(r[2], r[3]) * 2 <= 0.6}  # paredes: caixas altas e finas
    facades = {n: r for n, r in tall.items() if n not in walls}  # prédios e fachadas: blocos grossos
    zones = _zones(objs)
    out: dict[str, list[str]] = {k: [] for k in ("sobreposições", "portas e janelas", "fora das divisões",
                                                  "a flutuar ou enterrados", "assentos", "medidas")}

    # 1. Sobreposições (mobília dentro de paredes primeiro)
    pieces = [n for n, o in enumerate(objs) if o.get("type") == "piece" and n in rects
              and o.get("model") not in ("door", "window", "ceiling_lamp")]
    for n in pieces:
        for w, rw in walls.items():
            pen = _overlap_2d(rects[n], rw, shrink=0.10)  # até 10 cm é encostar a uma parede de 15 cm
            if pen and _z_overlap(rects[n], rw) > 0.1:
                out["sobreposições"].append(f"{_name(n, objs[n])} entra {pen:.2f} m na parede #{w}.")
    for i, n in enumerate(pieces):
        for m in pieces[i + 1:]:
            a, b = objs[n].get("model"), objs[m].get("model")
            if {a, b} & {"crate"} and abs(rects[n][5] - rects[m][6]) < 0.05:  # caixotes empilhados
                continue
            if (a in SEATS and b in TARGETS) or (b in SEATS and a in TARGETS):  # cadeira metida debaixo da mesa: normal
                continue
            pen = _overlap_2d(rects[n], rects[m], shrink=0.05)
            if pen > 0.1 and _z_overlap(rects[n], rects[m]) > 0.1:
                out["sobreposições"].append(f"{_name(n, objs[n])} e {_name(m, objs[m])} ocupam o mesmo sítio ({pen:.2f} m).")

    # 2. Portas num vão; janelas numa parede
    for n, o in enumerate(objs):
        model = o.get("model")
        if o.get("type") != "piece" or model not in ("door", "window") or n not in rects:
            continue
        r = rects[n]
        blocking = [w for w, rw in walls.items() if _overlap_2d(r, rw, shrink=0.03) > min(r[2], 0.3)
                    and _z_overlap(r, rw) > 0.5]
        cx, cy, hx, _hy, a, _z0, _z1 = r
        side = []  # paredes encostadas aos lados da porta (no eixo dela)
        for sgn in (-1, 1):
            px, py = cx + sgn * (hx + 0.15) * math.cos(a), cy + sgn * (hx + 0.15) * math.sin(a)
            probe = (px, py, 0.1, 0.1, 0.0, r[5], r[6])
            side.append(any(_overlap_2d(probe, rw, shrink=0.0) for rw in walls.values()))
        on_facade = any(_overlap_2d((cx, cy, r[2], r[3] + 0.25, a, r[5], r[6]), rf, shrink=0.0) for rf in facades.values())
        if on_facade and not blocking:  # porta ou montra na face de um prédio (sem vão: o prédio é um bloco)
            continue
        if model == "door":
            if blocking:
                out["portas e janelas"].append(f"{_name(n, o)} está tapada pela parede #{blocking[0]}: falta o vão (parte a parede em dois troços).")
            elif not _indoor(zones, cx, cy, r[5]):
                continue  # porta de rua: a fachada pode ser um quarteirão gerado, que aqui não se vê
            elif not all(side) and not any(side):
                out["portas e janelas"].append(f"{_name(n, o)} não tem parede de nenhum dos lados (está solta ou mal rodada).")
            elif not all(side):
                out["portas e janelas"].append(f"{_name(n, o)} só encosta à parede de um lado: vê se o vão tem a largura da porta.")
        else:
            inside = any(_overlap_2d(r, rw, shrink=0.0) for rw in walls.values()) or all(side)
            if not inside and _indoor(zones, cx, cy, r[5]):
                out["portas e janelas"].append(f"{_name(n, o)} não está em nenhuma parede.")

    # 3. Fora das divisões; divisões estranhas
    if zones:
        for n, o in enumerate(objs):
            if o.get("type") != "piece" or o.get("model") in OUTDOOR or o.get("model") in ("door", "window") or n not in rects:
                continue
            r = rects[n]
            if _zone_at(zones, r[0], r[1], r[5]) is None:
                out["fora das divisões"].append(f"{_name(n, o)} em [{r[0]:.1f}, {r[1]:.1f}] não fica dentro de nenhuma divisão (zone).")
        for n, o in zones:
            w, d = abs(o["to"][0] - o["from"][0]), abs(o["to"][1] - o["from"][1])
            if w < 0.5 or d < 0.5:
                out["fora das divisões"].append(f"#{n} zone «{o.get('name')}» quase sem área ({w:.1f} × {d:.1f} m): não tem chão.")
            for m, p in zones:
                if m <= n or abs((o.get("z") or 0) - (p.get("z") or 0)) > 0.3:
                    continue
                ox = min(max(o["from"][0], o["to"][0]), max(p["from"][0], p["to"][0])) - max(min(o["from"][0], o["to"][0]), min(p["from"][0], p["to"][0]))
                oy = min(max(o["from"][1], o["to"][1]), max(p["from"][1], p["to"][1])) - max(min(o["from"][1], o["to"][1]), min(p["from"][1], p["to"][1]))
                if ox > 0.3 and oy > 0.3:
                    out["fora das divisões"].append(f"#{n} «{o.get('name')}» e #{m} «{p.get('name')}» sobrepõem-se no mesmo piso ({ox:.1f} × {oy:.1f} m).")

    # 4. A flutuar ou enterrados
    supports = [(n, r) for n, r in rects.items()]
    for n, o in enumerate(objs):
        if n not in rects or o.get("model") in ("window", "ceiling_lamp", "street_lamp"):
            continue
        r = rects[n]
        zone = _zone_at(zones, r[0], r[1], r[5]) if zones else None
        floor = (zone.get("z") or 0) if zone else 0.0
        base = r[5]
        if base < floor - 0.05 and o.get("type") == "piece":
            out["a flutuar ou enterrados"].append(f"{_name(n, o)} está {floor - base:.2f} m abaixo do chão.")
        elif base > floor + 0.05 and o.get("type") == "piece":
            under = any(m != n and abs(rm[6] - base) < 0.08 and _overlap_2d(r, rm, shrink=0.0) for m, rm in supports)
            hung = o.get("model") in WALL_MOUNTED or any(  # encostado a uma parede ou fachada: pendurado
                _overlap_2d((r[0], r[1], r[2] + 0.1, r[3] + 0.1, r[4], r[5], r[6]), rw, shrink=0.0) for rw in tall.values())
            if not under and not hung:
                out["a flutuar ou enterrados"].append(f"{_name(n, o)} começa a {base - floor:.2f} m do chão sem nada por baixo.")

    # 5. Assentos virados para nada
    for n, o in enumerate(objs):
        if o.get("type") != "piece" or o.get("model") not in DIRECTED_SEATS or n not in rects:
            continue
        r = rects[n]
        near = [(math.hypot(rm[0] - r[0], rm[1] - r[1]), m) for m, rm in rects.items()
                if objs[m].get("model") in TARGETS and abs(rm[5] - r[5]) < 0.5]
        near = [x for x in near if x[0] < 2.5]
        if not near:
            continue
        dist, m = min(near)
        a = math.radians(o.get("rot_z", 0))
        fx, fy = -math.sin(a), math.cos(a)  # a frente das peças é +Y local
        tx, ty = rects[m][0] - r[0], rects[m][1] - r[1]
        cosang = (fx * tx + fy * ty) / (math.hypot(tx, ty) or 1)
        if cosang < 0.5:
            out["assentos"].append(f"{_name(n, o)} está virado {math.degrees(math.acos(max(-1, min(1, cosang)))):.0f}° ao lado de "
                                   f"{_name(m, objs[m])} ({dist:.1f} m): vê o rot_z.")

    # 6. Medidas estranhas
    for n, o in enumerate(objs):
        if o.get("model") == "door" and n in rects:
            w = rects[n][2] * 2
            if w < 0.7 or w > 1.5:
                out["medidas"].append(f"{_name(n, o)} tem {w:.2f} m de largura (o normal é 0,7 a 1,5 m; confirma se é um "
                                      "portão, uma porta dupla ou uma montra).")
        if o.get("type") == "zone":
            area = abs((o["to"][0] - o["from"][0]) * (o["to"][1] - o["from"][1]))
            ceil = o.get("ceiling", 2.9 if area < 400 else 0)
            if ceil and ceil < 2.3:
                out["medidas"].append(f"#{n} zone «{o.get('name')}» tem pé-direito de {ceil:.2f} m (menos de 2,3 m).")
    for w, rw in walls.items():
        h = rw[6] - rw[5]
        if h < 2.3 and objs[w].get("color") == "wall":
            out["medidas"].append(f"#{w} parede com {h:.2f} m de altura (pé-direito baixo).")

    lines = []
    for title, items in out.items():
        if items:
            lines.append(f"{title.upper()} ({len(items)}):")
            lines += [f"- {x}" for x in items[:max_items]] + ([f"- … e mais {len(items) - max_items}"] if len(items) > max_items else [])
    return lines or ["Sem avisos: a maquete passa em todas as verificações."]
