"""Sala de argumentistas: os ficheiros de texto do projeto, escritos pelo utilizador e pelas IAs do conector MCP (ex.:
o ChatGPT a escrever, o Claude a rever e a pensar nos prompts).

- Em escrita (draft) qualquer um muda um ficheiro; cada mudança é uma versão (texto inteiro, autor, mensagem) e nada
  disto mexe nos dados de produção da app.
- A discussão fica em comentários: de um ficheiro ou gerais.
- Só o utilizador aprova: o conteúdo passa para o destino (target) na app — um campo de texto (sinopse, arco, perfil de
  uma personagem…) ou as cenas de um episódio/ato com o guião — e o ficheiro fica trancado. Só o utilizador destranca;
  a app fica com a versão aprovada até ele voltar a aprovar.

Guião (destinos scenes:…): formato de texto simples, ver SCRIPT_FORMAT. Ao reaprovar, as cenas e as linhas que se
mantêm (por semelhança) continuam a ser as mesmas na app, com os planos, a encenação e as ligações às falas.
"""

import difflib
import re
import threading
import unicodedata
from datetime import datetime, timezone

from sqlmodel import Session, func, select

from app.config import settings

from app.models import (Act, Asset, AssetSceneLink, Episode, Project, Scene, SceneLine, Season, Shot, ShotLineLink,
                        WritingComment, WritingFile, WritingNotice, WritingProposal, WritingTask, WritingVersion)

USER = settings.author_name  # o autor quando é o utilizador na app (AUTHOR_NAME no .env)
MAX_CHARS = 2_000_000  # ~2 MB de texto por ficheiro (cada versão guarda o texto inteiro)
lock = threading.Lock()  # as gravações da sala, uma de cada vez (MCP em paralelo + a página)


class WritingError(Exception):
    pass


SCRIPT_FORMAT = """FORMATO DO GUIÃO (ficheiros com destino «cenas»):
## Título da cena            ← começa uma cena (o título é curto; a ordem no ficheiro é a ordem das cenas)
Local: <local>               ← opcional: nome ou código de um local do projeto
Hora: <hora do dia>          ← opcional: Madrugada, Manhã, Meio-dia, Tarde, Pôr do sol ou Noite
Resumo: <o que acontece>     ← opcional: a descrição da cena (pode continuar nas linhas seguintes até uma linha vazia)

Parágrafo de ação (cada parágrafo, separado por uma linha vazia, é uma linha de ação).

NOME
Fala da personagem (pode ter várias linhas e uma indicação entre parênteses).

Também serve um cabeçalho clássico «INT. LOCAL - NOITE» em vez de «## Título». NOME é o nome ou o código de uma
personagem do projeto, em maiúsculas, sozinho na linha e logo seguido da fala (ou «NOME: fala» numa só linha). Uma
linha começada por «!» é sempre ação. Ignoram-se: o texto antes da primeira cena, «# títulos de secção», [[notas]],
/* blocos */ e transições em maiúsculas acabadas em «:» (ex.: CORTA PARA:). Ações em português europeu, no presente;
falas na língua dos diálogos do projeto."""


# ── Utilidades ───────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", t.upper()).strip()


def clean_path(path: str) -> str:
    parts = [p.strip() for p in str(path or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p != "."]
    if not parts or any(p == ".." for p in parts) or parts[0] == TRASH:
        raise WritingError("Caminho inválido (ex.: «Temporada 1/E01/guiao.md»).")
    if "." not in parts[-1]:
        parts[-1] += ".md"
    out = "/".join(parts)
    if len(out) > 200:
        raise WritingError("Caminho demasiado longo.")
    return out


def author_label(author: str) -> str:
    return USER if author in (USER, "Utilizador") else (author or "?")


TRASH = ".apagados"  # os apagados guardam o caminho como .apagados/<id>/<caminho original>


def files(session: Session, project_id: int) -> list[WritingFile]:
    return list(session.exec(select(WritingFile).where(WritingFile.project_id == project_id,
                                                       WritingFile.deleted_at.is_(None)).order_by(WritingFile.path)).all())


def deleted_files(session: Session, project_id: int) -> list[WritingFile]:
    return list(session.exec(select(WritingFile).where(WritingFile.project_id == project_id,
                                                       WritingFile.deleted_at.is_not(None))
                             .order_by(WritingFile.deleted_at.desc())).all())


def original_path(f: WritingFile) -> str:
    return f.path.split("/", 2)[2] if f.deleted_at and f.path.startswith(TRASH + "/") else f.path


def get(session: Session, project_id: int, path: str) -> WritingFile | None:
    return session.exec(select(WritingFile).where(WritingFile.project_id == project_id, WritingFile.deleted_at.is_(None),
                                                  WritingFile.path == clean_path(path))).first()


def need(session: Session, project_id: int, path: str) -> WritingFile:
    f = get(session, project_id, path)
    if f is None:
        raise WritingError(f"Não existe o ficheiro «{path}». Usa list_files.")
    return f


def versions(session: Session, f: WritingFile) -> list[WritingVersion]:
    return list(session.exec(select(WritingVersion).where(WritingVersion.file_id == f.id)
                             .order_by(WritingVersion.version.desc())).all())


def version_text(session: Session, f: WritingFile, version: int | None) -> str:
    if version is None or version == f.version:
        return f.content
    v = session.exec(select(WritingVersion).where(WritingVersion.file_id == f.id, WritingVersion.version == version)).first()
    if v is None:
        raise WritingError(f"O ficheiro não tem a versão {version} (vai de 1 a {f.version}).")
    return v.content


def diff(old: str, new: str, old_label: str = "antes", new_label: str = "depois") -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=old_label, tofile=new_label, n=2)).rstrip("\n")


def diff_rows(old: str, new: str) -> list[dict]:
    """As diferenças para mostrar na app: tiradas, acrescentadas e 2 linhas iguais à volta de cada mudança."""
    rows: list[dict] = []
    a, b = old.splitlines(), new.splitlines()
    # nos ficheiros grandes, as linhas muito repetidas (vazias, nomes de personagens) não servem de âncora: muito mais rápido
    ops = difflib.SequenceMatcher(None, a, b, autojunk=len(a) + len(b) > 4000).get_opcodes()
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag != "equal":
            rows += [{"k": "del", "t": t} for t in a[i1:i2]] + [{"k": "add", "t": t} for t in b[j1:j2]]
            continue
        same = a[i1:i2]
        head = same[:2] if k > 0 else []
        tail = same[-2:] if k < len(ops) - 1 else []
        if len(same) <= len(head) + len(tail) + 1:
            rows += [{"k": "eq", "t": t} for t in same]
            continue
        rows += [{"k": "eq", "t": t} for t in head]
        rows.append({"k": "skip", "t": f"… {len(same) - len(head) - len(tail)} linha(s) igua(is) …"})
        rows += [{"k": "eq", "t": t} for t in tail]
    return rows


# ── Escrever ─────────────────────────────────────────────────

def _locked(f: WritingFile) -> None:
    if f.deleted_at:
        raise WritingError(f"«{original_path(f)}» está apagado: repõe-no primeiro (Apagados).")
    if f.status == "approved":
        raise WritingError(f"«{f.path}» está aprovado e trancado (a app usa a versão {f.approved_version}). Só o "
                           f"{USER} o pode destrancar na Sala de argumentistas para voltar a rever.")


def _new_version(session: Session, f: WritingFile, content: str, author: str, message: str) -> WritingFile:
    if len(content) > MAX_CHARS:
        raise WritingError(f"Texto demasiado longo (máx. {MAX_CHARS} caracteres): divide-o em vários ficheiros.")
    f.version += 1
    f.content, f.last_author, f.updated_at = content, author, now()
    session.add(f)
    session.add(WritingVersion(project_id=f.project_id, file_id=f.id, version=f.version, content=content,
                               author=author, message=message.strip()[:500]))
    return f


# ── Fusão a três (edições concorrentes) ──────────────────────

