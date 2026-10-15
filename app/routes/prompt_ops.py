"""Blocos de prompt nas páginas (folha, retrato, vista, vídeo, capa): o que o bloco mostra e as ações dos botões.

Os botões ficam no formulário da página (o resto da página grava-se na mesma); a rota de gravar de cada página chama
save() e run(). Campos e ações:
  p:<kind>:<ref>              o texto do prompt (editado à mão → versão nova)
  gen:<kind>:<ref>            o modelo escolhido ao lado do Gerar
  op = pgen:<kind>:<ref>      Gerar: escreve primeiro se o prompt falta ou está desatualizado, e gera a seguir
  op = pwrite:<kind>:<ref>    Escrever (ou reescrever) com o Assistente
  op = prestore:<kind>:<ref>:<versão>   repor uma versão
  op = pwrite:zona:<asset>-<n>          escrever todas as vistas de uma zona
"""

from fastapi import Request
from sqlmodel import Session

from app import model_profiles, prompt_checks, prompt_targets as PT, prompt_versions, prompt_writer, ref_tokens
from app.forms import text
from app.models import Project
from app.routes.files import flash, flash_info

import re

CUT_RISK = re.compile(r"\b(?:at\s+)?\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?\s*s(?:ec(?:onds?)?)?\b", re.I)

STATE_LABELS = {"missing": "Por escrever", "manual": "Editado à mão", "assistant": "Escrito pelo escritor",
                "stale": "Desatualizado"}


def panel(session: Session, t: PT.Target, title: str, *, people: int = 0, dialogue: str | None = None) -> dict:
    """Tudo o que o bloco de prompt precisa (macro prompt_panel em _macros.html)."""
    current = t.prompt or ""
    st = PT.state(session, t)
    sent = PT.inputs(session, t)
    video = t.kind == "video"
    fmt, model = None, None
    if video:
        from app.routes.video import shot_model

        model = shot_model(session, t.obj)
        fmt = model_profiles.ref_format_for(model["name"]) if model and model.get("refs") else None
    kind = "video" if video else "reference"
    cfg = model_profiles.checks_config(session, model["name"] if model else None)
    warnings = (prompt_checks.check(current, kind, cfg, people=people,
                                    dialogue=dialogue, mode="references" if video else None) if current else [])
    warnings += ref_tokens.check(session, current, sent)
    if video and current and not cfg.get("video_timeline") and CUT_RISK.search(current):
        warnings.append("O prompt parte o take em blocos com tempos (ex.: «At 0-2 s»): este modelo lê cada bloco "
                        "como um plano novo e cortam a meio. Um take é um só plano contínuo: carrega em Reescrever.")
    unused = ref_tokens.unused(session, current, sent) if current else []
    if unused:
        warnings.append("Imagens que seguem mas o prompt não refere: " + ", ".join(unused) + ".")
    task = prompt_writer.task_for(session, t.project.id, t.kind, t.ref)
    return {
        "kind": t.kind, "ref": t.ref, "name": f"p:{t.kind}:{t.ref}", "title": title, "text": current, "state": st,
        "state_label": STATE_LABELS[st], "use": t.use, "video": video, "task": task,
        "busy": task is not None and task.status == "running",
        "versions": prompt_versions.versions(session, t.project.id, t.field_key),
        "version_takes": prompt_versions.video_takes(session, t.project.id, t.field_key, t.obj.id) if t.kind == "video" else {},
        "warnings": warnings, "legend": ref_tokens.legend(session, sent, fmt or ("Image {n}" if not video else None)),
        "mentions": ref_tokens.mentions(session, sent), "writer": prompt_writer.writer_label(session),
        "max_chars": model_profiles.video_max_chars(session, model["name"] if model else None) if video else 2000,
    }


def save(session: Session, project: Project, form, op: str = "") -> None:
    """Grava os prompts do formulário (os p:<kind>:<ref>) e a reposição de versões."""
    verb, _, rest = (op or "").partition(":")
    for key in list(form.keys()):
        if not key.startswith("p:"):
            continue
        _p, kind, ref = key.split(":", 2)
        try:
            t = PT.get(session, project, kind, ref)
        except PT.TargetError:
            continue
        transform = None
        if kind == "video":
            transform = lambda s, d=t.obj.dialogue: prompt_checks.quote_dialogue(s, d)  # noqa: E731
        prompt_versions.handle(session, project.id, t.obj, t.attr, t.field_key, form, key, "", transform=transform)
        session.add(t.obj)
    if verb == "prestore":
        kind, ref, vid = (rest.split(":") + ["", "", ""])[:3]
        try:
            t = PT.get(session, project, kind, ref)
        except PT.TargetError:
            return
        prompt_versions.handle(session, project.id, t.obj, t.attr, t.field_key, {}, "", f"restore_version:{vid}")
        session.add(t.obj)


def run(request: Request, session: Session, project: Project, form, op: str) -> bool:
    """Ações Gerar / Escrever. Devolve True se a op era uma destas (a página só tem de voltar para onde estava)."""
    verb, _, rest = (op or "").partition(":")
    if verb not in ("pgen", "pwrite", "pdismiss"):
        return False
    kind, _, ref = rest.partition(":")
    if verb == "pdismiss":
        prompt_writer.dismiss(session, project.id, kind, ref)
        return True
    try:
        targets = prompt_writer._targets(session, project, kind, ref)
    except (PT.TargetError, ValueError):
        flash(request, "Esse prompt já não existe.")
        return True
    if verb == "pwrite":
        if not targets:
            flash(request, "Não há nada para escrever aqui (faltam os renders: abre a maquete do local).")
        elif prompt_writer.start(session, project, kind, ref) is None:
            flash(request, "O Assistente já está a escrever aqui. Espera que termine.")
        return True
    t = targets[0]
    gen = {"audio": bool(form.get("audio"))} if kind == "video" else {"gen": text(form, f"gen:{kind}:{ref}"),
                                                                     "res": text(form, f"res:{kind}:{ref}")}
    if PT.needs_writing(session, t):
        if prompt_writer.start(session, project, kind, ref, generate=gen) is None:
            flash(request, "O Assistente já está a escrever este prompt. Espera que termine.")
        else:
            flash_info(request, f"O {prompt_writer.writer_label(session)} está a escrever o prompt; a geração começa "
                                "logo a seguir, sozinha.")
        return True
    ok, msg = PT.enqueue(session, t, gen)
    session.commit()
    if ok:
        from app import jobs

        jobs.wake()
        flash_info(request, msg)
    else:
        flash(request, msg)
    return True
