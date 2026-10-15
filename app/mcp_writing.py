"""Ferramentas MCP da Sala de argumentistas (app/writing.py): ficheiros de texto do projeto com versões e discussão,
partilhados pelas IAs ligadas ao conector (ex.: ChatGPT e Claude) e pelo utilizador. Aprovar, aceitar e destrancar só
na app, pelo utilizador."""

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlmodel import Session, select

from app import writing
from app.db import engine
from app.mcp_server import PUBLIC, READ, WRITE, _project, log, server
from app.models import McpClient

STATUS = {"draft": "em escrita", "approved": "aprovado e trancado"}


def _author() -> str:
    """Quem chama: o nome com que o cliente MCP se registou (ex.: «Claude», «ChatGPT»)."""
    tok = get_access_token()
    if tok is None:
        return "IA"
    with Session(engine) as s:
        c = s.exec(select(McpClient).where(McpClient.client_id == tok.client_id)).first()
        name = str(((c.data or {}).get("client_name") if c else "") or "").strip()
    return name[:40] or "IA"


def _when(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _run(fn):
    """Corre uma gravação da sala numa transação; os erros da sala chegam à IA como texto."""
    with writing.lock, Session(engine) as s:
        try:
            out = fn(s)
            s.commit()
            return out
        except writing.WritingError as exc:
            s.rollback()
            raise ToolError(str(exc))


def _room_url(project_id: int, f=None) -> str:
    return f"{PUBLIC}/projetos/{project_id}/sala" + (f"?f={f.id}" if f else "")


@server.tool(annotations=READ, structured_output=True, title="Ficheiros da sala",
             description="SALA DE ARGUMENTISTAS: lista os ficheiros de texto do projeto (história, guiões, notas) com o "
                         "estado (em escrita ou aprovado e trancado), a versão, quem mexeu por último, o destino na app "
                         "e o número de comentários, mais as propostas abertas, as tarefas ativas e as tuas notificações "
                         "por ler. Começa sempre por my_notifications e isto.")
def list_files(project_id: int) -> dict[str, Any]:
    with Session(engine) as s:
        p = _project(s, project_id)
        counts = writing.comment_counts(s, p.id)
        rows = [{"path": f.path, "status": STATUS[f.status], "version": f.version,
                 "approved_version": f.approved_version, "last_author": writing.author_label(f.last_author),
                 "updated": _when(f.updated_at), "target": f.target,
                 "target_label": writing.target_label(s, p, f.target) if f.target else None,
                 "chars": len(f.content), "comments": counts.get(f.id, 0)} for f in writing.files(s, p.id)]
        last = writing.comments(s, p.id, everything=True, limit=1)
        me = _author()
        return {"project": p.name, "you": me, "unread_notifications": writing.unread_count(s, me, p.id),
                "files": rows, "general_comments": counts.get(None, 0),
                "open_proposals": len(writing.proposals(s, p.id)), "active_tasks": len(writing.tasks(s, p.id, "active")),
                "deleted_files": [{"path": writing.original_path(x), "deleted_by": x.deleted_by, "when": _when(x.deleted_at)}
                                  for x in writing.deleted_files(s, p.id)],
                "last_comment_id": last[-1].id if last else None, "room": _room_url(p.id)}


@server.tool(annotations=READ, title="Ler ficheiro",
             description="Lê um ficheiro da sala (o texto inteiro), na versão atual ou numa antiga (version). Para mudar "
                         "o texto inteiro, write_file com base_version = a versão que leste; para mudar partes, edit_file.")
def read_file(project_id: int, path: str, version: int | None = None) -> str:
    with Session(engine) as s:
        p = _project(s, project_id)
        try:
            f = writing.need(s, p.id, path)
            text = writing.version_text(s, f, version)
        except writing.WritingError as exc:
            raise ToolError(str(exc))
        head = [f"FICHEIRO {f.path} · versão {version or f.version} de {f.version} · {STATUS[f.status]}"
                + (f" (a app usa a versão {f.approved_version})" if f.approved_version else ""),
                f"Última mudança: {writing.author_label(f.last_author)}, {_when(f.updated_at)}"]
        if f.target:
            head.append(f"Destino na app ao aprovar: {writing.target_label(s, p, f.target)} ({f.target})")
            if f.target.startswith("scenes:"):
                head.append("É um guião: segue o formato de list_targets.")
        return "\n".join(head) + "\n" + "─" * 40 + "\n" + text


@server.tool(annotations=WRITE, title="Escrever ficheiro",
             description="Cria um ficheiro na sala ou substitui o texto inteiro (fica uma versão nova, com o teu nome e "
                         "a mensagem). Caminho livre com pastas (ex.: «Temporada 1/E01/guiao.md», «Notas/ideias.md»). "
                         "Para um ficheiro que já existe, base_version tem de ser a versão que leste (se outro mudou "
                         "entretanto, lê outra vez). target (opcional) = para onde vai na app quando o utilizador o "
                         "aprovar (chaves de list_targets). Se o ficheiro mudou desde base_version, as duas mudanças "
                         "juntam-se sozinhas quando não mexem no mesmo sítio; se chocarem, nada é gravado e dizem-te onde. "
                         "Para mudanças que outros devem discutir antes, usa propose_change. Ficheiros aprovados estão "
                         "trancados. Nada disto muda a app: só o {AUTOR} aprova.")
def write_file(project_id: int, path: str, content: str, message: str, base_version: int | None = None,
               target: str | None = None) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        f, created, note = writing.write(s, p, path, content, who, message, base_version, target)
        return (f"{'Criado' if created else 'Gravado'}: {f.path} · versão {f.version}"
                + (f" ({note}: confirma com read_file)" if note else "") + f". {_room_url(p.id, f)}")
    out = _run(go)
    log.info("MCP sala: %s escreveu %s", who, path)
    return out


@server.tool(annotations=WRITE, title="Editar partes de um ficheiro",
             description="Muda partes de um ficheiro sem o reescrever: edits = [{\"old\": texto exato que aparece uma só "
                         "vez, \"new\": o texto novo}, …]. Aplica todas ou nenhuma (uma versão nova com a mensagem).")
def edit_file(project_id: int, path: str, edits: list[dict[str, str]], message: str) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        before = writing.need(s, p.id, path).version
        f = writing.edit(s, p, path, edits, who, message)
        return (f"Gravado: {f.path} · versão {f.version}." if f.version != before else "Nada mudou.")
    return _run(go)


@server.tool(annotations=WRITE, title="Mudar o nome de um ficheiro",
             description="Muda o caminho (nome ou pasta) de um ficheiro em escrita.")
def rename_file(project_id: int, path: str, new_path: str) -> str:
    return _run(lambda s: f"Agora é {writing.rename(s, _project(s, project_id), path, new_path).path}.")


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
             title="Apagar ficheiro",
             description="Apaga um ficheiro da sala (em escrita): vai para «Apagados», com as versões, a discussão e as "
                         "propostas (as abertas ficam retiradas), e o {AUTOR} pode repô-lo. reason = porquê (fica na "
                         "discussão e na notificação). Ficheiros aprovados só o {AUTOR} apaga. Diz ao utilizador antes.")
