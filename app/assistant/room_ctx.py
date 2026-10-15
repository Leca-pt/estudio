"""O Assistente na Sala de argumentistas (app/writing.py): o que vê (a sala ou um ficheiro inteiro) e o que pode propor —
comentários, ficheiros novos, tarefas, mudanças por partes ou o texto inteiro de um ficheiro em escrita, e propostas
formais para os outros participantes darem parecer. Tudo só é gravado com «Aplicar», assinado «Assistente».

Chaves dos campos:
  room_comment            comentário na conversa geral          room_comment:<f>        comentário num ficheiro
  room_new_file           ficheiro novo                         room_task               tarefa nova
  room_edit:<f>:<v>       mudanças por partes (sobre a versão v) room_text:<f>:<v>       texto inteiro (sobre a versão v)
  room_proposal:<f>:<v>   proposta formal (sobre a versão v)
A versão na chave é a base da fusão a três: se o ficheiro mudou entretanto, as mudanças juntam-se quando não chocam.
"""

import json

from sqlmodel import Session

from app import writing
from app.models import Project, WritingFile

AUTHOR = "Assistente"
TEXT_LIMIT = 60_000  # acima disto, o texto inteiro não é proposto (só mudanças por partes)
VIEW_LIMIT = 150_000  # o que o Assistente lê de um ficheiro


class _Err(Exception):
    pass


def _fs(key, label, kind, current=None, help=""):
    from app.assistant.fields import FieldSpec

    return FieldSpec(key, label, kind, current=current, help=help)


def _room_summary(session: Session, project: Project) -> str:
    fs = writing.files(session, project.id)
    rows = ["SALA DE ARGUMENTISTAS — ficheiros de texto do projeto, escritos e discutidos por várias IAs (ex.: ChatGPT, "
            f"Claude) e pelo utilizador ({writing.USER}). Tu és o «{AUTHOR}». Só o utilizador aprova para a app."]
    rows.append("FICHEIROS: " + ("; ".join(
        f"{f.path} (v{f.version}, {'aprovado' if f.status == 'approved' else 'em escrita'}, {len(f.content)} car.)"
        for f in fs) or "nenhum"))
    props = writing.proposals(session, project.id)
    if props:
        rows.append("PROPOSTAS ABERTAS: " + "; ".join(f"#{p.number} «{p.title}» de {p.author}" for p in props[:15]))
    tasks = writing.tasks(session, project.id, "active")
    if tasks:
        rows.append("TAREFAS: " + "; ".join(f"#{t.number} {t.title} ({writing.TASK_STATUS[t.status]}, {t.assignee or 'qualquer um'})"
                                            for t in tasks[:15]))
    return "\n".join(rows)


def _thread(rows) -> str:
    return "\n".join(f"#{c.id} {writing.author_label(c.author)}"
                     + (f" (resposta ao #{c.parent_id})" if c.parent_id else "") + f": {c.text[:1200]}" for c in rows)


def _common_fields(ctx, session: Session, project: Project) -> None:
    people = ", ".join("@" + p for p in writing.participants(session, project.id))
    ctx.add(_fs("room_comment", "Sala › Conversa geral › Comentário", "room_comment", help=(
        f"value = o texto do comentário na conversa geral, assinado «{AUTHOR}». Menciona com {people} ou @todos para "
        "notificar.")))
    ctx.add(_fs("room_new_file", "Sala › Ficheiro novo", "room_new_file", help=(
        'value = JSON {"path": "pasta/nome.md", "content": "texto em markdown", "message": "porquê"}. Caminho livre com '
        "pastas (ex.: «Notas/ideias.md»).")))
    ctx.add(_fs("room_task", "Sala › Tarefa nova", "room_task", help=(
        'value = JSON {"title": "…", "details": "…", "assignee": "<nome de um participante ou vazio>", "path": '
        '"<ficheiro, opcional>"}.')))


