"""Catálogo de vídeo da fal.ai: a lista dos modelos de imagem→vídeo e vídeo→vídeo (api.fal.ai/v1/models) e, para os
que o utilizador ativa, o modelo tal como a app o usa, tirado do esquema de entrada público de cada um.

Guardado na configuração: "fal_video_catalog" = {"updated", "models": [{id, name, category, desc}]} e
"fal_video_models" = {endpoint: {"name", "model": {...}}} (só os ativados). O "model" tem o mesmo formato dos modelos
embutidos de image_models.FAL_VIDEO (family "catalog"), mais "fields": os nomes dos campos do pedido.

O que a app sabe preencher: o prompt, a imagem inicial e a final, a lista de imagens de referência, os vídeos de
referência (o vídeo-guia), os áudios de referência (as vozes), a duração, a resolução, a proporção e o som. Um modelo
que exija outra coisa fica de fora, com o motivo.
"""

import re
from datetime import datetime, timezone

import httpx

from app.fal_catalog import CATALOG, Unusable, _enum, _input_schema

CATEGORIES = ("image-to-video", "video-to-video")
# Ferramentas que não são «gerar um plano a partir de imagens» (não aparecem no catálogo)
SKIP = re.compile(r"effect|avatar|lip-?sync|upscale|transition|swap|extend|soundtrack|video-to-audio|interpolat|"
                  r"template|camera-control|/styles/|lora|talking|dubbing|remove|background|caption|motion-control|"
                  r"/edit|restyle|reframe|frames-to-video|keyframes", re.I)

START = ("start_image_url", "image_url", "first_frame_url", "first_frame_image_url", "start_frame_url", "image")
END = ("end_image_url", "last_frame_url", "last_image_url", "end_frame_url", "tail_image_url")
REFS = ("image_urls", "reference_image_urls", "input_image_urls", "ref_image_urls", "reference_images")
VIDEOS = ("video_urls", "reference_video_urls")
AUDIOS = ("audio_urls", "reference_audio_urls")
SOUND = ("generate_audio", "audio", "enable_audio", "with_audio")
RES = ("480p", "720p", "1080p")