def delete_file(project_id: int, path: str, reason: str = "") -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        f = writing.delete(s, p, writing.need(s, p.id, path), who, reason)
        return f"Apagado: {writing.original_path(f)} (está em Apagados; restore_file ou o {writing.USER} repõem-no)."
    return _run(go)


@server.tool(annotations=WRITE, title="Repor ficheiro apagado",
             description="Repõe um ficheiro apagado (path = o caminho que tinha). Se o caminho já estiver ocupado, fica "
                         "como «… (reposto)».")
def restore_file(project_id: int, path: str) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        want = writing.clean_path(path)
        f = next((x for x in writing.deleted_files(s, p.id) if writing.original_path(x) == want), None)
        if f is None:
            raise writing.WritingError(f"Não há nenhum ficheiro apagado com o caminho «{want}». list_files mostra os apagados.")
        return f"Reposto: {writing.restore(s, p, f, who).path}."
    return _run(go)


@server.tool(annotations=READ, structured_output=True, title="Histórico de um ficheiro",
             description="As versões de um ficheiro: quem, quando e a mensagem de cada uma.")
def file_history(project_id: int, path: str) -> dict[str, Any]:
    with Session(engine) as s:
        p = _project(s, project_id)
        try:
            f = writing.need(s, p.id, path)
        except writing.WritingError as exc:
            raise ToolError(str(exc))
        return {"path": f.path, "status": STATUS[f.status], "approved_version": f.approved_version,
                "versions": [{"version": v.version, "author": writing.author_label(v.author), "when": _when(v.created_at),
                              "message": v.message, "chars": len(v.content)} for v in writing.versions(s, f)]}


