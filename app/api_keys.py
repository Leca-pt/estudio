"""Chaves de API dos fornecedores de modelos (texto, imagem, vídeo).

As chaves ficam cifradas (Fernet) na configuração, com uma chave derivada do SECRET_KEY do .env — que não entra
nos backups. Nunca voltam a ser mostradas: só os últimos 4 caracteres. Cada fornecedor pode ter um teste simples
(pedido só de leitura, ex.: listar modelos) para confirmar que a chave é válida."""

import base64
import hashlib
import time

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

ASSISTANT, IMAGES, VIDEO = "Assistente", "Imagens", "Vídeo"

# id → dados do fornecedor. fields: key (chave), secret (segunda chave), base_url (endpoint/região).
PROVIDERS: dict[str, dict] = {
    "anthropic": {"name": "Anthropic", "covers": "Claude (Opus, Sonnet, Haiku)", "uses": [ASSISTANT],
                  "url": "https://console.anthropic.com/settings/keys",
                  "test": ("GET", "https://api.anthropic.com/v1/models", "x-api-key")},
    "openai": {"name": "OpenAI", "covers": "ChatGPT / GPT, GPT Image, Sora", "uses": [ASSISTANT, IMAGES, VIDEO],
               "url": "https://platform.openai.com/api-keys", "test": ("GET", "https://api.openai.com/v1/models", "bearer")},
    "google": {"name": "Google (Gemini API)", "covers": "Gemini, Nano Banana (imagem Gemini), Imagen, Veo, outros Gemini",
               "uses": [ASSISTANT, IMAGES, VIDEO], "url": "https://aistudio.google.com/apikey",
               "test": ("GET", "https://generativelanguage.googleapis.com/v1beta/models", "query-key")},
    "mistral": {"name": "Mistral", "covers": "Mistral Large / Medium, Pixtral", "uses": [ASSISTANT],
                "url": "https://console.mistral.ai/api-keys", "test": ("GET", "https://api.mistral.ai/v1/models", "bearer")},
    "zai": {"name": "Z.ai (Zhipu)", "covers": "GLM, CogView (imagem), CogVideoX (vídeo)", "uses": [ASSISTANT, IMAGES, VIDEO],
            "url": "https://z.ai/manage-apikey/apikey-list", "test": ("GET", "https://api.z.ai/api/paas/v4/models", "bearer")},
    "alibaba": {"name": "Alibaba Cloud Model Studio", "covers": "Qwen, Qwen-Image, Wan (imagem e vídeo)",
                "uses": [ASSISTANT, IMAGES, VIDEO], "url": "https://modelstudio.console.alibabacloud.com/",
                "test": ("GET", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models", "bearer")},
    "deepseek": {"name": "DeepSeek", "covers": "DeepSeek (chat e reasoner)", "uses": [ASSISTANT],
                 "url": "https://platform.deepseek.com/api_keys", "test": ("GET", "https://api.deepseek.com/models", "bearer")},
    "minimax": {"name": "MiniMax", "covers": "MiniMax (texto), Hailuo (vídeo), image-01 (imagem)",
                "uses": [ASSISTANT, IMAGES, VIDEO], "url": "https://www.minimax.io/platform", "test": None},
    "xai": {"name": "xAI", "covers": "Grok, Grok Imagine (imagem e vídeo)", "uses": [ASSISTANT, IMAGES, VIDEO],
            "url": "https://console.x.ai/", "test": ("GET", "https://api.x.ai/v1/models", "bearer")},
    "byteplus": {"name": "BytePlus ModelArk (ByteDance)", "covers": "Seedance 2.0 / 2.5 (vídeo) e registo das personagens",
                 "uses": [VIDEO], "url": "https://console.byteplus.com/ark",
                 "fields": ["key", "access", "secret", "base_url"], "base_url": "https://ark.ap-southeast.bytepluses.com/api/v3",
                 "labels": {"key": "Chave de API (gera os vídeos)", "access": "Access Key ID (registo de personagens)",
                            "secret": "Secret Access Key", "base_url": "Endereço da API (região)"},
                 "test": "byteplus"},
    "bfl": {"name": "Black Forest Labs", "covers": "Flux (Pro, Kontext, Ultra)", "uses": [IMAGES],
            "url": "https://dashboard.bfl.ai/", "test": None},
    "kling": {"name": "Kling AI", "covers": "Kling (vídeo e imagem)", "uses": [IMAGES, VIDEO],
              "url": "https://app.klingai.com/global/dev/api-key", "fields": ["key", "secret"],
              "labels": {"key": "Access key", "secret": "Secret key"}, "test": None},
    "microsoft": {"name": "Microsoft Azure AI Foundry", "covers": "MAI-Image (imagem); outros modelos do Foundry",
                  "uses": [IMAGES], "url": "https://ai.azure.com/", "fields": ["key", "base_url"], "base_url": "",
                  "labels": {"base_url": "Endpoint do recurso"}, "test": None},
    "meta": {"name": "Meta (Llama API)", "covers": "Llama; Muse Image — API pública por confirmar", "uses": [ASSISTANT, IMAGES],
             "url": "https://llama.developer.meta.com/", "test": ("GET", "https://api.llama.com/v1/models", "bearer")},
    "runway": {"name": "Runway", "covers": "Gen-4 (vídeo e imagem)", "uses": [IMAGES, VIDEO],
               "url": "https://dev.runwayml.com/", "test": None},
    "luma": {"name": "Luma AI", "covers": "Ray (vídeo), Photon (imagem)", "uses": [IMAGES, VIDEO],
             "url": "https://lumalabs.ai/api/keys", "test": None},
    # Agregadores: uma chave para muitos modelos de vários fornecedores.
    "openrouter": {"name": "OpenRouter", "covers": "Agregador de LLMs: Claude, GPT, Gemini, Qwen, DeepSeek, GLM, Mistral, MiniMax…",
                   "uses": [ASSISTANT], "url": "https://openrouter.ai/settings/keys", "group": "Agregadores",
                   "test": ("GET", "https://openrouter.ai/api/v1/key", "bearer")},
    "fal": {"name": "fal.ai", "covers": "Agregador de imagem e vídeo: Flux, Seedream, Seedance, Wan, Kling, Veo, Nano Banana, Hailuo…",
            "uses": [IMAGES, VIDEO], "url": "https://fal.ai/dashboard/keys", "group": "Agregadores", "test": None},
    "replicate": {"name": "Replicate", "covers": "Agregador de imagem e vídeo (Flux, Wan, Kling, Seedance…)",
                  "uses": [IMAGES, VIDEO], "url": "https://replicate.com/account/api-tokens", "group": "Agregadores",
                  "test": ("GET", "https://api.replicate.com/v1/account", "bearer")},
}

# Fornecedores que a app usa de facto (as chaves dos outros não fazem nada: a página mostra só estes e deixa apagar as
# que ficaram guardadas de antes).
CONNECTED = ("openrouter", "fal", "byteplus")

# Os modelos que pediste → onde se chega a eles com uma chave (fornecedor direto primeiro, depois agregadores).
MODEL_MAP = [
    ("Claude", ["anthropic", "openrouter"]), ("ChatGPT / GPT", ["openai", "openrouter"]),
    ("Gemini", ["google", "openrouter"]), ("Mistral", ["mistral", "openrouter"]), ("GLM", ["zai", "openrouter"]),
    ("Qwen", ["alibaba", "openrouter"]), ("DeepSeek", ["deepseek", "openrouter"]), ("MiniMax (texto)", ["minimax", "openrouter"]),
    ("MAI-Image", ["microsoft"]), ("Nano Banana", ["google", "fal"]), ("Seedream", ["byteplus", "fal"]),
    ("Flux", ["bfl", "fal", "replicate"]), ("Wan", ["alibaba", "fal", "replicate"]), ("Muse Image", ["meta"]),
    ("Gemini (imagem/omni)", ["google"]), ("Seedance", ["byteplus", "fal", "replicate"]),
    ("MiniMax Hailuo (vídeo)", ["minimax", "fal"]), ("Kling", ["kling", "fal", "replicate"]), ("Veo", ["google", "fal"]),
    ("GPT Image / Sora", ["openai"]), ("Grok Imagine", ["xai"]), ("Runway", ["runway"]), ("Luma", ["luma"]),
]


SECRET_FIELDS = ("key", "secret", "access")  # guardados cifrados


def fields_of(pid: str) -> list[str]:
    return PROVIDERS[pid].get("fields", ["key"])


def _fernet() -> Fernet:
    digest = hashlib.sha256((settings.secret_key + "|api-keys").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None  # SECRET_KEY mudou: a chave tem de ser introduzida de novo


def get(stored: dict, pid: str, field: str = "key") -> str | None:
    """Valor em claro de uma chave guardada (só para usar em pedidos ao fornecedor, nunca para mostrar)."""
    entry = (stored or {}).get(pid) or {}
    return decrypt(entry.get(field)) if field in SECRET_FIELDS else entry.get(field)


def public_view(stored: dict) -> dict:
    """O que a página pode mostrar: últimos 4 caracteres, estado do teste, endpoint."""
    out = {}
    for pid in PROVIDERS:
        entry = (stored or {}).get(pid) or {}
        out[pid] = {
            "has_key": bool(entry.get("key")), "last4": entry.get("last4") or "",
            "has_secret": bool(entry.get("secret")), "has_access": bool(entry.get("access")), "base_url": entry.get("base_url") or PROVIDERS[pid].get("base_url", ""),
            "status": entry.get("status") or "", "message": entry.get("message") or "", "tested_at": entry.get("tested_at") or "",
            "updated_at": entry.get("updated_at") or "", "broken": bool(entry.get("key")) and decrypt(entry.get("key")) is None,
        }
    return out


async def test(stored: dict, pid: str) -> tuple[str, str]:
    """("ok"|"fail"|"untested", mensagem) — pedido só de leitura ao fornecedor."""
    spec = PROVIDERS[pid].get("test")
    if spec == "byteplus":  # duas autenticações: a chave de API e a Access Key / Secret Key
        from app import bytedance

        return await bytedance.test(stored)
    key = get(stored, pid)
    if not key:
        return "fail", "Sem chave guardada."
    if not spec:
        return "untested", "Este fornecedor ainda não tem teste automático; a chave fica guardada."
    method, url, auth = spec
    url = url.replace("{base_url}", (get(stored, pid, "base_url") or PROVIDERS[pid].get("base_url", "")).rstrip("/"))
    headers, params = {}, {}
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif auth == "x-api-key":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif auth == "query-key":
        params["key"] = key
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.request(method, url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        return "fail", f"Sem ligação ao fornecedor ({exc.__class__.__name__})."
    if resp.status_code < 300:
        return "ok", "Chave válida."
    if resp.status_code in (401, 403):
        return "fail", f"Chave recusada ({resp.status_code})."
    return "fail", f"O fornecedor respondeu {resp.status_code}."


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