def _hunks(base: list[str], other: list[str]) -> list[tuple[int, int, list[str]]]:
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in
            difflib.SequenceMatcher(None, base, other, autojunk=False).get_opcodes() if tag != "equal"]


def _touch(a: tuple, b: tuple) -> bool:
    """Duas mudanças sobre a base mexem no mesmo sítio (sobrepõem-se ou inserem no mesmo ponto)."""
    if a[0] < b[1] and b[0] < a[1]:
        return True
    if a[0] == a[1] and b[0] <= a[0] <= b[1]:
        return True
    return b[0] == b[1] and a[0] <= b[0] <= a[1]


def merge3(base: str, ours: str, theirs: str) -> tuple[str, list[str]]:
    """Junta duas versões feitas a partir da mesma base: o que cada uma mudou em sítios diferentes fica tudo; se as duas
    mudaram o mesmo sítio de maneira diferente, é um conflito. Devolve (texto, conflitos)."""
    if ours == base or ours == theirs:
        return theirs, []
    if theirs == base:
        return ours, []
    b, o, t = base.split("\n"), ours.split("\n"), theirs.split("\n")
    changes = sorted([(*h, "o") for h in _hunks(b, o)] + [(*h, "t") for h in _hunks(b, t)], key=lambda h: (h[0], h[1]))
    groups: list[list[tuple]] = []
    for h in changes:
        if groups and any(_touch(h, g) for g in groups[-1]):
            groups[-1].append(h)
        else:
            groups.append([h])
    out, pos, conflicts = [], 0, []
    for g in groups:
        lo, hi = min(h[0] for h in g), max(h[1] for h in g)
        out += b[pos:lo]
        sides = {"o": [h for h in g if h[3] == "o"], "t": [h for h in g if h[3] == "t"]}

        def side(hs: list[tuple]) -> list[str]:  # a zona lo..hi da base com as mudanças de um dos lados
            res, p = [], lo
            for i1, i2, lines, _who in hs:
                res += b[p:i1] + lines
                p = i2
            return res + b[p:hi]
        if not sides["o"]:
            out += side(sides["t"])
        elif not sides["t"]:
            out += side(sides["o"])
        else:
            mine, other = side(sides["o"]), side(sides["t"])
            if mine == other:
                out += mine
            else:
                out += mine
                excerpt = " / ".join(x.strip() for x in b[lo:hi] if x.strip())[:160] or "(inserção)"
                conflicts.append(f"linhas {lo + 1}–{max(hi, lo + 1)} da base: «{excerpt}»")
        pos = hi
    out += b[pos:]
    return "\n".join(out), conflicts


def write(session: Session, project: Project, path: str, content: str, author: str, message: str = "",
          base_version: int | None = None, target: str | None = None) -> tuple[WritingFile, bool, str]:
    """Cria o ficheiro ou substitui o texto todo (nova versão). Se o ficheiro mudou desde base_version, junta as duas
    mudanças (fusão a três) quando não mexem no mesmo sítio; senão recusa. Devolve (ficheiro, criado, nota)."""
    content = str(content or "").replace("\r\n", "\n")
    if target:
        target_label(session, project, target)  # valida
    f = get(session, project.id, path)
    if f is None:
        if len(content) > MAX_CHARS:
            raise WritingError(f"Texto demasiado longo (máx. {MAX_CHARS} caracteres).")
        if target and session.exec(select(WritingFile).where(WritingFile.project_id == project.id, WritingFile.deleted_at.is_(None),
                                                             WritingFile.target == target)).first():
            raise WritingError("Já há um ficheiro com esse destino na app.")
        f = WritingFile(project_id=project.id, path=clean_path(path), content=content, version=1, target=target,
                        last_author=author)
        session.add(f)
        session.flush()
        session.add(WritingVersion(project_id=project.id, file_id=f.id, version=1, content=content, author=author,
                                   message=(message or "Ficheiro criado").strip()[:500]))
        return f, True, ""
    _locked(f)
    if base_version is None:
        raise WritingError(f"«{f.path}» já existe (versão {f.version}): lê-o primeiro e manda base_version={f.version} "
                           "(ou usa edit_file para mudar só partes).")
    note = ""
    if int(base_version) != f.version:
        base = version_text(session, f, int(base_version))
        merged, conflicts = merge3(base, f.content, content)
        if conflicts:
            raise WritingError(
                f"«{f.path}» mudou entretanto (estás na versão {base_version}, já vai na {f.version}, de "
                f"{author_label(f.last_author)}) e as mudanças chocam com as tuas em: " + "; ".join(conflicts[:6])
                + ". Nada foi gravado: lê a versão atual (file_diff mostra o que mudou) e refaz a mudança sobre ela.")
        content = merged
        note = f"fundido com as mudanças das versões {int(base_version) + 1}–{f.version}"
        message = (message + f" ({note})").strip()
    if target is not None:
        _set_target(session, project, f, target)
    if content == f.content:
        return f, False, note
    return _new_version(session, f, content, author, message), False, note


def _apply_edits(text: str, edits: list[dict], where: str) -> str:
    if not edits:
        raise WritingError("Sem mudanças.")
    for n, e in enumerate(edits, 1):
        old, new = str((e or {}).get("old") or ""), str((e or {}).get("new") or "")
        if not old:
            raise WritingError(f"Mudança {n}: falta o texto a substituir (old).")
        count = text.count(old)
        if count != 1:
            raise WritingError(f"Mudança {n}: o texto a substituir aparece {count} vezes em {where} (tem de aparecer "
                               "exatamente uma; acrescenta contexto à volta). Nada foi gravado.")
        text = text.replace(old, new, 1)
    return text


def edit(session: Session, project: Project, path: str, edits: list[dict], author: str, message: str = "") -> WritingFile:
    """Mudanças por partes: cada {"old": texto exato que existe uma só vez, "new": o novo}. Todas ou nenhuma."""
    f = need(session, project.id, path)
    _locked(f)
    text = _apply_edits(f.content, edits, f"«{f.path}»")
    if text == f.content:
        return f
    return _new_version(session, f, text, author, message)


def rename(session: Session, project: Project, path: str, new_path: str) -> WritingFile:
    f = need(session, project.id, path)
    _locked(f)
    dest = clean_path(new_path)
    if dest != f.path and get(session, project.id, dest):
        raise WritingError(f"Já existe «{dest}».")
    f.path = dest
    session.add(f)
    return f


# ── Participantes, menções e notificações ────────────────────

def participants(session: Session, project_id: int | None = None) -> list[str]:
    """Quem participa na sala: o utilizador e as IAs ligadas ao conector (o nome com que se registaram)."""
    from app.models import McpClient

    names = {USER}
    for c in session.exec(select(McpClient)).all():
        n = str((c.data or {}).get("client_name") or "").strip()[:40]
        if n:
            names.add(n)
    if project_id:
        names |= set(session.exec(select(WritingComment.author).where(WritingComment.project_id == project_id)).all())
    names.discard("")
    names.discard("Utilizador")
    return sorted(names, key=lambda n: (n != USER, n.lower()))


def _who(session: Session, project_id: int, name: str | None) -> str | None:
    """O nome certo de um participante (sem distinguir maiúsculas nem acentos); None = qualquer um."""
    if not name or _norm(name) in ("", "QUALQUER", "QUALQUER UM", "TODOS", "ANY", "ANYONE"):
        return None
    for n in participants(session, project_id):
        if _norm(n) == _norm(name.lstrip("@")):
            return n
    raise WritingError(f"Não conheço «{name}». Participantes: {', '.join(participants(session, project_id))}.")


def mentions_in(session: Session, project_id: int, text: str, author: str) -> list[str]:
    people = participants(session, project_id)
    found = []
    for m in re.finditer(r"@([\wÀ-ÿ.\-]+)", text):
        tok = _norm(m.group(1))
        if tok in ("TODOS", "ALL", "EVERYONE"):
            found += [p for p in people if p != author]
            continue
        found += [p for p in people if _norm(p) == tok or _norm(p).split(" ")[0] == tok]
    return list(dict.fromkeys(found))


