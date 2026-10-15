"""Agenda: o trabalho de todos os projetos num só sítio. Cada item é um bloco de tempo (ex.: «E04» de 1 a 15/11) ou um
dia (ex.: «Postar Short 12» a 4/11), com estado (por fazer, em curso, feito). Sem subtarefas: o que se faz dentro de um
episódio fica na app. Vistas: Hoje, Quadro, Linha do tempo e Visão geral. As publicações recorrentes criam itens de um
dia, numerados."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.models import PLAN_KINDS, PLAN_STATUS, PlanItem, PlanRecurrence, Project

TZ = ZoneInfo("Europe/Lisbon")
WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MAX_RECURRING = 200


class AgendaError(Exception):
    pass


def today() -> date:
    return datetime.now(TZ).date()


def fmt(d: date | None, with_weekday: bool = False) -> str:
    if d is None:
        return ""
    s = f"{d.day} {MONTHS[d.month - 1]}"
    if d.year != today().year:
        s += f" {d.year}"
    return f"{WEEKDAYS[d.weekday()]} {s}" if with_weekday else s


def first(item: PlanItem) -> date:
    return item.start or item.due


@dataclass
class Filter:
    """O filtro das vistas: projeto, tipo e texto (no título e nas notas)."""
    project_id: int | None = None
    kind: str | None = None
    q: str | None = None

    def __bool__(self) -> bool:
        return bool(self.project_id or self.kind or self.q)


def items(session: Session, project_id: "int | Filter | None" = None) -> list[PlanItem]:
    f = project_id if isinstance(project_id, Filter) else Filter(project_id)
    q = select(PlanItem)
    if f.project_id:
        q = q.where(PlanItem.project_id == f.project_id)
    if f.kind in PLAN_KINDS:
        q = q.where(PlanItem.kind == f.kind)
    rows = list(session.exec(q.order_by(PlanItem.due, PlanItem.position, PlanItem.id)).all())
    if f.q and f.q.strip():
        needle = f.q.strip().lower()
        rows = [i for i in rows if needle in f"{i.title} {i.notes or ''}".lower()]
    return rows


def bulk(session: Session, ids: list[int], action: str, days: int = 0) -> int:
    """Ações em massa: done | doing | todo | shift (adiar/antecipar N dias) | delete. Devolve quantos mudaram."""
    n = 0
    for i in session.exec(select(PlanItem).where(PlanItem.id.in_(ids))).all() if ids else []:
        if action in PLAN_STATUS:
            set_status(i, action)
        elif action == "shift" and days:
            i.due += timedelta(days=days)
            if i.start:
                i.start += timedelta(days=days)
        elif action == "delete":
            session.delete(i)
            n += 1
            continue
        else:
            continue
        session.add(i)
        n += 1
    return n


def update_recurrence(session: Session, rec: PlanRecurrence, form) -> int:
    """Muda o título, os dias e o fim de uma recorrência: as publicações de hoje em diante por fazer são refeitas (as
    feitas e as atrasadas ficam). Devolve quantas ficaram de hoje em diante."""
    title = (form.get("title") or rec.title).strip()
    if "{n}" not in title:
        title += " {n}"
    weekdays = sorted({int(x) for x in form.getlist("weekdays") if str(x).isdigit() and 0 <= int(x) <= 6}) or rec.weekdays
    try:
        end = date.fromisoformat(form.get("end")) if form.get("end") else rec.end
    except ValueError:
        raise AgendaError("Data de fim inválida.")
    if end < rec.start:
        raise AgendaError("O fim tem de ser depois do início.")
    dates = recurring_dates(rec.start, end, weekdays)
    if len(dates) > MAX_RECURRING:
        raise AgendaError(f"Isso dá mais de {MAX_RECURRING} publicações: encurta o período.")
    t = today()
    kept = {}
    for i in session.exec(select(PlanItem).where(PlanItem.recurrence_id == rec.id)).all():
        if i.status == "done" or i.due < t:
            kept[i.due] = i
        else:
            session.delete(i)
    session.flush()
    n = 0
    for k, d in enumerate(dates):
        if d < t or d in kept:
            continue
        session.add(PlanItem(project_id=rec.project_id, title=title.replace("{n}", str(rec.first_number + k)), kind=rec.kind,
                             due=d, recurrence_id=rec.id, position=k))
        n += 1
    rec.title, rec.weekdays, rec.end = title[:200], weekdays, end
    session.add(rec)
    return n


def info(item: PlanItem, t: date | None = None) -> dict:
    """O que a página precisa de saber sobre um item hoje: atrasado, a decorrer, dias que faltam, progresso do bloco."""
    t = t or today()
    open_ = item.status != "done"
    start = first(item)
    span = (item.due - start).days + 1
    left = (item.due - t).days
    progress = None
    if span > 1 and start <= t <= item.due:
        progress = round(100 * ((t - start).days + 1) / span)
    return {"overdue": open_ and item.due < t, "running": open_ and start <= t <= item.due, "left": left,
            "span": span, "progress": progress, "single": span == 1,
            "when": fmt(item.due, True) if span == 1 else f"{fmt(start)} → {fmt(item.due)}"}


def today_view(session: Session, project_id: int | None = None) -> dict:
    t = today()
    rows = items(session, project_id)
    open_rows = [i for i in rows if i.status != "done"]
    overdue = [i for i in open_rows if i.due < t]
    now = [i for i in open_rows if first(i) <= t <= i.due]
    soon = sorted((i for i in open_rows if t < first(i) <= t + timedelta(days=7)), key=lambda i: (first(i), i.due))
    done_today = [i for i in rows if i.status == "done" and i.done_at and i.done_at.astimezone(TZ).date() == t]
    # hoje: primeiro o que acaba hoje (publicações, prazos), depois os blocos a decorrer pelo prazo
    now.sort(key=lambda i: (i.due != t, i.due, i.position))
    return {"today": t, "overdue": overdue, "now": now, "soon": soon, "done_today": done_today}


def board(session: Session, project_id: int | None = None) -> dict[str, list[PlanItem]]:
    t = today()
    cols = {k: [] for k in PLAN_STATUS}
    for i in items(session, project_id):
        if i.status == "done" and (i.done_at is None or (t - i.done_at.astimezone(TZ).date()).days > 30):
            continue  # no quadro, só o que acabou nos últimos 30 dias
        cols[i.status].append(i)
    for k in ("todo", "doing"):
        cols[k].sort(key=lambda i: (i.position, i.due, i.id))
    cols["done"].sort(key=lambda i: i.done_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return cols


def move(session: Session, item: PlanItem, status: str, before_id: int | None = None) -> None:
    """Põe o item numa coluna do quadro (e antes de outro, se indicado); renumera a coluna."""
    if status not in PLAN_STATUS:
        raise AgendaError("Estado inválido.")
    set_status(item, status)
    col = [i for i in session.exec(select(PlanItem).where(PlanItem.status == status, PlanItem.id != item.id))
           .all()]
    col.sort(key=lambda i: (i.position, i.due, i.id))
    idx = next((k for k, i in enumerate(col) if i.id == before_id), len(col))
    col.insert(idx, item)
    for k, i in enumerate(col):
        if i.position != k:
            i.position = k
            session.add(i)
    session.add(item)


def set_status(item: PlanItem, status: str) -> None:
    if status == item.status:
        return
    item.status = status
    item.done_at = datetime.now(timezone.utc) if status == "done" else None


def timeline(session: Session, start: date, weeks: int, project_id: int | None = None) -> dict:
    """A linha do tempo de [start, start + weeks): dias, cabeçalhos de semana e de mês, e uma faixa por projeto, com
    os itens em linhas sem sobreposição (barra = bloco; ponto = um dia)."""
    days = [start + timedelta(days=k) for k in range(weeks * 7)]
    end = days[-1]
    t = today()
    projects = {p.id: p for p in session.exec(select(Project)).all()}
    lanes: dict = {}
    for i in items(session, project_id):
        a, b = first(i), i.due
        if b < start or a > end:
            continue
        lane = lanes.setdefault(i.project_id, {"project": projects.get(i.project_id), "rows": []})
        col_a, col_b = max((a - start).days, 0) + 1, min((b - start).days, len(days) - 1) + 2
        placed = False
        for row in lane["rows"]:  # a primeira linha onde cabe
            if all(col_a >= x["col_b"] or col_b <= x["col_a"] for x in row):
                row.append({"item": i, "col_a": col_a, "col_b": col_b, **info(i, t), "cut_l": a < start, "cut_r": b > end})
                placed = True
                break
        if not placed:
            lane["rows"].append([{"item": i, "col_a": col_a, "col_b": col_b, **info(i, t), "cut_l": a < start, "cut_r": b > end}])
    ordered = sorted(lanes.values(), key=lambda ln: (ln["project"] is None, (ln["project"].name.lower() if ln["project"] else "")))
    months, cur = [], None
    for k, d in enumerate(days):
        if d.month != cur:
            months.append({"label": f"{MONTHS[d.month - 1]} {d.year}", "col": k + 1, "span": 0})
            cur = d.month
        months[-1]["span"] += 1
    return {"days": [{"d": d, "col": k + 1, "today": d == t, "weekend": d.weekday() >= 5, "monday": d.weekday() == 0}
                     for k, d in enumerate(days)],
            "months": months, "lanes": ordered, "start": start, "end": end, "n": len(days),
            "today_col": (t - start).days + 1 if start <= t <= end else None}


def overview(session: Session) -> list[dict]:
    """Um cartão por projeto com itens: feitos/total, atrasados, o que está a decorrer e o próximo prazo."""
    t = today()
    out: dict = {}
    projects = {p.id: p for p in session.exec(select(Project)).all()}
    for i in items(session):
        c = out.setdefault(i.project_id, {"project": projects.get(i.project_id), "total": 0, "done": 0, "overdue": [],
                                          "running": [], "next": None, "kinds": {}})
        c["total"] += 1
        k = c["kinds"].setdefault(i.kind, [0, 0])
        k[1] += 1
        if i.status == "done":
            c["done"] += 1
            k[0] += 1
            continue
        if i.due < t:
            c["overdue"].append(i)
        elif first(i) <= t:
            c["running"].append(i)
        elif c["next"] is None or i.due < c["next"].due:
            c["next"] = i
    for c in out.values():
        c["pct"] = round(100 * c["done"] / c["total"]) if c["total"] else 0
        c["kinds"] = [(PLAN_KINDS.get(k, k), v[0], v[1]) for k, v in c["kinds"].items()]
    return sorted(out.values(), key=lambda c: (c["project"] is None, -len(c["overdue"]),
                                                (c["project"].name.lower() if c["project"] else "")))


def save_item(session: Session, item: PlanItem, form: dict) -> PlanItem:
    title = (form.get("title") or "").strip()
    if not title:
        raise AgendaError("Falta o título.")
    try:
        due = date.fromisoformat(form.get("due") or "")
    except ValueError:
        raise AgendaError("Falta o prazo (o dia em que tem de estar feito).")
    start = None
    if form.get("start"):
        try:
            start = date.fromisoformat(form["start"])
        except ValueError:
            raise AgendaError("Data de início inválida.")
        if start > due:
            raise AgendaError("O início tem de ser antes do prazo.")
        if start == due:
            start = None
    kind = form.get("kind") if form.get("kind") in PLAN_KINDS else "other"
    pid = form.get("project_id")
    item.project_id = int(pid) if pid and str(pid).isdigit() and session.get(Project, int(pid)) else None
    item.title, item.kind, item.start, item.due = title[:200], kind, start, due
    item.notes = (form.get("notes") or "").strip() or None
    link = (form.get("link") or "").strip()
    if link.startswith(("http://", "https://")):  # colaram o endereço inteiro: fica só o caminho na app
        from urllib.parse import urlsplit

        u = urlsplit(link)
        link = u.path + (f"?{u.query}" if u.query else "") + (f"#{u.fragment}" if u.fragment else "")
    if link and not link.startswith("/"):
        raise AgendaError("A ligação tem de ser uma página desta app (ex.: /projetos/2/sala?f=5 ou o endereço completo).")
    item.link = link if link and not link.startswith("//") else None
    if form.get("status") in PLAN_STATUS:
        set_status(item, form["status"])
    session.add(item)
    return item


def recurring_dates(start: date, end: date, weekdays: list[int]) -> list[date]:
    out, d = [], start
    while d <= end and len(out) <= MAX_RECURRING:
        if d.weekday() in weekdays:
            out.append(d)
        d += timedelta(days=1)
    return out


def create_recurrence(session: Session, form) -> tuple[PlanRecurrence, int]:
    title = (form.get("title") or "").strip()
    if not title:
        raise AgendaError("Falta o título (ex.: «Postar Short {n}»).")
    try:
        start, end = date.fromisoformat(form.get("start") or ""), date.fromisoformat(form.get("end") or "")
    except ValueError:
        raise AgendaError("Faltam as datas de início e de fim.")
    if end < start:
        raise AgendaError("O fim tem de ser depois do início.")
    weekdays = sorted({int(x) for x in form.getlist("weekdays") if str(x).isdigit() and 0 <= int(x) <= 6})
    if not weekdays:
        raise AgendaError("Escolhe pelo menos um dia da semana.")
    dates = recurring_dates(start, end, weekdays)
    if len(dates) > MAX_RECURRING:
        raise AgendaError(f"Isso dá mais de {MAX_RECURRING} publicações: encurta o período.")
    if not dates:
        raise AgendaError("Nenhum desses dias cai no período escolhido.")
    try:
        n0 = int(form.get("first_number") or 1)
    except ValueError:
        n0 = 1
    pid = form.get("project_id")
    project_id = int(pid) if pid and str(pid).isdigit() and session.get(Project, int(pid)) else None
    kind = form.get("kind") if form.get("kind") in PLAN_KINDS else "post"
    if "{n}" not in title:
        title += " {n}"
    rec = PlanRecurrence(project_id=project_id, title=title[:200], kind=kind, weekdays=weekdays, start=start, end=end,
                         first_number=n0)
    session.add(rec)
    session.flush()
    for k, d in enumerate(dates):
        session.add(PlanItem(project_id=project_id, title=title.replace("{n}", str(n0 + k)), kind=kind, due=d,
                             recurrence_id=rec.id, position=k))
    return rec, len(dates)


def delete_recurrence(session: Session, rec: PlanRecurrence) -> int:
    """Apaga a recorrência e as publicações dela que ainda não foram feitas (as feitas ficam no histórico)."""
    n = 0
    for i in session.exec(select(PlanItem).where(PlanItem.recurrence_id == rec.id)).all():
        if i.status != "done":
            session.delete(i)
            n += 1
        else:
            i.recurrence_id = None
            session.add(i)
    session.delete(rec)
    return n


def recurrences(session: Session) -> list[PlanRecurrence]:
    return list(session.exec(select(PlanRecurrence).order_by(PlanRecurrence.start)).all())


def weekdays_label(days: list[int]) -> str:
    return ", ".join(WEEKDAYS[d] for d in days)


# ── Calendário (mês e ano) ───────────────────────────────────

MONTHS_LONG = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
               "novembro", "dezembro"]


def _month_weeks(year: int, month: int) -> list[list[date]]:
    """As semanas (segunda a domingo) que mostram o mês inteiro."""
    first_day = date(year, month, 1)
    d = first_day - timedelta(days=first_day.weekday())
    weeks = []
    while True:
        weeks.append([d + timedelta(days=k) for k in range(7)])
        d += timedelta(days=7)
        if d.month != month and (d.year, d.month) > (year, month):
            break
    return weeks


def month_grid(session: Session, year: int, month: int, project_id: int | None = None) -> dict:
    """O mês como no calendário: semanas; em cada semana, os itens em faixas sem sobreposição (um bloco atravessa os
    dias dele; continua na semana seguinte com a ponta cortada)."""
    t = today()
    weeks = _month_weeks(year, month)
    rows = items(session, project_id)
    out = []
    for wk in weeks:
        a, b = wk[0], wk[-1]
        segs = []
        for i in rows:
            s, e = first(i), i.due
            if e < a or s > b:
                continue
            ca, cb = max((s - a).days, 0) + 1, min((e - a).days, 6) + 2
            segs.append({"item": i, "col_a": ca, "col_b": cb, "cont_l": s < a, "cont_r": e > b, **info(i, t)})
        segs.sort(key=lambda x: (x["col_a"], -(x["col_b"] - x["col_a"]), x["item"].id))
        lanes: list[list[dict]] = []
        for sg in segs:
            for lane in lanes:
                if all(sg["col_a"] >= o["col_b"] or sg["col_b"] <= o["col_a"] for o in lane):
                    lane.append(sg)
                    break
            else:
                lanes.append([sg])
        for k, lane in enumerate(lanes):
            for sg in lane:
                sg["row"] = k + 2
        out.append({"days": [{"d": d, "in": d.month == month, "today": d == t, "weekend": d.weekday() >= 5, "col": k + 1}
                             for k, d in enumerate(wk)],
                    "segs": [sg for lane in lanes for sg in lane], "lanes": len(lanes)})
    prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    return {"year": year, "month": month, "label": f"{MONTHS_LONG[month - 1]} {year}", "weeks": out,
            "prev": prev_m, "next": next_m}


def year_grid(session: Session, year: int, project_id: int | None = None) -> dict:
    """O ano em 12 meses pequenos: em cada dia, as cores (tipos) do que há nesse dia e quantos itens."""
    t = today()
    by_day: dict[date, list[PlanItem]] = {}
    a, b = date(year, 1, 1), date(year, 12, 31)
    for i in items(session, project_id):
        s, e = max(first(i), a), min(i.due, b)
        d = s
        while d <= e:
            by_day.setdefault(d, []).append(i)
            d += timedelta(days=1)
    months = []
    for m in range(1, 13):
        weeks = []
        for wk in _month_weeks(year, m):
            row = []
            for d in wk:
                if d.month != m:
                    row.append(None)
                    continue
                its = by_day.get(d, [])
                kinds = list(dict.fromkeys(i.kind for i in its if i.status != "done"))[:3]
                due_here = [i for i in its if i.due == d and i.status != "done"]
                row.append({"d": d, "today": d == t, "weekend": d.weekday() >= 5, "kinds": kinds, "n": len(its),
                            "late": any(i.due < t and i.status != "done" for i in due_here),
                            "titles": "; ".join(i.title for i in its[:6]) + ("…" if len(its) > 6 else "")})
            weeks.append(row)
        months.append({"m": m, "label": MONTHS_LONG[m - 1], "weeks": weeks, "current": (year, m) == (t.year, t.month)})
    return {"year": year, "months": months}