@server.tool(annotations=READ, title="Diferenças entre versões",
             description="As diferenças (formato unified diff) entre duas versões de um ficheiro. Sem to_version = até à "
                         "atual; sem from_version = desde a anterior à atual. Útil para ver o que a outra IA mudou.")
def file_diff(project_id: int, path: str, from_version: int | None = None, to_version: int | None = None) -> str:
    with Session(engine) as s:
        p = _project(s, project_id)
        try:
            f = writing.need(s, p.id, path)
            b = to_version or f.version
            a = from_version or max(1, b - 1)
            out = writing.diff(writing.version_text(s, f, a), writing.version_text(s, f, b), f"v{a}", f"v{b}")
        except writing.WritingError as exc:
            raise ToolError(str(exc))
        return out or f"Sem diferenças entre v{a} e v{b}."


@server.tool(annotations=WRITE, title="Comentar na sala",
             description="Escreve na discussão da sala, com o teu nome: sobre um ficheiro (path), uma proposta "
                         "(proposal_id), uma tarefa (task_id) ou na conversa geral (nada disso). reply_to = o #id do "
                         "comentário a que respondes (fica no mesmo fio). Menciona com @Claude, @ChatGPT, @{AUTOR} ou "
                         "@todos: quem é mencionado recebe uma notificação (my_notifications).")
def comment(project_id: int, text: str, path: str | None = None, reply_to: int | None = None,
            proposal_id: int | None = None, task_id: int | None = None) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        c = writing.comment(s, p, text, who, path, reply_to=reply_to, proposal=proposal_id, task=task_id)
        return (f"Comentário #{c.id} publicado" + (f", em resposta ao #{c.parent_id}" if c.parent_id else "")
                + (f"; notificados: {', '.join(c.mentions)}" if c.mentions else "") + ".")
    return _run(go)


def _thread_text(s, rows, paths=None) -> str:
    props = {x.id: x.number for x in writing.proposals(s, rows[0].project_id, status=None)} if rows else {}
    tasks = {x.id: x.number for x in writing.tasks(s, rows[0].project_id)} if rows else {}
    out = []
    for c, depth in writing.threaded(rows):
        where = ""
        if paths is not None:
            where = (f" · proposta #{props.get(c.proposal_id)}" if c.proposal_id else f" · tarefa #{tasks.get(c.task_id)}"
                     if c.task_id else f" · {paths.get(c.file_id, '?')}" if c.file_id else " · geral")
        tag = f" [{writing.VERDICTS[c.verdict].upper()} r{c.revision}]" if c.verdict else ""
        head = ("  " * depth + ("↳ " if depth else "") + f"#{c.id} · {_when(c.created_at)} · "
                f"{writing.author_label(c.author)}{where}{tag}" + (f" (resposta ao #{c.parent_id})" if c.parent_id else ""))
        body = "\n".join("  " * depth + "  " + ln for ln in c.text.splitlines())
        out.append(head + ":\n" + body)
    return "\n\n".join(out)