def notify(session: Session, project_id: int, recipients, kind: str, text: str, author: str, **refs) -> None:
    for r in dict.fromkeys(recipients):
        if r and r != author:
            session.add(WritingNotice(project_id=project_id, recipient=r, kind=kind, text=text[:500], author=author,
                                      **{k: v for k, v in refs.items() if v}))


def notices(session: Session, recipient: str, project_id: int | None = None, unread_only: bool = True,
            limit: int = 50) -> list[WritingNotice]:
    q = select(WritingNotice).where(WritingNotice.recipient == recipient)
    if project_id:
        q = q.where(WritingNotice.project_id == project_id)
    if unread_only:
        q = q.where(WritingNotice.read.is_(False))
    return list(session.exec(q.order_by(WritingNotice.id.desc()).limit(limit)).all())


def unread_count(session: Session, recipient: str, project_id: int) -> int:
    return session.exec(select(func.count()).select_from(WritingNotice).where(
        WritingNotice.recipient == recipient, WritingNotice.project_id == project_id, WritingNotice.read.is_(False))).one()


def mark_read(session: Session, items: list[WritingNotice]) -> None:
    for n in items:
        if not n.read:
            n.read = True
            session.add(n)


# ── Comentários ──────────────────────────────────────────────

def comment(session: Session, project: Project, text: str, author: str, path: str | None = None,
            reply_to: int | None = None, proposal: int | None = None, task: int | None = None,
            verdict: str | None = None) -> WritingComment:
    """proposal e task são os números (#n) no projeto."""
    text = str(text or "").strip()
    if not text:
        raise WritingError("Comentário vazio.")
    parent = None
    prop = get_proposal(session, project.id, proposal) if proposal else None
    task = get_task(session, project.id, task) if task else None
    if reply_to:
        parent = session.get(WritingComment, int(reply_to))
        if parent is None or parent.project_id != project.id:
            raise WritingError(f"Não há o comentário #{reply_to} neste projeto.")
        # a resposta fica no mesmo fio que o comentário a que responde
        path = path or (session.get(WritingFile, parent.file_id).path if parent.file_id else None)
        prop = prop or (session.get(WritingProposal, parent.proposal_id) if parent.proposal_id else None)
        task = task or (session.get(WritingTask, parent.task_id) if parent.task_id else None)
    f = None
    if path and not prop and not task:
        f = need(session, project.id, path)
    mentioned = mentions_in(session, project.id, text, author)
    c = WritingComment(project_id=project.id, file_id=f.id if f else None, version=f.version if f else None,
                       author=author, text=text[:100_000], parent_id=parent.id if parent else None,
                       proposal_id=prop.id if prop else None, task_id=task.id if task else None, mentions=mentioned,
                       verdict=verdict, revision=prop.revision if prop and verdict else None)
    session.add(c)
    session.flush()
    where = (f"na proposta #{prop.number}" if prop else f"na tarefa #{task.number}" if task
             else f"em {f.path}" if f else "na conversa geral")
    refs = {"comment_id": c.id, "file_id": f.id if f else (prop.file_id if prop else None),
            "proposal_id": prop.id if prop else None, "task_id": task.id if task else None}
    short = _short(text, 140)
    notify(session, project.id, mentioned, "mention", f"{author} mencionou-te {where}: «{short}»", author, **refs)
    if parent and parent.author not in mentioned:
        notify(session, project.id, [parent.author], "reply", f"{author} respondeu ao teu comentário #{parent.id} {where}: «{short}»",
               author, **refs)
    others = []
    if prop and not verdict:
        others = [prop.author]
    if task:
        others = [task.created_by, task.assignee]
    others = [o for o in others if o and o not in mentioned and (not parent or o != parent.author)]
    notify(session, project.id, others, "comment", f"{author} comentou {where}: «{short}»", author, **refs)
    return c


def comments(session: Session, project_id: int, file_id: int | None = None, everything: bool = False,
             since_id: int | None = None, limit: int = 200, proposal_id: int | None = None,
             task_id: int | None = None) -> list[WritingComment]:
    q = select(WritingComment).where(WritingComment.project_id == project_id)
    if proposal_id:
        q = q.where(WritingComment.proposal_id == proposal_id)
    elif task_id:
        q = q.where(WritingComment.task_id == task_id)
    elif not everything:
        q = q.where(WritingComment.proposal_id.is_(None), WritingComment.task_id.is_(None))
        q = q.where(WritingComment.file_id == file_id) if file_id else q.where(WritingComment.file_id.is_(None))
    if since_id:
        q = q.where(WritingComment.id > since_id)
    rows = list(session.exec(q.order_by(WritingComment.id.desc()).limit(limit)).all())
    return rows[::-1]


def threaded(rows: list[WritingComment]) -> list[tuple[WritingComment, int]]:
    """Os comentários por fio: cada resposta logo a seguir ao comentário a que responde (com o nível)."""
    ids = {c.id for c in rows}
    kids: dict = {}
    for c in rows:
        kids.setdefault(c.parent_id if c.parent_id in ids else None, []).append(c)
    out: list = []

    def walk(pid, depth):
        for c in kids.get(pid, []):
            out.append((c, depth))
            walk(c.id, min(depth + 1, 6))
    walk(None, 0)
    return out


def comment_counts(session: Session, project_id: int) -> dict:
    return dict(session.exec(select(WritingComment.file_id, func.count()).where(
        WritingComment.project_id == project_id, WritingComment.proposal_id.is_(None), WritingComment.task_id.is_(None))
        .group_by(WritingComment.file_id)).all())


# ── Propostas de alteração ───────────────────────────────────

PROPOSAL_STATUS = {"open": "aberta", "applied": "aplicada", "rejected": "rejeitada", "withdrawn": "retirada"}
VERDICTS = {"approve": "aprova", "contest": "contesta"}


def _next_number(session: Session, model, project_id: int) -> int:
    return (session.exec(select(func.max(model.number)).where(model.project_id == project_id)).one() or 0) + 1


def get_proposal(session: Session, project_id: int, proposal_id: int) -> WritingProposal:
    """Pelo número (#n) no projeto."""
    p = session.exec(select(WritingProposal).where(WritingProposal.project_id == project_id,
                                                   WritingProposal.number == int(proposal_id))).first()
    if p is None:
        raise WritingError(f"Não há a proposta {proposal_id} neste projeto. Usa list_proposals.")
    return p


def propose(session: Session, project: Project, path: str, author: str, title: str, rationale: str, impacts: str = "",
            decisions: str = "", content: str | None = None, edits: list[dict] | None = None,
            base_version: int | None = None, alternative_to: int | None = None) -> WritingProposal:
    f = need(session, project.id, path)
    base_version = int(base_version or f.version)
    base = version_text(session, f, base_version)
    if (content is None) == (not edits):
        raise WritingError("Manda o texto proposto inteiro (content) ou as mudanças por partes (edits), um dos dois.")
    text = str(content).replace("\r\n", "\n") if content is not None else _apply_edits(base, edits, f"«{f.path}» v{base_version}")
    if text == base:
        raise WritingError("A proposta não muda nada em relação à versão de base.")
    if not str(title or "").strip() or not str(rationale or "").strip():
        raise WritingError("Uma proposta precisa de título e de justificação (rationale).")
    alt = get_proposal(session, project.id, alternative_to) if alternative_to else None
    if alt and alt.file_id != f.id:
        raise WritingError(f"A proposta #{alt.number} é de outro ficheiro.")
    prop = WritingProposal(project_id=project.id, file_id=f.id, number=_next_number(session, WritingProposal, project.id),
                           title=title.strip()[:200], rationale=rationale.strip(), impacts=(impacts or "").strip(),
                           decisions=(decisions or "").strip(), content=text, base_version=base_version, author=author,
                           alternative_to=alt.id if alt else None)
    session.add(prop)
    session.flush()
    what = f"#{prop.number} «{prop.title}» em {f.path}" + (f" (alternativa à #{alt.number})" if alt else "")
    notify(session, project.id, participants(session, project.id), "proposal",
           f"{author} propôs {what}" + (" — precisa da tua decisão" if prop.decisions else ""), author,
           file_id=f.id, proposal_id=prop.id)
    return prop