def build(session: Session, project: Project, key: str, base: str):
    from app.assistant.contexts import Context

    kind, _, raw = key.partition(":")
    if kind == "room":
        rows = writing.comments(session, project.id)[-25:]
        ctx = Context(key, "Sala de argumentistas", 2, "\n\n".join(
            [base, _room_summary(session, project), "CONVERSA GERAL (mais recentes):\n" + (_thread(rows) or "—")]))
        _common_fields(ctx, session, project)
        ctx.suggestions = ["Resume o que se discutiu na sala", "O que está por decidir?", "Que ficheiros precisam de revisão?"]
        return ctx
    f = session.get(WritingFile, int(raw)) if raw.isdigit() else None
    if f is None or f.project_id != project.id:
        raise _Err("Ficheiro não encontrado.")
    path = writing.original_path(f)
    body = f.content if len(f.content) <= VIEW_LIMIT else (
        f.content[:VIEW_LIMIT] + f"\n\n[… cortado: o ficheiro tem {len(f.content)} caracteres; vês os primeiros {VIEW_LIMIT}.]")
    meta = (f"FICHEIRO ABERTO: {path} · versão {f.version} · "
            + ("APROVADO E TRANCADO (só se comenta; para mudar, o utilizador destranca)" if f.status == "approved" else "em escrita")
            + (" · APAGADO" if f.deleted_at else "")
            + (f" · destino na app: {writing.target_label(session, project, f.target)}" if f.target else ""))
    if f.target and f.target.startswith("scenes:"):
        meta += "\n" + writing.SCRIPT_FORMAT
    rows = writing.comments(session, project.id, f.id)[-20:]
    open_props = writing.proposals(session, project.id, "open", f.id)
    ctx = Context(key, f"Sala › {path.rsplit('/', 1)[-1]}", 2, "\n\n".join(
        [base, _room_summary(session, project), meta, "TEXTO DO FICHEIRO:\n" + body,
         "DISCUSSÃO DESTE FICHEIRO:\n" + (_thread(rows) or "—"),
         ("PROPOSTAS ABERTAS DESTE FICHEIRO: " + "; ".join(f"#{p.number} «{p.title}» de {p.author}" for p in open_props))
         if open_props else ""]))
    _common_fields(ctx, session, project)
    people = ", ".join("@" + p for p in writing.participants(session, project.id))
    ctx.add(_fs(f"room_comment:{f.id}", f"{path} › Comentário", "room_comment", help=(
        f"value = o texto do comentário sobre este ficheiro. Menciona com {people} ou @todos para notificar.")))
    if f.status == "draft" and not f.deleted_at:
        v = f.version
        ctx.add(_fs(f"room_edit:{f.id}:{v}", f"{path} › Mudanças por partes", "room_edits", help=(
            'value = JSON {"edits": [{"old": "texto EXATO que aparece uma só vez no ficheiro", "new": "o texto novo"}], '
            '"message": "o que mudou e porquê"}. É a forma preferida: muda só o que é preciso. Copia "old" tal e qual '
            "(com a pontuação e as quebras de linha), com contexto suficiente para ser único.")))
        if len(f.content) <= TEXT_LIMIT:
            ctx.add(_fs(f"room_text:{f.id}:{v}", f"{path} › Texto inteiro", "room_text", current=f.content, help=(
                "value = o ficheiro INTEIRO, já com as mudanças (nunca um excerto). Só para reescritas grandes; para "
                "mudanças pontuais usa as mudanças por partes.")))
        ctx.add(_fs(f"room_proposal:{f.id}:{v}", f"{path} › Proposta para discutir", "room_proposal", help=(
            'value = JSON {"title": "…", "rationale": "porquê", "impacts": "impactos na continuidade", "decisions": '
            '"o que precisa da decisão do utilizador", "edits": [{"old": "…", "new": "…"}]}. Para mudanças que os outros '
            "participantes devem rever antes de entrarem no ficheiro.")))
    ctx.suggestions = (["Revê este texto e aponta problemas", "Resume a discussão deste ficheiro"]
                       + (["Corrige a continuidade com o resto da sala"] if f.status == "draft" else []))
    return ctx


# ── Validar e aplicar ────────────────────────────────────────

def _json(value) -> dict:
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        raise ValueError("Formato inválido (JSON).")
    if not isinstance(data, dict):
        raise ValueError("Formato inválido (JSON com campos).")
    return data


def _edits(data: dict) -> list[dict]:
    edits = data.get("edits")
    if not isinstance(edits, list) or not edits or not all(isinstance(e, dict) and e.get("old") for e in edits):
        raise ValueError('Faltam as mudanças: "edits": [{"old": "…", "new": "…"}].')
    return [{"old": str(e["old"]), "new": str(e.get("new") or "")} for e in edits]