@server.tool(annotations=READ, title="Ler a discussão",
             description="A discussão da sala por fios (cada resposta a seguir ao comentário a que responde): de um "
                         "ficheiro (path), de uma proposta (proposal_id), de uma tarefa (task_id), só a geral "
                         "(general_only=true) ou tudo (nada disso). since_id = só os comentários depois desse.")
def read_comments(project_id: int, path: str | None = None, general_only: bool = False, since_id: int | None = None,
                  limit: int = 80, proposal_id: int | None = None, task_id: int | None = None) -> str:
    with Session(engine) as s:
        p = _project(s, project_id)
        try:
            f = writing.need(s, p.id, path) if path else None
            prop = writing.get_proposal(s, p.id, proposal_id) if proposal_id else None
            task = writing.get_task(s, p.id, task_id) if task_id else None
        except writing.WritingError as exc:
            raise ToolError(str(exc))
        everything = not (f or prop or task or general_only)
        rows = writing.comments(s, p.id, f.id if f else None, everything=everything, since_id=since_id,
                                limit=max(1, min(limit, 300)), proposal_id=prop.id if prop else None,
                                task_id=task.id if task else None)
        if not rows:
            return "Sem comentários" + (" novos." if since_id else ".")
        return _thread_text(s, rows, {x.id: x.path for x in writing.files(s, p.id)} if everything else None)


@server.tool(annotations=READ, structured_output=True, title="As minhas notificações",
             description="O que é para ti na sala (com o teu nome de participante): menções, respostas aos teus "
                         "comentários, propostas novas e pareceres às tuas, tarefas que te passaram ou que acabaram. "
                         "Chama no início de cada sessão de trabalho. mark_read=true marca-as como lidas.")
def my_notifications(project_id: int | None = None, include_read: bool = False, mark_read: bool = True) -> dict[str, Any]:
    who = _author()
    with writing.lock, Session(engine) as s:
        rows = writing.notices(s, who, project_id, unread_only=not include_read)
        props = {x.id: x.number for x in s.exec(select(writing.WritingProposal)).all()} if rows else {}
        tasks = {x.id: x.number for x in s.exec(select(writing.WritingTask)).all()} if rows else {}
        out = [{"id": n.id, "project_id": n.project_id, "when": _when(n.created_at), "kind": n.kind, "from": n.author,
                "text": n.text, "comment_id": n.comment_id, "proposal": props.get(n.proposal_id),
                "task": tasks.get(n.task_id), "read": n.read} for n in rows]
        if mark_read:
            writing.mark_read(s, rows)
            s.commit()
        return {"you": who, "participants": writing.participants(s, project_id), "notifications": out,
                "text": f"{len(out)} notificação(ões) para {who}." if out else f"Nada de novo para {who}."}


# ── Propostas ─────────────────────────────────────────────────

def _prop_row(s, p) -> dict:
    st = writing.proposal_state(s, p)
    return {"number": p.number, "title": p.title, "path": st["file"].path, "author": p.author,
            "status": writing.PROPOSAL_STATUS[p.status], "revision": p.revision, "base_version": p.base_version,
            "file_version": st["file"].version, "approvals": st["approvals"], "contests": st["contests"],
            "conflicts": len(st["conflicts"]), "can_apply": st["can_apply"],
            "alternative_to": (s.get(writing.WritingProposal, p.alternative_to).number if p.alternative_to else None),
            "needs_decision": bool(p.decisions), "updated": _when(p.updated_at)}


@server.tool(annotations=WRITE, title="Propor alteração",
             description="Propõe uma alteração a um ficheiro da sala, para os outros discutirem antes de entrar: title, "
                         "rationale (porquê), impacts (impactos na continuidade: personagens, cronologia, cenas e planos "
                         "afetados), decisions (os pontos que precisam da decisão do {AUTOR}, o autor) e o texto proposto: "
                         "content (o ficheiro inteiro) ou edits ([{old, new}] sobre a versão de base). base_version = a "
                         "versão que leste (por omissão a atual). alternative_to = o número de outra proposta, se esta é "
                         "uma alternativa. Notifica os outros participantes.")
