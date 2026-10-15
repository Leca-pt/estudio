"""Maquete 3D: a cena descrita pelo Assistente (JSON), desenhada no navegador com three.js.

Três peças:
- a maquete do local (Asset.layout3d): o espaço por zonas — divisões e espaços exteriores; as cenas passadas nesse
  local usam-na (Scene.set3d_asset_id), por isso a geografia fica sempre igual;
- a encenação da cena (Scene.stage3d): objetos extra, o elenco e os figurantes com as posições ao longo de TODA a cena
  (como no teatro), e as cores do elenco (Scene.cast3d);
- a câmara de cada plano (Shot.scene3d): o segundo da encenação onde o plano começa ("start") e a câmara durante o
  plano, com os momentos que contam marcados com "moment" (app/moments.py). Vários planos podem mostrar a mesma
  encenação de pontos de vista diferentes (campo/contracampo), com toda a gente sempre no mesmo sítio.

Coordenadas em metros, Z para cima, Y para a frente (norte), X para a direita; ângulos em graus (0 = virado para +Y).
O servidor só valida e junta as peças; o navegador desenha, anima, tira os renders (momentos e vistas) e grava o
vídeo-guia.
Cores: o que importa (elenco, adereços, marcos) com cores fortes; o cenário e os figurantes em cinzentos.
"""

import hashlib
import json
import math
import re

from sqlmodel import Session, select

from app.models import Asset, Scene, Shot
from app.memo import memo

# Cores do cenário (cinzentos) e dos marcos (fortes). O Assistente usa estes nomes (ou "#rrggbb").
NEUTRAL = {
    "ground": "#6b6c70", "road": "#4b4c51", "pavement": "#7d7e83", "building": "#9c9da3", "building_dark": "#7b7c82",
    "building_light": "#b9bac0", "tall": "#c8cbd1", "low": "#8f9197", "roof": "#85868b", "structure": "#8b8c92",
    "water": "#6f8aa0", "glass": "#a9b6c2", "wood": "#8e8176", "extra_person": "#c4b8a8", "extra_car": "#aeb2b8",
    # tons por material (discretos: dão pistas ao modelo de imagem sem parecer um render realista)
    "wood_light": "#a89582", "metal": "#8f969e", "fabric": "#6f6a73", "tile": "#a7a39b", "floor": "#7d7770",
    "wall": "#b2aea6", "plant": "#6f8a6a",
}
ACCENT = {
    "red": "#e8202a", "orange": "#ff7a00", "yellow": "#ffd400", "green": "#18c24a", "cyan": "#00c2d6",
    "blue": "#1e6bff", "purple": "#8a2be2", "magenta": "#e0249a", "lime": "#9be000", "pink": "#ff5fa2", "sky": "#4fc3ff",
    "teal": "#00a88f", "brown": "#8b4a2b", "white": "#f4f4f4",
}
# Elenco da encenação: a cor de cada asset escolhe-se na cena (Scene.cast3d), sem repetir dentro da cena. Vermelho,
# ciano e amarelo ficam de fora: são para os marcos do cenário (porta, letreiro) e para os veículos importantes (comboio).
CAST_ORDER = ["orange", "blue", "green", "magenta", "purple", "lime", "pink", "sky", "teal", "brown", "white"]
CAST_EN = {"orange": "orange", "blue": "blue", "green": "green", "magenta": "magenta", "purple": "purple", "lime": "lime",
           "pink": "pink", "sky": "sky-blue", "teal": "teal", "brown": "brown", "white": "white"}
CAST_NAMES = {"orange": "laranja", "blue": "azul", "green": "verde", "magenta": "magenta", "purple": "roxo",
              "lime": "lima", "pink": "rosa", "sky": "azul-céu", "teal": "verde-água", "brown": "castanho", "white": "branco"}
LANDMARK_COLORS = ("red", "cyan", "yellow")

OBJECT_TYPES = {"box", "blocks", "pillars", "beam", "cylinder", "piece", "zone", "prism", "extrude"}
PRISM_SHAPES = {"gable", "ramp"}  # gable: pico ao meio (telhado de duas águas, empena); ramp: sobe de -X para +X
ACTOR_KINDS = {"character", "person", "prop", "car", "vehicle", "train"}
# Peças prontas (desenhadas no navegador com a forma certa): nome → (largura, profundidade, altura) por omissão, em
# metros, e o tom. A frente (+Y) é o lado de quem a usa (assento de uma cadeira, porta de um frigorífico, frente do carro).
PIECES = {
    "table_rect": ((1.4, 0.8, 0.75), "wood"), "table_round": ((0.9, 0.9, 0.75), "wood"),
    "chair": ((0.45, 0.48, 0.9), "wood"), "bar_stool": ((0.4, 0.4, 0.75), "metal"), "sofa": ((2.0, 0.9, 0.85), "fabric"),
    "armchair": ((0.9, 0.85, 0.85), "fabric"), "booth": ((1.4, 0.6, 1.15), "fabric"), "bench": ((1.6, 0.5, 0.8), "wood"),
    "counter": ((3.0, 0.7, 1.1), "wood"), "shelf": ((1.6, 0.4, 2.0), "wood"), "fridge": ((0.7, 0.7, 1.85), "metal"),
    "register": ((0.4, 0.4, 0.3), "metal"), "stall": ((2.0, 1.2, 2.3), "wood"), "kitchen_counter": ((2.4, 0.6, 0.9), "wood_light"),
    "stove": ((0.6, 0.6, 0.9), "metal"), "sink": ((0.8, 0.6, 0.9), "metal"), "bed": ((1.6, 2.0, 0.55), "fabric"),
    "wardrobe": ((1.2, 0.6, 2.1), "wood"), "desk": ((1.4, 0.7, 0.75), "wood"), "tv": ((1.2, 0.1, 0.7), "metal"),
    "plant": ((0.5, 0.5, 1.2), "plant"), "door": ((0.9, 0.12, 2.1), "wood"), "window": ((1.2, 0.12, 1.3), "glass"),
    "stairs": ((1.2, 3.0, 2.8), "tile"), "column": ((0.5, 0.5, 3.0), "structure"), "ceiling_lamp": ((0.5, 0.5, 0.8), "metal"),
    "street_lamp": ((0.4, 1.4, 5.0), "metal"), "tree": ((3.0, 3.0, 6.0), "plant"), "car": ((1.8, 4.4, 1.5), "extra_car"),
    "motorbike": ((0.8, 2.0, 1.1), "metal"), "bin": ((0.6, 0.6, 1.0), "metal"), "crate": ((0.8, 0.8, 0.8), "wood"),
}
POSES = {"stand", "walk", "run", "sit", "point", "bend", "kneel", "lie"}
MAX_OBJECTS, MAX_LOTS, MAX_ACTORS, MAX_KEYS, LIMIT = 1500, 6000, 80, 40, 5000
MAX_POINTS = 64  # vértices de um polígono "extrude"


class SceneError(ValueError):
    pass


def _num(v, what: str, lo: float = -LIMIT, hi: float = LIMIT) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise SceneError(f"{what}: tem de ser um número.")
    if not math.isfinite(x) or not lo <= x <= hi:
        raise SceneError(f"{what}: fora dos limites ({lo} a {hi}).")
    return round(x, 3)


def _vec(v, what: str, n: int = 3, lo: float = -LIMIT, hi: float = LIMIT) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise SceneError(f"{what}: tem de ser uma lista de {n} números.")
    return [_num(x, what, lo, hi) for x in v]


def _color(v, default: str) -> str:
    v = str(v or default).strip()
    if v in NEUTRAL or v in ACCENT or re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return v
    raise SceneError(f"Cor desconhecida: {v} (usa um nome da lista ou #rrggbb).")


def hex_color(name: str) -> str:
    return NEUTRAL.get(name) or ACCENT.get(name) or name


