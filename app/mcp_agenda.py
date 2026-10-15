"""Ferramentas MCP da Agenda (app/agenda.py): os agentes leem, criam, mudam e apagam itens da agenda de todos os
projetos (blocos de trabalho e dias de entrega/publicação) e criam publicações recorrentes."""

from datetime import date
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlmodel import Session

from app import agenda
from app.db import engine
from app.mcp_server import PUBLIC, READ, WRITE, log, server
from app.models import PLAN_KINDS, PLAN_STATUS, PlanItem, Project

KINDS_HELP = "kind: " + ", ".join(f"{k} ({v})" for k, v in PLAN_KINDS.items())
STATUS_HELP = "status: " + ", ".join(f"{k} ({v})" for k, v in PLAN_STATUS.items())


def _row(s: Session, i: PlanItem) -> dict:
    p = s.get(Project, i.project_id) if i.project_id else None
    x = agenda.info(i)
    return {"id": i.id, "title": i.title, "project_id": i.project_id, "project": f"{p.prefix} · {p.name}" if p else None,
            "kind": i.kind, "start": i.start.isoformat() if i.start else None, "due": i.due.isoformat(),
            "status": i.status, "overdue": x["overdue"], "running": x["running"], "days_left": x["left"],
            "notes": i.notes, "link": f"{PUBLIC}{i.link}" if i.link else None, "recurring": bool(i.recurrence_id)}


def _item(s: Session, item_id: int) -> PlanItem:
    i = s.get(PlanItem, item_id)
    if i is None:
        raise ToolError(f"Não há o item {item_id} na agenda. Usa list_agenda.")
    return i


def _check_project(s: Session, project_id: int | None) -> None:
    if project_id and s.get(Project, project_id) is None:
        raise ToolError(f"Não existe o projeto {project_id}. Usa list_projects.")


def _commit(s: Session, fn):
    try:
        out = fn()
        s.commit()
        return out
    except agenda.AgendaError as exc:
        s.rollback()
        raise ToolError(str(exc))


@server.tool(annotations=READ, structured_output=True, title="Agenda de hoje",
             description="A agenda de hoje (de todos os projetos ou de um): o que está atrasado, o que está a decorrer ou "
                         "é para hoje, o que começa nos próximos 7 dias e o que já foi feito hoje.")
def agenda_today(project_id: int | None = None) -> dict[str, Any]:
    with Session(engine) as s:
        v = agenda.today_view(s, project_id)
        return {"today": v["today"].isoformat(), **{k: [_row(s, i) for i in v[k]] for k in ("overdue", "now", "soon", "done_today")}}


@server.tool(annotations=READ, structured_output=True, title="Itens da agenda",
             description="Os itens da agenda (blocos de trabalho com início e prazo, ou um só dia), por ordem de prazo. "
                         "Filtros: project_id; status = open (por fazer e em curso, por omissão), all, " + ", ".join(PLAN_STATUS)
                         + "; date_from / date_to (AAAA-MM-DD) para os itens que tocam esse período.")
def list_agenda(project_id: int | None = None, status: str = "open", date_from: str | None = None,
                date_to: str | None = None) -> dict[str, Any]:
    try:
        a = date.fromisoformat(date_from) if date_from else None
        b = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        raise ToolError("Datas no formato AAAA-MM-DD.")
    with Session(engine) as s:
        rows = agenda.items(s, project_id)
        if status == "open":
            rows = [i for i in rows if i.status != "done"]
        elif status in PLAN_STATUS:
            rows = [i for i in rows if i.status == status]
        if a:
            rows = [i for i in rows if i.due >= a]
        if b:
            rows = [i for i in rows if agenda.first(i) <= b]
        return {"today": agenda.today().isoformat(), "items": [_row(s, i) for i in rows]}


@server.tool(annotations=WRITE, structured_output=True, title="Acrescentar à agenda",
             description="Acrescenta um item à agenda: title; due = o prazo ou o dia (AAAA-MM-DD); start = o início, para "
                         "um bloco de trabalho (ex.: «E04» de 2026-11-01 a 2026-11-15; sem start é um só dia, como uma "
                         "publicação); project_id (opcional); " + KINDS_HELP + "; notes; link = caminho na app (ex.: "
                         "/projetos/2/sala?f=5). Sem subtarefas: um item é um bloco de tempo. Diz ao utilizador antes.")