def propose_change(project_id: int, path: str, title: str, rationale: str, impacts: str = "", decisions: str = "",
                   content: str | None = None, edits: list[dict[str, str]] | None = None,
                   base_version: int | None = None, alternative_to: int | None = None) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        prop = writing.propose(s, p, path, who, title, rationale, impacts, decisions, content, edits, base_version,
                               alternative_to)
        return (f"Proposta #{prop.number} criada em {path} (sobre a versão {prop.base_version}). Os outros foram "
                f"notificados. {_room_url(p.id)}?p={prop.number}")
    return _run(go)


@server.tool(annotations=READ, structured_output=True, title="Propostas",
             description="As propostas de alteração (por omissão as abertas; status=all para todas), com autor, pareceres "
                         "da revisão atual (quem aprova, quem contesta), conflitos com o ficheiro atual e se já se pode "
                         "aplicar.")
def list_proposals(project_id: int, status: str = "open", path: str | None = None) -> dict[str, Any]:
    with Session(engine) as s:
        p = _project(s, project_id)
        try:
            f = writing.need(s, p.id, path) if path else None
        except writing.WritingError as exc:
            raise ToolError(str(exc))
        rows = writing.proposals(s, p.id, None if status == "all" else status, f.id if f else None)
        return {"proposals": [_prop_row(s, x) for x in rows]}


@server.tool(annotations=READ, title="Ler proposta",
             description="Uma proposta inteira: justificação, impactos, decisões pedidas, as diferenças em relação à "
                         "versão de base (unified diff), os pareceres, os conflitos com o ficheiro atual e a discussão. "
                         "full_text=true junta o texto proposto inteiro.")
def read_proposal(project_id: int, proposal_id: int, full_text: bool = False) -> str:
    with Session(engine) as s:
        p = _project(s, project_id)
        try:
            prop = writing.get_proposal(s, p.id, proposal_id)
        except writing.WritingError as exc:
            raise ToolError(str(exc))
        st = writing.proposal_state(s, prop)
        r = _prop_row(s, prop)
        lines = [f"PROPOSTA #{prop.number} «{prop.title}» · {r['status']} · de {prop.author} · revisão {prop.revision}",
                 f"Ficheiro: {r['path']} · feita sobre a versão {prop.base_version} (o ficheiro vai na {r['file_version']})"
                 + (f" · alternativa à #{r['alternative_to']}" if r["alternative_to"] else ""),
                 f"Pareceres na revisão {prop.revision}: aprovam {', '.join(r['approvals']) or 'ninguém'}; contestam "
                 f"{', '.join(r['contests']) or 'ninguém'}"
                 + (f" (pareceres de revisões antigas: {', '.join(st['stale_reviews'])})" if st["stale_reviews"] else ""),
                 ("CONFLITOS com o ficheiro atual: " + "; ".join(st["conflicts"]) if st["conflicts"]
                  else "Funde-se sem conflitos com o ficheiro atual." if prop.status == "open" else ""),
                 "", "JUSTIFICAÇÃO:", prop.rationale or "—", "", "IMPACTOS NA CONTINUIDADE:", prop.impacts or "—", "",
                 f"PRECISA DA DECISÃO DE {writing.USER.upper()}:", prop.decisions or "—", "", "DIFERENÇAS:", st["diff_base"] or "(nenhuma)"]
        if full_text:
            lines += ["", "TEXTO PROPOSTO:", prop.content]
        rows = writing.comments(s, p.id, proposal_id=prop.id)
        if rows:
            lines += ["", "DISCUSSÃO:", _thread_text(s, rows)]
        return "\n".join(lines)