def _objects(items, where: str) -> list[dict]:
    if not isinstance(items, list):
        raise SceneError(f"{where}: \"objects\" tem de ser uma lista.")
    if len(items) > MAX_OBJECTS:
        raise SceneError(f"{where}: demasiados objetos ({len(items)}; máximo {MAX_OBJECTS}).")
    out, lots = [], 0
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict) or it.get("type") not in OBJECT_TYPES:
            raise SceneError(f"{where}, objeto {n}: tipo tem de ser um de {', '.join(sorted(OBJECT_TYPES))}.")
        t, w = it["type"], f"{where}, objeto {n} ({it['type']})"
        if t == "zone":  # divisão com nome (sala, cozinha, quarto…): só o chão marcado e o nome
            a, b = _vec(it.get("from"), w, 2), _vec(it.get("to"), w, 2)
            zone = {"type": "zone", "name": str(it.get("name") or "divisão")[:40],
                    "from": [min(a[0], b[0]), min(a[1], b[1])], "to": [max(a[0], b[0]), max(a[1], b[1])],
                    "z": _num(it.get("z", 0), w, -500, 2000)}
            if it.get("ceiling") is not None:  # altura do teto (0 = sem teto: pátio, rua); por omissão 2,9 m nos interiores
                zone["ceiling"] = _num(it["ceiling"], w, 0, 50)
            out.append(zone)
            continue
        if t == "piece":
            model = str(it.get("model") or "")
            if model not in PIECES:
                raise SceneError(f"{w}: peça desconhecida ({model}); usa uma de {', '.join(PIECES)}.")
            dims, tone = PIECES[model]
            o = {"type": "piece", "model": model, "center": _vec(it.get("center"), w), "rot_z": _num(it.get("rot_z", 0), w, -360, 360),
                 "size": _vec(it["size"], w, lo=0.05, hi=60) if it.get("size") is not None else list(dims),
                 "color": _color(it.get("color"), tone)}
            if it.get("label"):
                o["label"] = str(it["label"])[:60]
            out.append(o)
            continue
        o = {"type": t, "color": _color(it.get("color"), "building")}
        if it.get("label"):
            o["label"] = str(it["label"])[:60]
        if it.get("plain"):
            o["plain"] = True  # sem janelas desenhadas (paredes interiores, muros)
        if t == "box":
            o |= {"center": _vec(it.get("center"), w), "size": _vec(it.get("size"), w, lo=0.01, hi=3000),
                  "rot_z": _num(it.get("rot_z", 0), w, -360, 360)}
        elif t == "prism":
            shape = str(it.get("shape") or "gable")
            if shape not in PRISM_SHAPES:
                raise SceneError(f"{w}: \"shape\" tem de ser um de {', '.join(sorted(PRISM_SHAPES))}.")
            o |= {"center": _vec(it.get("center"), w), "size": _vec(it.get("size"), w, lo=0.01, hi=3000),
                  "rot_z": _num(it.get("rot_z", 0), w, -360, 360), "shape": shape}
        elif t == "extrude":
            pts = it.get("points")
            if not isinstance(pts, list) or not 3 <= len(pts) <= MAX_POINTS:
                raise SceneError(f"{w}: \"points\" tem de ter entre 3 e {MAX_POINTS} pontos [x, y].")
            o |= {"points": [_vec(p, w, 2) for p in pts], "z": _num(it.get("z", 0), w, -500, 2000),
                  "height": _num(it.get("height"), w, 0.01, 2000)}
        elif t == "cylinder":
            o |= {"center": _vec(it.get("center"), w), "radius": _num(it.get("radius"), w, 0.01, 500),
                  "height": _num(it.get("height"), w, 0.01, 2000)}
        elif t == "blocks":
            a, b = _vec(it.get("from"), w, 2), _vec(it.get("to"), w, 2)
            lot = _vec(it.get("lot"), w, 2, 1, 1000)
            h = _vec(it.get("height"), w, 2, 0.1, 2000)
            count = math.ceil(abs(b[0] - a[0]) / lot[0]) * math.ceil(abs(b[1] - a[1]) / lot[1])
            lots += count
            o |= {"from": [min(a[0], b[0]), min(a[1], b[1])], "to": [max(a[0], b[0]), max(a[1], b[1])], "lot": lot,
                  "height": sorted(h), "gap": _num(it.get("gap", 2), w, 0, 200),
                  "roof_bits": int(_num(it.get("roof_bits", 0), w, 0, 3)), "roof_color": _color(it.get("roof_color"), "roof")}
        elif t == "pillars":
            o |= {"from": _vec(it.get("from"), w, 2), "to": _vec(it.get("to"), w, 2),
                  "count": int(_num(it.get("count"), w, 1, 300)), "radius": _num(it.get("radius", 0.6), w, 0.05, 50),
                  "height": _num(it.get("height"), w, 0.1, 500)}
        elif t == "beam":
            o |= {"from": _vec(it.get("from"), w), "to": _vec(it.get("to"), w),
                  "width": _num(it.get("width", 2), w, 0.05, 200), "thickness": _num(it.get("thickness", 1), w, 0.05, 200)}
        out.append(o)
    if lots > MAX_LOTS:
        raise SceneError(f"{where}: os quarteirões dão {lots} edifícios (máximo {MAX_LOTS}); usa lotes maiores.")
    return out


def validate_set(data) -> dict:
    """Cenário de uma cena: {"objects": [...], "seed": 7, "ground": [largura, profundidade]}."""
    if not isinstance(data, dict):
        raise SceneError("O cenário tem de ser um objeto JSON.")
    out = {"objects": _objects(data.get("objects", []), "Cenário"), "seed": int(_num(data.get("seed", 7), "seed", 0, 1e6))}
    if data.get("ground") is not None:
        out["ground"] = _vec(data["ground"], "ground", 2, 1, 20000)
    if data.get("note"):
        out["note"] = str(data["note"])[:300]
    return out