def validate(session: Session, project: Project, kind: str, key: str, value) -> str:
    """Devolve o valor normalizado (texto ou JSON) ou levanta ValueError com a razão."""
    if kind == "room_comment":
        text = str(value or "").strip()
        if not text:
            raise ValueError("Comentário vazio.")
        return text
    if kind == "room_text":
        text = str(value or "").replace("\r\n", "\n")
        if not text.strip():
            raise ValueError("Texto vazio.")
        return text
    data = _json(value)
    if kind == "room_new_file":
        path = writing.clean_path(str(data.get("path") or ""))
        if writing.get(session, project.id, path):
            raise ValueError(f"Já existe «{path}».")
        return json.dumps({"path": path, "content": str(data.get("content") or ""),
                           "message": str(data.get("message") or "")}, ensure_ascii=False)
    if kind == "room_task":
        if not str(data.get("title") or "").strip():
            raise ValueError("A tarefa precisa de título.")
        if str(data.get("assignee") or "").strip():
            writing._who(session, project.id, str(data["assignee"]))  # tem de ser um participante (ou vazio)
        return json.dumps({k: str(data.get(k) or "") for k in ("title", "details", "assignee", "path")}, ensure_ascii=False)
    if kind == "room_edits":
        f = _file(session, project, key)
        out = writing._apply_edits(f.content, _edits(data), f"«{f.path}»")  # confirma já que cada «old» existe uma vez
        if out == f.content:
            raise ValueError("As mudanças não mudam nada.")
        return json.dumps({"edits": _edits(data), "message": str(data.get("message") or "")}, ensure_ascii=False)
    if kind == "room_proposal":
        f = _file(session, project, key)
        if not str(data.get("title") or "").strip() or not str(data.get("rationale") or "").strip():
            raise ValueError("Uma proposta precisa de título e de justificação.")
        writing._apply_edits(f.content, _edits(data), f"«{f.path}»")
        return json.dumps({k: data.get(k) or "" for k in ("title", "rationale", "impacts", "decisions")}
                          | {"edits": _edits(data)}, ensure_ascii=False)
    raise ValueError("Campo desconhecido.")


def _file(session: Session, project: Project, key: str) -> WritingFile:
    parts = key.split(":")
    f = session.get(WritingFile, int(parts[1])) if len(parts) > 1 and parts[1].isdigit() else None
    if f is None or f.project_id != project.id or f.deleted_at:
        raise ValueError("O ficheiro já não existe.")
    return f


def apply(session: Session, project: Project, key: str, value: str) -> None:
    """Aplica uma proposta aceite (com a fusão a três quando o ficheiro mudou desde a versão da chave)."""
    try:
        with writing.lock:
            _apply(session, project, key, value)
    except writing.WritingError as exc:
        raise ValueError(str(exc))


def _apply(session: Session, project: Project, key: str, value: str) -> None:
    kind = key.split(":")[0]
    if kind == "room_comment":
        f = _file(session, project, key) if ":" in key else None
        writing.comment(session, project, value, AUTHOR, f.path if f else None)
        return
    if kind == "room_new_file":
        d = json.loads(value)
        writing.write(session, project, d["path"], d["content"], AUTHOR, d.get("message") or "Criado pelo Assistente")
        return
    if kind == "room_task":
        d = json.loads(value)
        writing.create_task(session, project, AUTHOR, d["title"], d.get("details") or "", d.get("assignee") or None,
                            d.get("path") or None)
        return
    f = _file(session, project, key)
    base_version = int(key.split(":")[2])
    if kind == "room_text":
        writing.write(session, project, f.path, value, AUTHOR, "Texto revisto pelo Assistente", base_version=base_version)
        return
    d = json.loads(value)
    base = writing.version_text(session, f, base_version)
    proposed = writing._apply_edits(base, d["edits"], f"«{f.path}» v{base_version}")
    if kind == "room_edit":
        writing.write(session, project, f.path, proposed, AUTHOR, d.get("message") or "Mudanças do Assistente",
                      base_version=base_version)
        return
    if kind == "room_proposal":
        writing.propose(session, project, f.path, AUTHOR, d["title"], d["rationale"], d.get("impacts") or "",
                        d.get("decisions") or "", content=proposed, base_version=base_version)
        return
    raise ValueError("Campo desconhecido.")