def revise(session: Session, project: Project, proposal_id: int, author: str, note: str, content: str | None = None,
           edits: list[dict] | None = None, base_version: int | None = None, rationale: str | None = None,
           impacts: str | None = None, decisions: str | None = None) -> WritingProposal:
    prop = get_proposal(session, project.id, proposal_id)
    if prop.status != "open":
        raise WritingError(f"A proposta #{prop.number} já está {PROPOSAL_STATUS[prop.status]}.")
    if author not in (prop.author, USER):
        raise WritingError(f"Só {prop.author} (quem a fez) revê a proposta #{prop.number}; para outra solução, faz uma "
                           f"proposta alternativa (alternative_to={prop.number}).")
    f = session.get(WritingFile, prop.file_id)
    if base_version and int(base_version) != prop.base_version:
        if content is None:
            raise WritingError("Para passar a proposta para outra versão de base, manda o texto proposto inteiro (content) "
                               "feito sobre essa versão.")
        version_text(session, f, int(base_version))  # valida
        prop.base_version = int(base_version)
    if content is not None:
        prop.content = str(content).replace("\r\n", "\n")
    elif edits:
        prop.content = _apply_edits(prop.content, edits, f"a proposta #{prop.number}")
    for attr, val in (("rationale", rationale), ("impacts", impacts), ("decisions", decisions)):
        if val is not None:
            setattr(prop, attr, val.strip())
    prop.revision += 1
    session.add(prop)
    session.flush()
    c = WritingComment(project_id=project.id, proposal_id=prop.id, author=author,
                       text=f"✎ Revisão {prop.revision}: {(note or 'sem nota').strip()}")
    session.add(c)
    session.flush()
    reviewers = set(session.exec(select(WritingComment.author).where(WritingComment.proposal_id == prop.id,
                                                                     WritingComment.verdict.is_not(None))).all())
    notify(session, project.id, reviewers | {USER}, "proposal",
           f"{author} reviu a proposta #{prop.number} «{prop.title}» (revisão {prop.revision}): {_short(note, 120)}",
           author, proposal_id=prop.id, file_id=prop.file_id, comment_id=c.id)
    return prop


def review(session: Session, project: Project, proposal_id: int, author: str, verdict: str, text: str) -> WritingComment:
    prop = get_proposal(session, project.id, proposal_id)
    if verdict not in VERDICTS:
        raise WritingError("verdict: approve (aprovar) ou contest (contestar). Para outra solução, propose_change com "
                           f"alternative_to={prop.number}.")
    if prop.status != "open":
        raise WritingError(f"A proposta #{prop.number} já está {PROPOSAL_STATUS[prop.status]}.")
    if author == prop.author:
        raise WritingError("Não dás parecer sobre a tua própria proposta: revê-a (revise_proposal) ou comenta.")
    c = comment(session, project, text, author, proposal=prop.number, verdict=verdict)
    notify(session, project.id, [prop.author, USER], "review",
           f"{author} {VERDICTS[verdict]} a proposta #{prop.number} «{prop.title}»: «{_short(text, 140)}»", author,
           proposal_id=prop.id, file_id=prop.file_id, comment_id=c.id)
    return c


def proposal_state(session: Session, prop: WritingProposal) -> dict:
    """Pareceres da revisão atual e se se funde sem conflitos com o ficheiro atual."""
    f = session.get(WritingFile, prop.file_id)
    revs = [c for c in comments(session, prop.project_id, proposal_id=prop.id) if c.verdict]
    latest: dict[str, WritingComment] = {}
    for c in revs:
        latest[c.author] = c  # o último parecer de cada um
    current = {a: c for a, c in latest.items() if c.revision == prop.revision}
    approvals = sorted(a for a, c in current.items() if c.verdict == "approve" and a != prop.author)
    contests = sorted(a for a, c in current.items() if c.verdict == "contest")
    stale = sorted(a for a, c in latest.items() if c.revision != prop.revision)
    base = version_text(session, f, prop.base_version)
    merged, conflicts = merge3(base, f.content, prop.content)
    return {"file": f, "approvals": approvals, "contests": contests, "stale_reviews": stale,
            "behind": f.version - prop.base_version, "conflicts": conflicts, "merged": merged,
            "can_apply": prop.status == "open" and f.status == "draft" and not conflicts and bool(approvals) and not contests,
            "diff_base": diff(base, prop.content, f"{f.path} v{prop.base_version}", f"proposta #{prop.number} r{prop.revision}"),
            "rows": diff_rows(base, prop.content)}


def apply_proposal(session: Session, project: Project, proposal_id: int, by: str) -> WritingFile:
    """Aplica a proposta ao ficheiro (versão nova). Uma IA só aplica com o parecer favorável de outro participante na
    revisão atual e sem contestações; o utilizador aplica sempre."""
    prop = get_proposal(session, project.id, proposal_id)
    if prop.status != "open":
        raise WritingError(f"A proposta #{prop.number} já está {PROPOSAL_STATUS[prop.status]}.")
    st = proposal_state(session, prop)
    f = st["file"]
    _locked(f)
    if st["conflicts"]:
        raise WritingError(f"«{f.path}» mudou desde a versão {prop.base_version} em sítios que a proposta também muda: "
                           + "; ".join(st["conflicts"][:6]) + f". {prop.author} tem de a rever sobre a versão {f.version} "
                           "(revise_proposal com base_version).")
    if by != USER:
        if st["contests"]:
            raise WritingError(f"A proposta #{prop.number} está contestada por {', '.join(st['contests'])}: resolvam isso "
                               f"primeiro (ou o {USER} decide).")
        if not [a for a in st["approvals"] if a != by]:
            raise WritingError(f"A proposta #{prop.number} ainda não tem parecer favorável de outro participante na "
                               f"revisão {prop.revision}.")
    _new_version(session, f, st["merged"], by, f"Proposta #{prop.number} aplicada: {prop.title} (de {prop.author})")
    prop.status, prop.applied_version, prop.closed_by, prop.closed_at = "applied", f.version, by, now()
    session.add(prop)
    notify(session, project.id, participants(session, project.id), "applied",
           f"{by} aplicou a proposta #{prop.number} «{prop.title}» em {f.path} (versão {f.version})", by,
           proposal_id=prop.id, file_id=f.id)
    return f


def close_proposal(session: Session, project: Project, proposal_id: int, by: str, status: str, note: str = "") -> WritingProposal:
    prop = get_proposal(session, project.id, proposal_id)
    if prop.status != "open":
        raise WritingError(f"A proposta #{prop.number} já está {PROPOSAL_STATUS[prop.status]}.")
    if status == "withdrawn" and by != prop.author:
        raise WritingError("Só quem fez a proposta a retira.")
    if status == "rejected" and by != USER:
        raise WritingError(f"Só o {USER} rejeita propostas; tu podes contestá-la (review_proposal).")
    prop.status, prop.closed_by, prop.closed_at = status, by, now()
    session.add(prop)
    if note:
        session.add(WritingComment(project_id=project.id, proposal_id=prop.id, author=by, text=note.strip()))
    notify(session, project.id, [prop.author] + participants(session, project.id), "proposal",
           f"{by} {'retirou' if status == 'withdrawn' else 'rejeitou'} a proposta #{prop.number} «{prop.title}»"
           + (f": {_short(note, 120)}" if note else ""), by, proposal_id=prop.id, file_id=prop.file_id)
    return prop


def proposals(session: Session, project_id: int, status: str | None = "open", file_id: int | None = None) -> list[WritingProposal]:
    q = select(WritingProposal).where(WritingProposal.project_id == project_id)
    if status:
        q = q.where(WritingProposal.status == status)
    if file_id:
        q = q.where(WritingProposal.file_id == file_id)
    return list(session.exec(q.order_by(WritingProposal.number.desc())).all())


# ── Tarefas editoriais ───────────────────────────────────────