def layout_digest(layout: dict | None) -> str:
    """Impressão digital de uma maquete: uma edição só se aplica à versão que o Assistente viu."""
    return hashlib.sha1(json.dumps(layout or {}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def layout_listing(layout: dict | None) -> str:
    """A maquete numerada, um objeto por linha (#n), para o Assistente editar por partes."""
    if not layout:
        return "nenhuma"
    head = {k: v for k, v in layout.items() if k != "objects"}
    lines = [f"{json.dumps(head, ensure_ascii=False)} (base {layout_digest(layout)})"]
    lines += [f"#{n} {json.dumps(o, ensure_ascii=False)}" for n, o in enumerate(layout.get("objects", []))]
    return "\n".join(lines)


def apply_layout_edit(layout: dict | None, edit) -> dict:
    """Aplica uma edição por partes: {"update": [{"i": n, campos a mudar}], "remove": [n, …], "add": [objetos],
    "ground"/"seed" opcionais}. Os números são os da maquete numerada; muda primeiro, tira depois, junta no fim.
    Num "update", um campo a null tira-o (ex.: "label": null)."""
    if not isinstance(edit, dict):
        raise SceneError("A edição tem de ser um objeto JSON.")
    objs = [dict(o) for o in (layout or {}).get("objects", [])]
    n = len(objs)

    def index(raw, where):
        if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw < n:
            raise SceneError(f"{where}: #{raw} não existe na maquete (vai de #0 a #{n - 1}).")
        return raw

    for u in edit.get("update") or []:
        if not isinstance(u, dict):
            raise SceneError("update: cada entrada é {\"i\": n, campos a mudar}.")
        i = index(u.get("i"), "update")
        for k, v in u.items():
            if k == "i":
                continue
            if v is None:
                objs[i].pop(k, None)
            else:
                objs[i][k] = v
    gone = {index(r, "remove") for r in edit.get("remove") or []}
    adds = edit.get("add") or []
    if not isinstance(adds, list):
        raise SceneError("add: tem de ser uma lista de objetos.")
    if not (edit.get("update") or gone or adds or "ground" in edit or "seed" in edit):
        raise SceneError("A edição não muda nada.")
    data = {**(layout or {}), "objects": [o for k, o in enumerate(objs) if k not in gone] + adds}
    for k in ("ground", "seed"):
        if k in edit:
            data[k] = edit[k]
    return validate_set(data)


STAGE_MAX_S = 600  # duração máxima de uma encenação (10 min)


def _actors(actors, codes: dict, tmax: float) -> list[dict]:
    """O elenco, os figurantes e os veículos, cada um com as posições ao longo do tempo (0 a tmax segundos)."""
    if not isinstance(actors, list) or len(actors) > MAX_ACTORS:
        raise SceneError(f"\"actors\" tem de ser uma lista (máximo {MAX_ACTORS}).")
    out = []
    for n, it in enumerate(actors, 1):
        if not isinstance(it, dict) or it.get("kind") not in ACTOR_KINDS:
            raise SceneError(f"Ator {n}: \"kind\" tem de ser um de {', '.join(sorted(ACTOR_KINDS))}.")
        w = f"Ator {n} ({it['kind']})"
        a = {"kind": it["kind"]}
        if it.get("code"):
            asset = codes.get(str(it["code"]).upper())
            if not asset:
                raise SceneError(f"{w}: código desconhecido ({it['code']}).")
            a["code"] = asset.name
        if it.get("label"):
            a["label"] = str(it["label"])[:40]
        if it.get("color"):
            a["color"] = _color(it["color"], "extra_person")
        if it.get("size") is not None:
            a["size"] = _vec(it["size"], w, lo=0.05, hi=200)
        if it["kind"] == "train":
            a["cars"] = int(_num(it.get("cars", 4), w, 1, 12))
        keys = it.get("keys") or []
        if not isinstance(keys, list) or not keys or len(keys) > MAX_KEYS * 3:
            raise SceneError(f"{w}: \"keys\" tem de ter entre 1 e {MAX_KEYS * 3} posições.")
        a["keys"] = []
        for k in keys:
            if not isinstance(k, dict):
                raise SceneError(f"{w}: cada posição é {{\"t\", \"pos\", \"facing\"}}.")
            key = {"t": _num(k.get("t", 0), f"{w} t", 0, tmax), "pos": _vec(k.get("pos"), f"{w} pos")}
            if k.get("facing") is not None:
                key["facing"] = _num(k["facing"], f"{w} facing", -720, 720)
            if k.get("pose") is not None:
                if k["pose"] not in POSES:
                    raise SceneError(f"{w}: pose desconhecida ({k['pose']}); usa uma de {', '.join(sorted(POSES))}.")
                key["pose"] = k["pose"]
            a["keys"].append(key)
        a["keys"].sort(key=lambda k: k["t"])
        out.append(a)
    return out


def _camera(cam, tmax: float = 60) -> list[dict]:
    if not isinstance(cam, list) or not cam or len(cam) > MAX_KEYS:
        raise SceneError(f"\"camera\" tem de ter entre 1 e {MAX_KEYS} posições.")
    out = []
    for n, k in enumerate(cam, 1):
        if not isinstance(k, dict):
            raise SceneError(f"Câmara {n}: {{\"t\", \"pos\", \"look_at\", \"lens\", \"moment\"}}.")
        key = {"t": _num(k.get("t", 0), f"Câmara {n} t", 0, tmax), "pos": _vec(k.get("pos"), f"Câmara {n} pos"),
               "look_at": _vec(k.get("look_at"), f"Câmara {n} look_at"), "lens": _num(k.get("lens", 28), f"Câmara {n} lens", 8, 300)}
        if k.get("moment") or k.get("panel"):  # "panel": cenas antigas (o momento era um painel)
            key["moment"] = True
        out.append(key)
    out.sort(key=lambda k: k["t"])
    return out


def validate_stage(data, session: Session, project_id: int) -> dict:
    """Encenação de uma cena: objetos extra, o elenco e os figurantes ao longo de toda a cena e, opcionalmente, o
    segundo em que começa cada linha do guião ("lines": [{"n": número da linha, "t": segundo}])."""
    if not isinstance(data, dict):
        raise SceneError("A encenação tem de ser um objeto JSON.")
    out: dict = {"duration": _num(data.get("duration", 10), "duration", 1, STAGE_MAX_S)}
    codes = {a.name: a for a in session.exec(select(Asset).where(Asset.project_id == project_id)).all()}
    out["objects"] = _objects(data.get("objects", []), "Encenação")
    out["actors"] = _actors(data.get("actors") or [], codes, STAGE_MAX_S)
    lines = data.get("lines") or []
    if not isinstance(lines, list):
        raise SceneError('"lines" tem de ser uma lista de {"n": número da linha, "t": segundo}.')
    out["lines"] = sorted(({"n": int(_num(x.get("n"), "lines n", 1, 999)), "t": _num(x.get("t"), "lines t", 0, STAGE_MAX_S)}
                           for x in lines if isinstance(x, dict)), key=lambda x: x["t"])
    if data.get("seed") is not None:
        out["seed"] = int(_num(data["seed"], "seed", 0, 1e6))
    return out


def validate_camera(data) -> dict:
    """Câmara de um plano: {"start": segundo da encenação onde o plano começa (opcional), "camera": [posições com "t"
    desde o início do plano]}."""
    if not isinstance(data, dict):
        raise SceneError("A câmara do plano tem de ser um objeto JSON.")
    out = {"camera": _camera(data.get("camera"))}
    if data.get("start") is not None:
        out["start"] = _num(data["start"], "start", 0, STAGE_MAX_S)
    return out


def start_for_lines(scene: Scene, numbers: list[int]) -> float | None:
    """O segundo da encenação onde começa a primeira destas linhas do guião (pela marcação "lines" da encenação)."""
    marks = {x["n"]: x["t"] for x in (scene.stage3d or {}).get("lines", [])}
    times = [marks[n] for n in numbers if n in marks]
    return max(0.0, min(times) - 0.3) if times else None


def set_camera(shot: Shot, clean: dict) -> None:
    """Guarda a câmara validada no plano (o início vai para stage_start)."""
    clean = dict(clean)
    if "start" in clean:
        shot.stage_start = float(clean.pop("start"))
    shot.scene3d = clean


def retime_camera(spec: dict, old_d: float, new_d: float) -> dict:
    """A câmara faz o mesmo percurso noutra duração (a encenação não muda: o plano só mostra mais ou menos dela)."""
    spec = json.loads(json.dumps(spec))
    if old_d and abs(new_d - old_d) > 1e-6:
        k = new_d / old_d
        for key in spec.get("camera", []):
            key["t"] = round(min(60.0, key["t"] * k), 3)
    return spec


def _facing_at(keys: list[dict], t: float):
    withf = [k for k in keys if k.get("facing") is not None]
    if not withf:
        return None
    if len(withf) == 1 or t <= withf[0]["t"]:
        return withf[0]["facing"]
    if t >= withf[-1]["t"]:
        return withf[-1]["facing"]
    for a, b in zip(withf, withf[1:]):
        if t <= b["t"]:
            s = (t - a["t"]) / ((b["t"] - a["t"]) or 1e-6)
            d = ((b["facing"] - a["facing"] + 540) % 360) - 180
            return round(a["facing"] + d * s, 1)
    return withf[-1]["facing"]


def slice_actors(actors: list[dict], start: float, dur: float) -> list[dict]:
    """O troço [start, start + dur] da encenação, com o tempo a contar do início do plano (para o desenho do plano)."""
    out = []
    end = start + dur
    for a in actors:
        keys = a.get("keys") or []
        if not keys:
            continue

        def at(t):
            k = {"t": round(t - start, 3), "pos": _pos_at(keys, t)}
            f = _facing_at(keys, t)
            if f is not None:
                k["facing"] = f
            pose = next((x["pose"] for x in reversed(keys) if x["t"] <= t and x.get("pose")), None)
            if pose:
                k["pose"] = pose
            return k

        inner = [dict(k, t=round(k["t"] - start, 3)) for k in keys if start < k["t"] < end]
        out.append({**a, "keys": [at(start), *inner, at(end)]})
    return out


def _pos_at(keys: list[dict], t: float) -> list[float]:
    if t <= keys[0]["t"] or len(keys) == 1:
        return list(keys[0]["pos"])
    for a, b in zip(keys, keys[1:]):
        if t <= b["t"]:
            s = (t - a["t"]) / ((b["t"] - a["t"]) or 1e-6)
            return [round(x + (y - x) * s, 3) for x, y in zip(a["pos"], b["pos"])]
    return list(keys[-1]["pos"])


@memo
def scene_cast(session: Session, scene: Scene | None) -> dict[str, str]:
    """Elenco da encenação: {código: nome da cor}. Quem está na encenação sem cor escolhida recebe uma livre."""
    if scene is None:
        return {}
    cast = dict(scene.cast3d or {})
    used = set(cast.values())
    kinds = {a.name: a.kind for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    for a in (scene.stage3d or {}).get("actors", []):
        code = a.get("code")
        if code and code not in cast and kinds.get(code) in ("character", "prop", "element"):
            color = next((c for c in CAST_ORDER if c not in used), CAST_ORDER[0])
            cast[code] = color
            used.add(color)
    return cast


def suggest_color(session: Session, scene: Scene, code: str, used: set[str]) -> str:
    """Cor para um asset acabado de juntar à encenação: a que tem noutra cena do projeto (se estiver livre), senão a
    primeira livre — a mesma personagem tende a ter sempre a mesma cor."""
    for other in session.exec(select(Scene).where(Scene.project_id == scene.project_id, Scene.id != scene.id,
                                                  Scene.cast3d.is_not(None))).all():
        color = (other.cast3d or {}).get(code)
        if color and color not in used:
            return color
    return next((c for c in CAST_ORDER if c not in used), CAST_ORDER[0])


def scene_set(session: Session, scene: Scene | None) -> tuple[dict | None, Asset | None]:
    """(maquete em uso, o local dela) — a encenação da cena passa-se na maquete do local escolhido."""
    if scene is None or not scene.set3d_asset_id:
        return None, None
    loc = session.get(Asset, scene.set3d_asset_id)
    if loc is not None and loc.layout3d:
        return loc.layout3d, loc
    return None, None


def shot_window(shot: Shot) -> tuple[float, float]:
    """(início na encenação, duração) do plano."""
    return float(shot.stage_start or 0), float(shot.duration_s or 5)



def source_label(src) -> str:
    if isinstance(src, Asset):
        return f"maquete do local {src.name}"
    return "do próprio plano"


def _actors_out(actors: list[dict], colors: dict, labels: dict) -> list[dict]:
    out = []
    for a in actors:
        color = a.get("color")
        if not color:
            color = colors.get(a.get("code", ""), "extra_car" if a["kind"] in ("car", "vehicle") else "extra_person")
            if a["kind"] == "train":
                color = "yellow"
        out.append(a | {"hex": hex_color(color), "name": labels.get(a.get("code", ""), a.get("label") or "")})
    return out


@memo
def compose(session: Session, project_id: int, shot: Shot | None = None, location: Asset | None = None,
            scene: Scene | None = None) -> dict | None:
    """O que o navegador desenha:
    - um plano: a maquete da cena + o troço da encenação que o plano mostra (tempo a contar do início do plano) + a
      câmara do plano;
    - uma cena: a maquete + a encenação inteira, com a câmara de cada plano (para ver por ela no tempo do plano);
    - um local: só a maquete."""
    labels = {a.name: (a.label or a.name) for a in session.exec(select(Asset).where(Asset.project_id == project_id)).all()}
    if location is not None:
        if not location.layout3d:
            return None
        layers = [{"offset": [0, 0, 0], "rot_z": 0, **location.layout3d}]
        return {"layers": layers, "actors": [], "camera": [], "duration": 0, "legend": _legend(layers, [], {}, labels),
                "palette": NEUTRAL | ACCENT}
    if shot is not None:
        scene = session.get(Scene, shot.scene_id)
    base, _src = scene_set(session, scene)
    stage = (scene.stage3d if scene is not None else None) or {}
    colors = {code: hex_color(c) for code, c in scene_cast(session, scene).items()}
    layers = [{"offset": [0, 0, 0], "rot_z": 0, **base}] if base else []
    if stage.get("objects"):
        layers.append({"offset": [0, 0, 0], "rot_z": 0, "objects": stage["objects"], "seed": stage.get("seed", 11)})
    if shot is not None:
        cam = shot.scene3d or {}
        if not base and not cam:
            return None
        start, dur = shot_window(shot)
        actors = _actors_out(slice_actors(stage.get("actors", []), start, dur), colors, labels)
        return {"layers": layers, "actors": actors, "camera": list(cam.get("camera") or []), "duration": dur,
                "start": start, "legend": _legend(layers, actors, colors, labels), "palette": NEUTRAL | ACCENT}
    if scene is None or (not base and not stage):
        return None
    actors = _actors_out(stage.get("actors", []), colors, labels)
    shots = [{"id": sh.id, "n": sh.number, "start": shot_window(sh)[0], "duration": shot_window(sh)[1],
              "camera": (sh.scene3d or {}).get("camera") or []}
             for sh in session.exec(select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.number)).all()]
    return {"layers": layers, "actors": actors, "camera": [], "duration": float(stage.get("duration") or 0),
            "stage": True, "shots": [x for x in shots if x["camera"]],
            "legend": _legend(layers, actors, colors, labels), "palette": NEUTRAL | ACCENT}


def line_spans(scene: Scene | None) -> dict[int, tuple[float, float]]:
    """Quando acontece cada linha do guião na encenação: {número da linha: (início, fim)}; o fim é o início da linha
    seguinte (ou o fim da encenação)."""
    stage = (scene.stage3d if scene is not None else None) or {}
    marks = sorted(stage.get("lines") or [], key=lambda x: x["t"])
    end_all = float(stage.get("duration") or 0)
    out = {}
    for i, x in enumerate(marks):
        nxt = marks[i + 1]["t"] if i + 1 < len(marks) else max(end_all, x["t"] + 2)
        out[int(x["n"])] = (float(x["t"]), float(nxt))
    return out


def in_window(scene: Scene | None, shot: Shot, line_numbers: list[int]) -> list[int]:
    """As linhas (pelo número) que acontecem dentro do troço do plano. As que a encenação não marca no tempo contam."""
    spans = line_spans(scene)
    if not spans:
        return line_numbers
    start, dur = shot_window(shot)
    end = start + dur
    out = []
    for n in line_numbers:
        if n not in spans:
            out.append(n)
            continue
        a, b = spans[n]
        if a < end - 0.05 and b > start + 0.05:  # a linha começa antes do fim do plano e acaba depois do início
            out.append(n)
    return out


def _occluders(layout: dict | None) -> tuple[list, list]:
    """Paredes e volumes que tapam a vista (caixas altas) e as janelas (por onde se vê), em planta."""
    walls, windows = [], []
    for o in (layout or {}).get("objects", []):
        t = o.get("type")
        if t == "box" and "center" in o:
            c, sz = o["center"], o["size"]
            z0, z1 = c[2] - sz[2] / 2, c[2] + sz[2] / 2
            if sz[2] > 1.2:
                walls.append((c, sz, math.radians(o.get("rot_z", 0)), z0, z1))
        elif t == "piece" and o.get("model") == "window":
            c, sz = o["center"], o.get("size") or list(PIECES["window"][0])
            windows.append((c, sz, math.radians(o.get("rot_z", 0))))
        elif t == "piece" and "center" in o:  # mobília alta (prateleiras, armários): assenta no chão e tapa a vista
            c = o["center"]
            sz = o.get("size") or list((PIECES.get(o.get("model")) or [[0, 0, 0]])[0])
            if len(sz) == 3 and sz[2] > 1.5:
                walls.append((c, sz, math.radians(o.get("rot_z", 0)), c[2], c[2] + sz[2]))
    return walls, windows


def _seg_hits_box(p, q, c, sz, a) -> float | None:
    """Fração do segmento p→q (em planta) onde entra na caixa rodada (centro c, tamanho sz, rotação a); None se não."""
    def local(pt):
        dx, dy = pt[0] - c[0], pt[1] - c[1]
        return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
    (x0, y0), (x1, y1) = local(p), local(q)
    hx, hy = sz[0] / 2, sz[1] / 2
    t0, t1 = 0.0, 1.0
    for d0, d1, lo, hi in ((x0, x1, -hx, hx), (y0, y1, -hy, hy)):
        dd = d1 - d0
        if abs(dd) < 1e-9:
            if d0 < lo or d0 > hi:
                return None
            continue
        ta, tb = (lo - d0) / dd, (hi - d0) / dd
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 > t1:
            return None
    return t0


def _blocked(cam: list[float], target: list[float], walls: list, windows: list) -> bool:
    """A linha de vista da câmara à cabeça de alguém atravessa uma parede ou um volume (sem ser por uma janela)? Com a
    câmara metida dentro de uma parede, não se vê nada."""
    for c, sz, a, z0, z1 in walls:
        if z0 <= cam[2] <= z1 and _seg_hits_box(cam, cam, [c[0], c[1]], [sz[0] - 0.1, sz[1] - 0.1], a) is not None:
            return True
        f = _seg_hits_box(cam, target, c, sz, a)
        if f is None or f >= 0.98 or f <= 0.02:
            continue
        z = cam[2] + (target[2] - cam[2]) * f
        if not (z0 <= z <= z1):
            continue
        hit = (cam[0] + (target[0] - cam[0]) * f, cam[1] + (target[1] - cam[1]) * f)
        if any(_seg_hits_box(hit, hit, wc, [max(wsz[0], 0.3), max(wsz[1], 0.3)], wa) is not None
               and wc[2] <= z <= wc[2] + wsz[2] for wc, wsz, wa in windows):
            continue  # vê-se pela janela
        return True
    return False


def _actor_top(a: dict, p: list[float], t: float) -> tuple[float, float]:
    """(raio em planta, altura do topo) de um ator, para saber se tapa quem está atrás."""
    if a["kind"] in ("character", "person"):
        pose = next((k.get("pose") for k in reversed(a.get("keys") or []) if k["t"] <= t and k.get("pose")), "stand")
        return 0.25, p[2] + {"sit": 1.3, "kneel": 1.3, "bend": 1.35, "lie": 0.4}.get(pose, 1.75)
    if a["kind"] == "car":
        return 0.9, p[2] + 1.5
    return 0.0, 0.0


def _covered(pos: list[float], target: tuple[list[float], float], others: list[tuple[list[float], float, float]]) -> bool:
    """Outra pessoa (ou um carro) entre a câmara e esta pessoa tapa-a quase toda? target = (posição, altura da
    cabeça); others = [(posição, raio, topo)]. Por cima do ombro, quem está perto e à frente esconde quem está atrás."""
    tp, head = target
    dx, dy = tp[0] - pos[0], tp[1] - pos[1]
    dt = math.hypot(dx, dy) or 1e-6
    for op, radius, top in others:
        if radius <= 0:
            continue
        ox, oy = op[0] - pos[0], op[1] - pos[1]
        do = (ox * dx + oy * dy) / dt  # profundidade ao longo da linha de vista
        if do < 0.3 or do > dt - 0.4:
            continue
        lateral = abs(ox * dy - oy * dx) / dt
        sight = pos[2] + (head - pos[2]) * (do / dt)  # altura da linha de vista à profundidade de quem tapa
        if top < sight - 0.05:
            continue
        # quem tapa ocupa um ângulo maior do que o alvo e cobre o centro dele
        if math.atan2(lateral, do) + math.atan2(0.2, dt) < math.atan2(radius, do):
            return True
    return False


def _inside_outside(layout: dict, cam: list[float], target: list[float]) -> bool:
    """A câmara e a pessoa estão uma dentro (divisão com teto) e a outra fora (ao ar livre)?"""
    from app import location3d

    def outdoor(pt):
        name = location3d.zone_at(layout, pt[0], pt[1], pt[2] + 0.5)
        info = location3d.zone_info(layout, name) if name else None
        return None if info is None else bool(info.get("outdoor"))

    a, b = outdoor(cam), outdoor(target)
    return a is not None and b is not None and a != b


MIN_SCREEN = 0.06  # altura mínima no quadro (fração) para alguém contar como estando na imagem


def visible_actors(session: Session, shot: Shot) -> list[tuple[dict, float]]:
    """Os atores da encenação (elenco, figurantes, veículos) que a câmara do plano vê em algum momento do troço, com a
    distância mais curta a que os vê."""
    scene = session.get(Scene, shot.scene_id)
    cam = (shot.scene3d or {}).get("camera") or []
    stage = (scene.stage3d if scene else None) or {}
    if not cam or not stage.get("actors"):
        return []
    start, dur = shot_window(shot)
    base, _loc = scene_set(session, scene)
    walls, windows = _occluders(base)
    walls += _occluders({"objects": stage.get("objects", [])})[0]
    best: dict[int, float] = {}
    steps = max(2, int(dur / 0.5) + 1)
    for i in range(steps):
        t = dur * i / (steps - 1)
        c = _cam_at(cam, t)
        if c is None:
            continue
        pos, look, lens = c
        fx, fy = look[0] - pos[0], look[1] - pos[1]
        norm = math.hypot(fx, fy) or 1e-6
        half = math.atan(18 / lens) + math.radians(6)
        placed = {ai: _pos_at(a["keys"], start + t) for ai, a in enumerate(stage["actors"]) if a.get("keys")}
        bodies = {ai: (placed[ai], *_actor_top(stage["actors"][ai], placed[ai], start + t)) for ai in placed}
        for ai, a in enumerate(stage["actors"]):
            if ai not in placed:
                continue
            p = placed[ai]
            dx, dy = p[0] - pos[0], p[1] - pos[1]
            dist = math.hypot(dx, dy)
            if dist < 0.3 or dist > 60:
                continue
            ang = math.acos(max(-1.0, min(1.0, (dx * fx + dy * fy) / (dist * norm))))
            h = 1.5 if a["kind"] in ("character", "person") else 1.0
            tall = (a.get("size") or [0, 0, 1.75 if a["kind"] in ("character", "person") else 1.5])[2]
            if tall * lens / (20.25 * dist) < MIN_SCREEN:
                continue  # um ponto ao fundo: não conta como estar na imagem
            if ang > half or _blocked(pos, [p[0], p[1], p[2] + h], walls, windows):
                continue
            if _covered(pos, (p, p[2] + h), [b for oi, b in bodies.items() if oi != ai]):
                continue
            if dist > 6 and base and _inside_outside(base, pos, p):
                continue
            best[ai] = min(best.get(ai, 1e9), dist)
    return [(stage["actors"][ai], d) for ai, d in sorted(best.items())]


# Testado no Seedance 2.5 (5 de outubro de 2026): com 1 figura além do elenco o take sai certo; com 9 o modelo copia
# as figuras do previs (manequins, cores), estica o take e repete a ação.
MAX_GUIDE_FIGURES = 3


def crowd(session: Session, shot: Shot) -> dict | None:
    """As figuras que a câmara do plano vê além das personagens principais (figurantes, elementos do mundo, veículos),
    quando passam de MAX_GUIDE_FIGURES: {"n", "detail"}; None quando estão dentro do limite."""
    scene = session.get(Scene, shot.scene_id)
    kinds = {a.name: (a.kind, a.label or a.name) for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    people = vehicles = 0
    elements: dict[str, int] = {}
    for a, _d in visible_actors(session, shot):
        code = a.get("code")
        if code and kinds.get(code, ("",))[0] in ("character", "prop"):
            continue
        if code:
            label = kinds.get(code, ("", code))[1]
            elements[label] = elements.get(label, 0) + 1
        elif a["kind"] == "person":
            people += 1
        else:
            vehicles += 1
    n = people + vehicles + sum(elements.values())
    if n <= MAX_GUIDE_FIGURES:
        return None
    parts = ([f"{people} figurante(s)"] if people else []) + [f"{v} {k}" for k, v in elements.items()] \
        + ([f"{vehicles} veículo(s)"] if vehicles else [])
    return {"n": n, "detail": ", ".join(parts)}


def extras_note(session: Session, shot: Shot) -> tuple[int, str]:
    """(quantas pessoas figurantes a câmara vê, um resumo para o escritor). Não se listam um a um (numa multidão o
    prompt ficava gigante): só quantos há e de que tipo. Os elementos do mundo (robots, táxis autónomos…) têm uma cor
    própria na maquete e a folha deles segue: vão pelo código e pela cor, uma vez por elemento."""
    scene = session.get(Scene, shot.scene_id)
    cast = scene_cast(session, scene)
    kinds = {a.name: a.kind for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    people = vehicles = 0
    elements: dict[str, int] = {}
    for a, _d in visible_actors(session, shot):
        code = a.get("code")
        if code and kinds.get(code) in ("character", "prop"):
            continue
        if code:
            elements[code] = elements.get(code, 0) + 1
        elif a["kind"] == "person":
            people += 1
        else:
            vehicles += 1
    parts = []
    for code, n in elements.items():
        color = CAST_EN.get(cast.get(code, ""), "coloured")
        parts.append(f"@{code}: {n} no plano — no vídeo-guia, as figuras/volumes {color} (\"the {color} figures in "
                     f"@Video1 are {'' if n > 1 else 'a '}… exactly as in @{code}\")")
    if people:
        parts.append(f"{people} figurante(s) a pé — no vídeo-guia, as figuras cinzento-claro (marcam pessoas reais, não manequins)")
    if vehicles:
        parts.append(f"{vehicles} veículo(s) — no vídeo-guia, os volumes cinzentos com rodas")
    return people, "; ".join(parts)


@memo
def in_frame(session: Session, shot: Shot) -> list[str]:
    """Os códigos (elenco, adereços e elementos do mundo) que a câmara do plano vê em algum momento do troço, pela ordem
    do elenco da cena: são as folhas destes que seguem para o vídeo."""
    scene = session.get(Scene, shot.scene_id)
    cast = list(scene_cast(session, scene))
    seen = {a["code"] for a, _d in visible_actors(session, shot) if a.get("code")}
    return [c for c in cast if c in seen] + sorted(seen - set(cast))


def frame_notes(session: Session, shot: Shot) -> list[str]:
    """Para o escritor de prompts: onde está cada pessoa do elenco em relação à câmara a meio do plano (perto ou longe,
    de frente ou de costas — por cima do ombro, quem está de costas e perto fica em primeiro plano)."""
    scene = session.get(Scene, shot.scene_id)
    cam = (shot.scene3d or {}).get("camera") or []
    stage = (scene.stage3d if scene else None) or {}
    if not cam:
        return []
    start, dur = shot_window(shot)
    c = _cam_at(cam, dur / 2)
    pos, look, _lens = c
    out = []
    kinds = {x.name: x.kind for x in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    codes = {c for c in in_frame(session, shot) if kinds.get(c) in ("character", "prop")}
    for a in stage.get("actors", []):
        code = a.get("code")
        if code not in codes:
            continue
        p = _pos_at(a["keys"], start + dur / 2)
        dist = math.hypot(p[0] - pos[0], p[1] - pos[1])
        f = _facing_at(a["keys"], start + dur / 2)
        side = ""
        if f is not None:  # para onde a pessoa olha vs. de onde vem a câmara
            face = (-math.sin(math.radians(f)), math.cos(math.radians(f)))
            to_cam = ((pos[0] - p[0]) / (dist or 1e-6), (pos[1] - p[1]) / (dist or 1e-6))
            dot = face[0] * to_cam[0] + face[1] * to_cam[1]
            side = "de frente para a câmara" if dot > 0.5 else "de costas para a câmara" if dot < -0.5 else "de perfil"
        where = "em primeiro plano" if dist < 2.2 else "no meio" if dist < 6 else "ao fundo"
        out.append(f"{code}: {where} ({dist:.1f} m){', ' + side if side else ''}"
                   + (" — por cima do ombro: só ombro e nuca, desfocado" if dist < 2.2 and side == "de costas para a câmara" else ""))
    return out


def moment_facts(session: Session, shot: Shot) -> list[dict]:
    """O que o vídeo-guia mostra de cada pessoa do elenco em cada momento da câmara: [{"n", "t", "people": [{"code",
    "label", "face" ("front" | "back" | "profile" | None), "side", "move", "dist"}]}]."""
    from app import moments

    scene = session.get(Scene, shot.scene_id)
    stage = (scene.stage3d if scene else None) or {}
    cam = (shot.scene3d or {}).get("camera") or []
    if not cam or not stage.get("actors"):
        return []
    labels = {a.name: (a.label or a.name, a.kind) for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    base, _loc = scene_set(session, scene)
    walls, windows = _occluders(base)
    start, dur = shot_window(shot)
    out = []
    for m in moments.keys(shot):
        t = min(float(m["t"]), dur)
        pos, look, lens = _cam_at(cam, t)
        fx, fy = look[0] - pos[0], look[1] - pos[1]
        n = math.hypot(fx, fy) or 1e-6
        fx, fy = fx / n, fy / n
        half = math.atan(18 / lens)
        people = []
        bodies = {i: (_pos_at(b["keys"], start + t), *_actor_top(b, _pos_at(b["keys"], start + t), start + t))
                  for i, b in enumerate(stage["actors"]) if b.get("keys")}
        for ai, a in enumerate(stage["actors"]):
            code = a.get("code")
            if not code or labels.get(code, ("", ""))[1] not in ("character", "prop") or not a.get("keys"):
                continue
            p = _pos_at(a["keys"], start + t)
            dx, dy = p[0] - pos[0], p[1] - pos[1]
            dist = math.hypot(dx, dy)
            if dist < 0.3:
                continue
            ang = math.atan2(fx * dy - fy * dx, fx * dx + fy * dy)  # + = à esquerda do ecrã
            if abs(ang) > half + math.radians(4) or _blocked(pos, [p[0], p[1], p[2] + 1.5], walls, windows):
                continue
            if _covered(pos, (p, p[2] + 1.5), [b for oi, b in bodies.items() if oi != ai]):
                continue  # tapado por quem está à frente (ex.: por cima do ombro)
            if dist > 6 and base and _inside_outside(base, pos, p):
                continue  # dentro, visto da rua ao longe (ou o contrário): mal se vê
            side = "ao centro" if abs(ang) < half / 3 else ("à esquerda" if ang > 0 else "à direita")
            f = _facing_at(a["keys"], start + t)
            face = None
            if f is not None:
                fv = (-math.sin(math.radians(f)), math.cos(math.radians(f)))
                dot = fv[0] * (-dx / dist) + fv[1] * (-dy / dist)
                face = "front" if dot > 0.5 else "back" if dot < -0.5 else "profile"
            p0, p1 = _pos_at(a["keys"], start + max(0.0, t - 0.3)), _pos_at(a["keys"], start + t + 0.3)
            vx, vy = (p1[0] - p0[0]) / 0.6, (p1[1] - p0[1]) / 0.6
            vf, vr = vx * fx + vy * fy, vx * fy - vy * fx
            if math.hypot(vx, vy) < 0.3:
                move = "parado"
            elif abs(vf) >= abs(vr):
                move = "a afastar-se da câmara" if vf > 0 else "a vir na direção da câmara"
            else:
                move = "a andar para a direita do ecrã" if vr > 0 else "a andar para a esquerda do ecrã"
            people.append({"code": code, "label": labels[code][0], "face": face, "side": side, "move": move,
                           "dist": round(dist, 1)})
        out.append({"n": m["n"], "t": t, "people": people})
    return out


FACE_PT = {"front": "de frente para a câmara", "back": "de costas para a câmara", "profile": "de perfil"}


def moment_notes(session: Session, shot: Shot) -> list[str]:
    """Para o escritor: em cada momento da câmara, o que o vídeo-guia mostra de cada pessoa do elenco — de frente ou de
    costas, de que lado do ecrã e para onde anda. O prompt descreve a ação assim e nunca ao contrário (se o texto
    contradiz o vídeo-guia, o modelo segue o texto)."""
    out = []
    for m in moment_facts(session, shot):
        bits = [f"{x['label']} {', '.join(v for v in (FACE_PT.get(x['face']), x['side'], x['move']) if v)} ({x['dist']:.1f} m)"
                for x in m["people"]]
        out.append(f"momento {m['n']} ({m['t']:g} s): " + ("; ".join(bits) if bits else "ninguém do elenco à vista"))
    return out


def turn_warnings(shot: Shot) -> list[str]:
    """Passagens da câmara que rodam ou andam depressa demais: o modelo de vídeo lê-as como um corte."""
    cam = (shot.scene3d or {}).get("camera") or []
    out = []
    for a, b in zip(cam, cam[1:]):
        dt = (b["t"] - a["t"]) or 1e-6
        da = [a["look_at"][i] - a["pos"][i] for i in range(2)]
        db = [b["look_at"][i] - b["pos"][i] for i in range(2)]
        turn = abs(math.degrees(math.atan2(da[0] * db[1] - da[1] * db[0], da[0] * db[0] + da[1] * db[1])))
        move = math.dist(a["pos"], b["pos"])
        if turn / dt > 45 or move / dt > 3:
            out.append(f"de {a['t']:g} s a {b['t']:g} s a câmara roda {turn:.0f}°" + (f" e anda {move:.1f} m" if move > 0.5 else "")
                       + f" em {dt:g} s")
    return out


def _cam_at(cam: list[dict], t: float):
    if not cam:
        return None
    if t <= cam[0]["t"] or len(cam) == 1:
        k = cam[0]
        return k["pos"], k["look_at"], k["lens"]
    for a, b in zip(cam, cam[1:]):
        if t <= b["t"]:
            s = (t - a["t"]) / ((b["t"] - a["t"]) or 1e-6)
            mix = lambda u, v: [x + (y - x) * s for x, y in zip(u, v)]  # noqa: E731
            return mix(a["pos"], b["pos"]), mix(a["look_at"], b["look_at"]), a["lens"] + (b["lens"] - a["lens"]) * s
    k = cam[-1]
    return k["pos"], k["look_at"], k["lens"]


def _legend(layers, actors, colors, labels) -> list[dict]:
    """O que cada cor forte é (para o utilizador e para o escritor de prompts)."""
    out, seen = [], set()
    for a in actors:
        if a.get("code") and a["code"] not in seen:
            seen.add(a["code"])
            out.append({"hex": a["hex"], "text": f"{a['name']} ({a['code']})"})
        elif a["kind"] == "train" and "train" not in seen:
            seen.add("train")
            out.append({"hex": a["hex"], "text": a.get("label") or "comboio"})
    for layer in layers:
        for o in layer.get("objects", []):
            if o.get("label") and o["label"] not in seen and o["color"] in ACCENT:
                seen.add(o["label"])
                out.append({"hex": hex_color(o["color"]), "text": o["label"]})
    return out


RENDER_VERSION = "5"  # muda quando o desenho no navegador muda (ex.: tetos): os renders antigos ficam desatualizados


def preview_hash(spec: dict | None) -> str | None:
    """Impressão digital do que o navegador desenha: as vistas para o Assistente só valem para esta versão."""
    if not spec:
        return None
    return hashlib.sha1((RENDER_VERSION + json.dumps(spec, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:12]


PREVIEW_NAMES = {"top": "vista de cima (o caminho da câmara a rosa, as divisões com nome)",
                 **{f"inside{i}": f"vista de cima por dentro, piso {i + 1} a contar de baixo (cortada 2,6 m acima do chão "
                                  "desse piso: sem tetos, telhados nem andares de cima)" for i in range(6)},
                 "a": "vista A a partir do centro, à altura dos olhos", "b": "vista B (direita) a partir do centro",
                 "c": "vista C (trás) a partir do centro", "d": "vista D (esquerda) a partir do centro"}


def previews(session: Session, digest: str | None, **target) -> list:
    """Vistas da maquete guardadas pelo navegador para esta versão (caminho, legenda) — para o Assistente."""
    from app.media import media_root
    from app.models import MediaFile

    if not digest:
        return []
    stmt = select(MediaFile).where(MediaFile.role == "preview3d", MediaFile.status == "approved")
    for field, value in target.items():
        stmt = stmt.where(getattr(MediaFile, field) == value)
    out = []
    for m in session.exec(stmt.order_by(MediaFile.id)).all():
        match = re.search(r"preview_([0-9a-f]{12})_([a-z0-9]+)\.png$", m.path)
        if match and match.group(1) == digest and (media_root() / m.path).is_file():
            out.append((media_root() / m.path, "maquete 3D: " + PREVIEW_NAMES.get(match.group(2), match.group(2))))
    return out


def preview_have(session: Session, **target) -> str:
    from app.models import MediaFile

    stmt = select(MediaFile).where(MediaFile.role == "preview3d", MediaFile.status == "approved")
    for field, value in target.items():
        stmt = stmt.where(getattr(MediaFile, field) == value)
    m = session.exec(stmt.order_by(MediaFile.id.desc())).first()
    match = re.search(r"preview_([0-9a-f]{12})_", m.path) if m else None
    return match.group(1) if match else ""


async def save_previews(request, session: Session, project, folder: str, **target) -> None:
    """Guarda as vistas enviadas pelo navegador (substitui as anteriores deste alvo)."""
    from pathlib import PurePosixPath

    from app.media import MAX_IMAGE_BYTES, delete_file, image_extension, read_limited, safe_segment, write_file
    from app.models import MediaFile

    form = await request.form()
    digest = str(form.get("hash") or "")
    if not re.fullmatch(r"[0-9a-f]{12}", digest):
        return
    stmt = select(MediaFile).where(MediaFile.role == "preview3d")
    for field, value in target.items():
        stmt = stmt.where(getattr(MediaFile, field) == value)
    for old in session.exec(stmt).all():
        delete_file(old.path)
        session.delete(old)
    base = PurePosixPath(safe_segment(project.prefix), *[safe_segment(p) for p in folder.split("/")])
    for name in PREVIEW_NAMES:
        upload = form.get(name)
        if upload is None or not hasattr(upload, "read"):
            continue
        data = await read_limited(upload, MAX_IMAGE_BYTES)
        image_extension(data, f"{name}.png")
        rel = base / f"preview_{digest}_{name}.png"
        write_file(str(rel), data)
        session.add(MediaFile(project_id=project.id, path=str(rel), kind="image", role="preview3d", status="approved",
                              source="maquete", **target))
    session.commit()


SEAT_H = {"chair": 0.46, "bar_stool": 0.75, "bench": 0.45, "sofa": 0.45, "armchair": 0.45, "booth": 0.45, "bed": 0.55}


def staging_plan(layout: dict | None) -> str:
    """A planta da maquete para encenar: as divisões, a mobília e as peças (onde sentar, encostar, contornar), as
    paredes e as portas — com posições, rotações e a divisão de cada coisa. Sem isto, o Assistente adivinha onde estão
    os bancos e as mesas."""
    from app import location3d

    if not layout:
        return "sem maquete."
    r = lambda v: round(float(v), 2)  # noqa: E731
    lines, walls, other = [], [], {}
    for z in location3d.zones(layout):
        lines.append(f"zona «{z['name']}»: de [{r(z['from'][0])}, {r(z['from'][1])}] a [{r(z['to'][0])}, {r(z['to'][1])}], "
                     f"chão a {r(z['z'])} m" + (" (ao ar livre)" if z["outdoor"] else ""))
    for o in layout.get("objects", []):
        t = o.get("type")
        if t == "piece":
            c, size = o["center"], o.get("size") or list(PIECES[o["model"]][0])
            where = location3d.zone_at(layout, c[0], c[1], c[2] + 0.5) or "—"
            seat = ""
            if o["model"] in SEAT_H:
                base_h = PIECES[o["model"]][0][2]
                seat = f", assento a {r(c[2] + SEAT_H[o['model']] * (size[2] / base_h if base_h else 1))} m"
            lines.append(f"{o['model']}{' «' + o['label'] + '»' if o.get('label') else ''} em [{r(c[0])}, {r(c[1])}, {r(c[2])}] "
                         f"rot {r(o.get('rot_z', 0))}° ({r(size[0])}×{r(size[1])}×{r(size[2])} m{seat}) · zona {where}")
        elif t == "box" and o.get("label"):
            c, sz = o["center"], o["size"]
            lines.append(f"«{o['label']}» (caixa) em [{r(c[0])}, {r(c[1])}, {r(c[2])}] tamanho {[r(x) for x in sz]} rot {r(o.get('rot_z', 0))}°")
        elif t == "box" and min(o["size"][0], o["size"][1]) < 0.5 and o["size"][2] > 1.5:  # parede: de ponta a ponta
            c, sz, a = o["center"], o["size"], math.radians(o.get("rot_z", 0))
            half = (sz[0] if sz[0] >= sz[1] else sz[1]) / 2
            dx, dy = (math.cos(a) * half, math.sin(a) * half) if sz[0] >= sz[1] else (-math.sin(a) * half, math.cos(a) * half)
            walls.append(f"[{r(c[0] - dx)}, {r(c[1] - dy)}]→[{r(c[0] + dx)}, {r(c[1] + dy)}] (chão {r(c[2] - sz[2] / 2)} m)")
        elif t != "zone":
            other[t] = other.get(t, 0) + 1
    out = "\n".join(f"- {x}" for x in lines)
    if walls:
        out += "\n- paredes (não atravessar; os vãos são as portas acima): " + "; ".join(walls)
    if other:
        out += "\n- resto do cenário (prédios, chão, lajes…): " + ", ".join(f"{n} {k}" for k, n in other.items())
    return out


def set_summary(data: dict | None) -> str:
    """Resumo de um cenário para o Assistente (extensão e marcos com nome)."""
    if not data:
        return "sem cenário 3D."
    objs = data.get("objects", [])
    xs, ys = [], []
    for o in objs:
        for key in ("center", "from", "to"):
            if key in o:
                xs.append(o[key][0])
                ys.append(o[key][1])
    marks = [f"{o['label']} ({o['type']} em {o.get('center') or o.get('from')})" for o in objs if o.get("label")]
    ext = f"x {min(xs):.0f}..{max(xs):.0f}, y {min(ys):.0f}..{max(ys):.0f}" if xs else "vazio"
    return f"{len(objs)} objeto(s), extensão {ext}" + (f"; marcos: {'; '.join(marks)}" if marks else "")


SET_EXAMPLE = (  # exemplo completo para o Assistente: uma cidade com avenida, monorail, rio e a rua de um bar
    '{"seed": 7, "ground": [3000, 3000], "objects": ['
    '{"type": "box", "center": [9, -300, 0.02], "size": [32, 860, 0.05], "color": "road"}, '
    '{"type": "blocks", "from": [-420, -200], "to": [-8, 130], "lot": [28, 24], "height": [14, 26], "color": "building"}, '
    '{"type": "blocks", "from": [26, -200], "to": [320, 130], "lot": [28, 24], "height": [14, 26], "color": "building"}, '
    '{"type": "blocks", "from": [-420, -760], "to": [320, -200], "lot": [60, 45], "height": [7, 12], "color": "low", "gap": 6}, '
    '{"type": "blocks", "from": [-300, 460], "to": [320, 900], "lot": [45, 45], "height": [60, 150], "color": "tall", "gap": 12}, '
    '{"type": "box", "center": [380, 0, 0.05], "size": [70, 1800, 0.1], "color": "water"}, '
    '{"type": "pillars", "from": [0, -900], "to": [0, 900], "count": 61, "radius": 1.1, "height": 13.3, "color": "structure"}, '
    '{"type": "beam", "from": [0, -900, 14], "to": [0, 900, 14], "width": 3, "thickness": 1.5, "color": "structure"}, '
    '{"type": "box", "center": [9, 145, 4.5], "size": [34, 30, 9], "color": "building_dark", "label": "bar"}, '
    '{"type": "box", "center": [10, 129.8, 1.7], "size": [3.2, 0.4, 3.4], "color": "red", "label": "porta do bar"}, '
    '{"type": "box", "center": [10, 129.7, 4.4], "size": [8, 0.3, 1.3], "color": "cyan", "label": "letreiro do bar"}, '
    '{"type": "blocks", "from": [-7, 40], "to": [-3, 124], "lot": [4, 6], "height": [2.2, 2.8], "color": "wood", "gap": 1.2}]}'
)
CAMERA_EXAMPLE = (  # por cima do ombro de A (de costas, à esquerda), a olhar para B, que fala
    '{"start": 6, "camera": [{"t": 0, "pos": [-1.1, -1.6, 1.55], "look_at": [0.6, 1.4, 1.6], "lens": 40, "moment": true}, '
    '{"t": 4, "pos": [-1.0, -1.4, 1.55], "look_at": [0.6, 1.4, 1.6], "lens": 45, "moment": true}]}'
)
BAR_EXAMPLE = (  # exemplo de planta com divisões, peças e portas: um bar com sala, cozinha e arrumos
    '''{"seed": 3, "ground": [40, 40], "objects": [{"type": "zone", "name": "sala", "from": [-7, -6], "to": [7, 4]}, {"type": "zone", "name": "cozinha", "from": [-7, 4], "to": [1, 9]}, {"type": "zone", "name": "arrumos", "from": [1, 4], "to": [7, 9]}, {"type": "box", "center": [0, -6, 1.4], "size": [14, 0.15, 2.8], "color": "wall", "plain": true}, {"type": "box", "center": [0, 9, 1.4], "size": [14, 0.15, 2.8], "color": "wall", "plain": true}, {"type": "box", "center": [-7, 1.5, 1.4], "size": [0.15, 15, 2.8], "color": "wall", "plain": true}, {"type": "box", "center": [7, 1.5, 1.4], "size": [0.15, 15, 2.8], "color": "wall", "plain": true}, {"type": "box", "center": [-4.5, 4, 1.4], "size": [5, 0.15, 2.8], "color": "wall", "plain": true}, {"type": "box", "center": [3.5, 4, 1.4], "size": [7, 0.15, 2.8], "color": "wall", "plain": true}, {"type": "box", "center": [1, 6.5, 1.4], "size": [0.15, 5, 2.8], "color": "wall", "plain": true}, {"type": "piece", "model": "door", "center": [0, -6, 0], "rot_z": 180, "color": "red", "label": "porta do bar"}, {"type": "piece", "model": "door", "center": [-1.5, 4, 0]}, {"type": "piece", "model": "door", "center": [1, 7.5, 0], "rot_z": 90}, {"type": "piece", "model": "counter", "center": [0, 2.6, 0], "size": [6, 0.7, 1.1], "rot_z": 180, "label": "balcão"}, {"type": "piece", "model": "shelf", "center": [0, 3.75, 0], "size": [5, 0.4, 2.0], "rot_z": 180}, {"type": "piece", "model": "bar_stool", "center": [-2, 1.7, 0]}, {"type": "piece", "model": "bar_stool", "center": [-0.7, 1.7, 0]}, {"type": "piece", "model": "bar_stool", "center": [0.7, 1.7, 0]}, {"type": "piece", "model": "bar_stool", "center": [2, 1.7, 0]}, {"type": "piece", "model": "booth", "center": [-6.4, -3, 0], "rot_z": -90}, {"type": "piece", "model": "table_rect", "center": [-5.6, -3, 0], "size": [0.8, 1.4, 0.75], "label": "mesa do canto"}, {"type": "piece", "model": "table_round", "center": [3, -3, 0]}, {"type": "piece", "model": "chair", "center": [3, -2.3, 0], "rot_z": 180}, {"type": "piece", "model": "chair", "center": [3, -3.7, 0]}, {"type": "piece", "model": "window", "center": [4, -6, 1.0], "size": [2.4, 0.12, 1.4]}, {"type": "piece", "model": "ceiling_lamp", "center": [0, -1, 2.0]}, {"type": "piece", "model": "kitchen_counter", "center": [-4, 8.6, 0], "rot_z": 180}, {"type": "piece", "model": "stove", "center": [-6.4, 6.5, 0], "rot_z": -90}, {"type": "piece", "model": "fridge", "center": [-0.4, 8.5, 0], "rot_z": 180}, {"type": "piece", "model": "crate", "center": [5.5, 8, 0]}, {"type": "piece", "model": "crate", "center": [5.5, 8, 0.8]}, {"type": "piece", "model": "shelf", "center": [4, 8.7, 0], "rot_z": 180}]}'''
)
LAYOUT_HELP_PREFIX = (
    'value = JSON da maquete 3D deste local (substitui a atual): o espaço fixo do local, para todos os planos que se '
    'passam nele ficarem com a mesma geografia (porta, balcão, mesas sempre no mesmo sítio). Centro do espaço principal '
    'na origem [0,0], Y = direção da vista A (a da imagem geral), X para a direita. Pode ser a planta completa (ex.: o bar '
    'com a sala, a cozinha e as arrumações na traseira; um apartamento com cozinha, sala e quartos), com uma "zone" por '
    'divisão. Pode incluir o exterior imediato quando há cenas à porta ou nas traseiras (ex.: "rua da frente", '
    '"traseiras"): zonas com "ceiling": 0 coladas à fachada, com a fachada do edifício (paredes com as MESMAS portas e '
    'janelas do interior, vistas de fora), o pavimento, os passeios e os prédios em frente; as zonas ao ar livre cobrem '
    'TODO o chão exterior modelado onde pode haver câmara (se a rua cresce, a zona cresce; um cruzamento ou uma rua '
    'transversal podem ser zonas próprias); o que só existe na rua e '
    'não depende do edifício é outro local. Exemplo de planta (bar com sala, cozinha e arrumos): ' + BAR_EXAMPLE + ' Mesmo formato e regras do cenário: ')
LAYOUT_EDIT_HELP = (
    'value = JSON com SÓ o que muda na maquete (a maquete numerada está em DADOS): {"update": [{"i": 12, "center": '
    '[1, 2, 0], "label": "mesa da janela"}], "remove": [40, 41], "add": [objetos novos, no formato da maquete], '
    '"ground": [largura, profundidade] opcional}. "update" muda só os campos indicados do objeto #i (um campo a null '
    'tira-o); "remove" tira objetos pelo número; "add" junta objetos novos (zonas incluídas). Os números são sempre os '
    'da maquete numerada atual. É a forma por omissão de mudar a maquete: muito mais rápida, e o resto fica '
    'exatamente igual. Uma só edição por resposta, com todas as mudanças. Mesmas regras e formato de objetos da '
    'maquete completa (abaixo). Mesmo para mudanças grandes (um piso novo, um prédio inteiro), junta só esses objetos com "add" '
    'e muda ou tira só os que mudam: nunca voltes a escrever o que fica igual. A reescrita completa só existe quando o '
    'utilizador pede para refazer a maquete de raiz.')
LAYOUT_REWRITE_NOTE = (
    'Só quando o utilizador pede para refazer a maquete de raiz; tudo o resto (acrescentar, tirar, ajustar, mesmo '
    'muito) é com «Alterar por partes». ')
SET_HELP = (
    'value = JSON da maquete 3D do local (substitui a atual): tudo o que os planos neste local vêem, sem pessoas nem '
    'veículos em movimento, com a geografia da descrição, das notas e das fotos de referência. '
    'Coordenadas em metros: Z para cima, Y para a frente, X para a direita. Formato: {"seed": n, "ground": [largura, '
    'profundidade], "objects": [...]} com objetos: box {center, size [x,y,z], rot_z, color, label}; cylinder {center, '
    'radius, height, color, label}; blocks (quarteirões de prédios com alturas ao acaso) {from [x,y], to [x,y], lot '
    '[largura, profundidade], height [min, max], gap, color, roof_bits 0–3 (equipamento de telhado: 0 por omissão; só quando o utilizador o pedir)}; pillars {from [x,y], to [x,y], count, '
    'radius, height, color}; beam (viga/via entre dois pontos) {from [x,y,z], to [x,y,z], width, thickness, color}; '
    'prism (prisma triangular) {center [x,y,z do meio], size [x,y,z], rot_z, shape, color, label} com shape "gable" '
    '(secção em triângulo com o pico ao meio de X, a cumeeira ao longo de Y: telhado de duas águas, empena, frontão) '
    'ou "ramp" (sobe de -X, onde tem altura 0, para +X, onde tem a altura toda: rampa, telhado de uma água, talude, '
    'degrau inclinado); extrude (qualquer polígono em planta, levantado) {points [[x,y], …] (3 a 64, pela ordem à '
    'volta), z (base), height, color, label, plain}: prédios triangulares ou de esquina em ângulo, terrenos, praças, '
    'canteiros e muros em diagonal, passeios irregulares — os prédios (cores building*) ganham janelas como as caixas; '
    'piece (peça pronta, desenhada com a forma certa: tampo e pernas, costas, rodas…) {model, center [x,y,z do chão], '
    'rot_z (0 = frente para +Y, 90 = para -X, 180 = para -Y, -90 = para +X), size [largura, profundidade, altura] '
    'opcional, color opcional, label} com model um de: ' + ", ".join(PIECES)
    + ' (a frente +Y é o lado de quem a usa: o assento da cadeira, a porta do frigorífico, a frente do carro; uma '
    'cadeira virada para a mesa tem rot_z a apontar para ela); zone (divisão com nome) {name, from [x,y], to [x,y], z '
    '(altura do chão desse piso, 0 no rés-do-chão), ceiling}. '
    'Usa SEMPRE peças para mobília, portas, janelas, escadas, candeeiros, árvores e carros estacionados; caixas só '
    'para paredes, chão, muros, prédios e o que não houver na biblioteca. Os prédios (cores building*, tall, low) '
    'ganham janelas desenhadas sozinhos; "plain": true numa caixa tira-as (paredes interiores, muros). '
    'Cores do cenário (cinzentos e tons de material): ' + ", ".join(NEUTRAL) + '. Cores fortes só para marcos que '
    'interessam à história (ex.: a porta do bar, o letreiro), sempre com "label": ' + ", ".join(LANDMARK_COLORS)
    + ' (as outras cores fortes são do elenco). Escala real (porta 0,9 m × 2,1 m, pé-direito 2,8–3 m, mesa 0,75 m, rua '
    '8–15 m). Cenário a sério: cobre tudo o que a câmara vai ver, à volta e ao fundo, não só o sítio da ação. Plantas '
    'com várias divisões (bar com sala, cozinha e arrumos; apartamento): paredes exteriores e interiores em caixas '
    '"plain" de 0,15 m de espessura e 2,8 m de altura, com vãos onde ficam peças "door", e uma "zone" por divisão (as divisões ganham teto a 2,9 m; "ceiling": 0 numa divisão ao ar livre, outro valor para '
    'pés-direitos altos). Vários andares (um apartamento no 2.º andar, a escada do prédio): cada piso tem as suas zones '
    'com "z" = altura do chão desse piso (3 m por andar: 0, 3, 6…), uma laje (caixa "plain" de 0,2 m) por baixo de '
    'cada piso acima do chão, e as paredes e peças desse piso com o center z a partir dessa altura (peças: z do chão '
    'do piso; paredes: z do piso + 1,4); escadas com a peça "stairs" (sobe ~2,8 m) dentro de uma zone própria '
    '("escada" ou "patamar"). Só modela por dentro os pisos onde há cenas; os outros andares e os prédios à volta '
    'ficam como volumes (caixas building*), com a fachada a ganhar janelas sozinha. Na vista livre o utilizador vê '
    'por dentro piso a piso. '
    'Silhuetas claras e reconhecíveis, sem pormenor a mais: o modelo de imagem trata dos materiais e da decoração. '
    'Exemplo (cidade com avenida, monorail, rio e a rua de um bar): ' + SET_EXAMPLE
)
# O formato dos objetos (sem a frase do "substitui a atual"), para a edição por partes, que é o que o Assistente usa
# sempre que a maquete já existe.
OBJECTS_HELP = "Formato dos objetos da maquete: " + SET_HELP[SET_HELP.index("Coordenadas em metros"):]
STAGE_HELP = (
    'value = JSON da ENCENAÇÃO da cena (substitui a atual): quem está onde e o que se mexe ao longo de TODA a cena, '
    'dentro da maquete do local, como numa peça de teatro — sem câmara (as câmaras são dos planos). Todos os planos da '
    'cena mostram esta mesma encenação, cada um num troço de tempo, por isso a geografia, as posições e a continuidade '
    'ficam sempre iguais entre planos (campo/contracampo). Coordenadas em metros: Z para cima, Y para a frente, X para '
    'a direita. Ângulos ("facing") em graus, a rodar para a esquerda: 0 = virado para +Y, 90 = para -X, 180 = para -Y, '
    '-90 = para +X (para olhar de (x1,y1) para (x2,y2): facing = atan2(-(x2-x1), y2-y1) em graus). Formato: '
    '{"duration": s (a cena toda, até 600), "objects": [objetos extra só desta cena], "actors": [{"kind": '
    '"character|prop|person|car|vehicle|train", "code": "<código do asset, para elenco e adereços>", "label": "<nome '
    'curto para figurantes/veículos>", "color": "<só para coisas importantes sem asset: red, cyan ou yellow>", "size": '
    '[x,y,z] (adereços/veículos), "cars": n (comboio), "keys": [{"t": s, "pos": [x,y,z], "facing": graus, "pose": '
    '"..."}]}], "lines": [{"n": <número da linha do guião>, "t": <segundo em que começa>}]}. Marca em "lines" o segundo em '
    'que começa cada linha do guião (ações e falas): os planos usam-no para saber que troço mostram. Dá tempo às falas '
    '(~2,5 palavras por segundo) e às ações. Personagens e figurantes são cápsulas lisas com uma viseira escura no rosto (a '
    'frente): o elenco (o do ELENCO DA ENCENAÇÃO, com "code") num tom da cor escolhida, os figurantes em cinzento claro. Cada posição pode '
    'ter "pose": ' + ", ".join(sorted(POSES)) + ' (vale a partir dessa posição; quem se desloca anda sozinho). Para '
    'sentar, "pose": "sit" com "pos" no centro do assento e "facing" para onde olha. "bend" = dobrado para a frente (golpe '
    'no corpo, cansaço), "kneel" = de joelhos, "lie" = deitado de costas no chão (cai para trás do "facing"; para cair, '
    'passa de "stand" a "lie" em ~0,5 s). As figuras não têm braços nem pernas, de propósito: o modelo de vídeo copia o '
    'corpo do boneco, por isso a encenação marca só onde está cada um, para onde está virado e os momentos-chave. Numa '
    'luta ou noutra ação física, marca só esses momentos (quem avança, quem se dobra, quem cai e quando, a distância de '
    'um braço entre quem luta) e deixa os golpes para a descrição do plano e o prompt. Usa "facing" para os olhares (quem '
    'fala para quem). O que se move tem várias posições no tempo (linha reta entre elas). "pos" é a base (no chão; o '
    'comboio com a base em cima da via). Ninguém atravessa paredes nem mobília. Põe figurantes e veículos quando a cena '
    'os pede ou quando um sítio vazio não faria sentido. Figurantes que são elementos do mundo do projeto (robots, '
    'peace keepers, táxis autónomos, drones…) levam SEMPRE "code" do elemento (kind "person" para quem anda, "car" '
    'ou "vehicle" para veículos): ganham a cor do elemento na maquete e a folha dele segue para os vídeos onde '
    'aparecem. Os outros figurantes ficam sem código (cinzentos, pessoas e carros genéricos). Respeita as notas de blocking (lados do ecrã, regra dos 180°).'
)
CAMERA_HELP = (
    'value = JSON da CÂMARA deste plano sobre a encenação da cena: {"start": <segundo da encenação onde o plano '
    'começa>, "camera": [{"t": s desde o início do plano, "pos": [x,y,z], "look_at": [x,y,z], "lens": mm, "moment": '
    'true}]}. O plano dura a DURAÇÃO DO PLANO e mostra a encenação de "start" a "start" + duração: escolhe "start" pelos '
    'segundos das linhas que o plano cobre (ENCENAÇÃO › lines). Não mexas nas pessoas: estão na encenação (vês as '
    'posições delas no troço do plano). A câmara nunca atravessa paredes. Lente: 18–24 mm plano geral, 28–35 mm plano '
    'médio, 50–85 mm grande plano. Por cima do ombro (campo/contracampo): a câmara atrás e um pouco ao lado de quem '
    'ouve, à altura dos ombros (~1,5 m), a olhar para a cara de quem fala, com o ombro e a nuca de quem ouve em '
    'primeiro plano, de um dos lados do quadro; o contracampo fica do MESMO lado do eixo entre os dois (regra dos 180°). '
    'Marca com "moment": true as posições que contam (o início, o fim e cada mudança importante; até 6): em cada '
    'momento, a vista do local mais parecida dá o aspeto do sítio ao vídeo. Podes pôr posições intermédias sem '
    '"moment" para curvas. Exemplo: ' + CAMERA_EXAMPLE
)


# Para começar uma maquete sem o Assistente: uma sala de 10 × 8 m com paredes, porta e janela.
BLANK_ROOM = {
    "seed": 1, "ground": [40, 40], "objects": [
        {"type": "zone", "name": "sala", "from": [-5, -4], "to": [5, 4]},
        {"type": "box", "center": [0, -4, 1.4], "size": [10, 0.15, 2.8], "color": "wall", "plain": True},
        {"type": "box", "center": [0, 4, 1.4], "size": [10, 0.15, 2.8], "color": "wall", "plain": True},
        {"type": "box", "center": [-5, 0, 1.4], "size": [0.15, 8, 2.8], "color": "wall", "plain": True},
        {"type": "box", "center": [5, 0, 1.4], "size": [0.15, 8, 2.8], "color": "wall", "plain": True},
        {"type": "piece", "model": "door", "center": [0, -4, 0], "rot_z": 180, "label": "porta"},
        {"type": "piece", "model": "window", "center": [3, 4, 1.0], "size": [2.0, 0.12, 1.3]},
    ],
}
