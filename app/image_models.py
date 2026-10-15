# Fornecedores de imagem e quantos pedidos cada um corre ao mesmo tempo (None = o valor da Configuração). Cada um
# tem a sua fila de processamento (app/jobs.py): um fornecedor novo entra aqui e ganha a fila sozinho.
PROVIDER_PARALLEL = {"gpt": 1, "artlist": None, "fal": 4, "bytedance": 3}
PAID_PROVIDERS = {"artlist", "fal", "bytedance"}  # pagos por pedido: um pedido interrompido não se repete sozinho

"""Modelos de imagem por chave de API (para já, a fal.ai), com o que cada um sabe fazer.

Capacidades:
- "t2i": gera só a partir do texto;
- "refs": aceita várias imagens de referência (até max_refs);
- "edit": edita uma imagem (a primeira) com as outras como referência;
- "structure": segue a geometria de uma imagem de controlo (depth/canny) — o melhor para as vistas da maquete.

Cada modelo diz que pedido fazer em cada modo (os campos são os da fal: ver docs de cada modelo). Os preços são
indicativos (a fal muda-os; o rótulo mostra o que estava na documentação em out/2026).
"""

PRESETS = {"16:9": "landscape_16_9", "9:16": "portrait_16_9", "4:3": "landscape_4_3", "3:4": "portrait_4_3",
           "1:1": "square_hd"}
NANO_RATIOS = {"21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"}


