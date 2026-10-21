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
        sync_video(get_config(session, "fal_video_models", {}) or {})


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
# Seedance (por referências: imagens @Image1…, vídeo-guia @Video1, vozes @Audio1; ou imagem inicial e final)
_SEEDANCE = {"family": "seedance", "refs_ok": True, "video_refs": True, "audio_refs": True}
FAL_VIDEO: dict[str, dict] = {
    "seedance-2.0": {"name": "Seedance 2.0 · fal.ai", "refs": "bytedance/seedance-2.0/reference-to-video",
                     "i2v": "bytedance/seedance-2.0/image-to-video", "max_images": 9, "max_videos": 3, "max_audios": 3,
                     "max_audio_s": 15, "max_files": 12,
                     "durations": list(range(4, 16)), "res": ["480p", "720p", "1080p"], "per_ktok": 0.014, **_SEEDANCE},
    "seedance-2.0-fast": {"name": "Seedance 2.0 Fast · fal.ai", "refs": "bytedance/seedance-2.0/fast/reference-to-video",
                          "i2v": "bytedance/seedance-2.0/fast/image-to-video", "max_images": 9, "max_videos": 3,
                          "max_audios": 3, "max_audio_s": 15, "max_files": 12,
                          "durations": list(range(4, 16)), "res": ["480p", "720p"], "per_ktok": 0.0112, **_SEEDANCE},
    "seedance-2.0-mini": {"name": "Seedance 2.0 Mini · fal.ai", "refs": "bytedance/seedance-2.0/mini/reference-to-video",
                          "i2v": "bytedance/seedance-2.0/mini/image-to-video", "max_images": 9, "max_videos": 3,
                          "max_audios": 3, "max_audio_s": 15, "max_files": 12,
                          "durations": list(range(4, 16)), "res": ["480p", "720p"], "per_ktok": 0.007, **_SEEDANCE},
    "seedance-2.5": {"name": "Seedance 2.5 · fal.ai", "refs": "bytedance/seedance-2.5/reference-to-video",
                     "i2v": "bytedance/seedance-2.5/image-to-video", "max_images": 30, "max_videos": 10, "max_audios": 10,
                     "max_audio_s": 30, "max_files": 50,
                     "durations": list(range(4, 31)), "res": ["480p", "720p", "1080p"], "per_ktok": 0.0214, **_SEEDANCE},
    # Os outros: só imagem inicial (e final) — sem referências de elenco, sem vídeo-guia, sem vozes — exceto o Kling O3
    # por referências (até 4 imagens, @Image1…). Preços da página de cada modelo na fal (20 out. 2026).
    "flux-3": {"name": "Flux 3 · fal.ai", "family": "flux3", "i2v": "blackforestlabs/flux-3/image-to-video",
               "flf": "blackforestlabs/flux-3/first-last-frame-to-video", "durations": list(range(5, 21)),
               "res": ["720p", "1080p"], "price_s": {"720p": 0.17, "1080p": 0.29}},
    "flux-3-draft": {"name": "Flux 3 Draft · fal.ai", "family": "flux3", "no_res": True,
                     "i2v": "blackforestlabs/flux-3/image-to-video/draft",
                     "flf": "blackforestlabs/flux-3/first-last-frame-to-video/draft", "durations": list(range(5, 21)),
                     "res": ["720p"], "price_s": {"720p": 0.06}},
    "flux-3-max": {"name": "Flux 3 Max · fal.ai", "family": "flux3max", "i2v": "blackforestlabs/flux-3-max/image-to-video",
                   "durations": list(range(5, 21)), "res": ["720p", "1080p"], "price_s": {"720p": 0.17, "1080p": 0.29}},
    "kling-3-pro": {"name": "Kling 3 Pro · fal.ai", "family": "kling", "i2v": "fal-ai/kling-video/v3/pro/image-to-video",
                    "durations": list(range(3, 16)), "res": ["1080p"], "price_audio": {True: 0.168, False: 0.112}},
    "kling-3-standard": {"name": "Kling 3 Standard · fal.ai", "family": "kling",
                         "i2v": "fal-ai/kling-video/v3/standard/image-to-video",
                         "durations": list(range(3, 16)), "res": ["720p"], "price_audio": {True: 0.126, False: 0.084}},
    "kling-o3-pro": {"name": "Kling O3 Pro · fal.ai", "family": "kling_o3",
                     "refs": "fal-ai/kling-video/o3/pro/reference-to-video", "i2v": "fal-ai/kling-video/o3/pro/image-to-video",
                     "max_images": 4, "durations": list(range(3, 16)), "res": ["1080p"],
                     "price_audio": {True: 0.14, False: 0.112}},
    "wan-3.0": {"name": "Wan 3.0 · fal.ai", "family": "wan", "i2v": "alibaba/wan-3.0/image-to-video",
                "durations": list(range(2, 16)), "res": ["480p", "720p", "1080p"],
                "price_s": {"480p": 0.05, "720p": 0.10, "1080p": 0.20}},
    "kling-4": {"name": "Kling 4 · fal.ai", "family": "kling4", "refs": "kling/v4/reference-to-video",
                "i2v": "kling/v4/reference-to-video", "max_images": 10, "max_videos": 5, "video_refs": True,
                "end": False, "audio": False, "durations": list(range(3, 31)), "res": ["720p", "1080p"],
                "price_s": {"720p": 0.157, "1080p": 0.235},
                # com vídeo de referência (o vídeo-guia): por segundo de entrada + saída, no mínimo 2× a saída
                "price_s_video": {"720p": 0.094, "1080p": 0.141}},
    "ltx-2.5-pro": {"name": "LTX 2.5 Pro · fal.ai", "family": "ltx", "i2v": "lightricks/ltx-2.5/image-to-video/pro",
                    "durations": [6, 8, 10], "res": ["720p", "1080p"], "price_s": {"720p": 0.12, "1080p": 0.17}},
}
FAL_VIDEO_CATALOG: dict[str, dict] = {}  # os ativados do catálogo da fal (Configuração), por endpoint


