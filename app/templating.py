from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.security import csrf_token

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["type_short"] = {"short": "Curta", "series": "Série", "film": "Filme"}


def _nav_projects():
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Project

    with Session(engine) as session:
        return list(session.exec(select(Project).order_by(Project.name)).all())


templates.env.globals["nav_projects"] = _nav_projects


def relative_time(dt) -> str:
    from datetime import datetime, timezone

    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 60:
        return "agora"
    if seconds < 3600:
        return f"há {int(seconds // 60)} min"
    if seconds < 86400:
        return f"há {int(seconds // 3600)} h"
    if seconds < 2 * 86400:
        return "ontem"
    months = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."]
    return f"{dt.day} {months[dt.month - 1]}"


templates.env.filters["relative"] = relative_time


def format_duration(seconds) -> str:
    """45 s · 5 min · 1:30 (min:s)."""
    if seconds is None or seconds == "":
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min" if not rest else f"{minutes}:{rest:02d}"


templates.env.filters["duration"] = format_duration


def is_htmx(request) -> bool:
    """Pedido HTMX parcial (não conta navegação com hx-boost, que quer a página inteira)."""
    return request.headers.get("hx-request") == "true" and request.headers.get("hx-boosted") != "true"


def clock_filter(seconds) -> str:
    seconds = int(round(seconds or 0))
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


templates.env.filters["clock"] = clock_filter


def _wordcount(text) -> int:
    from app.prompt_checks import word_count

    return word_count(text or "")


templates.env.filters["wordcount"] = _wordcount


def _image_providers() -> dict:
    """Fornecedores de imagem disponíveis (para os botões Gerar/Editar)."""
    from sqlmodel import Session

    from app.db import engine
    from app.routes.gpt import providers

    with Session(engine) as session:
        return providers(session)


templates.env.globals["image_providers"] = _image_providers


_GENS: dict[str, tuple[float, dict]] = {}  # uso → (quando, resultado): uma página com 50 botões calcula-o uma vez


def _image_generators(use: str = "generate") -> dict:
    import time

    hit = _GENS.get(use)
    if hit and time.monotonic() - hit[0] < 3:
        return hit[1]
    result = _image_generators_now(use)
    _GENS[use] = (time.monotonic(), result)
    return result


def _image_generators_now(use: str = "generate") -> dict:
    """Modelos para o seletor de um botão: {"options": [(valor, rótulo)], "default": valor}. use: "generate",
    "edit" ou "maquete" (os modelos que não servem para esse uso não aparecem; os limitados levam um aviso)."""
    from sqlmodel import Session

    from app.db import engine
    from app.routes.gpt import default_generator, generator_options

    from app import image_models
    from app.routes.gpt import parse_generator

    with Session(engine) as session:
        opts = generator_options(session, use)
        res = {v: ",".join(image_models.resolutions(*parse_generator(v))) for v, _l in opts}
        return {"options": opts, "default": default_generator(session, use=use), "res": res,
                "edit_options": generator_options(session, "edit"),
                "default_edit": default_generator(session, edit=True)}


templates.env.globals["image_generators"] = _image_generators


def _writer_elapsed(task) -> int:
    from app import prompt_writer

    return prompt_writer.elapsed(task)


templates.env.globals["writer_elapsed"] = _writer_elapsed


def _thumb(path: str, width: int = 640) -> str:
    """URL da miniatura WebP de uma imagem de /media (as originais ficam para abrir e descarregar)."""
    return f"/media/thumb/{width}/{path}"


templates.env.filters["thumb"] = _thumb


def _prompt_origin(project, text, scene=None):
    from app.prompt_origin import prompt_origin

    return prompt_origin(project, text, scene)


def _bible_warnings(project):
    from app.prompt_origin import bible_warnings

    return bible_warnings(project) if project is not None else {}


templates.env.globals["prompt_origin"] = _prompt_origin
templates.env.globals["bible_warnings"] = _bible_warnings


def _fmt_seconds(value):
    from app.shot_script import fmt_seconds

    return fmt_seconds(value)


templates.env.globals["fmt_seconds"] = _fmt_seconds


def _trash_count(project_id: int) -> int:
    """Quantas cenas e planos estão na reciclagem do projeto (para a ligação na barra de topo)."""
    from sqlmodel import Session, func, select

    from app.db import engine
    from app.models import TrashItem

    with Session(engine) as s:
        return s.exec(select(func.count()).select_from(TrashItem).where(TrashItem.project_id == project_id)).one()


templates.env.globals["trash_count"] = _trash_count


def _live_version(project_id: int) -> int:
    from app import live

    return live.version(project_id)


templates.env.globals["live_version"] = _live_version


def _room_unread(project_id: int) -> int:
    """Notificações da Sala de argumentistas por ler pelo utilizador (para a ligação na barra de topo)."""
    from sqlmodel import Session

    from app import writing
    from app.db import engine

    with Session(engine) as s:
        return writing.unread_count(s, writing.USER, project_id)


templates.env.globals["room_unread"] = _room_unread


def _agenda_today() -> dict:
    """O contador da Agenda na barra de topo: o que acaba hoje (publicações, entregas, fim de um bloco) e o que está
    atrasado, por fazer. Vermelho se houver atrasos; o título diz o que conta."""
    from sqlmodel import Session

    from app import agenda
    from app.db import engine

    with Session(engine) as s:
        v = agenda.today_view(s)
        due = [i for i in v["now"] if i.due == v["today"]]
        late = v["overdue"]
        lines = [f"Atrasado: {i.title}" for i in late[:5]] + [f"Hoje: {i.title}" for i in due[:5]]
        more = len(late) + len(due) - len(lines)
        tip = "\n".join(lines + ([f"… e mais {more}"] if more > 0 else []))
        return {"n": len(late) + len(due), "late": bool(late),
                "tip": (tip + "\n\nAbre a Agenda › Hoje para marcar como feito.") if lines else ""}


templates.env.globals["agenda_today"] = _agenda_today