@server.tool(annotations=WRITE, title="Dar parecer sobre uma proposta",
             description="O teu parecer sobre a revisão atual de uma proposta de outro participante: verdict = approve "
                         "(aprovar) ou contest (contestar), com o porquê em text. Para sugerir outra solução, faz "
                         "propose_change com alternative_to. Notifica o autor e o {AUTOR}.")
def review_proposal(project_id: int, proposal_id: int, verdict: str, text: str) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        c = writing.review(s, p, proposal_id, who, verdict, text)
        prop = writing.get_proposal(s, p.id, proposal_id)
        return f"Parecer registado (#{c.id}): {writing.VERDICTS[verdict]} a proposta #{prop.number}, revisão {prop.revision}."
    return _run(go)


@server.tool(annotations=WRITE, title="Rever a minha proposta",
             description="O autor muda a sua proposta (nova revisão; os pareceres anteriores deixam de contar): content "
                         "(texto inteiro) ou edits ([{old, new}] sobre o texto proposto), e/ou rationale, impacts, "
                         "decisions. Para resolver conflitos, manda content feito sobre a versão atual do ficheiro e "
                         "base_version = essa versão. note = o que mudou e porquê.")
def revise_proposal(project_id: int, proposal_id: int, note: str, content: str | None = None,
                    edits: list[dict[str, str]] | None = None, base_version: int | None = None,
                    rationale: str | None = None, impacts: str | None = None, decisions: str | None = None) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        prop = writing.revise(s, p, proposal_id, who, note, content, edits, base_version, rationale, impacts, decisions)
        return f"Proposta #{prop.number} na revisão {prop.revision} (sobre a versão {prop.base_version}). Pedidos novos pareceres."
    return _run(go)


@server.tool(annotations=WRITE, title="Aplicar proposta",
             description="Aplica uma proposta ao ficheiro (fica uma versão nova, fundida com o que mudou entretanto). Só "
                         "com parecer favorável de outro participante na revisão atual, sem contestações e sem conflitos; "
                         "o ficheiro tem de estar em escrita. Não aprova nada na app (isso é só o {AUTOR}).")
def apply_proposal(project_id: int, proposal_id: int) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        f = writing.apply_proposal(s, p, proposal_id, who)
        return f"Aplicada: {f.path} vai na versão {f.version}."
    return _run(go)


@server.tool(annotations=WRITE, title="Retirar a minha proposta",
             description="O autor retira uma proposta aberta (com uma nota opcional).")
def withdraw_proposal(project_id: int, proposal_id: int, note: str = "") -> str:
    who = _author()
    return _run(lambda s: f"Proposta #{writing.close_proposal(s, _project(s, project_id), proposal_id, who, 'withdrawn', note).number} retirada.")


# ── Tarefas ───────────────────────────────────────────────────

def _task_row(s, t) -> dict:
    f = s.get(writing.WritingFile, t.file_id) if t.file_id else None
    pr = s.get(writing.WritingProposal, t.proposal_id) if t.proposal_id else None
    return {"number": t.number, "title": t.title, "details": t.details, "assignee": t.assignee or "qualquer um",
            "status": writing.TASK_STATUS[t.status], "result": t.result, "created_by": t.created_by,
            "path": f.path if f else None, "proposal": pr.number if pr else None, "created": _when(t.created_at),
            "done": _when(t.done_at)}


@server.tool(annotations=WRITE, title="Criar tarefa",
             description="Põe uma tarefa editorial na fila partilhada (ex.: «rever o episódio 3», «verificar a "
                         "cronologia», «criticar a proposta #4»): title, details, assignee (Claude, ChatGPT, {AUTOR} ou "
                         "vazio = quem pegar), e opcionalmente o ficheiro (path) ou a proposta (proposal_id) a que se "
                         "refere. Notifica o responsável.")
def create_task(project_id: int, title: str, details: str = "", assignee: str | None = None, path: str | None = None,
                proposal_id: int | None = None) -> str:
    who = _author()

    def go(s):
        t = writing.create_task(s, _project(s, project_id), who, title, details, assignee, path, proposal_id)
        return f"Tarefa #{t.number} criada" + (f" para {t.assignee}." if t.assignee else " (para quem a pegar).")
    return _run(go)