TASK_STATUS = {"open": "por fazer", "doing": "em curso", "done": "feita", "cancelled": "cancelada"}


def get_task(session: Session, project_id: int, task_id: int) -> WritingTask:
    t = session.exec(select(WritingTask).where(WritingTask.project_id == project_id,
                                               WritingTask.number == int(task_id))).first()
    if t is None:
        raise WritingError(f"Não há a tarefa {task_id} neste projeto. Usa list_tasks.")
    return t


def create_task(session: Session, project: Project, author: str, title: str, details: str = "",
                assignee: str | None = None, path: str | None = None, proposal_id: int | None = None) -> WritingTask:
    if not str(title or "").strip():
        raise WritingError("A tarefa precisa de um título (ex.: «rever o episódio 3»).")
    who = _who(session, project.id, assignee)
    f = need(session, project.id, path) if path else None
    prop = get_proposal(session, project.id, proposal_id) if proposal_id else None
    t = WritingTask(project_id=project.id, number=_next_number(session, WritingTask, project.id), title=title.strip()[:200],
                    details=(details or "").strip(), assignee=who, created_by=author, file_id=f.id if f else None,
                    proposal_id=prop.id if prop else None)
    session.add(t)
    session.flush()
    notify(session, project.id, [who] if who else participants(session, project.id), "task",
           f"{author} criou a tarefa #{t.number} «{t.title}»" + (f" para ti" if who else " (para quem a pegar)"), author,
           task_id=t.id, file_id=t.file_id, proposal_id=t.proposal_id)
    return t


def update_task(session: Session, project: Project, task_id: int, author: str, status: str | None = None,
                result: str | None = None, assignee: str | None = "__keep__") -> WritingTask:
    t = get_task(session, project.id, task_id)
    old_assignee = t.assignee
    if assignee != "__keep__":
        t.assignee = _who(session, project.id, assignee)
    if status:
        if status not in TASK_STATUS:
            raise WritingError(f"Estado: {', '.join(TASK_STATUS)}.")
        if status == "done" and not (result or t.result).strip():
            raise WritingError("Para dar a tarefa por feita, diz o resultado (result).")
        if status == "doing" and not t.assignee:
            t.assignee = author  # quem pega nela fica responsável
        t.status = status
        t.done_at = now() if status in ("done", "cancelled") else None
    if result is not None:
        t.result = result.strip()
    session.add(t)
    session.flush()
    if t.assignee and t.assignee != old_assignee:
        notify(session, project.id, [t.assignee], "task", f"{author} passou-te a tarefa #{t.number} «{t.title}»", author,
               task_id=t.id)
    if status in ("done", "cancelled"):
        notify(session, project.id, [t.created_by, USER], "task",
               f"{author} deu a tarefa #{t.number} «{t.title}» por {TASK_STATUS[status]}"
               + (f": {_short(t.result, 160)}" if t.result else ""), author, task_id=t.id)
    return t


def tasks(session: Session, project_id: int, status: str | None = None, assignee: str | None = None) -> list[WritingTask]:
    q = select(WritingTask).where(WritingTask.project_id == project_id)
    if status == "active":
        q = q.where(WritingTask.status.in_(("open", "doing")))
    elif status:
        q = q.where(WritingTask.status == status)
    if assignee:
        q = q.where((WritingTask.assignee == assignee) | WritingTask.assignee.is_(None))
    return list(session.exec(q.order_by(WritingTask.status.in_(("done", "cancelled")), WritingTask.number.desc())).all())


# ── Destinos na app ──────────────────────────────────────────

TEXT_FIELDS = {
    "project": {"premise": "Premissa", "logline": "Logline", "synopsis": "Sinopse", "world": "O mundo"},
    "season": {"arc": "Arco", "summary": "Resumo"},
    "episode": {"logline": "Logline", "synopsis": "Sinopse"},
    "act": {"arc": "Arco", "summary": "Resumo"},
    "asset": {"profile": "Perfil", "canonical_description": "Descrição canónica (EN)"},
}
MODELS = {"project": Project, "season": Season, "episode": Episode, "act": Act, "asset": Asset}


def _seasons(session, project):
    return list(session.exec(select(Season).where(Season.project_id == project.id).order_by(Season.number)).all())


def _episodes(session, season):
    return list(session.exec(select(Episode).where(Episode.season_id == season.id).order_by(Episode.number)).all())


def targets(session: Session, project: Project) -> list[dict]:
    """Os destinos possíveis na app, por ordem: {"key", "label", "kind": "text"|"scenes"}."""
    out = [{"key": f"project:{project.id}:{a}", "label": f"Projeto › {lbl}", "kind": "text"}
           for a, lbl in TEXT_FIELDS["project"].items()]
    if project.type == "series":
        for se in _seasons(session, project):
            out += [{"key": f"season:{se.id}:{a}", "label": f"T{se.number} › {lbl}", "kind": "text"}
                    for a, lbl in TEXT_FIELDS["season"].items()]
            for ep in _episodes(session, se):
                code = f"T{se.number} E{ep.number:02d}"
                out += [{"key": f"episode:{ep.id}:{a}", "label": f"{code} › {lbl}", "kind": "text"}
                        for a, lbl in TEXT_FIELDS["episode"].items()]
                out.append({"key": f"scenes:episode:{ep.id}", "label": f"{code} › Guião (cenas)", "kind": "scenes"})
    elif project.type == "film":
        for act in session.exec(select(Act).where(Act.project_id == project.id).order_by(Act.number)).all():
            out += [{"key": f"act:{act.id}:{a}", "label": f"Ato {act.number} › {lbl}", "kind": "text"}
                    for a, lbl in TEXT_FIELDS["act"].items()]
            out.append({"key": f"scenes:act:{act.id}", "label": f"Ato {act.number} › Guião (cenas)", "kind": "scenes"})
    else:
        out.append({"key": "scenes:project", "label": "Guião (cenas)", "kind": "scenes"})
    for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "character").order_by(Asset.name)).all():
        out.append({"key": f"asset:{a.id}:profile", "label": f"Personagem {a.label or a.name} › Perfil", "kind": "text"})
    return out


def _resolve(session: Session, project: Project, key: str):
    """(tipo, objeto, atributo) de um destino deste projeto; tipo "text" ou "scenes"."""
    parts = str(key or "").split(":")
    if parts[0] == "scenes":
        if parts[1:] == ["project"] and project.type == "short":
            return "scenes", project, None
        if len(parts) == 3 and parts[2].isdigit():
            if parts[1] == "episode":
                ep = session.get(Episode, int(parts[2]))
                se = session.get(Season, ep.season_id) if ep else None
                if se and se.project_id == project.id:
                    return "scenes", ep, None
            if parts[1] == "act":
                act = session.get(Act, int(parts[2]))
                if act and act.project_id == project.id:
                    return "scenes", act, None
    elif len(parts) == 3 and parts[0] in MODELS and parts[1].isdigit() and parts[2] in TEXT_FIELDS[parts[0]]:
        obj = session.get(MODELS[parts[0]], int(parts[1]))
        owner = None
        if isinstance(obj, Project):
            owner = obj.id
        elif isinstance(obj, Episode):
            se = session.get(Season, obj.season_id)
            owner = se.project_id if se else None
        elif obj is not None:
            owner = obj.project_id
        if owner == project.id:
            return "text", obj, parts[2]
    raise WritingError(f"Destino desconhecido neste projeto: «{key}». Usa list_targets.")


def target_label(session: Session, project: Project, key: str | None) -> str:
    if not key:
        return ""
    _resolve(session, project, key)
    return next((t["label"] for t in targets(session, project) if t["key"] == key), key)


def _set_target(session: Session, project: Project, f: WritingFile, key: str | None) -> None:
    key = key or None
    if key:
        _resolve(session, project, key)
        other = session.exec(select(WritingFile).where(WritingFile.project_id == project.id, WritingFile.target == key,
                                                       WritingFile.deleted_at.is_(None), WritingFile.id != f.id)).first()
        if other:
            raise WritingError(f"«{other.path}» já tem esse destino.")
    f.target = key
    session.add(f)


