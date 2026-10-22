"""Escritor de prompts (docs/DECISOES.md): um modelo (Configuração → Assistente → "Modelo para escrever prompts") escreve
TODOS os prompts da app — folhas, retratos, vistas da maquete, vídeo e capa. A app não monta prompts: dá-lhe o
contexto completo (bíblia, mundo, regras, cena, plano, elenco), a guia do tipo de prompt e as imagens, e grava o que
ele escreve (com versão e a impressão digital dos dados, para saber quando fica desatualizado).

Corre em segundo plano. Cada trabalho fica numa PromptTask (sobrevive a reinícios: o que estava a meio fica como
falhado, para repetir). Com "generate", a geração é pedida logo a seguir (o botão Gerar faz tudo num clique).

Trabalhos: kind/ref de app/prompt_targets.py, mais "zona" / "<asset>-<n.º da zona>" (todas as vistas de uma zona).
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlmodel import Session, select

from app import prompt_targets as PT
from app.config import settings
from app.db import engine
from app.models import Asset, Project, PromptTask
from app.options import get_config

log = logging.getLogger(__name__)
TIMEOUT = 600
TASKS: set = set()  # referências fortes às tarefas em curso (o asyncio só guarda referências fracas)
MAX_IMAGES = 20


# ── Motor ─────────────────────────────────────────────────────

def writer_cfg(session: Session) -> dict:
    cfg = (get_config(session, "assistant", {}) or {}).get("writer") or {}
    return {"engine": cfg.get("engine") or "claude", "model": cfg.get("model") or "sonnet",
            "openrouter_model": cfg.get("openrouter_model")}


def writer_label(session: Session) -> str:
    c = writer_cfg(session)
    if c["engine"] == "openrouter":
        return (c["openrouter_model"] or "OpenRouter").split("/")[-1]
    return "Claude " + str(c["model"]).removeprefix("claude-")


def _ask(session: Session):
    from app.assistant import llm

    c = writer_cfg(session)
    if c["engine"] == "openrouter":
        from app import api_keys
        from app.assistant import openrouter

        key = api_keys.get(get_config(session, "api_keys", {}) or {}, "openrouter")

        async def ask(system, prompt, images):
            if not key:
                raise llm.LLMError("Falta a chave do OpenRouter (Configuração → Chaves de API).")
            return await openrouter.ask(key, system, prompt, c["openrouter_model"] or "openrouter/free", TIMEOUT, images,
                                        referer=settings.public_url)
        return ask

    async def ask(system, prompt, images):
        return await llm.ask(system, prompt, c["model"], TIMEOUT, images)
    return ask


SYSTEM = """És o escritor de prompts de uma app de produção de vídeo com IA. Escreves, em INGLÊS, o prompt COMPLETO que
vai para o modelo de imagem ou de vídeo: a app não lhe junta nada.
Para cada item recebes o que se gera, a guia desse tipo de prompt, as imagens que seguem para o gerador (pela ordem de
envio, cada uma com o seu marcador) e o contexto do projeto. Regras:
- Segue a guia do item e as REGRAS DE PROMPT do projeto. A bíblia manda no estilo e nas exclusões.
- Refere cada imagem que segue SEMPRE pelo marcador indicado (ex.: @PRJ_ANA, @PRJ_BAR.v20, @PRJ_BAR.r20, @Video1),
  nunca por "Image 1" nem por números: a app troca cada marcador pela sintaxe do modelo no envio, pela ordem certa.
- As imagens marcadas "só para ti" (fotografias reais, previs) NÃO vão para o gerador: usa-as para descrever por
  palavras o que precisares, sem lhes dar marcador.
- Descreve o que as imagens mostram mesmo; a geometria vem do render ou do previs indicado.
- Frases concretas e visuais, sem adjetivos vazios ("beautiful", "stunning"), sem marcas nem texto legível, sem
  formato nem resolução no texto.