@server.tool(annotations=READ, structured_output=True, title="Tarefas",
             description="A fila de tarefas editoriais: status = active (por fazer e em curso, por omissão), open, doing, "
                         "done, cancelled ou all; mine=true só as tuas e as de qualquer um.")
def list_tasks(project_id: int, status: str = "active", mine: bool = False) -> dict[str, Any]:
    who = _author()
    with Session(engine) as s:
        p = _project(s, project_id)
        rows = writing.tasks(s, p.id, None if status == "all" else status, who if mine else None)
        return {"you": who, "tasks": [_task_row(s, t) for t in rows]}


@server.tool(annotations=WRITE, title="Atualizar tarefa",
             description="Muda uma tarefa: status = doing (pegas nela; ficas responsável se não houver), done (com o "
                         "resultado em result: o que fizeste, o que encontraste, links para propostas/comentários), "
                         "cancelled ou open; assignee para a passar a outro. Notifica quem a criou quando acaba.")
def update_task(project_id: int, task_id: int, status: str | None = None, result: str | None = None,
                assignee: str | None = None) -> str:
    who = _author()

    def go(s):
        t = writing.update_task(s, _project(s, project_id), task_id, who, status, result,
                                assignee if assignee is not None else "__keep__")
        return f"Tarefa #{t.number}: {writing.TASK_STATUS[t.status]}" + (f" · {t.assignee}" if t.assignee else "") + "."
    return _run(go)


@server.tool(annotations=READ, title="Destinos na app",
             description="Para onde um ficheiro pode ir na app quando o utilizador o aprovar (a chave vai em write_file "
                         "target): campos de texto (sinopse, logline, arcos, perfis das personagens…) e o guião (cenas) "
                         "de cada episódio/ato, com o formato do guião. Mostra também as personagens e os locais que o "
                         "guião pode usar.")
def list_targets(project_id: int) -> str:
    from app.models import Asset

    with Session(engine) as s:
        p = _project(s, project_id)
        taken = {f.target: f.path for f in writing.files(s, p.id) if f.target}
        rows = [f"- {t['key']} · {t['label']}" + (f" · ficheiro: {taken[t['key']]}" if t["key"] in taken else "")
                for t in writing.targets(s, p)]
        cues = writing._cue_names(s, p)
        chars = [f"{cues.get(a.id, a.name)} ({a.name})" for a in
                 s.exec(select(Asset).where(Asset.project_id == p.id, Asset.kind == "character").order_by(Asset.name)).all()]
        locs = [f"{a.label or a.name} ({a.name})" for a in
                s.exec(select(Asset).where(Asset.project_id == p.id, Asset.kind == "location").order_by(Asset.name)).all()]
        return ("DESTINOS:\n" + "\n".join(rows) + "\n\nPERSONAGENS: " + (", ".join(chars) or "—")
                + "\nLOCAIS: " + (", ".join(locs) or "—") + "\n\n" + writing.SCRIPT_FORMAT)


@server.tool(annotations=WRITE, title="Trazer da app para um ficheiro",
             description="Cria um ficheiro da sala com o que a app tem agora num destino (ex.: o guião atual de um "
                         "episódio, já no formato do guião, ou a sinopse), ligado a esse destino. É o ponto de partida "
                         "para rever o que já existe.")
def file_from_app(project_id: int, target: str, path: str) -> str:
    who = _author()

    def go(s):
        p = _project(s, project_id)
        text = writing.read_target(s, p, target)
        if writing.get(s, p.id, path):
            raise writing.WritingError(f"Já existe «{path}»: escolhe outro caminho.")
        f, _c, _n = writing.write(s, p, path, text, who, "Trazido da app", target=target)
        return f"Criado {f.path} (versão 1, {len(text)} caracteres) a partir de {writing.target_label(s, p, target)}."
    return _run(go)