def fal_video(key: str | None) -> dict:
    """Um modelo de vídeo da fal: os embutidos (testados) ou um ativado do catálogo; {} se não existe."""
    return FAL_VIDEO.get(key or "") or FAL_VIDEO_CATALOG.get(key or "") or {}


def all_fal_video() -> dict[str, dict]:
    return {**FAL_VIDEO, **{k: v for k, v in FAL_VIDEO_CATALOG.items() if k not in FAL_VIDEO}}


def sync_video(models: dict | None) -> None:
    """Os modelos de vídeo do catálogo ativados (configuração "fal_video_models") passam a existir na app."""
    FAL_VIDEO_CATALOG.clear()
    for endpoint, entry in (models or {}).items():
        if isinstance(entry, dict) and entry.get("model"):
            FAL_VIDEO_CATALOG[endpoint] = entry["model"]


VIDEO_PIXELS = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
VIDEO_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}


def fal_video_cost(key: str, resolution: str, seconds: int, audio: bool = True, guide: bool = False) -> float:
    """Custo estimado (USD) de um clip: pela tabela de tokens (Seedance), por segundo e resolução, ou por segundo com
    ou sem som (Kling 3); guide = segue um vídeo de referência (o Kling 4 cobra-o à parte)."""
    m = fal_video(key)
    res = resolution if resolution in m["res"] else m["res"][0]
    if "per_ktok" in m:
        w, h = VIDEO_PIXELS.get(res, VIDEO_PIXELS["720p"])
        return round(m["per_ktok"] * w * h * 24 * seconds / 1024 / 1000, 2)
    if "price_audio" in m:
        return round(m["price_audio"][bool(audio)] * seconds, 2)
    if guide and "price_s_video" in m:
        return round(m["price_s_video"][res] * seconds * 2, 2)
    if "price_flat" in m:
        return round(m["price_flat"], 2)
    if "price_s" in m:
        return round(m["price_s"].get(res, max(m["price_s"].values())) * seconds, 2)
    return 0.0


def fal_video_caps(m: dict) -> dict:
    """O que o modelo aceita, para a app escolher o modo e as imagens: refs (várias imagens de referência),
    video_refs (vídeo-guia), audio_refs (vozes), end_frame (imagem final), audio (gera som)."""
    return {"refs": bool(m.get("refs_ok") or m.get("refs")), "video_refs": bool(m.get("video_refs")),
            "audio_refs": bool(m.get("audio_refs")), "end_frame": m.get("end", True), "audio": m.get("audio", True)}


