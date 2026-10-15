"""Configuração: listas dos drop-downs, modelos e prompts, conta."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session

from app import artlist, model_profiles
from app.db import get_session
from app.forms import FormError, integer, lines, text
from app.models import Credential, OptionList
from app.options import (
    NUMERIC_KEYS, OPTION_GROUPS, OPTION_KEYS, get_config, get_option_rows, list_labels,
    option_counts, rename_usages, set_config, usage_count,
)
from app.security import csrf_protect, require_login
from app.seeds import DEFAULT_PROMPT_GUIDES, GUIDE_LABELS
from app.templating import templates

router = APIRouter(prefix="/configuracao", dependencies=[Depends(require_login), Depends(csrf_protect)])

FIXED_SIZE_KEYS = {"project_type"}  # comportamento fixo: só se mudam os nomes


def _render(request: Request, template: str, section: str, ctx: dict, status_code: int = 200):
    base = {"in_config": True, "section": section, "error": None, "saved": request.query_params.get("guardado")}
    return templates.TemplateResponse(request, template, base | ctx, status_code=status_code)


@router.get("")
def config_home():
    return RedirectResponse("/configuracao/listas", status_code=303)


# ── Listas ───────────────────────────────────────────────────

def _lists_page(request: Request, session: Session, key: str, error=None, status_code=200):
    rows = get_option_rows(session, key)
    return _render(request, "config/lists.html", "listas", {
        "groups": OPTION_GROUPS,
        "labels": list_labels(session),
        "counts": option_counts(session),
        "key": key,
        "rows": rows,
        "usage": {r.id: usage_count(session, key, r.value) for r in rows},
        "fixed_size": key in FIXED_SIZE_KEYS,
        "numeric": key in NUMERIC_KEYS,
        "unit": {1: "segundos", 60: "minutos"}.get(NUMERIC_KEYS.get(key)),
        "error": error,
    }, status_code)


@router.get("/listas")
def lists_page(request: Request, lista: str = "genre", session: Session = Depends(get_session)):
    if lista not in OPTION_KEYS:
        raise HTTPException(status_code=404, detail="Lista inexistente.")
    return _lists_page(request, session, lista)


def _clean_label(key: str, raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise FormError("O rótulo não pode ficar vazio.")
    if key in NUMERIC_KEYS and (not value.isdigit() or int(value) < 1):
        unit = "minutos" if NUMERIC_KEYS[key] == 60 else "segundos"
        raise FormError(f"Nesta lista cada rótulo é um número inteiro de {unit} (ex.: {5 if unit == 'minutos' else 60}).")
    return value


@router.post("/listas/{key}")
async def list_save(request: Request, key: str, session: Session = Depends(get_session)):
    if key not in OPTION_KEYS:
        raise HTTPException(status_code=404)
    form = await request.form()
    op = text(form, "op") or ""
    verb, _, arg = op.partition(":")
    rows = get_option_rows(session, key)
    by_id = {r.id: r for r in rows}
    try:
        # Nome da lista
        if "list_label" in form:
            label = text(form, "list_label") or OPTION_KEYS[key]
            overrides = dict(get_config(session, "option_list_labels", {}) or {})
            overrides[key] = label
            set_config(session, "option_list_labels", overrides)

        # Edição dos itens existentes
        renames = []
        for id_raw, value_raw, prompt_raw in zip(form.getlist("item_id"), form.getlist("item_value"), form.getlist("item_prompt")):
            row = by_id.get(int(id_raw)) if id_raw.isdigit() else None
            if row is None:
                continue
            new_value = _clean_label(key, value_raw)
            if new_value != row.value:
                renames.append((row, row.value, new_value))
                row.value = new_value
            row.prompt_text = prompt_raw.strip() or None
        values = [r.value.casefold() for r in rows]
        if len(set(values)) != len(values):
            raise FormError("A lista tem rótulos repetidos.")
        for row, old, new in renames:
            rename_usages(session, key, old, new)
            session.add(row)
        for row in rows:
            session.add(row)

        # Operações
        if verb in ("up", "down") and arg.isdigit():
            idx = next((i for i, r in enumerate(rows) if r.id == int(arg)), None)
            j = None if idx is None else idx + (-1 if verb == "up" else 1)
            if idx is not None and 0 <= j < len(rows):
                rows[idx], rows[j] = rows[j], rows[idx]
        elif verb == "del" and arg.isdigit():
            row = by_id.get(int(arg))
            if row is None:
                raise HTTPException(status_code=404)
            if key in FIXED_SIZE_KEYS:
                raise FormError("Os tipos de projeto são sempre três; só podes mudar os nomes.")
            if usage_count(session, key, row.value):
                raise FormError(f"“{row.value}” está em uso e não pode ser removido (pode ser renomeado).")
            session.delete(row)
            rows.remove(row)
        elif verb == "add":
            if key in FIXED_SIZE_KEYS:
                raise FormError("Os tipos de projeto são sempre três; só podes mudar os nomes.")
            value = _clean_label(key, text(form, "new_value"))
            if value.casefold() in {r.value.casefold() for r in rows}:
                raise FormError(f"“{value}” já existe nesta lista.")
            row = OptionList(key=key, value=value, prompt_text=text(form, "new_prompt"))
            session.add(row)
            rows.append(row)
        for n, row in enumerate(rows):
            row.order = n
    except FormError as exc:
        session.rollback()
        return _lists_page(request, session, key, str(exc), 400)
    session.commit()
    suffix = "&guardado=1" if not op else ""
    return RedirectResponse(f"/configuracao/listas?lista={key}{suffix}", status_code=303)


# ── Modelos e prompts ────────────────────────────────────────

def _generation_ctx(session: Session) -> dict:
    """Fornecedores e modelos de imagem e vídeo para os seletores da Configuração."""
    from app.routes.gpt import default_generator, generator_options

    options = generator_options(session)
    img_groups = []
    if any(v == "gpt" for v, _l in options):
        img_groups.append(("gpt", "GPT Image · subscrição ChatGPT (Codex)", [("gpt", "GPT Image")]))
    arts = [(v, l) for v, l in options if v.startswith("artlist:")]
    if arts:
        img_groups.append(("artlist", "Artlist · créditos", arts))
    fals = [(v, l) for v, l in options if v.startswith("fal:")]
    if fals:
        img_groups.append(("fal", "fal.ai · créditos da tua conta fal", fals))
    stored = get_config(session, "api_keys", {}) or {}
    pending = lambda use: []  # noqa: E731 — só os fornecedores ligados (api_keys.CONNECTED) aparecem
    cfg = get_config(session, "image_generator", {}) or {}
    from app.routes.gpt import artlist_enabled
    from app.routes.video import video_models

    art_on = artlist_enabled(session)
    art_videos = [(f"artlist:{m['name']}", m["name"] + (" · vistas, elenco e vídeo-guia" if m.get("refs") else " · só imagem inicial")
                   + (f" · ≈ {m['cost']} cr. (480p, 5 s)" if m.get("cost") else ""))
                  for m in sorted(artlist.VIDEO_MODELS.values(), key=lambda m: not m.get("refs"))]
    vid_default = "artlist:" + next((m["name"] for m in video_models(session) if m.get("default")), "")
    from app.options import video_settings

    vs = video_settings(session)
    from app.routes.gpt import fal_enabled

    fal_videos = [(f"fal:{m['name']}", m["name"] + " · " + m.get("cost_text", "")) for m in video_models(session)
                  if m.get("provider") == "fal"]
    default_video = next((m for m in video_models(session) if m.get("default")), None)
    if default_video and default_video.get("provider") in ("fal", "bytedance"):
        vid_default = default_video["provider"] + ":" + default_video["name"]
    bd_videos = [(f"bytedance:{m['name']}", m["name"] + (" · " + m["cost_text"] if m.get("cost_text") else ""))
                 for m in video_models(session) if m.get("provider") == "bytedance"]
    vid_groups = [("artlist", "Artlist · créditos" + ("" if art_on else " (não ligado: gerar fora da app)"), art_videos)]
    if fal_enabled(session) and fal_videos:
        vid_groups.insert(0, ("fal", "fal.ai · créditos da tua conta fal (vídeo-guia: sim)", fal_videos))
    if bd_videos:
        vid_groups.insert(0, ("bytedance", "ByteDance · a tua conta BytePlus (vídeo-guia: sim; personagens registadas)", bd_videos))
    return {
        "img_groups": img_groups, "img_default": default_generator(session),
        "img_edit": cfg.get("edit") if cfg.get("edit") in {v for v, _l in options} else "",
        "img_pending": pending("Imagens"),
        "vid_groups": vid_groups,
        "video_quality": vs["quality"], "video_audio": vs["audio"],
        "art_on": art_on,
        "vid_default": vid_default, "vid_pending": pending("Vídeo"),
        "active_profiles": model_profiles.active(session),
        **_fal_ctx(session, stored),
    }


def _fal_ctx(session: Session, stored: dict) -> dict:
    """Secção "Modelos da fal.ai": o catálogo (no modal de seleção) e os ativos."""
    from app import api_keys, image_models

    if not api_keys.get(stored, "fal"):
        return {"fal_on": False}
    catalog = get_config(session, "fal_catalog", {}) or {}
    active = get_config(session, "fal_models", {}) or {}
    groups = {"text-to-image": [], "image-to-image": []}
    for m in catalog.get("models", []):
        if m["id"] in image_models.RECOMMENDED:
            continue
        groups.setdefault(m["category"], []).append((m["id"], None, f"{m['name']} · {m['id']}"))
    return {"fal_on": True, "fal_catalog_n": len(catalog.get("models", [])), "fal_updated": catalog.get("updated"),
            "fal_active": active, "fal_recommended": [image_models.FAL[k]["name"] for k in image_models.RECOMMENDED],
            "fal_groups": [("Texto → imagem", groups["text-to-image"]), ("Imagem → imagem (edição, referências, "
                           "estrutura e ferramentas)", groups["image-to-image"])]}


# A antiga página única dividida por tema; cada página grava só as suas secções.
PAGES = {
    "modelos": ("Modelos e geração", ["geracao"]),
    "ligacoes": ("Ligações", ["claude", "gpt", "artlist"]),
    "prompts": ("Prompts", ["guias", "regras", "validacoes"]),
    "assistente": ("Assistente", ["assistente"]),
}


def _prompts_page(request: Request, session: Session, error=None, draft=None, status_code=200, page: str = "modelos"):
    data = draft or {
        "guides": {**DEFAULT_PROMPT_GUIDES, **(get_config(session, "prompt_guides", {}) or {})},
        "checks": get_config(session, "prompt_checks", {}) or {},
        "profiles": model_profiles.all_profiles(get_config(session, "prompt_checks", {}) or {}),
        "rules": get_config(session, "prompt_rules", []),
        "max_chars": get_config(session, "prompt_max_chars", 2000),
        "warn_enabled": get_config(session, "prompt_warn_enabled", True),
        "assistant": get_config(session, "assistant", {}) or {},
        "gpt": get_config(session, "gpt_image", {}) or {},
        "gpt_status": _gpt_status(session),
        "artlist": get_config(session, "artlist", {}) or {},
        "artlist_connected": artlist.connected(),
        "artlist_msg": request.session.pop("artlist_msg", None),
        "artlist_login_url": request.session.get("artlist_login_url"),
        "artlist_models": artlist.MODELS,
        **_generation_ctx(session),
    }
    page = page if page in PAGES else "modelos"
    extra = {}
    if page == "ligacoes":
        extra = {"codex": _codex_view(request), "claude_status": _claude_status()}
    if page == "assistente":
        from app import api_keys
        from app.assistant import openrouter

        or_key = api_keys.get(get_config(session, "api_keys", {}) or {}, "openrouter")
        from app.assistant import llm as claude_llm

        extra = {"or_key": bool(or_key), "or_models": [], "or_info": None, "or_free_only": True,
                 "claude_aliases": claude_llm.ALIASES, "claude_models": list(claude_llm.models())}
        cur_claude = (data.get("assistant") or {}).get("model")
        if cur_claude and cur_claude not in {a[0] for a in claude_llm.ALIASES} | {m["id"] for m in extra["claude_models"]}:
            extra["claude_models"].insert(0, {"id": cur_claude, "name": cur_claude})
        if or_key:
            import httpx

            try:
                extra["or_info"] = httpx.get(f"{openrouter.API}/key", headers={"Authorization": f"Bearer {or_key}"},
                                             timeout=10).json().get("data")
            except (httpx.HTTPError, ValueError):
                pass
            # Plano grátis: só os modelos gratuitos (os pagos falham sem saldo). Com saldo: todos.
            extra["or_free_only"] = not extra["or_info"] or bool(extra["or_info"].get("is_free_tier"))
            # Lista completa: os grátis primeiro; os pagos também aparecem (no plano grátis precisam de saldo).
            all_or = openrouter.models(free_only=False)
            extra["or_models"] = [m for m in all_or if m["free"]] + [m for m in all_or if not m["free"]]
            current = (data.get("assistant") or {}).get("openrouter_model")
            if current and current not in {m["id"] for m in extra["or_models"]}:
                extra["or_models"].insert(0, {"id": current, "name": current, "json": False, "images": False})
        extra["llm_pending"] = []
    return _render(request, "config/prompts.html", page, data | extra | {
        "page": page, "page_title": PAGES[page][0], "sections": PAGES[page][1],
        "guide_labels": GUIDE_LABELS, "error": error,
        "options_aspect": ["16:9", "9:16", "1:1", "4:3", "3:4"],
    }, status_code)


def _gpt_status(session: Session) -> dict:
    from datetime import datetime, timedelta, timezone

    from sqlmodel import func, select

    from app import gpt_image
    from app.models import ImageJob

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    counts = dict(session.exec(select(ImageJob.status, func.count()).where(ImageJob.created_at >= since,
                                                                          ImageJob.provider == "gpt")
                               .group_by(ImageJob.status)).all())
    return {"logged_in": gpt_image.configured(), "counts": counts}


@router.get("/modelos")
def prompts_page(request: Request, session: Session = Depends(get_session)):
    return _prompts_page(request, session, page="modelos")


@router.get("/ligacoes")
def connections_page(request: Request, session: Session = Depends(get_session)):
    return _prompts_page(request, session, page="ligacoes")


def _claude_status() -> dict:
    from app.assistant import llm
    from app.config import settings

    if not llm.cli_path():
        return {"ok": False, "message": "O Claude Code CLI não está instalado no contentor."}
    if not (settings.claude_code_oauth_token or settings.anthropic_api_key):
        return {"ok": False, "message": "Falta o token (CLAUDE_CODE_OAUTH_TOKEN no .env do servidor)."}
    return {"ok": True, "message": "Token configurado no servidor. É o motor por omissão do Assistente."}


def _codex_view(request: Request) -> dict:
    from app import gpt_image

    state = gpt_image.login_state()
    msg = request.session.pop("codex_msg", None)
    return state | {"logged_in": gpt_image.configured(), "account": gpt_image.account(),
                    "message": state.get("message") or msg}


@router.post("/ligacoes/codex/ligar")
def codex_connect(request: Request):
    from app import gpt_image

    try:
        gpt_image.start_login()
    except Exception as exc:
        request.session["codex_msg"] = str(exc)
    return RedirectResponse("/configuracao/ligacoes#gpt-image", status_code=303)


@router.get("/ligacoes/codex/estado")
def codex_state(request: Request):
    from app.templating import is_htmx

    view = _codex_view(request)
    if view.get("state") in ("done", "idle") and is_htmx(request):
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "config/_codex_login.html", {"codex": view})


@router.post("/ligacoes/codex/cancelar")
def codex_cancel(request: Request):
    from app import gpt_image

    if gpt_image.cancel_login():
        request.session["codex_msg"] = "Login cancelado; o acesso anterior continua."
    return RedirectResponse("/configuracao/ligacoes#gpt-image", status_code=303)


@router.post("/ligacoes/codex/desligar")
def codex_disconnect(request: Request):
    from app import gpt_image

    gpt_image.logout()
    return RedirectResponse("/configuracao/ligacoes#gpt-image", status_code=303)


@router.get("/prompts")
def prompts_rules_page(request: Request, session: Session = Depends(get_session)):
    return _prompts_page(request, session, page="prompts")


@router.get("/assistente")
def assistant_page(request: Request, session: Session = Depends(get_session)):
    return _prompts_page(request, session, page="assistente")


def _move_item(items: list, idx: int, delta: int) -> None:
    j = idx + delta
    if 0 <= idx < len(items) and 0 <= j < len(items):
        items[idx], items[j] = items[j], items[idx]


@router.post("/modelos")
async def prompts_save(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    page = text(form, "page") if text(form, "page") in PAGES else "modelos"
    sec = set(PAGES[page][1])
    op = text(form, "op") or ""
    verb, _, arg = op.partition(":")
    idx = int(arg) if arg.isdigit() else -1

    # Guias do escritor de prompts (uma por tipo de prompt) e as proporções das folhas e retratos
    guides = {k: (text(form, f"guide_{k}") or "").replace("\r\n", "\n").strip() or DEFAULT_PROMPT_GUIDES[k]
              for k in GUIDE_LABELS}
    for k in ("sheet_aspect", "portrait_aspect"):
        guides[k] = text(form, k) or DEFAULT_PROMPT_GUIDES[k]

    # Validações (listas editáveis, uma entrada por linha)
    checks = {}
    profiles = {}
    for video, table in ((False, model_profiles.IMAGE_PROFILES), (True, model_profiles.VIDEO_PROFILES)):
        for pkey, base in table.items():
            profiles[pkey] = {}
            for field in (model_profiles.VIDEO_FIELDS if video else model_profiles.IMAGE_FIELDS):
                try:
                    profiles[pkey][field] = integer(form, f"p_{pkey}_{field}", "Limite de palavras", minimum=1) or base[field]
                except FormError:
                    profiles[pkey][field] = base[field]
    checks["profiles"] = profiles
    for key in ("motion_words", "camera_words", "fast_words", "empty_adjectives", "format_words"):
        checks[key] = lines(form, key)

    # Assistente
    try:
        timeout_s = integer(form, "asst_timeout", "Tempo limite", minimum=10) or 120
    except FormError:
        timeout_s = 120
    assistant = (get_config(session, "assistant", {}) or {}) | {"timeout_s": min(timeout_s, 600)}
    from app.assistant import llm as claude_llm

    engine, _, chosen = (text(form, "asst_choice") or "").partition(":")
    if engine == "openrouter" and chosen:
        assistant.update({"engine": "openrouter", "openrouter_model": chosen})
    elif engine == "claude" and claude_llm.valid_model(chosen):
        assistant.update({"engine": "claude", "model": chosen})
    w_engine, _, w_chosen = (text(form, "writer_choice") or "").partition(":")  # modelo do escritor de prompts
    if w_engine == "openrouter" and w_chosen:
        assistant["writer"] = {"engine": "openrouter", "openrouter_model": w_chosen}
    elif w_engine == "claude" and claude_llm.valid_model(w_chosen):
        assistant["writer"] = {"engine": "claude", "model": w_chosen}
    try:
        gpt_timeout = integer(form, "gpt_timeout", "Tempo limite", minimum=30) or 300
    except FormError:
        gpt_timeout = 300
    gpt = {"enabled": bool(form.get("gpt_enabled")), "timeout_s": min(gpt_timeout, 900)}
    artlist_cfg = (get_config(session, "artlist", {}) or {}) | {
        "enabled": bool(form.get("artlist_enabled")),
        "quality": text(form, "artlist_quality") if text(form, "artlist_quality") in ("1k", "2k") else "1k",
        "parallel": int(text(form, "artlist_parallel")) if (text(form, "artlist_parallel") or "") in ("1", "2", "3", "4") else 3,
    }
    artlist_cfg["model_name"] = artlist.model(artlist_cfg.get("model_group_id"))["name"]

    # Regras
    rules = [{"text": t.strip(), "active": str(i) in form.getlist("r_active")}
             for i, t in enumerate(form.getlist("r_text"))]

    # Operações
    if verb == "add_rule":
        rules.append({"text": "", "active": True})
    elif verb == "del_rule" and 0 <= idx < len(rules):
        rules.pop(idx)

    draft = {"guides": guides, "rules": rules, "assistant": assistant, "gpt": gpt,
             "gpt_status": _gpt_status(session), "checks": checks,
             "profiles": model_profiles.all_profiles(checks),
             "max_chars": text(form, "max_chars"), "warn_enabled": bool(form.get("warn_enabled"))}
    try:
        max_chars = (integer(form, "max_chars", "Limite de caracteres", minimum=1) or 2000) if "regras" in sec else 2000
        if not op:
            rules[:] = [r for r in rules if r["text"]]
    except FormError as exc:
        return _prompts_page(request, session, str(exc), draft, 400, page=page)

    video_default = text(form, "video_default") or ""
    if "geracao" in sec:  # vídeo: o modelo por omissão, a resolução e o som valem para todos os fornecedores
        vcfg = dict(get_config(session, "video_generator", {}) or {})
        default_changed = False
        if video_default.startswith(("artlist:", "fal:", "bytedance:")):
            default_changed = vcfg.get("default") != video_default.partition(":")[2]
            vcfg["default"] = video_default.partition(":")[2]
        if text(form, "video_quality") in ("480p", "720p", "1080p"):
            vcfg["quality"] = text(form, "video_quality")
        vcfg["audio"] = bool(form.get("video_audio"))
        set_config(session, "video_generator", vcfg)
        if default_changed:  # cada projeto tem o seu padrão (etapa Vídeo); este vale para os projetos sem padrão
            request.session["flash_info"] = (f"Modelo por omissão: {vcfg['default']}. Vale para os projetos novos; cada "
                                             "projeto tem o seu padrão na etapa Vídeo.")
    # Só as secções desta página (as outras páginas têm os seus próprios campos).
    if "guias" in sec:
        set_config(session, "prompt_guides", guides)
    if "regras" in sec:
        set_config(session, "prompt_max_chars", max_chars)
        set_config(session, "prompt_warn_enabled", bool(form.get("warn_enabled")))
    if "validacoes" in sec:
        set_config(session, "prompt_checks", checks)
    if "regras" in sec:
        set_config(session, "prompt_rules", rules)
    if "assistente" in sec:
        set_config(session, "assistant", assistant)
    if "gpt" in sec:
        set_config(session, "gpt_image", gpt)
    if "artlist" in sec:
        set_config(session, "artlist", artlist_cfg)
    if "geracao" in sec and text(form, "image_generator_default"):
        img_default, img_edit = text(form, "image_generator_default"), text(form, "image_generator_edit") or ""
        set_config(session, "image_generator", {"default": img_default, "edit": img_edit})
        # O Artlist usa estes modelos quando um pedido não traz modelo escolhido.
        acfg = get_config(session, "artlist", {}) or {}
        if img_default.startswith("artlist:"):
            acfg["model_group_id"] = int(img_default.split(":")[1])
        acfg["edit_model_group_id"] = int(img_edit.split(":")[1]) if img_edit.startswith("artlist:") else None
        acfg["model_name"] = artlist.model(acfg.get("model_group_id"))["name"]
        set_config(session, "artlist", acfg)
    session.commit()
    return RedirectResponse(f"/configuracao/{page}" + ("?guardado=1" if not op else ""), status_code=303)


# ── Artlist (login OAuth da conta, uma vez) ─────────────────

def _public_origin(request: Request) -> str:
    """O endereço público (PUBLIC_URL; atrás do nginx o pedido chega em http)."""
    from app.config import settings

    origin = str(request.base_url).rstrip("/")
    return origin.replace("http://", "https://", 1) if settings.cookie_secure else origin


@router.post("/artlist/ligar")
def artlist_connect(request: Request, session: Session = Depends(get_session)):
    """Começa o login (Claude Code como cliente MCP) e mostra o endereço do Artlist."""
    try:
        artlist.disconnect_marker()
        request.session["artlist_login_url"] = artlist.start_login()
        cfg = get_config(session, "artlist", {}) or {}
        set_config(session, "artlist", cfg | {"public_base": _public_origin(request)})
        session.commit()
    except Exception as exc:
        request.session["artlist_msg"] = f"Não foi possível começar o login no Artlist: {exc}"
    return RedirectResponse("/configuracao/ligacoes#artlist", status_code=303)


@router.post("/artlist/concluir")
async def artlist_finish(request: Request):
    form = await request.form()
    try:
        from starlette.concurrency import run_in_threadpool

        await run_in_threadpool(artlist.finish_login, text(form, "redirect_url") or "")
        request.session.pop("artlist_login_url", None)
        request.session["artlist_msg"] = "ok"
    except Exception as exc:
        request.session["artlist_msg"] = str(exc)
    return RedirectResponse("/configuracao/ligacoes#artlist", status_code=303)


@router.post("/artlist/desligar")
def artlist_disconnect(request: Request):
    artlist.disconnect()
    return RedirectResponse("/configuracao/ligacoes#artlist", status_code=303)


@router.get("/artlist/saldo")
async def artlist_balance(request: Request):
    data = await artlist.balance() if artlist.connected() else None
    return templates.TemplateResponse(request, "config/_artlist_balance.html", {"b": data})


# ── Chaves de API ────────────────────────────────────────────

def _keys_page(request: Request, session: Session, error=None):
    from app import api_keys

    stored = get_config(session, "api_keys", {}) or {}
    groups = {"Ligadas à app": [(pid, api_keys.PROVIDERS[pid]) for pid in api_keys.CONNECTED]}
    leftover = [(pid, api_keys.PROVIDERS[pid]["name"]) for pid in stored
                if pid in api_keys.PROVIDERS and pid not in api_keys.CONNECTED]
    return _render(request, "config/keys.html", "chaves", {
        "groups": groups, "leftover": leftover, "view": api_keys.public_view(stored),
        "providers": api_keys.PROVIDERS, "fields_of": api_keys.fields_of, "error": error,
        "msg": request.session.pop("keys_msg", None),
    })


@router.get("/chaves")
def keys_page(request: Request, session: Session = Depends(get_session)):
    return _keys_page(request, session)


@router.post("/chaves")
async def keys_save(request: Request, session: Session = Depends(get_session)):
    from app import api_keys

    form = await request.form()
    op = text(form, "op") or ""
    verb, _, target = op.partition(":")
    stored = dict(get_config(session, "api_keys", {}) or {})
    changed = []
    for pid in api_keys.CONNECTED:
        entry = dict(stored.get(pid) or {})
        key, secret = (form.get(f"k_{pid}_key") or "").strip(), (form.get(f"k_{pid}_secret") or "").strip()
        if key:
            entry.update({"key": api_keys.encrypt(key), "last4": key[-4:], "updated_at": api_keys.now(),
                          "status": "", "message": ""})
            changed.append(pid)
        if secret:
            entry["secret"] = api_keys.encrypt(secret)
            changed.append(pid)
        access = (form.get(f"k_{pid}_access") or "").strip()
        if access:
            entry["access"] = api_keys.encrypt(access)
            changed.append(pid)
        base_url = (text(form, f"k_{pid}_base_url") or "").strip()
        if base_url or "base_url" in entry:
            entry["base_url"] = base_url
        if entry.get("key") or entry.get("secret") or entry.get("access") or entry.get("base_url"):
            stored[pid] = entry
        else:
            stored.pop(pid, None)  # sem chave nem endpoint: não guarda uma entrada vazia
    if verb == "remove" and target in stored:
        stored.pop(target)
    if verb == "remove_unused":  # chaves de fornecedores que a app não usa
        for pid in [p for p in stored if p not in api_keys.CONNECTED]:
            stored.pop(pid)
    tests = set(changed) | ({target} if verb == "test" and target in stored else set())
    for pid in tests:  # testa logo as chaves novas (pedido só de leitura)
        status, message = await api_keys.test(stored, pid)
        stored[pid].update({"status": status, "message": message, "tested_at": api_keys.now()})
    set_config(session, "api_keys", stored)
    session.commit()
    if verb == "remove_unused":
        request.session["keys_msg"] = "Chaves dos fornecedores sem ligação removidas."
    elif verb == "remove":
        request.session["keys_msg"] = f"Chave de {api_keys.PROVIDERS[target]['name']} removida."
    elif changed or tests:
        request.session["keys_msg"] = "Guardado." + (f" Testadas: {', '.join(api_keys.PROVIDERS[p]['name'] for p in tests)}." if tests else "")
    anchor = f"#k-{target}" if target else ""
    return RedirectResponse("/configuracao/chaves" + anchor, status_code=303)


# ── Conta ────────────────────────────────────────────────────

@router.get("/conta")
def account_page(request: Request, cred: Credential = Depends(require_login)):
    return _render(request, "config/account.html", "conta", {"email": cred.email})


# ── Modelos da fal.ai (catálogo completo) ────────────────────

def _fal_key(session: Session) -> str | None:
    from app import api_keys

    return api_keys.get(get_config(session, "api_keys", {}) or {}, "fal")


@router.post("/fal/catalogo")
def fal_catalog_refresh(request: Request, session: Session = Depends(get_session)):
    """Descarrega a lista atual dos modelos de imagem da fal (não gasta créditos)."""
    from app import fal_catalog
    from app.routes.files import flash, flash_info

    key = _fal_key(session)
    if not key:
        flash(request, "Guarda primeiro a chave da fal.ai em Chaves de API.")
    else:
        try:
            catalog = fal_catalog.refresh(key)
            set_config(session, "fal_catalog", catalog)
            session.commit()
            flash_info(request, f"Catálogo da fal atualizado: {len(catalog['models'])} modelos de imagem.")
        except Exception as exc:  # rede, resposta inesperada
            flash(request, f"Não foi possível obter o catálogo da fal ({exc.__class__.__name__}).")
    return RedirectResponse("/configuracao/modelos#fal", status_code=303)


@router.post("/fal/modelos")
async def fal_models_save(request: Request, session: Session = Depends(get_session)):
    """Modelos do catálogo que aparecem nos botões Gerar: lê o esquema de cada um novo (o que sabe fazer e como se
    pede) e o preço; os que a app não sabe chamar ficam de fora, com o motivo."""
    import asyncio

    from app import fal, fal_catalog, image_models
    from app.routes.files import flash, flash_info

    form = await request.form()
    names = {m["id"]: m["name"] for m in (get_config(session, "fal_catalog", {}) or {}).get("models", [])}
    chosen = [e for e in dict.fromkeys(form.getlist("fal_model")) if e in names and e not in image_models.RECOMMENDED]
    current = get_config(session, "fal_models", {}) or {}
    kept = {e: current[e] for e in chosen if e in current}
    new = [e for e in chosen if e not in current][:40]
    failed = []
    for endpoint in new:
        try:
            adapter = await asyncio.to_thread(fal_catalog.adapter, endpoint)
            kept[endpoint] = {"name": names[endpoint], "adapter": adapter}
        except fal_catalog.Unusable as exc:
            failed.append(f"{names[endpoint]} ({exc})")
        except Exception as exc:
            failed.append(f"{names[endpoint]} (esquema indisponível: {exc.__class__.__name__})")
    key = _fal_key(session)
    if key and new:
        prices = await fal.prices(key, [e for e in new if e in kept])
        for e, p in prices.items():
            if e in kept and p.get("unit_price") is not None:
                unit = {"image": "imagem", "images": "imagem", "megapixels": "MP"}.get(p.get("unit"), p.get("unit") or "")
                kept[e]["price"] = f"≈ ${p['unit_price']}" + (f" /{unit}" if unit else "")
    set_config(session, "fal_models", kept)
    session.commit()
    image_models.sync(kept)
    flash_info(request, f"{len(kept)} modelo(s) do catálogo ativos nos botões Gerar"
               + (f"; {len(new) - len(failed)} novo(s)." if new else "."))
    if failed:
        flash(request, "Ficaram de fora (a app não os sabe chamar): " + "; ".join(failed[:8])
              + ("…" if len(failed) > 8 else ""))
    return RedirectResponse("/configuracao/modelos#fal", status_code=303)