def set_target(session: Session, project: Project, f: WritingFile, key: str | None) -> None:
    _locked(f)
    _set_target(session, project, f, key)


def _container_scenes(session: Session, project: Project, obj) -> list[Scene]:
    q = select(Scene).where(Scene.project_id == project.id)
    if isinstance(obj, Episode):
        q = q.where(Scene.episode_id == obj.id)
    elif isinstance(obj, Act):
        q = q.where(Scene.act_id == obj.id)
    else:
        q = q.where(Scene.episode_id.is_(None), Scene.act_id.is_(None))
    return list(session.exec(q.order_by(Scene.number, Scene.id)).all())


def _lines(session: Session, scene: Scene) -> list[SceneLine]:
    return list(session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order, SceneLine.id)).all())


def _cue_names(session: Session, project: Project) -> dict[int, str]:
    """O nome com que cada personagem aparece no guião em texto: o nome visível em maiúsculas (o código, se repetir)."""
    chars = list(session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "character")).all())
    counts: dict[str, int] = {}
    for a in chars:
        counts[_norm(a.label)] = counts.get(_norm(a.label), 0) + 1
    return {a.id: (a.label.upper() if a.label and counts[_norm(a.label)] == 1 else a.name) for a in chars}


def export_scenes(session: Session, project: Project, obj) -> str:
    cues = _cue_names(session, project)
    out = []
    for sc in _container_scenes(session, project, obj):
        out.append(f"## {sc.title or f'Cena {sc.number}'}")
        loc = session.get(Asset, sc.location_id) if sc.location_id else None
        if loc:
            out.append(f"Local: {loc.label or loc.name}")
        if sc.time_of_day:
            out.append(f"Hora: {sc.time_of_day}")
        if sc.description:
            out.append("Resumo: " + " ".join(sc.description.split()))
        out.append("")
        for ln in _lines(session, sc):
            if not ln.text.strip():
                continue
            if ln.kind == "dialogue" and ln.character_id in cues:
                out.append(cues[ln.character_id])
                out.append(ln.text.strip())
            else:
                txt = ln.text.strip()
                out.append(("!" + txt) if _looks_like_cue(txt.splitlines()[0]) else txt)
            out.append("")
    return "\n".join(out).strip() + "\n" if out else ""


def read_target(session: Session, project: Project, key: str) -> str:
    kind, obj, attr = _resolve(session, project, key)
    if kind == "scenes":
        return export_scenes(session, project, obj)
    return (getattr(obj, attr) or "").strip() + ("\n" if getattr(obj, attr) else "")


# ── Ler o guião em texto ─────────────────────────────────────

_HEADING = re.compile(r"^(INT\.?/EXT\.?|EXT\.?/INT\.?|I/E|INT\.|EXT\.)\s+(.+)$", re.I)
_META = re.compile(r"^(local|hora|resumo)\s*:\s*(.*)$", re.I)
_EXT = re.compile(r"\s*\((?:V\.?O\.?|O\.?S\.?|O\.?C\.?|CONT'?D|CONT\.?|OFF|V\.?F\.?)\)\s*$", re.I)


def _looks_like_cue(line: str) -> bool:
    t = _EXT.sub("", line.strip()).rstrip(":").strip()
    return bool(t) and len(t) <= 40 and any(c.isalpha() for c in t) and t == t.upper() and not t.endswith(".")


def _lookup(session: Session, project: Project, kind: str) -> dict[str, Asset]:
    out: dict[str, Asset] = {}
    for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == kind)).all():
        bare = a.name.split("_", 1)[1] if "_" in a.name else a.name
        for k in (a.name, bare, a.label):
            if k and _norm(k) not in out:
                out[_norm(k)] = a
    return out


def _time_options(session: Session) -> dict[str, str]:
    from app.options import get_option_rows

    return {_norm(r.value): r.value for r in get_option_rows(session, "time_of_day")}


def parse_script(session: Session, project: Project, text: str) -> tuple[list[dict], list[str], list[str]]:
    """O guião em texto → cenas [{title, location, location_label, time, description, lines: [{kind, character, text}]}],
    erros (impedem aprovar) e avisos."""
    chars, locs, times = _lookup(session, project, "character"), _lookup(session, project, "location"), _time_options(session)
    text = re.sub(r"/\*.*?\*/", "", str(text or "").replace("\r\n", "\n"), flags=re.S)
    text = re.sub(r"\[\[.*?\]\]", "", text, flags=re.S)
    raw = [ln.rstrip() for ln in text.split("\n")]
    scenes: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    before = False
    cur: dict | None = None
    i = 0

    def para_end(k: int) -> int:
        while k < len(raw) and raw[k].strip():
            k += 1
        return k

    while i < len(raw):
        line = raw[i].replace("**", "").strip()
        n = i + 1
        if not line:
            i += 1
            continue
        heading = None
        if line.startswith("## ") or line == "##":
            heading = {"title": line[2:].strip() or None}
        elif _HEADING.match(line):
            rest = _HEADING.match(line).group(2)
            bits = re.split(r"\s+[-–—]\s+", rest, maxsplit=1)
            heading = {"title": None, "loc_text": bits[0].strip(), "time_text": bits[1].strip() if len(bits) > 1 else ""}
        if heading is not None:
            cur = {"title": heading["title"], "location": None, "location_label": "", "time": None, "description": None,
                   "lines": [], "at": n}
            scenes.append(cur)
            if heading.get("loc_text"):
                _set_loc(cur, heading["loc_text"], locs, warnings, n)
            if heading.get("time_text"):
                _set_time(cur, heading["time_text"], times, warnings, n)
            i += 1
            while i < len(raw):  # Local / Hora / Resumo logo a seguir ao cabeçalho
                m = _META.match(raw[i].replace("**", "").strip())
                if not m:
                    break
                key, val = m.group(1).lower(), m.group(2).strip()
                if key == "resumo":
                    end = para_end(i + 1)
                    cur["description"] = " ".join([val] + [x.strip() for x in raw[i + 1:end]]).strip() or None
                    i = end
                    continue
                if key == "local":
                    _set_loc(cur, val, locs, warnings, i + 1)
                else:
                    _set_time(cur, val, times, warnings, i + 1)
                i += 1
            continue
        if line.startswith("#") or set(line) <= set("-=*_ "):  # títulos de secção, separadores
            i += 1
            continue
        if cur is None:
            before = True
            i += 1
            continue
        if line.startswith("!"):  # ação forçada
            end = para_end(i + 1)
            cur["lines"].append({"kind": "action", "character": None,
                                 "text": "\n".join([line[1:].strip()] + [x.strip() for x in raw[i + 1:end]]).strip()})
            i = end
            continue
        # «NOME: fala» numa só linha, com NOME de uma personagem conhecida
        m = re.match(r"^@?([^:()]{1,40}?)\s*(\([^)]*\))?\s*:\s+(\S.*)$", line)
        if m and _norm(m.group(1)) in chars:
            end = para_end(i + 1)
            txt = "\n".join([m.group(3)] + [x.strip() for x in raw[i + 1:end]]).strip()
            cur["lines"].append({"kind": "dialogue", "character": chars[_norm(m.group(1))].name, "text": txt})
            i = end
            continue
        nxt = raw[i + 1].strip() if i + 1 < len(raw) else ""
        if (line.startswith("@") or _looks_like_cue(line)) and nxt:
            name = _EXT.sub("", line.lstrip("@")).rstrip(":").strip()
            who = chars.get(_norm(name))
            if who is None:
                errors.append(f"Linha {n}: personagem desconhecida «{name}» (cria-a primeiro no projeto, usa o nome ou o "
                              "código de uma personagem que exista, ou começa a linha com «!» se for ação).")
            end = para_end(i + 1)
            txt = "\n".join(x.replace("**", "").strip() for x in raw[i + 1:end]).strip()
            cur["lines"].append({"kind": "dialogue", "character": who.name if who else None, "text": txt})
            i = end
            continue
        if _looks_like_cue(line) and line.rstrip().endswith(":") and not nxt:  # transição (CORTA PARA:)
            i += 1
            continue
        end = para_end(i + 1)
        cur["lines"].append({"kind": "action", "character": None,
                             "text": "\n".join(x.replace("**", "").strip() for x in raw[i:end]).strip()})
        i = end
    if before:
        warnings.append("O texto antes da primeira cena não passa para a app (fica só no ficheiro).")
    if not scenes and str(text).strip():
        errors.append("Não encontrei nenhuma cena: cada cena começa com «## Título» (ou «INT. LOCAL - NOITE»).")
    for k, sc in enumerate(scenes, 1):
        if not sc["lines"]:
            warnings.append(f"A cena {k} («{sc['title'] or sc['location_label'] or '—'}») não tem ações nem falas.")
    return scenes, errors, warnings