def fal_video_request(key: str, mode: str, prompt: str, aspect: str | None, image_urls: list[str],
                      video_urls: list[str], seconds: int | None, resolution: str, audio: bool) -> tuple[str, dict]:
    """(endpoint, argumentos) do pedido à fal, no formato de cada família de modelos."""
    m = fal_video(key)
    a = (aspect or "16:9").split(" ")[0]
    res = resolution if resolution in m["res"] else m["res"][0]
    durs = m["durations"]
    dur = seconds if seconds in durs else min(durs, key=lambda d: abs(d - (seconds or durs[0])))
    family = m.get("family", "seedance")
    start, end = image_urls[0], (image_urls[1] if len(image_urls) > 1 else None)
    if family == "seedance":
        base = {"prompt": prompt, "duration": str(seconds) if seconds in durs else "auto",
                "resolution": res, "aspect_ratio": a if a in VIDEO_RATIOS else "auto", "generate_audio": bool(audio)}
        if mode == "references":
            args = base | {"image_urls": image_urls[: m["max_images"]]}
            if video_urls:
                args["video_urls"] = video_urls[: m["max_videos"]]
            return m["refs"], args
        args = base | {"image_url": start}
        if end:
            args["end_image_url"] = end
        return m["i2v"], args
    flux_ratios = {"21:9", "2:1", "16:9", "4:3", "1:1", "3:4", "9:16"}
    if family == "flux3":
        args = {"prompt": prompt, "duration": dur, "aspect_ratio": a if a in flux_ratios else "auto",
                "generate_audio": bool(audio)} | ({} if m.get("no_res") else {"resolution": res})
        if end:
            return m["flf"], args | {"start_image_url": start, "end_image_url": end}
        return m["i2v"], args | {"image_url": start}
    if family == "flux3max":
        return m["i2v"], {"prompt": prompt, "image_url": start, "duration": dur, "resolution": res,
                          "aspect_ratio": a if a in flux_ratios else "auto", "generate_audio": bool(audio)} \
            | ({"end_image_url": end} if end else {})
    if family in ("kling", "kling_o3"):
        base = {"prompt": prompt, "duration": str(dur), "generate_audio": bool(audio)}
        if family == "kling_o3" and mode == "references":
            return m["refs"], base | {"image_urls": image_urls[: m["max_images"]],
                                      "aspect_ratio": a if a in ("16:9", "9:16", "1:1") else "16:9"}
        first = "image_url" if family == "kling_o3" else "start_image_url"
        return m["i2v"], base | {first: start} | ({"end_image_url": end} if end else {})
    if family == "wan":
        return m["i2v"], {"prompt": prompt, "start_image_url": start, "duration": dur, "resolution": res,
                          "aspect_ratio": a if a in ("16:9", "4:3", "1:1", "3:4", "9:16") else "adaptive",
                          "audio": bool(audio)} | ({"end_image_url": end} if end else {})
    if family == "kling4":  # um só endpoint: referências (@Image1…, @Video1…) e, sem elas, a imagem inicial
        args = {"prompt": prompt, "duration": dur, "resolution": res,
                "aspect_ratio": a if a in ("16:9", "9:16", "1:1", "21:9") else "auto"}
        if mode == "references":
            args["image_urls"] = image_urls[: m["max_images"]]
            if video_urls:
                args["video_urls"] = video_urls[: m["max_videos"]]
        else:
            args["start_image_url"] = start
        return m["refs"], args
    if family == "catalog":
        from app import fal_video_catalog

        return fal_video_catalog.request(m, mode, prompt, a, image_urls, video_urls, dur, res, audio)
    if family == "ltx":
        return m["i2v"], {"prompt": prompt, "image_url": start, "duration": dur, "resolution": res,
                          "aspect_ratio": a if a in ("16:9", "9:16") else "auto", "generate_audio": bool(audio)} \
            | ({"end_image_url": end} if end else {})
    raise ValueError(f"Família de modelo desconhecida: {family}.")