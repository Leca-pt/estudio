"""Agenda (app/agenda.py): Hoje, Quadro, Linha do tempo e Visão geral, de todos os projetos."""

from datetime import date, timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app import agenda
from app.db import get_session
from app.models import PLAN_KINDS, PLAN_STATUS, PlanItem, PlanRecurrence, Project
from app.security import csrf_protect, require_login
from app.templating import templates

router = APIRouter(prefix="/agenda", dependencies=[Depends(require_login), Depends(csrf_protect)])
VIEWS = {"hoje": "Hoje", "quadro": "Quadro", "calendario": "Calendário", "linha": "Linha do tempo", "geral": "Visão geral"}


def _back(request: Request, default: str = "/agenda") -> RedirectResponse:
    """Volta à vista de onde veio (só caminhos da agenda)."""
    ref = urlsplit(request.headers.get("referer") or "")
    url = ref.path + (f"?{ref.query}" if ref.query else "") if ref.path.startswith("/agenda") else default
    if "/itens/" in ref.path:
        url = default
    return RedirectResponse(url, status_code=303)


def _ctx(request: Request, session: Session, view: str, project_id: int | None) -> dict:
    projects = list(session.exec(select(Project).order_by(Project.name)).all())
    return {"view": view, "VIEWS": VIEWS, "projects": projects, "pmap": {p.id: p for p in projects},
            "pid": project_id, "KINDS": PLAN_KINDS, "STATUS": PLAN_STATUS, "info": agenda.info, "fmt": agenda.fmt,
            "today": agenda.today(), "WEEKDAYS": agenda.WEEKDAYS, "recurrences": agenda.recurrences(session),
            "weekdays_label": agenda.weekdays_label,
            "notice": request.session.pop("flash_info", None), "error": request.session.pop("flash_error", None)}


def _pid(p: str | None) -> int | None:
    return int(p) if p and p.isdigit() else None


@router.get("")
def today_page(request: Request, p: str | None = None, session: Session = Depends(get_session)):
    ctx = _ctx(request, session, "hoje", _pid(p))
    ctx.update(agenda.today_view(session, ctx["pid"]))
    return templates.TemplateResponse(request, "agenda/page.html", ctx)


@router.get("/quadro")
def board_page(request: Request, p: str | None = None, session: Session = Depends(get_session)):
    ctx = _ctx(request, session, "quadro", _pid(p))
    ctx["cols"] = agenda.board(session, ctx["pid"])
    return templates.TemplateResponse(request, "agenda/page.html", ctx)


@router.get("/linha")
def timeline_page(request: Request, p: str | None = None, semana: int = 0, semanas: int = 8,
                  session: Session = Depends(get_session)):
    ctx = _ctx(request, session, "linha", _pid(p))
    semanas = max(2, min(semanas, 53))
    t = agenda.today()
    start = t - timedelta(days=t.weekday()) - timedelta(days=7) + timedelta(weeks=semana)  # começa na semana passada
    ctx.update(tl=agenda.timeline(session, start, semanas, ctx["pid"]), semana=semana, semanas=semanas)
    return templates.TemplateResponse(request, "agenda/page.html", ctx)


@router.get("/calendario")
def calendar_page(request: Request, p: str | None = None, modo: str = "mes", ano: int | None = None, mes: int | None = None,
                  session: Session = Depends(get_session)):
    ctx = _ctx(request, session, "calendario", _pid(p))
    t = agenda.today()
    ano = ano if ano and 1990 <= ano <= 2100 else t.year
    mes = mes if mes and 1 <= mes <= 12 else (t.month if ano == t.year else 1)
    ctx["modo"] = "ano" if modo == "ano" else "mes"
    if ctx["modo"] == "ano":
        ctx["yr"] = agenda.year_grid(session, ano, ctx["pid"])
    else:
        ctx["mg"] = agenda.month_grid(session, ano, mes, ctx["pid"])
    ctx.update(ano=ano, mes=mes)
    return templates.TemplateResponse(request, "agenda/page.html", ctx)