- Respeita o limite de palavras (imagens) ou de caracteres (vídeo) de cada item.
Responde no formato pedido: "reply" curto em português, e em "proposals" um item por prompt com "field" = o id do
item (exatamente) e "value" = o prompt."""

REVISE_SYSTEM = """Recebes um prompt (em inglês) de um modelo de imagem ou de vídeo e um pedido do utilizador (em
português). Devolve o prompt COMPLETO já com a alteração pedida. Regras:
- Muda só o que o pedido diz; o resto fica igual, palavra por palavra (incluindo os marcadores @CÓDIGO, o bloco de
  estilo e as exclusões).
- Olha para as imagens: não acrescentes nada que contradiga a geometria do render ou do previs.
- Inglês claro e visual, sem adjetivos vazios, sem marcas nem texto legível.
Responde no formato pedido: "reply" = uma frase curta em português a dizer o que mudaste; "proposals" = um item com
"field": "prompt" e "value" = o prompt completo."""


# ── Estado ────────────────────────────────────────────────────

def task_for(session: Session, project_id: int, kind: str, ref: str) -> PromptTask | None:
    """O trabalho a mostrar no botão: a correr, ou o último que falhou (até se voltar a carregar)."""
    return session.exec(select(PromptTask).where(PromptTask.project_id == project_id, PromptTask.kind == kind,
                                                 PromptTask.ref == str(ref), PromptTask.status.in_(("running", "failed")))
                        .order_by(PromptTask.id.desc())).first()


def busy_views(session: Session, project_id: int) -> set[int]:
    """Vistas com um trabalho de escrita a correr (sozinhas ou pela zona)."""
    out = set()
    for t in session.exec(select(PromptTask).where(PromptTask.project_id == project_id, PromptTask.status == "running",
                                                   PromptTask.kind.in_(("vista", "zona")))).all():
        if t.kind == "vista" and t.ref.isdigit():
            out.add(int(t.ref))
        out.update(int(v) for v in (t.request or {}).get("views") or [])
    return out


def recover() -> None:
    """No arranque: os trabalhos que estavam a meio ficam como falhados (o botão mostra e deixa repetir)."""
    with Session(engine) as session:
        for t in session.exec(select(PromptTask).where(PromptTask.status == "running")).all():
            t.status, t.error = "failed", "A app reiniciou a meio. Carrega outra vez."
            session.add(t)
        for t in session.exec(select(PromptTask).where(PromptTask.status == "done")).all():
            session.delete(t)
        session.commit()


def _targets(session: Session, project: Project, kind: str, ref: str) -> list[PT.Target]:
    if kind == "zona":
        from app import location3d as L

        asset_id, _, zi = ref.partition("-")
        asset = session.get(Asset, int(asset_id)) if asset_id.isdigit() else None
        if asset is None or asset.project_id != project.id:
            raise PT.TargetError("Local não encontrado.")
        zones = [z["name"] for z in L.zones(asset.layout3d)]
        zone = zones[int(zi) - 1] if zi.isdigit() and 0 < int(zi) <= len(zones) else None
        return PT.zone_targets(session, project, asset, zone)
    return [PT.get(session, project, kind, ref)]


def start(session: Session, project: Project, kind: str, ref: str, generate: dict | None = None) -> PromptTask | None:
    """Começa a escrever (e, com generate, a gerar a seguir). None se já há um trabalho a correr para aqui."""
    current = task_for(session, project.id, kind, ref)
    if current is not None and current.status == "running":
        return None
    if current is not None:
        session.delete(current)
    targets = _targets(session, project, kind, ref)
    task = PromptTask(project_id=project.id, kind=kind, ref=str(ref), model=writer_label(session), generate=generate,
                      request={"views": [int(t.ref) for t in targets if t.kind == "vista"]} if kind == "zona" else None)
    session.add(task)
    session.commit()
    _spawn(_run(task.id))
    return task


def dismiss(session: Session, project_id: int, kind: str, ref: str) -> None:
    current = task_for(session, project_id, kind, ref)
    if current is not None and current.status == "failed":
        session.delete(current)
        session.commit()


def _spawn(coro) -> None:
    t = asyncio.get_event_loop().create_task(coro)
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)


def _finish(task_id: int, status: str, error: str | None = None, result: dict | None = None) -> None:
    with Session(engine) as session:
        task = session.get(PromptTask, task_id)
        if task is None:
            return
        task.status, task.error = status, (error[:400] if error else None)
        if result is not None:
            task.result = result
        session.add(task)
        session.commit()


# ── Pedido ao modelo ──────────────────────────────────────────

def limit_text(session: Session, t: PT.Target, gen: dict | None) -> str:
    """O limite e as dicas do perfil do modelo que vai gerar (o escolhido no botão, ou o da Configuração)."""
    from app import model_profiles

    cfg = get_config(session, "prompt_checks", {}) or {}
    if t.kind == "video":
        from app.routes.video import shot_model

        name = (shot_model(session, t.obj) or {}).get("name")
        key = model_profiles.profile_for(name, True)
        p = model_profiles.VIDEO_PROFILES[key]
        lim = model_profiles.limits(cfg, key, True)
        return (f"modelo {name or '—'} (perfil {p['name']}): até {lim['video_max_words_timed']} palavras e "
                f"{p.get('max_chars', 4000)} caracteres. {model_profiles.video_format(name)}")
    from app.routes.gpt import default_generator, generator_label

    label = generator_label(session, (gen or {}).get("gen") or default_generator(session, use=t.use, project=t.project))
    key = model_profiles.profile_for(label, False)
    p = model_profiles.IMAGE_PROFILES[key]
    n = model_profiles.limits(cfg, key, False)["reference_max_words"]  # as frases sobre as imagens também contam
    return f"modelo {label} (perfil {p['name']}): até {n} palavras. Dicas: {p['hint']}"


def _payload(session: Session, project: Project, targets: list[PT.Target], gen: dict | None):
    """(texto do pedido, imagens anexadas [(caminho, rótulo)])."""
    from app import ref_tokens
    from app.media import media_root

    attached: list = []  # (MediaFile, rótulo)
    index: dict[int, int] = {}

    def attach(m, label) -> int | None:
        if m is None or not (media_root() / m.path).is_file():
            return None
        if m.id not in index:
            if len(attached) >= MAX_IMAGES:
                return None
            attached.append((m, label))
            index[m.id] = len(attached)
        return index[m.id]

    items = []
    for t in targets:
        lines = []
        for m in PT.inputs(session, t):
            token, desc = ref_tokens.token_for(session, m)
            n = attach(m, f"{token} — {desc}")
            lines.append(f"    {token}: {desc}" + (f" (imagem anexada {n})" if n else ""))
        for m in PT.voices(session, t):
            token, desc = ref_tokens.token_for(session, m)
            lines.append(f"    {token}: {desc} — áudio que segue para o modelo; usa-o na fala dessa personagem")
        if PT.video_guide(session, t) is not None:
            lines.append("    @Video1: o vídeo-guia (previs cinzento da cena 3D; não vai anexado: vê o previs dos momentos)")
        only = []
        for m, label in PT.seen_only(session, t):
            n = attach(m, f"só para ti — {label}")
            if n:
                only.append(f"    imagem anexada {n}: {label}")
        indent = lambda txt: "\n".join("    " + ln for ln in txt.splitlines())  # noqa: E731
        items.append(
            f"- id: {t.field_key}\n  o quê: {t.what}\n  guia:\n{indent(PT.guide_text(session, t))}"
            + "\n  imagens que seguem para o gerador, por ordem (usa os marcadores):\n" + ("\n".join(lines) or "    nenhuma")
            + ("\n  só para ti (não seguem):\n" + "\n".join(only) if only else "")
            + f"\n  limite: {limit_text(session, t, gen)}"
            + f"\n  contexto:\n{indent(PT.context(session, t))}"
            + (f"\n  prompt atual (melhora-o ou reescreve-o): {t.prompt[:1500]}" if (t.prompt or "").strip() else ""))
    text = (PT.shared_context(session, project) + "\n\nIMAGENS ANEXADAS:\n"
            + ("\n".join(f"Imagem anexada {n}: {label}" for n, (_m, label) in enumerate(attached, 1)) or "(nenhuma)")
            + "\n\nITENS:\n" + "\n".join(items) + "\n\nEscreve um prompt completo por item.")
    return text, [(media_root() / m.path, label) for m, label in attached]


async def _run(task_id: int) -> None:
    try:
        with Session(engine) as session:
            task = session.get(PromptTask, task_id)
            project = session.get(Project, task.project_id)
            targets = _targets(session, project, task.kind, task.ref)
            if not targets:
                raise ValueError("Não há nada para escrever (faltam os renders das vistas: abre a maquete do local).")
            text, images = _payload(session, project, targets, task.generate)
            ask = _ask(session)
        data = await ask(SYSTEM, text, images)
        got = {str(p.get("field")): str(p.get("value") or "").strip() for p in data.get("proposals") or []
               if isinstance(p, dict)}
        for _attempt in range(GUIDE_RETRIES):  # o prompt de vídeo tem de seguir o vídeo-guia: se não segue, volta atrás
            with Session(engine) as session:
                bad = _guide_errors(session, task_id, got)
            if not bad:
                break
            log.info("prompt writer task %s: guide mismatch, retrying: %s", task_id, bad)
            data = await ask(SYSTEM, text + "\n\n" + "\n\n".join(f"Item {k}:\n{v}" for k, v in bad.items()), images)
            got.update({str(p.get("field")): str(p.get("value") or "").strip() for p in data.get("proposals") or []
                        if isinstance(p, dict) and str(p.get("value") or "").strip()})
        from app import prompt_versions

        messages = []
        with Session(engine) as session:
            task = session.get(PromptTask, task_id)
            project = session.get(Project, task.project_id)
            targets = _targets(session, project, task.kind, task.ref)
            done = 0
            for t in targets:
                value = got.get(t.field_key)
                if not value:
                    continue
                setattr(t.obj, t.attr, value)
                if t.kind == "video":  # para avisar se o plano mudar de modelo depois
                    from app.routes.video import shot_model

                    t.obj.video_prompt_model = (shot_model(session, t.obj) or {}).get("name")
                session.add(t.obj)
                session.flush()
                prompt_versions.record(session, project.id, t.field_key, value, "assistente", basis=PT.basis(session, t),
                                       parts=PT.basis_parts(session, t))
                done += 1
            session.commit()
            if not done:
                raise ValueError("O modelo não devolveu prompts.")
            if task.generate is not None:  # Gerar: o pedido segue logo
                for t in targets:
                    ok, msg = PT.enqueue(session, t, task.generate)
                    session.commit()
                    if not ok:
                        messages.append(msg)
                from app import jobs

                jobs.wake()
        if messages:
            _finish(task_id, "failed", "O prompt foi escrito, mas a geração não começou: " + " ".join(messages))
        else:
            _finish(task_id, "done")
    except Exception as exc:  # noqa: BLE001 — fica visível no botão
        log.warning("prompt writer task %s failed: %s", task_id, exc)
        _finish(task_id, "failed", str(exc))


GUIDE_RETRIES = 2


def _guide_errors(session: Session, task_id: int, got: dict[str, str]) -> dict[str, str]:
    """{id do item: o que corrigir} para os prompts de vídeo que contradizem o vídeo-guia (guide_check)."""
    from app import guide_check

    task = session.get(PromptTask, task_id)
    project = session.get(Project, task.project_id)
    out = {}
    for t in _targets(session, project, task.kind, task.ref):
        if t.kind == "video" and got.get(t.field_key):
            issues = [i for i in guide_check.check(session, t.obj, got[t.field_key]) if i["level"] == "error"]
            if issues:
                out[t.field_key] = guide_check.feedback(issues) + "\nPROMPT ANTERIOR:\n" + got[t.field_key]
    return out


# ── Rever um prompt (mini chat no modal do prompt) ─────────────

def revise_start(session: Session, project: Project, kind: str, ref: str, prompt: str, instruction: str) -> PromptTask:
    PT.get(session, project, kind, ref)  # valida o alvo
    task = PromptTask(project_id=project.id, kind="rever", ref=f"{kind}:{ref}", model=writer_label(session),
                      request={"prompt": prompt, "instruction": instruction})
    session.add(task)
    session.commit()
    _spawn(_revise(task.id))
    return task


async def _revise(task_id: int) -> None:
    from app import ref_tokens
    from app.media import media_root

    try:
        with Session(engine) as session:
            task = session.get(PromptTask, task_id)
            project = session.get(Project, task.project_id)
            kind, _, ref = task.ref.partition(":")
            t = PT.get(session, project, kind, ref)
            images = []
            for m in PT.inputs(session, t):
                token, desc = ref_tokens.token_for(session, m)
                images.append((media_root() / m.path, f"{token} — {desc}"))
            images += [(media_root() / m.path, f"só para ti — {label}") for m, label in PT.seen_only(session, t)]
            images = [(p, label) for p, label in images if p.is_file()][:MAX_IMAGES]
            req = dict(task.request or {})
            ask = _ask(session)
            facts = (PT._frame_block(session, t.obj) + "\nO texto NUNCA contradiz o vídeo-guia (de frente, de perfil, de "
                     "costas, pela ordem acima): o modelo segue o texto." if kind == "video" and t.obj.scene3d else "")
        numbered = "\n".join(f"Imagem anexada {n}: {label}" for n, (_p, label) in enumerate(images, 1))
        question = (f"{numbered}\n\n" + (f"O QUE O VÍDEO-GUIA (@Video1) MOSTRA:\n{facts}\n\n" if facts else "")
                    + f"PROMPT ATUAL:\n{req.get('prompt')}\n\nPEDIDO DO UTILIZADOR:\n{req.get('instruction')}")
        new = ""
        for _attempt in range(1 + (GUIDE_RETRIES if facts else 0)):
            data = await ask(REVISE_SYSTEM, question, images)
            new = next((str(p.get("value") or "").strip() for p in data.get("proposals") or []
                        if isinstance(p, dict) and p.get("value")), "") or new
            if not facts or not new:
                break
            from app import guide_check

            with Session(engine) as session:
                issues = [i for i in guide_check.check(session, PT.get(session, session.get(Project, task.project_id),
                                                                       kind, ref).obj, new) if i["level"] == "error"]
            if not issues:
                break
            question += "\n\n" + guide_check.feedback(issues) + "\nPROMPT QUE DEVOLVESTE:\n" + new
        if not new:
            raise ValueError("O modelo não devolveu o prompt.")
        _finish(task_id, "done", result={"prompt": new, "reply": str(data.get("reply") or "").strip()})
    except Exception as exc:  # noqa: BLE001
        log.warning("prompt revise %s failed: %s", task_id, exc)
        _finish(task_id, "failed", str(exc))


def revision(session: Session, project_id: int, task_id: int) -> dict:
    task = session.get(PromptTask, task_id)
    if task is None or task.project_id != project_id or task.kind != "rever":
        return {"status": "error", "error": "Pedido perdido. Tenta outra vez."}
    if task.status == "running":
        return {"status": "running"}
    if task.status == "failed":
        return {"status": "error", "error": task.error}
    return {"status": "done", **(task.result or {})}


def elapsed(task: PromptTask) -> int:
    created = task.created_at if task.created_at.tzinfo else task.created_at.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - created).total_seconds())
