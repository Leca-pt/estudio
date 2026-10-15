"""Relevo das maquetes 3D (canyons, colinas, mesas, cristas, zonas planas): a altura do chão em qualquer ponto.

A mesma conta existe no navegador (static/js/terrain.js), linha a linha: o servidor precisa dela para pôr as
pessoas no chão, saber se uma parede do canyon esconde alguém da câmara e dar as alturas ao Assistente; o navegador,
para desenhar o terreno, as rochas e o que se espalha por cima. Só aritmética simples (inteiros de 32 bits para o
ruído, somas, produtos e raízes): dá o mesmo número nas duas linguagens.

Um objeto "terrain" cobre um retângulo (from/to) e tem uma altura de base (z), uma ondulação suave (relief, scale) e
formas ("features"):
- canyon {path: [[x, y], …], width, floor, depth, terraces}: um corte ao longo do caminho, com fundo plano;
- hill {center, radius, height}: colina redonda;
- mesa {center, radius, height}: planalto de topo plano e encostas íngremes;
- ridge {path, width, height}: crista ao longo de um caminho;
- flat {from, to, z, blend}: zona aplanada à altura z (estradas, praças, onde há prédios).
Fora dos terrenos o chão está a 0.
"""

import math

M32 = 0xFFFFFFFF