def refresh(key: str) -> dict:
    """Lista atual dos modelos de vídeo ativos da fal que partem de imagens (sem efeitos, avatares, lip-sync…)."""
    models, seen = [], set()
    with httpx.Client(timeout=60, headers={"Authorization": f"Key {key}"}) as client:
        for cat in CATEGORIES:
            cursor, pages = None, 0
            while pages < 30:
                params = {"category": cat, "status": "active", "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                data = client.get(CATALOG, params=params).json()
                for m in data.get("models", []):
                    endpoint, meta = m["endpoint_id"], m.get("metadata") or {}
                    if endpoint in seen or SKIP.search(endpoint):
                        continue
                    seen.add(endpoint)
                    models.append({"id": endpoint, "name": meta.get("display_name") or endpoint, "category": cat,
                                   "desc": (meta.get("description") or "").strip()[:160]})
                cursor, pages = data.get("next_cursor"), pages + 1
                if not data.get("has_more") or not cursor:
                    break
    pricing = _pricing_texts()
    for m in models:
        if pricing.get(m["id"]):
            m["pricing"] = pricing[m["id"]]
    return {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "models": models}


def _pricing_texts() -> dict[str, str]:
    """O texto de preço que a fal publica em cada modelo (por resolução, com/sem som…): a API de preços só dá o
    escalão mais baixo."""
    out = {}
    try:
        with httpx.Client(timeout=60) as client:
            for cat in CATEGORIES:
                data = client.get("https://fal.ai/api/models", params={"categories": cat, "limit": 400}).json()
                for m in data.get("items", []):
                    if m.get("pricingInfoOverride"):
                        out[m["id"]] = m["pricingInfoOverride"]
    except (httpx.HTTPError, ValueError):
        pass
    return out


_MONEY = r"(?:\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*\$)"


def _money(groups) -> list[float]:
    return [float(v) for v in groups if v]


def parse_pricing(text: str | None) -> dict:
    """{"price_s": {res: $/s}} ou {"price_flat": $} a partir do texto da fal; com dois valores (sem/com som) fica o
    maior. {} se não se percebe (quem chama confere com o preço mínimo da API)."""
    if not text:
        return {}
    t = re.sub(r"\*\*|\*", "", text)
    t = re.split(r"(?i)\bfor example\b|\be\.g\.", t)[0]  # os exemplos repetem totais que não são preços unitários
    t = " ".join(x for x in re.split(r"(?<=[.;])\s+", t)  # e as frases-exemplo («A 6s 480p video will cost $0.30…»)
                 if not re.search(r"(?i)\b\d+\s*-?\s*(?:s|sec|second)s?\b[^.]{0,40}?\b(?:video|clip)\b[^.]{0,30}?\bcosts?\b", x))
    per_second = bool(re.search(r"per second|/\s*sec|per sec|second of|every second", t, re.I))
    found: dict[str, float] = {}
    tokens = [(m.start(), m.end(), m.group(0).lower()) for m in re.finditer(r"\b\d{3,4}p\b|\b\dK\b", t)]
    for mm in re.finditer(_MONEY, t):
        value = max(_money(mm.groups()))
        nxt = next((x for x in tokens if x[0] >= mm.end()), None)
        prv = next((x for x in reversed(tokens) if x[1] <= mm.start()), None)
        # «$0.05 per second at 480p»: o preço liga-se à resolução seguinte se nada parte a frase até lá
        if nxt and nxt[0] - mm.end() <= 45 and re.fullmatch(r"[^;,.$]*?\b(?:at|for)\s+(?:\w+\s+)?",
                                                              t[mm.end():nxt[0]], re.I | re.S):
            res = nxt[2]
        elif prv and mm.start() - prv[1] <= 60:  # senão à resolução da frase onde está
            res = prv[2]
        else:
            continue
        if res in RES:
            found[res] = max(found.get(res, 0), value)
    if found and per_second:
        return {"price_s": found}
    flat = re.search(_MONEY + r"\s*per\s*video", t, re.I)
    if flat and not per_second:
        return {"price_flat": max(_money(flat.groups()))}
    if per_second:
        values = _money(v for mm in re.finditer(_MONEY, t) for v in mm.groups())
        if values:  # um preço por segundo sem resolução (ex.: sem som / com som): fica o maior
            return {"price_s": {r: max(values) for r in RES}}
    return {}


def _max_items(prop: dict, default: int) -> int:
    for alt in [prop, *(prop.get("anyOf") or [])]:
        if alt.get("maxItems"):
            return int(alt["maxItems"])
    m = re.search(r"max(?:imum)?\.?\s*(?:of\s*)?(\d+)", prop.get("description") or "", re.I)
    return int(m.group(1)) if m else default


def _durations(prop: dict | None) -> tuple[list[int], str | None]:
    """(durações aceites em segundos, como se pedem: "int", "str" ou "str_s"); ([], None) se não se escolhe."""
    if not prop:
        return [], None
    values = [v for v in _enum(prop) if str(v).rstrip("s").isdigit()]
    if values:
        as_text = isinstance(values[0], str)
        return sorted({int(str(v).rstrip("s")) for v in values}), ("str_s" if str(values[0]).endswith("s") else
                                                                   "str" if as_text else "int")
    for alt in [prop, *(prop.get("anyOf") or [])]:
        if alt.get("type") == "integer" and alt.get("maximum"):
            lo, hi = int(alt.get("minimum") or 1), int(alt["maximum"])
            return list(range(max(lo, 1), min(hi, 30) + 1)), "int"
    return [], None


def model_from_schema(endpoint: str, name: str, schema: dict) -> dict:
    """O modelo como a app o usa, a partir do esquema de entrada; Unusable se a app não o souber chamar."""
    props, required = schema.get("properties") or {}, set(schema.get("required") or [])
    if "prompt" not in props:
        raise Unusable("não recebe um prompt de texto")
    pick = lambda names: next((n for n in names if n in props), None)  # noqa: E731
    f = {"start": pick(START), "end": pick(END), "refs": pick(REFS), "videos": pick(VIDEOS), "audios": pick(AUDIOS),
         "sound": pick(SOUND), "duration": "duration" if "duration" in props else None,
         "resolution": "resolution" if "resolution" in props else None,
         "aspect": "aspect_ratio" if "aspect_ratio" in props else None}
    if not f["start"] and not f["refs"]:
        raise Unusable("não parte de imagens (só texto ou só vídeo)")
    if f["end"] and f["end"] in required:
        raise Unusable("exige sempre imagem inicial e final (usa a versão imagem → vídeo do mesmo modelo)")
    known = {"prompt", *[v for v in f.values() if v]}
    missing = sorted(r for r in required if r not in known)
    if missing:
        raise Unusable("precisa de campos que a app não preenche: " + ", ".join(missing))
    durations, dur_kind = _durations(props.get("duration"))
    res = [r for r in RES if r in [str(x) for x in _enum(props.get("resolution") or {})]]
    if f["resolution"] and not res:
        f["resolution"] = None  # resoluções que a app não usa (ex.: 540p): fica a do modelo
    ratios = [str(x) for x in _enum(props.get("aspect_ratio") or {})]
    desc = " ".join((props.get(k) or {}).get("description") or "" for k in (f["refs"], f["videos"], "prompt") if k)
    model = {"name": name if "fal" in name.lower() else f"{name} · fal.ai", "family": "catalog", "endpoint": endpoint,
             "fields": f, "dur_kind": dur_kind, "ratios": ratios,
             "durations": durations or [int((props.get("duration") or {}).get("default") or 5)],
             "res": res or ["720p"], "end": bool(f["end"]), "audio": bool(f["sound"])}
    if f["refs"]:
        model.update(refs=endpoint, max_images=_max_items(props[f["refs"]], 4),
                     ref_format="@Image{n}" if "@image1" in desc.lower() else None)
    model["video_refs"] = bool(f["videos"])
    model["max_videos"] = _max_items(props[f["videos"]], 1) if f["videos"] else 0
    model["audio_refs"] = bool(f["audios"])
    model["max_audios"] = _max_items(props[f["audios"]], 1) if f["audios"] else 0
    return model


def adapter(endpoint: str, name: str) -> dict:
    return model_from_schema(endpoint, name, _input_schema(endpoint))


def apply_price(model: dict, price: dict | None, text: str | None = None) -> None:
    """O preço no modelo: o publicado na página (por resolução); senão o da API de preços (só o escalão mais baixo:
    fica marcado como «a partir de»), por segundo, por vídeo ou por 1000 tokens (a fórmula do Seedance)."""
    if text:
        model["price_text"] = re.sub(r"\*\*", "", text)[:400]
    parsed = parse_pricing(text)
    floor = float(price["unit_price"]) if price and price.get("unit_price") is not None and \
        "second" in (price.get("unit") or "").lower() else None
    if parsed.get("price_s") and floor is not None and min(parsed["price_s"].values()) < floor * 0.99:
        parsed = {}  # leu mal o texto: abaixo do escalão mais baixo da fal
    if parsed:
        if "price_s" in parsed:
            known = parsed["price_s"]
            model["price_s"] = {r: known.get(r, max(known.values())) for r in model["res"]}
        else:
            model.update(parsed)
        model["price_src"] = "página da fal"
        return
    if not price or price.get("unit_price") is None:
        return
    model["price_src"] = "a partir de (API da fal)"
    unit, value = (price.get("unit") or "").lower(), float(price["unit_price"])
    if "token" in unit:
        model["per_ktok"] = value
    elif "second" in unit:
        model["price_s"] = {r: value for r in model["res"]}
    elif unit in ("video", "videos", "generation", "generations", "request", "requests"):
        model["price_flat"] = value
    model["price_unit"] = unit


def request(m: dict, mode: str, prompt: str, aspect: str, image_urls: list[str], video_urls: list[str],
            duration: int, resolution: str, audio: bool) -> tuple[str, dict]:
    """(endpoint, argumentos) de um modelo do catálogo, com os nomes dos campos do esquema dele."""
    f = m["fields"]
    args: dict = {"prompt": prompt}
    if mode == "references" and f.get("refs"):
        args[f["refs"]] = image_urls[: m.get("max_images") or 4]
        if video_urls and f.get("videos"):
            args[f["videos"]] = video_urls[: m.get("max_videos") or 1]
    else:
        if f.get("start"):
            args[f["start"]] = image_urls[0]
        elif f.get("refs"):
            args[f["refs"]] = image_urls[:1]
        if len(image_urls) > 1 and f.get("end"):
            args[f["end"]] = image_urls[1]
    if f.get("duration") and m.get("dur_kind"):
        args[f["duration"]] = {"str": str(duration), "str_s": f"{duration}s"}.get(m["dur_kind"], duration)
    if f.get("resolution"):
        args[f["resolution"]] = resolution
    if f.get("aspect") and m.get("ratios"):
        args[f["aspect"]] = aspect if aspect in m["ratios"] else next(
            (x for x in ("auto", "adaptive") if x in m["ratios"]), m["ratios"][0])
    if f.get("sound"):
        args[f["sound"]] = bool(audio)
    return m["endpoint"], args