def image_size(aspect: str | None):
    """Proporção do projeto → campo image_size da fal (preset, ou largura × altura com o lado maior a 1536)."""
    aspect = (aspect or "16:9").split(" ")[0]
    if aspect in PRESETS:
        return PRESETS[aspect]
    try:
        w, h = (float(x) for x in aspect.split(":"))
    except ValueError:
        return "landscape_16_9"
    scale = 1536 / max(w, h)
    return {"width": int(w * scale) // 16 * 16, "height": int(h * scale) // 16 * 16}


def _nano(prompt, aspect, urls):
    args = {"prompt": prompt, "num_images": 1, "output_format": "png", "resolution": "1K"}
    a = (aspect or "16:9").split(" ")[0]
    args["aspect_ratio"] = a if a in NANO_RATIOS else "auto"
    if urls:
        args["image_urls"] = urls
    return args


def _sized(prompt, aspect, urls, **extra):
    args = {"prompt": prompt, "image_size": image_size(aspect), **extra}
    if urls:
        args["image_urls"] = urls
    return args


FAL: dict[str, dict] = {
    # Várias referências e edição (o mesmo modelo faz os dois passos das vistas com referências)
    "nano-banana-2": {
        "res": ["1K", "2K", "4K"],
        "name": "Nano Banana 2", "family": "Nano Banana", "price": "≈ $0.08", "caps": {"t2i", "refs", "edit"}, "max_refs": 14,
        "t2i": "fal-ai/nano-banana-2", "multi": "fal-ai/nano-banana-2/edit", "args": _nano},
    "nano-banana-pro": {
        "res": ["1K", "2K", "4K"],
        "name": "Nano Banana Pro", "family": "Nano Banana", "price": "≈ $0.15", "caps": {"t2i", "refs", "edit"}, "max_refs": 14,
        "t2i": "fal-ai/nano-banana-pro", "multi": "fal-ai/nano-banana-pro/edit", "args": _nano},
    "seedream-5-pro": {
        "res": ["1K", "2K"],
        "name": "Seedream 5 Pro", "family": "Seedream", "price": "≈ $0.07", "caps": {"t2i", "refs", "edit"}, "max_refs": 10,
        "t2i": "bytedance/seedream/v5/pro/text-to-image", "multi": "bytedance/seedream/v5/pro/edit",
        "args": lambda p, a, u: _sized(p, a, u, num_images=1)},
    "seedream-5-flash": {
        "res": ["1K", "2K"],
        "name": "Seedream 5 Flash", "family": "Seedream", "price": "≈ $0.03", "caps": {"t2i", "refs", "edit"}, "max_refs": 10,
        "t2i": "bytedance/seedream/v5/flash/text-to-image", "multi": "bytedance/seedream/v5/flash/edit",
        "args": lambda p, a, u: _sized(p, a, u, num_images=1)},
    "flux-2-pro": {
        "res": ["1K", "2K"],
        "name": "Flux 2 Pro", "family": "Flux", "price": "≈ $0.03+ /MP", "caps": {"t2i", "refs", "edit"}, "max_refs": 9,
        "t2i": "fal-ai/flux-2-pro", "multi": "fal-ai/flux-2-pro/edit",
        "args": lambda p, a, u: _sized(p, a, u, output_format="png")},
    "flux-2-max": {
        "res": ["1K", "2K"],
        "name": "Flux 2 Max", "family": "Flux", "price": "≈ $0.07+ /MP", "caps": {"t2i", "refs", "edit"}, "max_refs": 8,
        "t2i": "fal-ai/flux-2-max", "multi": "fal-ai/flux-2-max/edit",
        "args": lambda p, a, u: _sized(p, a, u, output_format="png")},
    "gpt-image-2.5": {
        "res": ["1K", "2K", "4K"],
        "name": "GPT Image 2.5 (fal)", "family": "GPT Image", "price": "≈ $0.05", "caps": {"t2i", "refs", "edit"}, "max_refs": 16,
        "t2i": "openai/gpt-image-2.5/flare/text-to-image", "multi": "openai/gpt-image-2.5/flare/edit",
        "args": lambda p, a, u: _sized(p, a, u, quality="high", num_images=1, output_format="png")},
    "qwen-image-3": {
        "res": ["1K", "2K"],
        "name": "Qwen Image 3", "family": "Qwen", "price": "≈ $0.04", "caps": {"t2i", "refs", "edit"}, "max_refs": 3,
        "t2i": "alibaba/qwen-image-3/text-to-image", "multi": "alibaba/qwen-image-3/edit",
        "args": lambda p, a, u: _sized(p, a, u, num_images=1)},
    # Controlo de estrutura: o render da maquete como imagem de controlo (geometria garantida); sem referências de
    # estilo — o aspeto das referências entra num 2.º passo, com o modelo de edição da Configuração.
    "flux-pro-depth": {
        "res": ["1K"],
        "name": "Flux Pro Depth (estrutura)", "family": "Flux", "price": "≈ $0.05 /MP", "caps": {"structure"}, "max_refs": 0,
        "structure": "fal-ai/flux-pro/v1/depth",
        "control": lambda p, a, url, depth=False: {"prompt": p, "control_image_url": url, "image_size": image_size(a),
                                                   "num_images": 1, "output_format": "png"}},
}

RECOMMENDED = set(FAL)  # os testados na app: sempre ativos


def _generic_args(f: dict):
    """Pedido de um modelo do catálogo, a partir dos campos que o esquema dele tem."""
    def build(prompt, aspect, urls):
        args = {"prompt": prompt}
        if urls and f.get("images"):
            args[f["images"]] = urls[: f.get("max", 10)]
        elif urls and f.get("image"):
            args[f["image"]] = urls[0]
        if f.get("size") == "image_size":
            args["image_size"] = image_size(aspect)
        elif f.get("size") == "aspect_ratio":
            a = (aspect or "16:9").split(" ")[0]
            ratios = f.get("ratios") or []
            if a in ratios or not ratios:
                args["aspect_ratio"] = a
        if f.get("n"):
            args["num_images"] = 1
        if f.get("png"):
            args["output_format"] = "png"
        return args
    return build


def _generic_control(f: dict):
    def build(prompt, aspect, url, depth=False):
        args = {"prompt": prompt, f["control"]: url}
        if f.get("size") == "image_size":
            args["image_size"] = image_size(aspect)
        if f.get("n"):
            args["num_images"] = 1
        if f.get("png"):
            args["output_format"] = "png"
        return args
    return build


def sync(models: dict | None) -> None:
    """Junta à lista os modelos do catálogo ativados na Configuração ({endpoint: {name, adapter, price}})."""
    for key in [k for k in FAL if k not in RECOMMENDED]:
        del FAL[key]
    for endpoint, m in (models or {}).items():
        a = m.get("adapter") or {}
        f, caps = a.get("fields") or {}, set(a.get("caps") or [])
        entry = {"name": m.get("name") or endpoint, "family": m.get("name") or endpoint,
                 "price": m.get("price") or "preço na fal", "caps": caps, "max_refs": a.get("max_refs", 0),
                 "generic_size": f.get("size")}  # com image_size: escolhe-se 1K ou 2K em píxeis
        if "structure" in caps:
            entry |= {"structure": endpoint, "control": _generic_control(f)}
        else:
            entry |= {"t2i": endpoint, "multi": endpoint, "args": _generic_args(f)}
        FAL[endpoint] = entry


def sync_from_db() -> None:
    from sqlmodel import Session

    from app.db import engine
    from app.options import get_config

    with Session(engine) as session:
        sync(get_config(session, "fal_models", {}) or {})


CAP_WORDS = {"structure": "segue a maquete", "refs": "referências", "edit": "edita"}


def label(m: dict) -> str:
    caps = " · ".join(CAP_WORDS[c] for c in ("structure", "refs", "edit") if c in m["caps"])
    return f"{m['name']} · fal.ai · {m['price']}" + (f" · {caps}" if caps else "")


# ── Resolução das imagens (1K / 2K / 4K) ──────────────────────────────────────────────────────────────────────────

RESOLUTIONS = ("1K", "2K", "4K")
RES_PIXELS = {"1K": 1024 * 1024, "2K": 2048 * 2048, "4K": 4096 * 4096}
# Limites de píxeis de cada família na fal (esquemas): total máximo e lado máximo
PIXEL_LIMITS = {"Seedream": (2048 * 2048, 14142), "Qwen": (2048 * 2048, 14142), "Flux": (2048 * 2048, 14142),
                "GPT Image": (8_294_400, 3840)}


def dims(aspect: str | None, res: str, family: str | None = None) -> dict:
    """Largura × altura (múltiplos de 16) com a proporção pedida e os píxeis da resolução, dentro dos limites do modelo."""
    try:
        w, h = (float(x) for x in (aspect or "16:9").split(" ")[0].split(":"))
    except ValueError:
        w, h = 16.0, 9.0
    total, edge = PIXEL_LIMITS.get(family or "", (RES_PIXELS["4K"], 14142))
    px = min(RES_PIXELS.get(res, RES_PIXELS["1K"]), total)
    width = (px * w / h) ** 0.5
    height = width * h / w
    scale = min(1.0, edge / max(width, height))
    return {"width": int(width * scale) // 16 * 16, "height": int(height * scale) // 16 * 16}


def resolutions(provider: str, model=None) -> list[str]:
    """As resoluções que o modelo aceita; [] = tamanho fixo do modelo (sem escolha)."""
    if provider == "fal":
        m = FAL.get(model) or {}
        if "res" in m:
            return list(m["res"])
        return ["1K", "2K"] if m.get("generic_size") == "image_size" else []
    if provider == "artlist":
        from app import artlist

        return list((artlist.RESOLUTIONS.get(int(model or artlist.DEFAULT_MODEL)) or {}).get("values") or [])
    return []  # GPT Image pela subscrição: o tamanho é o do modelo


def fit_resolution(wanted: str | None, allowed: list[str]) -> str | None:
    """A resolução pedida, ou a mais próxima que o modelo aceita (None se o modelo não tem escolha)."""
    if not allowed:
        return None
    wanted = wanted if wanted in RESOLUTIONS else "2K"
    return wanted if wanted in allowed else min(allowed, key=lambda r: abs(RESOLUTIONS.index(r) - RESOLUTIONS.index(wanted)))


def _with_res(model_id: str, args: dict, aspect: str | None, res: str | None) -> dict:
    """Põe a resolução no campo que o modelo usa: "resolution" (Nano Banana) ou "image_size" em píxeis."""
    m = FAL[model_id]
    res = fit_resolution(res, resolutions("fal", model_id))
    if not res:
        return args
    if "resolution" in args:
        args["resolution"] = res
    elif "image_size" in args:
        args["image_size"] = dims(aspect, res, m.get("family"))
    return args


def request(model_id: str, mode: str, prompt: str, aspect: str | None, urls: list[str],
            depth: bool = False, res: str | None = None) -> tuple[str, dict]:
    """(endpoint, argumentos) para um pedido. mode: "generate" (texto + referências), "edit" (a 1.ª imagem é a
    editada), "structure" (a 1.ª imagem é o controlo de geometria). res: 1K / 2K / 4K (a mais próxima que o modelo aceita)."""
    endpoint, args = _request(model_id, mode, prompt, aspect, urls, depth)
    return endpoint, _with_res(model_id, args, aspect, res)


def _request(model_id: str, mode: str, prompt: str, aspect: str | None, urls: list[str],
             depth: bool = False) -> tuple[str, dict]:
    m = FAL[model_id]
    if mode == "structure":
        if "structure" not in m["caps"] or not urls:
            raise ValueError(f"{m['name']} não segue uma imagem de controlo.")
        return m["structure"], m["control"](prompt, aspect, urls[0], depth)
    if "structure" in m["caps"]:  # modelo só de estrutura usado para outra coisa: a 1.ª imagem é o controlo
        if not urls:
            raise ValueError(f"{m['name']} precisa de uma imagem de controlo (é um modelo de estrutura).")
        return m["structure"], m["control"](prompt, aspect, urls[0])
    urls = urls[: m["max_refs"]]
    if urls and "refs" in m["caps"]:
        return m["multi"], m["args"](prompt, aspect, urls)
    if "t2i" not in m["caps"]:
        raise ValueError(f"{m['name']} precisa de uma imagem de entrada.")
    return m["t2i"], m["args"](prompt, aspect, [])


# ── Capacidades de qualquer modelo (GPT Image, Artlist, fal) e se serve para cada uso ─────────────────────────────

# Máximo de imagens de entrada por pedido, quando o fornecedor não o documenta (GPT Image pela subscrição, Artlist):
# um valor prudente. Os modelos da fal trazem o seu (max_refs, do esquema de cada um).
UNDOCUMENTED_MAX_INPUTS = 6


def max_inputs(provider: str, model=None) -> int:
    """Quantas imagens de entrada o modelo aceita num pedido."""
    if provider == "fal":
        m = FAL.get(str(model or ""), {})
        return max(1, int(m.get("max_refs") or 1)) if m.get("caps") and "structure" not in m["caps"] else 1
    return UNDOCUMENTED_MAX_INPUTS


def caps_of(provider: str, model=None) -> set:
    """O que um modelo sabe fazer, seja de que fornecedor for."""
    if provider == "fal":
        return set(FAL.get(str(model or ""), {}).get("caps", set()))
    if provider == "artlist":
        from app import artlist

        m = artlist.MODELS.get(int(model)) if str(model or "").isdigit() else None
        # todos os modelos de imagem do Artlist aceitam referências (e editam: a 1.ª imagem é a base)
        return {"refs", "edit"} | (set() if (m or {}).get("needs_refs") else {"t2i"})
    return {"t2i", "refs", "edit"}  # GPT Image (subscrição)


USES = {"generate": "gerar", "edit": "editar", "maquete": "vistas da maquete"}


def fit(caps: set, use: str) -> tuple[bool, str]:
    """(pode ser usado, aviso). Um modelo que não pode ser usado não aparece no seletor desse uso."""
    if use == "edit":
        return ("edit" in caps, "")
    if use == "maquete":
        if "structure" in caps or "edit" in caps:
            return True, ""
        if "refs" in caps:
            return True, "não segue bem a maquete (sem edição): a vista pode sair com outra composição"
        return True, "não segue a maquete (só texto): a vista sai com outra composição"
    # gerar (folhas, retratos, capa): texto + referências
    if "structure" in caps and not ({"t2i", "refs"} & caps):
        return False, ""
    if "refs" not in caps:
        return True, "ignora as imagens de referência"
    if "t2i" not in caps:
        return True, "só gera com imagens de referência"
    return True, ""


# ── Vídeo pela fal ────────────────────────────────────────────────────────────────────────────────────────────────
# Cada modelo: o pedido com referências (imagens @Image1…, vídeos @Video1…: o vídeo-guia da maquete) e o pedido com
# imagem inicial (e final). Preço da fal por 1000 tokens de vídeo; tokens ≈ largura × altura × 24 fps × segundos / 1024.
FAL_VIDEO: dict[str, dict] = {
    "seedance-2.0": {"name": "Seedance 2.0 · fal.ai", "refs": "bytedance/seedance-2.0/reference-to-video",
                     "i2v": "bytedance/seedance-2.0/image-to-video", "max_images": 9, "max_videos": 3, "max_audios": 3,
                     "max_audio_s": 15, "max_files": 12,
                     "durations": list(range(4, 16)), "res": ["480p", "720p", "1080p"], "per_ktok": 0.014},
    "seedance-2.0-fast": {"name": "Seedance 2.0 Fast · fal.ai", "refs": "bytedance/seedance-2.0/fast/reference-to-video",
                          "i2v": "bytedance/seedance-2.0/fast/image-to-video", "max_images": 9, "max_videos": 3,
                          "max_audios": 3, "max_audio_s": 15, "max_files": 12,
                          "durations": list(range(4, 16)), "res": ["480p", "720p"], "per_ktok": 0.0112},
    "seedance-2.0-mini": {"name": "Seedance 2.0 Mini · fal.ai", "refs": "bytedance/seedance-2.0/mini/reference-to-video",
                          "i2v": "bytedance/seedance-2.0/mini/image-to-video", "max_images": 9, "max_videos": 3,
                          "max_audios": 3, "max_audio_s": 15, "max_files": 12,
                          "durations": list(range(4, 16)), "res": ["480p", "720p"], "per_ktok": 0.007},
    "seedance-2.5": {"name": "Seedance 2.5 · fal.ai", "refs": "bytedance/seedance-2.5/reference-to-video",
                     "i2v": "bytedance/seedance-2.5/image-to-video", "max_images": 30, "max_videos": 10, "max_audios": 10,
                     "max_audio_s": 30, "max_files": 50,
                     "durations": list(range(4, 31)), "res": ["480p", "720p", "1080p"], "per_ktok": 0.0214},
}
VIDEO_PIXELS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
VIDEO_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}


def fal_video_cost(key: str, resolution: str, seconds: int) -> float:
    """Custo estimado (USD) de um clip, pela tabela de tokens da fal."""
    w, h = VIDEO_PIXELS.get(resolution, VIDEO_PIXELS["720p"])
    return round(FAL_VIDEO[key]["per_ktok"] * w * h * 24 * seconds / 1024 / 1000, 2)


def fal_video_request(key: str, mode: str, prompt: str, aspect: str | None, image_urls: list[str],
                      video_urls: list[str], seconds: int | None, resolution: str, audio: bool) -> tuple[str, dict]:
    m = FAL_VIDEO[key]
    a = (aspect or "16:9").split(" ")[0]
    res = resolution if resolution in m["res"] else m["res"][0]
    base = {"prompt": prompt, "duration": str(seconds) if seconds in m["durations"] else "auto",
            "resolution": res, "aspect_ratio": a if a in VIDEO_RATIOS else "auto", "generate_audio": bool(audio)}
    if mode == "references":
        args = base | {"image_urls": image_urls[: m["max_images"]]}
        if video_urls:
            args["video_urls"] = video_urls[: m["max_videos"]]
        return m["refs"], args
    args = base | {"image_url": image_urls[0]}
    if len(image_urls) > 1:
        args["end_image_url"] = image_urls[1]
    return m["i2v"], args