def _hash(ix: int, iy: int, seed: int) -> float:
    """Número entre 0 e 1 para cada canto da grelha do ruído (o mesmo em JS: Math.imul e >>> 0)."""
    h = (ix * 374761393 + iy * 668265263 + seed * 2246822519) & M32
    h = ((h ^ (h >> 13)) * 1274126177) & M32
    h = h ^ (h >> 16)
    return h / 4294967296.0


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _noise(x: float, y: float, seed: int) -> float:
    """Ruído de valor entre -1 e 1, suave."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    a, b = _hash(ix, iy, seed), _hash(ix + 1, iy, seed)
    c, d = _hash(ix, iy + 1, seed), _hash(ix + 1, iy + 1, seed)
    u, v = _smooth(fx), _smooth(fy)
    top = a + (b - a) * u
    bottom = c + (d - c) * u
    return (top + (bottom - top) * v) * 2 - 1


def fbm(x: float, y: float, seed: int) -> float:
    """Quatro oitavas de ruído (detalhe grande e pequeno), entre cerca de -1 e 1."""
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(4):
        total += _noise(x * freq, y * freq, seed + o * 31) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def _seg_dist(px: float, py: float, path: list) -> float:
    """Distância de (px, py) ao caminho (linha quebrada)."""
    best = 1e18
    if len(path) == 1:
        return math.sqrt((px - path[0][0]) * (px - path[0][0]) + (py - path[0][1]) * (py - path[0][1]))
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        s = 0.0 if L <= 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
        qx, qy = ax + dx * s, ay + dy * s
        d = math.sqrt((px - qx) * (px - qx) + (py - qy) * (py - qy))
        if d < best:
            best = d
    return best


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def height(t: dict, x: float, y: float) -> float:
    """Altura do terreno t em (x, y) (dentro do retângulo dele)."""
    seed = int(t.get("seed", 1))
    scale = float(t.get("scale", 40)) or 40.0
    h = float(t.get("z", 0)) + float(t.get("relief", 0)) * fbm(x / scale, y / scale, seed)
    flats = []
    for n, f in enumerate(t.get("features") or []):
        k = f["kind"]
        if k == "canyon":
            half_floor, half_w = f["floor"] / 2, max(f["width"] / 2, f["floor"] / 2 + 0.5)
            # paredes irregulares: a distância ao caminho é baralhada por ruído (mais nas paredes do que no fundo)
            d = _seg_dist(x, y, f["path"]) + f.get("jagged", 0.4) * (half_w - half_floor) * 0.35 * fbm(x / 9, y / 9, seed + 101 + n)
            s = _clamp01((d - half_floor) / (half_w - half_floor))
            drop = 1 - _smooth(s)
            terr = f.get("terraces", 0)
            if terr > 0:  # degraus (estratos de rocha)
                drop = drop + (math.floor(drop * 5 + 0.5) / 5 - drop) * terr
            h -= f["depth"] * drop
        elif k == "ridge":
            d = _seg_dist(x, y, f["path"])
            h += f["height"] * (1 - _smooth(_clamp01(d / (f["width"] / 2))))
        elif k == "hill":
            cx, cy = x - f["center"][0], y - f["center"][1]
            d = math.sqrt(cx * cx + cy * cy)
            h += f["height"] * (1 - _smooth(_clamp01(d / f["radius"])))
        elif k == "mesa":
            cx, cy = x - f["center"][0], y - f["center"][1]
            d = math.sqrt(cx * cx + cy * cy)
            d += f["radius"] * 0.12 * fbm(x / 7, y / 7, seed + 201 + n)
            edge = f["radius"] * 0.8
            s = _clamp01((d - edge) / (f["radius"] - edge))
            h += f["height"] * (1 - _smooth(s))
        elif k == "flat":
            flats.append(f)
    for f in flats:  # as zonas planas mandam no fim (com uma margem de transição)
        (x0, y0), (x1, y1), blend = f["from"], f["to"], max(f.get("blend", 4), 0.01)
        dx = max(x0 - x, 0.0, x - x1)
        dy = max(y0 - y, 0.0, y - y1)
        w = 1 - _smooth(_clamp01(math.sqrt(dx * dx + dy * dy) / blend))
        h += (f["z"] - h) * w
    return h


def terrains(layers: list[dict]) -> list[dict]:
    return [o for layer in layers for o in (layer.get("objects") or []) if o.get("type") == "terrain"]


def ground_fn(layers: list[dict]):
    """Função (x, y) → altura do chão, ou None se não há terreno (o chão está todo a 0)."""
    ts = terrains(layers)
    if not ts:
        return None

    def ground(x: float, y: float) -> float:
        best = None
        for t in ts:
            (x0, y0), (x1, y1) = t["from"], t["to"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                h = height(t, x, y)
                best = h if best is None else max(best, h)
        return 0.0 if best is None else best
    return ground


def blocks_sight(ground, a: list[float], b: list[float], samples: int = 40) -> bool:
    """O terreno passa por cima da linha de a para b (ex.: a parede do canyon entre a câmara e a pessoa)?"""
    if ground is None:
        return False
    for i in range(1, samples):
        s = i / samples
        x, y, z = (a[k] + (b[k] - a[k]) * s for k in range(3))
        if ground(x, y) > z + 0.05:
            return True
    return False


def summary(t: dict, label: str = "") -> str:
    """Para o Assistente: o que o terreno é e as alturas num quadriculado (para pôr câmaras e pessoas)."""
    (x0, y0), (x1, y1) = t["from"], t["to"]
    lines = [f"terreno {label}de [{x0:g}, {y0:g}] a [{x1:g}, {y1:g}], base {t.get('z', 0):g} m, ondulação "
             f"{t.get('relief', 0):g} m"]
    for f in t.get("features") or []:
        if f["kind"] in ("canyon", "ridge"):
            lines.append(f"  {f['kind']} pelo caminho {f['path']}: largura {f['width']:g} m"
                         + (f", fundo {f['floor']:g} m, profundidade {f['depth']:g} m" if f["kind"] == "canyon"
                            else f", altura {f['height']:g} m"))
        elif f["kind"] in ("hill", "mesa"):
            lines.append(f"  {f['kind']} em {f['center']}: raio {f['radius']:g} m, altura {f['height']:g} m")
        else:
            lines.append(f"  zona plana de {f['from']} a {f['to']} à altura {f['z']:g} m")
    n = 7
    xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
    ys = [y1 - (y1 - y0) * j / (n - 1) for j in range(n)]
    lines.append("  alturas do chão (linhas de y, de cima para baixo; colunas de x): x = " + ", ".join(f"{x:g}" for x in xs))
    for y in ys:
        lines.append(f"    y {y:g}: " + ", ".join(f"{height(t, x, y):.1f}" for x in xs))
    for f in t.get("features") or []:
        if f["kind"] == "canyon":  # o fundo, ao longo do caminho (onde as pessoas andam)
            pts = f["path"]
            lines.append("  fundo do canyon ao longo do caminho: " + "; ".join(
                f"[{p[0]:g}, {p[1]:g}] → {height(t, p[0], p[1]):.1f} m" for p in pts))
    return "\n".join(lines)
