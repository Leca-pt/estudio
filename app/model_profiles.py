"""Perfis de prompt por família de modelos (docs/PROMPT_RULES.md, secção 6).

As regras gerais valem para todos os modelos; o limite de palavras e as dicas de sintaxe vêm do perfil do modelo
escolhido por omissão em Configuração → Modelos e geração (imagem e vídeo). Escolher outro modelo no botão Gerar
não muda as regras: o prompt não tem de ser refeito. No vídeo, o prompt de cada plano monta-se pelo perfil do modelo
desse plano (secções, tempos, forma das falas e do som: video_format).
"""

from sqlmodel import Session

from app.options import get_config
from app.memo import memo

# Cada perfil: nome, modelos que abrange (texto no nome do modelo), limites por omissão e dicas para o Assistente.
IMAGE_PROFILES = {
    "brief": {
        "name": "Brief curto",
        "models": "Flux, Wan, Grok",
        "match": ("flux", "wan", "grok"),
        "image_max_words": 80, "reference_max_words": 120,
        "hint": "30–80 palavras (Flux: guia oficial; Wan e Grok sem guia, fica o mais seguro); o que vem primeiro pesa mais: "
                "sujeito → ação → estilo → contexto; referências pelo papel de cada imagem.",
        "source": "Flux: BFL (oficial). Wan e Grok: sem orientação publicada.",
    },
    "medium": {
        "name": "Brief médio",
        "models": "Seedream, Qwen",
        "match": ("seedream", "qwen"),
        "image_max_words": 100, "reference_max_words": 150,
        "hint": "frases naturais, sujeito primeiro; prompts muito longos dispersam e perdem detalhes; texto na imagem entre "
                "aspas; diz o papel de cada referência (ex.: \"@AVT_VERA: character sheet\").",
        "source": "Seedream: ByteDance (teto de 600 palavras, prompts longos perdem detalhe). Qwen: Alibaba e Runware.",
    },
    "long": {
        "name": "Instruções longas",
        "models": "GPT Image, Nano Banana, Kling O3 Image, Meta Muse",
        "match": ("gpt", "nano banana", "kling", "muse"),
        "image_max_words": 150, "reference_max_words": 200,
        "hint": "descrição narrativa em frases completas (nunca listas de palavras); pode levar mais detalhe de cada personagem "
                "(roupa, materiais, marcas), mas o sujeito continua primeiro; texto na imagem entre aspas; cada referência "
                "com o seu papel (\"@AVT_VERA: character sheet …\").",
        "source": "GPT Image: OpenAI Cookbook. Nano Banana: Google. Kling O3 e Meta Muse: plataformas (Runware, fal).",
    },
}
# Vídeo: além do limite e das dicas, cada perfil diz como se monta o prompt por secções (docs/PROMPT_RULES.md §6):
#   layout   "sections" (uma secção por linha, com etiqueta) ou "paragraph" (a mesma ordem, num só parágrafo)
#   timeline True quando o modelo segue blocos com tempos ("0s-3s: …") sem cortar (só o Seedance 2.5)
#   dialogue a forma da fala que o modelo reconhece; sound a forma do som
VIDEO_PROFILES = {
    "short": {
        "name": "Prompt curto",
        "models": "Grok Imagine Video, Luma Ray, Flux 3 (sem guia publicado)",
        "match": ("grok", "luma"),
        "max_chars": 4000, "video_max_words": 100, "video_max_words_timed": 150,
        "layout": "paragraph", "timeline": False,
        "dialogue": '<personagem> says: "…"', "sound": "uma frase no fim (\"Sound: …\")",
        "hint": "presente do indicativo, centrado na ação, com um movimento de câmara nomeado; Luma: nenhuma frase negativa "
                "(\"no\", \"without\" podem ter o efeito contrário), descreve só o que se quer; Luma sem som documentado.",
        "source": "Luma: FAQ oficial (a maioria abaixo de 1000 caracteres). Grok: sem orientação publicada.",
    },
    "seedance25": {
        "name": "Seedance 2.5",
        "models": "Seedance 2.5",
        "match": ("seedance 2.5",),
        "ref_format": "@image{n}", "max_chars": 6000, "video_max_words": 250, "video_max_words_timed": 300,
        "layout": "sections", "timeline": False,
        "dialogue": 'Dialogue (<personagem>, <tom>): "…"', "sound": "\"Sound: …\" (ambiente e efeitos com nome)",
        "hint": "guia oficial da ByteDance: 1) referências numeradas, cada uma com o seu papel; 2) resumo numa frase "
                "(quem, onde, o que acontece, género, câmara); 3) a ação; 4) notas que valem para o clip todo (luz, "
                "ambiente, som). SEM blocos de tempo (\"0s-3s: …\"): testado em 2026-10-04, o Seedance 2.5 corta "
                "para um plano novo em cada bloco, mesmo com \"same continuous take\" — a ação é um movimento contínuo "
                "ligado por \"then, without a cut\". Negativas só para legendas e música.",
        "source": "ByteDance (guia oficial do Seedance 2.5, ago. 2026: amostras de 500–1500 palavras; tempos ao segundo).",
    },
    "seedance": {
        "name": "Seedance 2.0",
        "models": "Seedance 2.0, 2.0 Fast, 2.0 Mini",
        "match": ("seedance",),
        "ref_format": "@image{n}", "max_chars": 6000, "video_max_words": 200, "video_max_words_timed": 250,
        "layout": "sections", "timeline": False,
        "dialogue": 'Dialogue (<personagem>, <tom>): "…"', "sound": "\"Sound: …\" (ambiente e efeitos com nome)",
        "hint": "ordem oficial: sujeito, ação, cenário, luz e cor, câmara, estilo, restrições; cada referência com um "
                "papel explícito (\"@Video1 = camera movement and timing only\"); o que vem primeiro pesa mais, frases "
                "curtas e concretas; SEM tempos (\"0-3s: …\", \"At 2-4 s\"): no 2.0 são instáveis e fazem cortar — a "
                "ação é um movimento contínuo ligado por \"then, without a cut\"; negativas só no fim (legendas, música, "
                "logótipos).",
        "source": "ByteDance (guia do Seedance 2.0; teto de 1000 palavras); falhas comuns via plataformas (Runway).",
    },
    "wan": {
        "name": "Wan",
        "models": "Wan 3.0",
        "match": ("wan",),
        "max_chars": 5000, "video_max_words": 200, "video_max_words_timed": 250,
        "layout": "sections", "timeline": False,
        "dialogue": "[<personagem>, <tom da voz>]: '…'", "sound": "\"Sound: …\"",
        "hint": "imagem-para-vídeo: descreve só o movimento e a câmara, sem voltar a descrever a imagem; prompts detalhados "
                "dão melhor resultado do que os simples.",
        "source": "Alibaba Model Studio (guia oficial do Wan 3.0).",
    },
    "ltx": {
        "name": "LTX",
        "models": "LTX 2.5 Pro",
        "match": ("ltx",),
        "max_chars": 5000, "video_max_words": 150, "video_max_words_timed": 200,
        "layout": "paragraph", "timeline": False,
        "dialogue": '<personagem> says: "…"', "sound": "no próprio texto",
        "hint": "um só parágrafo, no presente, a começar pela ação; depois movimento, aparência, ambiente, câmara, luz e o que "
                "muda.",
        "source": "Lightricks (LTX-2, oficial: até 200 palavras).",
    },
    "minimax": {
        "name": "MiniMax H3",
        "models": "MiniMax H3, H3 Max",
        "match": ("minimax", "hailuo"),
        "max_chars": 7000, "video_max_words": 400, "video_max_words_timed": 500,
        "layout": "sections", "timeline": False,
        "dialogue": "<d>[English] …</d>", "sound": "\"Soundscape: …\" (1–4 frases) e \"Music: …\" (1–3)",
        "hint": "prompt longo e estruturado: primeiro ancora o estilo, os sujeitos e a composição da imagem, depois descreve a "
                "ação; câmara em prosa (tipo, amplitude, velocidade), sem comandos entre parênteses retos.",
        "source": "MiniMax (guia oficial do H3: 350–500 palavras; limite de 7000 caracteres).",
    },
    "veo": {
        "name": "Veo",
        "models": "Veo 3.1, Fast, Lite",
        "match": ("veo",),
        "max_chars": 4000, "video_max_words": 175, "video_max_words_timed": 220,
        "layout": "sections", "timeline": False,
        "dialogue": '<personagem> says in a <tom> voice: "…"', "sound": "\"SFX: …\" e \"Ambient noise: …\", cada um na sua linha",
        "hint": "fórmula oficial: câmara → sujeito → ação → contexto → estilo e ambiente; passado das ~175 palavras partes "
                "são ignoradas; o Lite não aceita imagens de referência.",
        "source": "Google Cloud (guia oficial do Veo 3.1; limite de 1024 tokens).",
    },
    "kling": {
        "name": "Kling",
        "models": "Kling v3, Kling O3",
        "match": ("kling",),
        "ref_format": "@Image{n}",  # o O3 por referências: image_urls nomeadas @Image1, @Image2… (esquema da fal)
        "max_chars": 2500, "video_max_words": 180, "video_max_words_timed": 220,
        "layout": "sections", "timeline": False,
        "dialogue": '[<personagem>, <tom>]: "…" (etiqueta única e sempre igual; o gesto antes da fala, sem pronomes)',
        "sound": "\"Sound: …\"",
        "hint": "cinco camadas: cena → personagens → ação → câmara → som e estilo; a imagem é a âncora, descreve como a cena "
                "evolui.",
        "source": "Kling (guias oficiais do Video 3.0 e 3.0 Omni); limite de 2500 caracteres via plataformas.",
    },
}
SECTION_LABELS = ("References", "Summary", "Scene and look", "Action", "Dialogue", "Sound", "Constraints")


