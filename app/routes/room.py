"""Sala de argumentistas (app/writing.py): a página do utilizador. Vê os ficheiros, as versões, as propostas, as tarefas e a
discussão das IAs, participa em tudo, e é o único que aprova para a app (e tranca) e destranca."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app import writing
from app.db import get_session
from app.forms import integer, text
from app.models import WritingFile
from app.security import csrf_protect, require_login
from app.stages import get_project
from app.templating import templates

router = APIRouter(prefix="/projetos/{project_id}/sala", dependencies=[Depends(require_login), Depends(csrf_protect)])
TABS = ("texto", "discussao", "historico", "aprovar")
ME = writing.USER


def _file(session: Session, project_id: int, file_id: int) -> WritingFile:
    f = session.get(WritingFile, file_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")
    return f


def _back(project_id: int, f: WritingFile | None = None, tab: str | None = None, **extra) -> RedirectResponse:
    q = []
    if f is not None:
        q.append(f"f={f.id}")
    if tab:
        q.append(f"tab={tab}")
    q += [f"{k}={quote(str(v))}" for k, v in extra.items() if v is not None]
    return RedirectResponse(f"/projetos/{project_id}/sala" + ("?" + "&".join(q) if q else ""), status_code=303)


def _to(project_id: int, back: str | None) -> RedirectResponse:
    """Volta à vista de onde veio o formulário (só a parte do endereço depois de ?)."""
    q = (back or "").lstrip("?")
    if any(c in q for c in "/\\:") or len(q) > 200:
        q = ""
    return RedirectResponse(f"/projetos/{project_id}/sala" + (f"?{q}" if q else ""), status_code=303)


def _tree(fs: list[WritingFile], cur: WritingFile | None = None) -> dict:
    """Os ficheiros em árvore de pastas: {"name", "path", "dirs": [...], "files": [{"f", "name"}], "n", "has_cur"}."""
    root = {"name": "", "path": "", "dirs": {}, "files": [], "n": 0, "has_cur": False}
    for f in fs:
        *folders, name = f.path.split("/")
        node = root
        node["n"] += 1
        for k, part in enumerate(folders):
            node = node["dirs"].setdefault(part, {"name": part, "path": "/".join(folders[:k + 1]), "dirs": {}, "files": [],
                                                 "n": 0, "has_cur": False})
            node["n"] += 1
            if cur is not None and cur.id == f.id:
                node["has_cur"] = True
        node["files"].append({"f": f, "name": name})

    def done(node):
        node["dirs"] = [done(d) for _k, d in sorted(node["dirs"].items(), key=lambda kv: kv[0].lower())]
        node["files"].sort(key=lambda x: x["name"].lower())
        return node
    return done(root)


@router.get("")
def room_page(request: Request, project_id: int, f: int | None = None, tab: str = "texto", ver: int | None = None,
              d: int | None = None, p: int | None = None, v: str | None = None, modo: str | None = None, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    fs = writing.files(session, project.id)
    cur = _file(session, project.id, f) if f else None
    tab = tab if tab in TABS else "texto"
    view = "file" if cur else "proposal" if p else v if v in ("propostas", "tarefas", "avisos", "apagados") else "geral"
    ctx = {"project": project, "tree": _tree(fs, cur if f else None), "cur": cur, "tab": tab, "view": view, "files_n": len(fs),
           "counts": writing.comment_counts(session, project.id), "targets": writing.targets(session, project),
           "taken": {x.target: x.path for x in fs if x.target}, "author_label": writing.author_label,
           "paths": {x.id: x.path for x in fs}, "people": writing.participants(session, project.id), "me": ME,
           "open_props": len(writing.proposals(session, project.id)),
           "active_tasks": len(writing.tasks(session, project.id, "active")),
           "unread": writing.unread_count(session, ME, project.id),
           "trash": writing.deleted_files(session, project.id), "original_path": writing.original_path,
           "PSTATUS": writing.PROPOSAL_STATUS, "TSTATUS": writing.TASK_STATUS, "VERDICTS": writing.VERDICTS,
           "back": request.url.query,
           "notice": request.session.pop("flash_info", None), "error": request.session.pop("flash_error", None)}
    if view == "geral":
        ctx["thread"] = writing.threaded(writing.comments(session, project.id))
    elif view == "propostas":
        ctx["props"] = [(x, writing.proposal_state(session, x)) for x in writing.proposals(session, project.id, None)]
    elif view == "proposal":
        try:
            prop = writing.get_proposal(session, project.id, p)
        except writing.WritingError:
            raise HTTPException(status_code=404, detail="Proposta não encontrada.")
        ctx["prop"], ctx["pst"] = prop, writing.proposal_state(session, prop)
        ctx["alt_of"] = session.get(writing.WritingProposal, prop.alternative_to) if prop.alternative_to else None
        ctx["alts"] = [x for x in writing.proposals(session, project.id, None) if x.alternative_to == prop.id]
        ctx["thread"] = writing.threaded(writing.comments(session, project.id, proposal_id=prop.id))
    elif view == "tarefas":
        ts = writing.tasks(session, project.id)
        ctx["tasks"] = ts
        ctx["task_threads"] = {t.id: writing.threaded(writing.comments(session, project.id, task_id=t.id)) for t in ts}
        ctx["prop_numbers"] = {x.id: x.number for x in writing.proposals(session, project.id, None)}
    elif view == "avisos":
        items = writing.notices(session, ME, project.id, unread_only=False, limit=80)
        ctx["items"] = items
        ctx["prop_numbers"] = {x.id: x.number for x in writing.proposals(session, project.id, None)}
        unread = [n for n in items if not n.read]
        if unread:  # ver é ler
            writing.mark_read(session, unread)
            session.commit()
            ctx["fresh"] = {n.id for n in unread}
    if cur is not None:
        ctx["target_label"] = writing.target_label(session, project, cur.target) if cur.target else ""
        ctx["thread"] = writing.threaded(writing.comments(session, project.id, cur.id))
        ctx["file_props"] = writing.proposals(session, project.id, "open", cur.id)
        if tab == "texto":
            ctx["ver"] = ver if ver and 1 <= ver < cur.version else None
            ctx["shown"] = writing.version_text(session, cur, ctx["ver"]) if ctx["ver"] else cur.content
            ctx["mode"] = modo if modo in ("editar", "simples") else "ler"
            if ctx["mode"] == "editar" and (ctx["ver"] or cur.status != "draft" or cur.deleted_at):
                ctx["mode"] = "simples"
            if ctx["mode"] == "ler":
                from app import reading

                ctx["reading"] = reading.render(session, project, cur, ctx["shown"])
        if tab == "historico":
            ctx["versions"] = writing.versions(session, cur)
            sel = d if d and 1 < d <= cur.version else None
            if sel:
                ctx["d"] = sel
                ctx["diff"] = writing.diff_rows(writing.version_text(session, cur, sel - 1), writing.version_text(session, cur, sel))
        if tab == "aprovar" and cur.status == "draft":
            try:
                ctx["preview"] = writing.preview(session, project, cur)
            except writing.WritingError as exc:
                ctx["preview"] = {"kind": "none", "errors": [str(exc)], "warnings": []}
    return templates.TemplateResponse(request, "room/room.html", ctx)


def _do(request: Request, session: Session, fn, ok: str | None = None) -> bool:
    try:
        with writing.lock:
            out = fn()
            session.commit()
        if ok or isinstance(out, str):
            request.session["flash_info"] = ok or out
        return True
    except writing.WritingError as exc:
        session.rollback()
        request.session["flash_error"] = str(exc)
        return False


@router.get("/{file_id}/pdf")
async def file_pdf(request: Request, project_id: int, file_id: int, ver: int | None = None,
                   session: Session = Depends(get_session)):
    """O ficheiro em PDF (como no modo de leitura, em papel A4; guiões com as margens de um argumento)."""
    import re
    import unicodedata
    from urllib.parse import quote as q

    from fastapi.responses import Response

    from app import reading, room_pdf

    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    v = ver if ver and 1 <= ver < f.version else f.version
    text = writing.version_text(session, f, v)
    path = writing.original_path(f)
    body = reading.render(session, project, f, text)
    title = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    sub = f"{project.name} · versão {v}" + (" · aprovada" if f.approved_version == v else "")
    try:
        pdf = await room_pdf.to_pdf(room_pdf.page_html(title, sub, body), f"{project.prefix} · {path} · v{v}")
    except Exception as exc:  # noqa: BLE001 — o browser do servidor falhou: diz-se na página
        request.session["flash_error"] = f"Não foi possível gerar o PDF ({type(exc).__name__})."
        return _back(project.id, f)
    name = f"{title}{'' if v == f.version else f'-v{v}'}.pdf"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()) or "ficheiro.pdf"
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{q(name)}"})


@router.post("/novo")
async def new_file(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    path, target = text(form, "path") or "", text(form, "target") or None
    created = {}

    def go():
        if target and not form.get("content") and writing.get(session, project.id, path) is None:
            content = writing.read_target(session, project, target)  # começa pelo que a app já tem
        else:
            content = form.get("content") or ""
        f, new, _note = writing.write(session, project, path, content, ME, "Ficheiro criado" if not target else
                                      "Ficheiro criado (com o que a app tem)", target=target)
        if not new:
            raise writing.WritingError(f"Já existe «{f.path}».")
        created["f"] = f
    if _do(request, session, go):
        return _back(project.id, created["f"])
    return _back(project.id)


@router.post("/comentar")
async def comment(request: Request, project_id: int, session: Session = Depends(get_session)):
    """Um comentário em qualquer fio: geral, de um ficheiro (file_id), de uma proposta ou de uma tarefa (números),
    ou resposta a outro (reply_to)."""
    project = get_project(session, project_id)
    form = await request.form()
    fid = integer(form, "file_id", "Ficheiro")
    path = _file(session, project.id, fid).path if fid else None

    def go():
        writing.comment(session, project, form.get("text") or "", ME, path, reply_to=integer(form, "reply_to", "Resposta"),
                        proposal=integer(form, "proposal", "Proposta"), task=integer(form, "task", "Tarefa"))
    _do(request, session, go)
    return _to(project.id, text(form, "back"))


@router.post("/{file_id}/gravar")
async def save(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    form = await request.form()
    base = integer(form, "base_version", "Versão")

    def go():
        writing._locked(f)
        _f, _c, note = writing.write(session, project, f.path, form.get("content") or "", ME, text(form, "message") or "",
                                     base_version=base)
        return f"Gravado ({note})." if note else None
    _do(request, session, go)
    return _back(project.id, f)


@router.post("/{file_id}/destino")
async def set_target(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    form = await request.form()
    _do(request, session, lambda: writing.set_target(session, project, f, text(form, "target") or None))
    return _back(project.id, f, text(form, "tab") or None)


@router.post("/{file_id}/renomear")
async def rename(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    form = await request.form()
    _do(request, session, lambda: writing.rename(session, project, f.path, text(form, "path") or "") and None)
    return _back(project.id, f)


@router.post("/{file_id}/aprovar")
async def approve(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    form = await request.form()
    version = integer(form, "version", "Versão") or 0
    if _do(request, session, lambda: "Aprovado e trancado: " + writing.approve(session, project, f, version) + "."):
        return _back(project.id, f)
    return _back(project.id, f, "aprovar")


@router.post("/{file_id}/destrancar")
def unlock(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    _do(request, session, lambda: writing.unlock(session, project, f),
        ok="Destrancado: as IAs já o podem mudar. A app continua com a versão aprovada até voltares a aprovar.")
    return _back(project.id, f)


@router.post("/{file_id}/repor/{version}")
def revert(request: Request, project_id: int, file_id: int, version: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    _do(request, session, lambda: writing.revert(session, project, f, version, ME) and None,
        ok=f"Reposta a versão {version} (ficou como versão nova).")
    return _back(project.id, f)


@router.post("/{file_id}/apagar")
async def delete(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    form = await request.form()
    path = f.path
    if _do(request, session, lambda: writing.delete(session, project, f, ME, text(form, "reason") or "") and None,
           ok=f"Apagado: {path}. Está em Apagados, de onde o podes repor."):
        return _back(project.id)
    return _back(project.id, f)


@router.post("/{file_id}/repor-ficheiro")
def restore(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    if _do(request, session, lambda: f"Reposto: {writing.restore(session, project, f, ME).path}."):
        return _back(project.id, f)
    return _back(project.id, v="apagados")


@router.post("/{file_id}/apagar-de-vez")
def purge(request: Request, project_id: int, file_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    f = _file(session, project.id, file_id)
    path = writing.original_path(f)
    _do(request, session, lambda: writing.purge(session, f), ok=f"Apagado de vez: {path}.")
    return _back(project.id, v="apagados")


# ── Propostas ─────────────────────────────────────────────────

@router.post("/propostas/{number}/aplicar")
def proposal_apply(request: Request, project_id: int, number: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    _do(request, session, lambda: f"Proposta #{number} aplicada: {(f := writing.apply_proposal(session, project, number, ME)).path} "
                                  f"vai na versão {f.version}.")
    return _back(project.id, p=number)


@router.post("/propostas/{number}/rejeitar")
async def proposal_reject(request: Request, project_id: int, number: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    _do(request, session, lambda: writing.close_proposal(session, project, number, ME, "rejected", text(form, "note") or "") and None,
        ok=f"Proposta #{number} rejeitada.")
    return _back(project.id, p=number)


@router.post("/propostas/{number}/parecer")
async def proposal_review(request: Request, project_id: int, number: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    _do(request, session, lambda: writing.review(session, project, number, ME, text(form, "verdict") or "",
                                                 text(form, "text") or "") and None)
    return _back(project.id, p=number)


# ── Tarefas ───────────────────────────────────────────────────

@router.post("/tarefas/nova")
async def task_new(request: Request, project_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    fid = integer(form, "file_id", "Ficheiro")
    path = _file(session, project.id, fid).path if fid else None
    _do(request, session, lambda: writing.create_task(session, project, ME, text(form, "title") or "", text(form, "details") or "",
                                                      text(form, "assignee"), path, integer(form, "proposal", "Proposta")) and None)
    return _back(project.id, v="tarefas")


@router.post("/tarefas/{number}")
async def task_update(request: Request, project_id: int, number: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    form = await request.form()
    _do(request, session, lambda: writing.update_task(session, project, number, ME, text(form, "status"),
                                                      form.get("result") if "result" in form else None,
                                                      text(form, "assignee") if "assignee" in form else "__keep__") and None)
    return _back(project.id, v="tarefas")
