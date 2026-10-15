"""Modelos de voz (vozes de referência das personagens), de qualquer fornecedor, pelo que cada um sabe fazer:

  catalog  fala o texto com uma voz do catálogo do fornecedor (escolhida pelo utilizador)
  design   cria a voz a partir de uma descrição ("warm baritone, light Portuguese accent") e fala o texto

O utilizador escolhe o modelo e a voz; a app não decide por ele. Cada modelo da fal diz em que campos vão o texto, a
voz e a descrição (lidos do esquema dele); o Artlist escolhe sozinho o modelo da voz escolhida.
"""

from sqlmodel import Session

from app.options import get_config, set_config

# fal: id → nome, capacidades e campos do pedido (do esquema público de cada modelo)
FAL_VOICE: dict[str, dict] = {
    "fal-ai/minimax/voice-design": {"name": "MiniMax Voice Design", "caps": {"design"},
                                    "text": "preview_text", "description": "prompt", "max_text": 500},
    "fal-ai/qwen-3-tts/voice-design/1.7b": {"name": "Qwen 3 TTS Voice Design", "caps": {"design"},
                                            "text": "text", "description": "prompt", "max_text": 2000},
    "elevenlabs/tts/eleven-v4": {"name": "ElevenLabs v4", "caps": {"catalog"}, "text": "text", "voice": "voice",
                                 "voice_hint": "nome de uma voz da ElevenLabs (ex.: Roger, George, Aria)", "max_text": 5000},
    "fal-ai/minimax/speech-2.8-hd": {"name": "MiniMax Speech 2.8 HD", "caps": {"catalog"}, "text": "prompt",
                                     "voice": "voice_setting.voice_id",
                                     "voice_hint": "id de uma voz da MiniMax (ex.: Deep_Voice_Man, Wise_Woman)",
                                     "max_text": 5000},
}
CAP_LABELS = {"catalog": "voz de catálogo", "design": "voz a partir da descrição"}


def options(session: Session) -> list[dict]:
    """Os modelos de voz disponíveis agora: [{value, label, provider, caps, voice_hint}]."""
    from app.routes.gpt import artlist_enabled, fal_enabled

    out = []
    if artlist_enabled(session):
        out.append({"value": "artlist", "label": "Artlist · vozes do catálogo (ElevenLabs, MiniMax, Cartesia)",
                    "provider": "artlist", "caps": {"catalog"}, "voice_hint": ""})
    if fal_enabled(session):
        for mid, m in FAL_VOICE.items():
            out.append({"value": f"fal:{mid}", "label": f"{m['name']} · fal.ai · {CAP_LABELS[next(iter(m['caps']))]}",
                        "provider": "fal", "caps": m["caps"], "voice_hint": m.get("voice_hint", "")})
    return out


def parse(value: str | None) -> tuple[str, str | None]:
    if value and value.startswith("fal:") and value[4:] in FAL_VOICE:
        return "fal", value[4:]
    return "artlist", None


def fal_request(model: str, text: str, voice: str | None, description: str | None) -> dict:
    """Os argumentos do pedido à fal, nos campos que o modelo usa."""
    m = FAL_VOICE[model]
    args: dict = {}

    def put(path: str, value) -> None:
        node = args
        *parents, last = path.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[last] = value

    put(m["text"], text[: m.get("max_text", 2000)])
    if "design" in m["caps"]:
        if not (description or "").strip():
            raise ValueError("Este modelo cria a voz a partir da descrição: escreve como é a voz.")
        put(m["description"], description.strip())
    if "catalog" in m["caps"]:
        if not (voice or "").strip():
            raise ValueError(f"Escolhe a voz ({m.get('voice_hint', 'nome da voz')}).")
        put(m["voice"], voice.strip())
    return args


# ── Vozes do catálogo do Artlist (lidas pela ligação MCP e guardadas, porque a lista demora a vir) ──

ARTLIST_KEY = "artlist_voices"


def artlist_voices(session: Session) -> dict:
    """{"voices": [{id, name, gender, age, accents, languages, preview}], "updated", "status", "error"}"""
    return get_config(session, ARTLIST_KEY, {}) or {}


def save_artlist_voices(session: Session, data: dict) -> None:
    set_config(session, ARTLIST_KEY, data)
