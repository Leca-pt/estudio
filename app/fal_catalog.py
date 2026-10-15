"""Catálogo completo da fal.ai: a lista de modelos de imagem (api.fal.ai/v1/models) e, para os que o utilizador
ativa, o esquema de entrada de cada um (público), de onde a app tira o que o modelo sabe fazer e como pedir.

Guardado na configuração: "fal_catalog" = {"updated", "models": [{id, name, category, desc}]} e "fal_models" =
{endpoint: adaptador} (só os ativados). O adaptador diz os campos: prompt, lista de imagens, imagem única, imagem de
controlo, tamanho (image_size ou aspect_ratio), num_images, output_format.
"""

from datetime import datetime, timezone

import httpx

CATALOG = "https://api.fal.ai/v1/models"
SCHEMA = "https://fal.ai/api/openapi/queue/openapi.json"
CATEGORIES = ("text-to-image", "image-to-image")

LIST_FIELDS = ("image_urls", "input_image_urls", "reference_image_urls", "images_urls", "ref_image_urls")
CONTROL_FIELDS = ("control_image_url", "control_lora_image_url")
KNOWN = {"prompt", "image_size", "aspect_ratio", "num_images", "output_format", "image_url", *LIST_FIELDS,
         *CONTROL_FIELDS}


class Unusable(ValueError):
    pass


def refresh(key: str) -> dict:
    """Lista atual dos modelos de imagem ativos da fal (texto→imagem e imagem→imagem)."""
    models = []
    with httpx.Client(timeout=60, headers={"Authorization": f"Key {key}"}) as client:
        for cat in CATEGORIES:
            cursor, pages = None, 0
            while pages < 30:
                params = {"category": cat, "status": "active", "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                data = client.get(CATALOG, params=params).json()
                for m in data.get("models", []):
                    meta = m.get("metadata") or {}
                    models.append({"id": m["endpoint_id"], "name": meta.get("display_name") or m["endpoint_id"],
                                   "category": cat, "desc": (meta.get("description") or "").strip()[:160]})
                cursor, pages = data.get("next_cursor"), pages + 1
                if not data.get("has_more") or not cursor:
                    break
    return {"updated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "models": models}


def _input_schema(endpoint: str) -> dict:
    data = httpx.get(SCHEMA, params={"endpoint_id": endpoint}, timeout=30).json()
    schemas = (data.get("components") or {}).get("schemas") or {}
    ref = None
    for path in (data.get("paths") or {}).values():  # o corpo do POST de submissão
        post = path.get("post") or {}
        body = (((post.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
        if "$ref" in body:
            ref = body["$ref"].rsplit("/", 1)[-1]
            break
    schema = schemas.get(ref) if ref else next((v for k, v in schemas.items() if k.lower().endswith("input")), None)
    if not schema:
        raise Unusable("sem esquema de entrada público")
    return schema


def _enum(prop: dict) -> list:
    if "enum" in prop:
        return list(prop["enum"])
    for alt in prop.get("anyOf") or prop.get("oneOf") or []:
        if "enum" in alt:
            return list(alt["enum"])
    return []


def adapter(endpoint: str) -> dict:
    """Lê o esquema do modelo e devolve o adaptador; Unusable se a app não o souber chamar."""
    schema = _input_schema(endpoint)
    props, required = schema.get("properties") or {}, set(schema.get("required") or [])
    if "prompt" not in props:
        raise Unusable("não recebe um prompt de texto")
    missing = [r for r in required if r not in KNOWN]
    if missing:
        raise Unusable("precisa de campos que a app não preenche: " + ", ".join(sorted(missing)))
    f: dict = {}
    list_field = next((k for k in LIST_FIELDS if k in props), None)
    control = next((k for k in CONTROL_FIELDS if k in props), None)
    structure_words = any(w in endpoint for w in ("depth", "canny", "controlnet"))
    if control or (structure_words and "image_url" in props):
        f["control"] = control or "image_url"
    elif list_field:
        f["images"] = list_field
        f["max"] = int((props[list_field] or {}).get("maxItems") or 10)
    elif "image_url" in props:
        f["image"] = "image_url"
    if "image_size" in props:
        f["size"] = "image_size"
    elif "aspect_ratio" in props:
        f["size"], f["ratios"] = "aspect_ratio", [str(x) for x in _enum(props["aspect_ratio"])]
    if "num_images" in props:
        f["n"] = True
    if "png" in _enum(props.get("output_format") or {}):
        f["png"] = True
    needs_image = bool(required & {"image_url", list_field or "", f.get("control") or ""})
    caps = set()
    if f.get("control"):
        caps.add("structure")
    else:
        if not needs_image:
            caps.add("t2i")
        if f.get("images") or f.get("image"):
            caps |= {"refs", "edit"}
    if not caps:
        raise Unusable("não percebo que imagens recebe")
    return {"fields": f, "caps": sorted(caps), "max_refs": f.get("max", 1 if f.get("image") else 0)}