def _set_loc(cur: dict, value: str, locs: dict, warnings: list, n: int) -> None:
    value = value.strip()
    if not value:
        return
    a = locs.get(_norm(value))
    cur["location_label"] = a.label or a.name if a else value
    cur["location"] = a.id if a else None
    if a is None:
        warnings.append(f"Linha {n}: o local «{value}» não existe no projeto (a cena fica sem local).")


def _set_time(cur: dict, value: str, times: dict, warnings: list, n: int) -> None:
    value = value.strip()
    if not value:
        return
    EN = {"NIGHT": "Noite", "DAY": "Tarde", "MORNING": "Manhã", "DAWN": "Madrugada", "DUSK": "Pôr do sol",
          "SUNSET": "Pôr do sol", "NOON": "Meio-dia", "AFTERNOON": "Tarde", "DIA": "Tarde", "MADRUGADA": "Madrugada"}
    hit = times.get(_norm(value)) or (EN.get(_norm(value)) if EN.get(_norm(value)) in times.values() else None)
    if hit:
        cur["time"] = hit
    else:
        cur["time"] = value[:1].upper() + value[1:].lower()
        warnings.append(f"Linha {n}: a hora «{value}» não está na lista ({', '.join(times.values())}); fica como está escrita.")


# ── Aprovar ──────────────────────────────────────────────────

def _scene_text(lines: list[tuple[str, str | None, str]]) -> str:
    return "\n".join(t for _k, _c, t in lines)[:4000]


def _match(n: int, m: int, sim) -> list[tuple[int, int]]:
    """Pares (i velho, j novo) um-para-um, os mais parecidos primeiro (só com semelhança ≥ 0.35). A ordem não conta: uma
    cena que mudou de sítio continua a ser a mesma."""
    cands = sorted(((sim(i, j), i, j) for i in range(n) for j in range(m)), key=lambda x: (-x[0], abs(x[1] - x[2])))
    used_i, used_j, pairs = set(), set(), []
    for score, i, j in cands:
        if score < 0.35:
            break
        if i not in used_i and j not in used_j:
            used_i.add(i)
            used_j.add(j)
            pairs.append((i, j))
    return pairs


def _line_key(kind: str, char, text: str) -> str:
    return f"{kind}|{char or ''}|{' '.join(text.split())}"


def plan_scenes(session: Session, project: Project, obj, text: str) -> dict:
    """O que aprovar o guião faz às cenas do episódio/ato: {"errors", "warnings", "scenes": [...], "trash": [...],
    "lost": [...], "pairs"…}. Usado para mostrar antes e, outra vez, para aplicar."""
    parsed, errors, warnings = parse_script(session, project, text)
    old = _container_scenes(session, project, obj)
    names = {a.id: a.name for a in session.exec(select(Asset).where(Asset.project_id == project.id)).all()}
    old_lines = {sc.id: _lines(session, sc) for sc in old}
    old_t = {sc.id: _scene_text([(ln.kind, ln.character_id, ln.text) for ln in old_lines[sc.id]]) for sc in old}
    new_t = [_scene_text([(x["kind"], x["character"], x["text"]) for x in sc["lines"]]) for sc in parsed]

    def sim(i: int, j: int) -> float:
        a, b = old[i], parsed[j]
        if old_t[a.id] and new_t[j]:
            r = difflib.SequenceMatcher(None, old_t[a.id], new_t[j]).ratio()
        elif a.description and b["description"]:  # cenas ainda sem guião: pela descrição
            r = 0.8 * difflib.SequenceMatcher(None, a.description, b["description"]).ratio()
        else:
            r = 0.0
        if a.location_id and a.location_id == b["location"]:
            r += 0.2
        if a.title and b["title"] and _norm(a.title) == _norm(b["title"]):
            r += 0.3
        return r

    pairs = dict((j, i) for i, j in _match(len(old), len(parsed), sim))
    out_scenes, lost = [], []
    for j, sc in enumerate(parsed):
        row = {"n": j + 1, "title": sc["title"] or sc["location_label"] or f"Cena {j + 1}", "parsed": sc}
        if j not in pairs:
            row.update(action="create", added=len(sc["lines"]), changed=0, removed=0, items=[
                {k: v for k, v in x.items()} for x in sc["lines"]])
            out_scenes.append(row)
            continue
        osc = old[pairs[j]]
        olines = old_lines[osc.id]
        okeys = [_line_key(ln.kind, names.get(ln.character_id) if ln.kind == "dialogue" else None, ln.text) for ln in olines]
        nkeys = [_line_key(x["kind"], x["character"], x["text"]) for x in sc["lines"]]
        items = [dict(x) for x in sc["lines"]]
        added = changed = removed = 0
        removed_ids = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, okeys, nkeys, autojunk=False).get_opcodes():
            if tag == "equal":
                for a, b in zip(range(i1, i2), range(j1, j2)):
                    items[b]["id"] = olines[a].id
                continue
            both = min(i2 - i1, j2 - j1) if tag == "replace" else 0
            for a, b in zip(range(i1, i1 + both), range(j1, j1 + both)):
                items[b]["id"] = olines[a].id
                changed += 1
            removed_ids += [olines[a].id for a in range(i1 + both, i2)]
            added += (j2 - j1) - both
        # linhas que só mudaram de sítio na cena: continuam a ser as mesmas (com os planos)
        by_id = {ln.id: k for k, ln in enumerate(olines)}
        for b, it in enumerate(items):
            if "id" in it or not removed_ids:
                continue
            best = max(removed_ids, key=lambda x: difflib.SequenceMatcher(None, okeys[by_id[x]], nkeys[b]).ratio())
            if difflib.SequenceMatcher(None, okeys[by_id[best]], nkeys[b]).ratio() >= 0.85:
                it["id"] = best
                removed_ids.remove(best)
                added -= 1
                changed += 1
        removed = len(removed_ids)
        if removed_ids:
            for sh_id, line_id in session.exec(select(ShotLineLink.shot_id, ShotLineLink.line_id)
                                               .where(ShotLineLink.line_id.in_(removed_ids))).all():
                sh = session.get(Shot, sh_id)
                ln = next(x for x in olines if x.id == line_id)
                lost.append(f"C{j + 1:02d} P{sh.number:02d} cobria «{_short(ln.text)}», que deixa de existir.")
        meta = []
        if (sc["title"] or None) and sc["title"] != osc.title:
            meta.append("título")
        if sc["location"] != osc.location_id:
            meta.append("local")
        if (sc["time"] or None) != (osc.time_of_day or None):
            meta.append("hora")
        if (sc["description"] or None) != (osc.description or None):
            meta.append("resumo")
        same = not (added or changed or removed or meta or osc.number != j + 1)
        row.update(action="same" if same else "update", scene_id=osc.id, old_number=osc.number, added=added,
                   changed=changed, removed=removed, meta=meta, items=items)
        out_scenes.append(row)
    matched = set(pairs.values())
    trash = []
    for i, osc in enumerate(old):
        if i in matched:
            continue
        shots = session.exec(select(func.count(Shot.id)).where(Shot.scene_id == osc.id)).one()
        trash.append({"scene_id": osc.id, "label": f"C{osc.number:02d} «{osc.title or '—'}»", "shots": shots,
                      "lines": len(old_lines[osc.id])})
    return {"errors": errors, "warnings": warnings, "scenes": out_scenes, "trash": trash, "lost": lost}