@router.get("/geral")
def overview_page(request: Request, session: Session = Depends(get_session)):
    ctx = _ctx(request, session, "geral", None)
    ctx["cards"] = agenda.overview(session)
    return templates.TemplateResponse(request, "agenda/page.html", ctx)


@router.get("/itens/{item_id}")
def item_page(request: Request, item_id: int, session: Session = Depends(get_session)):
    item = session.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item não encontrado.")
    ctx = _ctx(request, session, "item", None)
    ctx["item"] = item
    ctx["rec"] = session.get(PlanRecurrence, item.recurrence_id) if item.recurrence_id else None
    return templates.TemplateResponse(request, "agenda/item.html", ctx)


def _form(form) -> dict:
    return {k: form.get(k) for k in ("title", "kind", "start", "due", "project_id", "notes", "link", "status")}


@router.post("/itens")
async def item_new(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    try:
        agenda.save_item(session, PlanItem(title="", due=date.today()), _form(form))
        session.commit()
        request.session["flash_info"] = "Acrescentado à agenda."
    except agenda.AgendaError as exc:
        session.rollback()
        request.session["flash_error"] = str(exc)
    return _back(request)


@router.post("/itens/{item_id}")
async def item_save(request: Request, item_id: int, session: Session = Depends(get_session)):
    item = session.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    try:
        agenda.save_item(session, item, _form(form))
        session.commit()
        request.session["flash_info"] = "Gravado."
    except agenda.AgendaError as exc:
        session.rollback()
        request.session["flash_error"] = str(exc)
        return RedirectResponse(f"/agenda/itens/{item.id}", status_code=303)
    back = form.get("back") or "/agenda"
    return RedirectResponse(back if back.startswith("/agenda") else "/agenda", status_code=303)


@router.post("/itens/{item_id}/estado")
async def item_status(request: Request, item_id: int, session: Session = Depends(get_session)):
    item = session.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    status = form.get("status")
    if status in PLAN_STATUS:
        agenda.set_status(item, status)
        session.add(item)
        session.commit()
    return _back(request)


@router.post("/itens/{item_id}/mover")
async def item_move(request: Request, item_id: int, session: Session = Depends(get_session)):
    """Arrastar no quadro (pedido do JS): status e, opcionalmente, o item antes do qual fica."""
    item = session.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    before = form.get("before")
    try:
        agenda.move(session, item, form.get("status") or "", int(before) if before and before.isdigit() else None)
        session.commit()
    except agenda.AgendaError as exc:
        session.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True})


@router.post("/itens/{item_id}/apagar")
def item_delete(request: Request, item_id: int, session: Session = Depends(get_session)):
    item = session.get(PlanItem, item_id)
    if item is not None:
        session.delete(item)
        session.commit()
        request.session["flash_info"] = f"Apagado: {item.title}."
    return RedirectResponse("/agenda", status_code=303) if "/itens/" in (request.headers.get("referer") or "") else _back(request)


@router.post("/recorrencias")
async def recurrence_new(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    try:
        rec, n = agenda.create_recurrence(session, form)
        session.commit()
        request.session["flash_info"] = f"{n} publicações acrescentadas ({agenda.weekdays_label(rec.weekdays)})."
    except agenda.AgendaError as exc:
        session.rollback()
        request.session["flash_error"] = str(exc)
    return _back(request)


@router.post("/recorrencias/{rec_id}/apagar")
def recurrence_delete(request: Request, rec_id: int, session: Session = Depends(get_session)):
    rec = session.get(PlanRecurrence, rec_id)
    if rec is not None:
        n = agenda.delete_recurrence(session, rec)
        session.commit()
        request.session["flash_info"] = f"Recorrência apagada ({n} publicações por fazer saíram da agenda; as feitas ficam)."
    return _back(request)