def video_profile(model_name: str | None) -> dict:
    return VIDEO_PROFILES[profile_for(model_name, True)]


def video_format(model_name: str | None) -> str:
    """Como se monta o prompt de vídeo para este modelo (vai para o escritor e para a impressão digital do prompt)."""
    p = video_profile(model_name)
    layout = ("uma secção por linha, cada uma a começar pela etiqueta: " + ", ".join(f"\"{x}:\"" for x in SECTION_LABELS)
              if p["layout"] == "sections" else
              "um só parágrafo, sem etiquetas, pela mesma ordem (referências, resumo, cenário e aspeto, ação, fala, som, "
              "restrições)")
    action = ("Action: linha de tempo em segundos inteiros, sem buracos, até ao fim do clip (\"0s-3s: … 3s-6s: …\"; nunca meios segundos), cada "
              "bloco a continuar o mesmo take" if p["timeline"] else
              "Action: SEM tempos — a ação pela ordem, como um movimento contínuo (\"then, without a cut…\")")
    return (f"Formato do perfil {p['name']}: {layout}. {action}. Fala: {p['dialogue']}. Som: {p['sound']}. "
            f"Dicas: {p['hint']}")


IMAGE_FALLBACK, VIDEO_FALLBACK = "brief", "short"
IMAGE_FIELDS = ("image_max_words", "reference_max_words")
VIDEO_FIELDS = ("video_max_words", "video_max_words_timed")