def add_agenda_item(title: str, due: str, start: str | None = None, project_id: int | None = None, kind: str = "other",
                    notes: str | None = None, link: str | None = None) -> dict[str, Any]:
    if kind not in PLAN_KINDS:
        raise ToolError(KINDS_HELP)
    with Session(engine) as s:
        _check_project(s, project_id)
        i = _commit(s, lambda: agenda.save_item(s, PlanItem(title="", due=agenda.today()), {
            "title": title, "due": due, "start": start, "project_id": str(project_id or ""), "kind": kind, "notes": notes,
            "link": link}))
        log.info("mcp: agenda + %s (%s)", i.title, i.due)
        return {**_row(s, i), "text": f"Acrescentado à agenda: {i.title} ({agenda.info(i)['when']})."}


@server.tool(annotations=WRITE, structured_output=True, title="Mudar um item da agenda",
             description="Muda um item da agenda (item_id de list_agenda): só os campos que mandares — title, start, due "
                         "(AAAA-MM-DD; start=\"\" tira o início e fica um só dia), project_id (0 = nenhum), " + KINDS_HELP
                         + ", " + STATUS_HELP + ", notes, link. Diz ao utilizador antes.")
def update_agenda_item(item_id: int, title: str | None = None, start: str | None = None, due: str | None = None,
                       project_id: int | None = None, kind: str | None = None, status: str | None = None,
                       notes: str | None = None, link: str | None = None) -> dict[str, Any]:
    with Session(engine) as s:
        i = _item(s, item_id)
        if project_id:
            _check_project(s, project_id)
        if status is not None and status not in PLAN_STATUS:
            raise ToolError(STATUS_HELP)
        if kind is not None and kind not in PLAN_KINDS:
            raise ToolError(KINDS_HELP)
        form = {"title": i.title if title is None else title,
                "start": (i.start.isoformat() if i.start else "") if start is None else start,
                "due": i.due.isoformat() if due is None else due,
                "project_id": str(i.project_id or "") if project_id is None else str(project_id or ""),
                "kind": i.kind if kind is None else kind, "status": status or i.status,
                "notes": i.notes if notes is None else notes, "link": i.link if link is None else link}
        _commit(s, lambda: agenda.save_item(s, i, form))
        return {**_row(s, i), "text": f"Mudado: {i.title} ({agenda.info(i)['when']}, {PLAN_STATUS[i.status].lower()})."}


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
             title="Apagar da agenda",
             description="Apaga um item da agenda (item_id de list_agenda). Não se desfaz: diz ao utilizador antes.")
def delete_agenda_item(item_id: int) -> str:
    with Session(engine) as s:
        i = _item(s, item_id)
        title = i.title
        s.delete(i)
        s.commit()
        log.info("mcp: agenda - %s", title)
        return f"Apagado da agenda: {title}."


class _Form(dict):
    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([] if v is None else [v])


@server.tool(annotations=WRITE, structured_output=True, title="Publicações recorrentes",
             description="Cria publicações recorrentes na agenda (itens de um dia, numerados): title com {n} (ex.: "
                         "«Postar Short {n}»), weekdays = dias da semana (0 = segunda … 6 = domingo, ou «seg», «ter»…), "
                         "start e end (AAAA-MM-DD), project_id, first_number. Máx. 200. Diz ao utilizador antes.")
def add_recurring_posts(title: str, weekdays: list[Any], start: str, end: str, project_id: int | None = None,
                        first_number: int = 1) -> dict[str, Any]:
    names = {n.lower()[:3]: k for k, n in enumerate(agenda.WEEKDAYS)}
    days = []
    for d in weekdays or []:
        sd = str(d).strip().lower()
        if sd.isdigit():
            days.append(sd)
        elif sd[:3] in names:
            days.append(str(names[sd[:3]]))
        else:
            raise ToolError(f"Dia da semana desconhecido: {d}.")
    with Session(engine) as s:
        _check_project(s, project_id)
        rec, n = _commit(s, lambda: agenda.create_recurrence(s, _Form(
            title=title, start=start, end=end, weekdays=days, project_id=str(project_id or ""),
            first_number=str(first_number), kind="post")))
        return {"created": n, "weekdays": agenda.weekdays_label(rec.weekdays),
                "text": f"{n} publicações na agenda ({agenda.weekdays_label(rec.weekdays)}, de {start} a {end})."}