def _short(t: str, n: int = 60) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def preview(session: Session, project: Project, f: WritingFile) -> dict:
    """O que aprovar faz, para mostrar antes: {"kind", "label", "errors", "warnings", …}."""
    if not f.target:
        return {"kind": "none", "label": "", "errors": [], "warnings": []}
    kind, obj, attr = _resolve(session, project, f.target)
    label = target_label(session, project, f.target)
    if kind == "text":
        cur = (getattr(obj, attr) or "").strip()
        new = f.content.strip()
        return {"kind": "text", "label": label, "errors": [], "warnings": [], "same": cur == new,
                "rows": diff_rows(cur, new)}
    plan = plan_scenes(session, project, obj, f.content)
    return {"kind": "scenes", "label": label, **plan}


def approve(session: Session, project: Project, f: WritingFile, version: int) -> str:
    """Passa o ficheiro para a app (destino) e tranca-o. version = a versão que o utilizador viu."""
    if f.deleted_at:
        raise WritingError("O ficheiro está apagado: repõe-no primeiro.")
    if f.status == "approved":
        raise WritingError("Já está aprovado.")
    if int(version) != f.version:
        raise WritingError(f"O ficheiro mudou enquanto vias (vai na versão {f.version}): vê outra vez antes de aprovar.")
    summary = "sem destino na app (fica só trancado)"
    if f.target:
        kind, obj, attr = _resolve(session, project, f.target)
        if kind == "text":
            setattr(obj, attr, f.content.strip() or None)
            session.add(obj)
            summary = f"«{target_label(session, project, f.target)}» atualizado"
        else:
            summary = _apply_scenes(session, project, obj, f.content)
    f.status, f.approved_version, f.approved_at = "approved", f.version, now()
    session.add(f)
    session.add(WritingComment(project_id=project.id, file_id=f.id, version=f.version, author=USER,
                               text=f"✔ Aprovado (versão {f.version}): {summary}. Trancado."))
    return summary


def _apply_scenes(session: Session, project: Project, obj, text: str) -> str:
    from app import trash
    from app.assistant.fields import set_script

    plan = plan_scenes(session, project, obj, text)
    if plan["errors"]:
        raise WritingError("O guião tem erros: " + " ".join(plan["errors"][:5]))
    for t in plan["trash"]:
        sc = session.get(Scene, t["scene_id"])
        if sc:
            trash.trash_scene(session, project, sc)
    session.flush()
    created = updated = 0
    for row in plan["scenes"]:
        p = row["parsed"]
        if row["action"] == "create":
            sc = Scene(project_id=project.id, number=row["n"],
                       episode_id=obj.id if isinstance(obj, Episode) else None,
                       act_id=obj.id if isinstance(obj, Act) else None)
            sc.title = p["title"] or p["location_label"] or None
            created += 1
        else:
            sc = session.get(Scene, row["scene_id"])
            sc.number = row["n"]
            if p["title"]:
                sc.title = p["title"]
            if row["action"] == "update":
                updated += 1
        sc.location_id, sc.time_of_day, sc.description = p["location"], p["time"], p["description"]
        session.add(sc)
        session.flush()
        set_script(session, project, sc, [{k: v for k, v in it.items() if k in ("id", "kind", "character", "text")}
                                          for it in row["items"]])
        session.flush()
        wanted = {sc.location_id} if sc.location_id else set()
        ids = {a.name: a.id for a in session.exec(select(Asset).where(Asset.project_id == project.id)).all()}
        wanted |= {ids[it["character"]] for it in row["items"] if it.get("character") in ids}
        for aid in wanted:
            if not session.get(AssetSceneLink, (aid, sc.id)):
                session.add(AssetSceneLink(asset_id=aid, scene_id=sc.id))
    bits = [f"{len(plan['scenes'])} cena(s)"]
    if created:
        bits.append(f"{created} nova(s)")
    if updated:
        bits.append(f"{updated} mudada(s)")
    if plan["trash"]:
        bits.append(f"{len(plan['trash'])} para a Reciclagem")
    return "guião passado para a app: " + ", ".join(bits)


def unlock(session: Session, project: Project, f: WritingFile) -> None:
    if f.status != "approved":
        return
    f.status = "draft"
    session.add(f)
    session.add(WritingComment(project_id=project.id, file_id=f.id, version=f.version, author=USER,
                               text=f"🔓 Destrancado para rever. A app continua com a versão {f.approved_version} até nova aprovação."))


def revert(session: Session, project: Project, f: WritingFile, version: int, author: str) -> WritingFile:
    _locked(f)
    text = version_text(session, f, int(version))
    if text == f.content:
        return f
    return _new_version(session, f, text, author, f"Reposta a versão {version}")


def delete(session: Session, project: Project, f: WritingFile, by: str, reason: str = "") -> WritingFile:
    """Apaga o ficheiro para «Apagados», com as versões, a discussão e as propostas (repõe-se com restore). Um ficheiro
    aprovado só o utilizador apaga (o que já passou para a app fica na app)."""
    if f.deleted_at:
        raise WritingError(f"«{original_path(f)}» já está apagado.")
    if f.status == "approved" and by != USER:
        raise WritingError(f"«{f.path}» está aprovado e trancado: só o {USER} o pode apagar.")
    path = f.path
    f.path, f.deleted_at, f.deleted_by = f"{TRASH}/{f.id}/{path}", now(), by
    session.add(f)
    for prop in session.exec(select(WritingProposal).where(WritingProposal.file_id == f.id,
                                                           WritingProposal.status == "open")).all():
        prop.status, prop.closed_by, prop.closed_at = "withdrawn", by, now()
        session.add(prop)
    session.add(WritingComment(project_id=project.id, file_id=f.id, version=f.version, author=by,
                               text=f"🗑 Apagado{': ' + reason.strip() if reason and reason.strip() else ''}."))
    notify(session, project.id, participants(session, project.id), "deleted",
           f"{by} apagou «{path}»" + (f": {_short(reason, 120)}" if reason else "") + f" (o {USER} pode repô-lo em Apagados)",
           by, file_id=f.id)
    return f


def restore(session: Session, project: Project, f: WritingFile, by: str) -> WritingFile:
    if not f.deleted_at:
        raise WritingError("Este ficheiro não está apagado.")
    path = original_path(f)
    if get(session, project.id, path):  # o caminho já está ocupado: fica ao lado
        stem, dot, ext = path.rpartition(".")
        path = f"{stem} (reposto).{ext}" if dot else path + " (reposto)"
    f.path, f.deleted_at, f.deleted_by = path, None, None
    session.add(f)
    session.add(WritingComment(project_id=project.id, file_id=f.id, version=f.version, author=by, text="♻ Reposto."))
    notify(session, project.id, participants(session, project.id), "deleted", f"{by} repôs «{path}»", by, file_id=f.id)
    return f


def purge(session: Session, f: WritingFile) -> None:
    """Apaga de vez (só a partir de «Apagados»): o ficheiro, as versões, a discussão e as propostas."""
    if not f.deleted_at:
        raise WritingError("Primeiro apaga o ficheiro; depois, em Apagados, apaga-o de vez.")
    for model in (WritingNotice, WritingComment, WritingVersion):
        for row in session.exec(select(model).where(model.file_id == f.id)).all():
            session.delete(row)
    for prop in session.exec(select(WritingProposal).where(WritingProposal.file_id == f.id)).all():
        for model in (WritingNotice, WritingComment):
            for row in session.exec(select(model).where(model.proposal_id == prop.id)).all():
                session.delete(row)
        for t in session.exec(select(WritingTask).where(WritingTask.proposal_id == prop.id)).all():
            t.proposal_id = None
            session.add(t)
        session.delete(prop)
    for t in session.exec(select(WritingTask).where(WritingTask.file_id == f.id)).all():
        t.file_id = None
        session.add(t)
    session.flush()
    session.delete(f)