def profile_for(model_name: str | None, video: bool) -> str:
    profiles, fallback = (VIDEO_PROFILES, VIDEO_FALLBACK) if video else (IMAGE_PROFILES, IMAGE_FALLBACK)
    name = (model_name or "").lower()
    for key, p in profiles.items():
        if any(m in name for m in p["match"]):
            return key
    return fallback


def default_image_model(session: Session) -> str:
    """Nome do modelo de imagem por omissão (Configuração), ex.: "GPT Image" ou "Seedream 5.0 Pro"."""
    from app import artlist
    from app.routes.gpt import default_generator, parse_generator

    provider, group = parse_generator(default_generator(session))
    if provider == "artlist":
        return artlist.model(group)["name"]
    if provider == "fal":
        from app import image_models

        return image_models.FAL[group]["name"]
    return "GPT Image"


def default_video_model(session: Session) -> str:
    """Nome do modelo de vídeo por omissão (Configuração), ex.: "Seedance 2.0 Mini"."""
    from app.routes.video import video_models

    return next((m["name"] for m in video_models(session) if m.get("default")), "")


def limits(cfg: dict, key: str, video: bool) -> dict:
    """Limites de um perfil: os editados em Configuração → Prompts ou os de origem."""
    base = (VIDEO_PROFILES if video else IMAGE_PROFILES)[key]
    stored = (cfg.get("profiles") or {}).get(key) or {}
    out = {}
    for field in (VIDEO_FIELDS if video else IMAGE_FIELDS):
        value = stored.get(field)
        if value is None and key == (VIDEO_FALLBACK if video else IMAGE_FALLBACK):
            value = cfg.get(field)  # limites antigos (um só conjunto) passam a ser os do perfil curto
        out[field] = int(value or base[field])
    return out


@memo
def active(session: Session) -> dict:
    """Perfis em uso (os dos modelos por omissão), com nome, modelo e dicas."""
    cfg = get_config(session, "prompt_checks", {}) or {}
    img_model, vid_model = default_image_model(session), default_video_model(session)
    img_key, vid_key = profile_for(img_model, False), profile_for(vid_model, True)
    return {
        "image": {"key": img_key, "model": img_model, **IMAGE_PROFILES[img_key], **limits(cfg, img_key, False)},
        "video": {"key": vid_key, "model": vid_model, **VIDEO_PROFILES[vid_key], **limits(cfg, vid_key, True)},
    }


def ref_format_for(model_name: str | None) -> str:
    """Sintaxe das referências numeradas para um modelo de vídeo (pelo nome)."""
    return VIDEO_PROFILES[profile_for(model_name, True)].get("ref_format") or "Image {n}"


def ref_format(session: Session) -> str:
    """Como o prompt de vídeo nomeia as imagens de referência (modo Referências), ex.: "Image {n}" ou "@image{n}"."""
    return active(session)["video"].get("ref_format") or "Image {n}"


def video_max_chars(session: Session, video_model: str | None = None) -> int:
    """Limite de caracteres do prompt de vídeo: o do modelo do plano (ou o de vídeo por omissão)."""
    p = video_profile(video_model) if video_model else active(session)["video"]
    return int(p.get("max_chars") or get_config(session, "prompt_max_chars", 2000))


def checks_config(session: Session, video_model: str | None = None) -> dict:
    """Configuração das validações com os limites dos perfis em uso (para prompt_checks.check). video_model: o modelo
    do plano (senão, o de vídeo por omissão)."""
    cfg = dict(get_config(session, "prompt_checks", {}) or {})
    act = active(session)
    if video_model:
        key = profile_for(video_model, True)
        act["video"] = {"key": key, "model": video_model, **VIDEO_PROFILES[key], **limits(cfg, key, True)}
    for kind in ("image", "video"):
        p = act[kind]
        cfg.update({f: p[f] for f in (VIDEO_FIELDS if kind == "video" else IMAGE_FIELDS)})
        cfg[f"{kind}_profile"] = f"{p['model']}, perfil {p['name']}"
    cfg["video_timeline"] = bool(act["video"].get("timeline"))
    return cfg


def all_profiles(cfg: dict) -> dict:
    """Perfis com os limites atuais, para a página Configuração → Prompts."""
    return {
        "image": [{"key": k, **p, **limits(cfg, k, False)} for k, p in IMAGE_PROFILES.items()],
        "video": [{"key": k, **p, **limits(cfg, k, True)} for k, p in VIDEO_PROFILES.items()],
    }
