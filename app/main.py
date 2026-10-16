import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app import jobs, mcp_server
from app.routes import agenda as agenda_routes, assets, assistant, auth, config_page, files, gpt, media, montage, projects, room, story, storyboard, video, writer
from app.security import LoginRequired, PasswordChangeRequired
from app.templating import is_htmx, templates

# os registos da app (estudio.*) aparecem no docker logs, como os do uvicorn
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
logging.getLogger("estudio").addHandler(_handler)
logging.getLogger("estudio").setLevel(logging.INFO)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _slow_pages(request, call_next):
    """Regista as páginas que demoram mais de 1 s (para dar logo por uma página que ficou lenta)."""
    import time as _time

    t0 = _time.perf_counter()
    response = await call_next(request)
    ms = (_time.perf_counter() - t0) * 1000
    path = request.url.path
    if ms > 1000 and request.method == "GET" and not path.startswith(("/media/", "/pub/", "/static/", "/mcp", "/interno/maquete")) \
            and not path.endswith(("/custo", ".zip", "/pdf")):
        logging.getLogger("estudio.lento").warning("página lenta: %s %.0f ms", path, ms)
    return response

_PLACE = __import__("re").compile(r"^/projetos/(\d+)/(etapa|historia|assets|storyboard|video|montagem|sala)(/|$)")
_NOT_PLACE = ("/assistente", "/trabalhos", "/escritor", "/custo", "/pdf", "/descarregar", "/rever", ".zip")


@app.middleware("http")
async def _remember_place(request, call_next):
    """Lembra o projeto e a última página de cada projeto (o cartão do projeto, o seletor do topo e as páginas globais —
    Agenda, Configuração — voltam lá)."""
    response = await call_next(request)
    path = request.url.path
    m = _PLACE.match(path)
    if (m and request.method == "GET" and response.status_code == 200 and not path.endswith(_NOT_PLACE)
            and (not request.headers.get("hx-request") or request.headers.get("hx-boosted"))):
        url = path + (f"?{request.url.query}" if request.url.query else "")
        places = dict(request.session.get("places") or {})
        if m.group(2) != "sala":  # a última página fora da sala (o «Voltar» da sala vai lá)
            before = dict(request.session.get("places_before_room") or {})
            before.pop(m.group(1), None)
            before[m.group(1)] = url[:200]
            request.session["places_before_room"] = dict(list(before.items())[-5:])
        places.pop(m.group(1), None)
        places[m.group(1)] = url[:200]
        request.session["places"] = dict(list(places.items())[-5:])  # os últimos 5 projetos
        request.session["last_project"] = int(m.group(1))
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="director_session",
    max_age=settings.session_max_age_s,
    same_site="lax",
    https_only=settings.cookie_secure,
)

from app.i18n import TranslateMiddleware  # noqa: E402 — com a interface em inglês, traduz as páginas à saída

app.add_middleware(TranslateMiddleware)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(auth.router)
from app import live  # noqa: E402 — páginas ao vivo (contador de mudanças por projeto)

app.include_router(live.router)
app.include_router(projects.router)
app.include_router(story.router)
app.include_router(assets.router)
app.include_router(files.router)
app.include_router(storyboard.router)
app.include_router(video.router)
app.include_router(montage.router)
app.include_router(assistant.router)
app.include_router(gpt.router)
app.include_router(writer.router)
app.include_router(room.router)
app.include_router(agenda_routes.router)


@app.on_event("startup")
async def _start_jobs() -> None:
    from app import prompt_writer

    prompt_writer.recover()  # o que o escritor de prompts tinha a meio fica como falhado (para repetir)
    jobs.start()
app.include_router(config_page.router)
app.include_router(media.router)
app.include_router(mcp_server.router)
from app import maquete_render  # noqa: E402

app.include_router(maquete_render.router)


@app.on_event("startup")
async def _start_mcp() -> None:
    """O gestor de sessões do MCP corre em fundo enquanto a app estiver de pé."""
    import asyncio

    async def run() -> None:
        async with mcp_server.server.session_manager.run():
            await asyncio.Event().wait()

    app.state.mcp_task = asyncio.create_task(run())


def _redirect(request: Request, url: str) -> Response:
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(LoginRequired)
def _login_required(request: Request, _exc: LoginRequired):
    target = request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return _redirect(request, f"/login?next={quote(target)}")


@app.exception_handler(PasswordChangeRequired)
def _password_change_required(request: Request, _exc: PasswordChangeRequired):
    return _redirect(request, "/alterar-password")


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    messages = {403: "Pedido recusado.", 404: "Página não encontrada."}
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Forbidden") else None
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status": exc.status_code, "message": detail or messages.get(exc.status_code, "Ocorreu um erro.")},
        status_code=exc.status_code,
    )


@app.get("/pub/ref/{token}.png", include_in_schema=False)
@app.get("/pub/ref/{token}.mp4", include_in_schema=False)
@app.get("/pub/ref/{token}.webm", include_in_schema=False)
@app.get("/pub/ref/{token}.mp3", include_in_schema=False)
@app.get("/pub/ref/{token}.wav", include_in_schema=False)
@app.get("/pub/ref/{token}.m4a", include_in_schema=False)
@app.get("/pub/ref/{token}.ogg", include_in_schema=False)
def shared_reference(token: str):
    """Referência enviada ao Artlist: link temporário (15 min) e aleatório, sem login."""
    from fastapi.responses import FileResponse

    from app.artlist import shared_file

    path = shared_file(token)
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "no-store"})


# App instalável (PWA): manifest e service worker na raiz, públicos. O service worker não guarda nada em cache
# (a app precisa de login e dos dados do servidor); existe só para o browser oferecer a instalação.
SW_JS = """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
"""


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    from fastapi.responses import FileResponse

    return FileResponse(Path(__file__).parent / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return Response(SW_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/i18n/en.json", include_in_schema=False)
def i18n_dict() -> Response:
    """O dicionário da interface em inglês, para o navegador (guardado por versão)."""
    from app import i18n

    path = i18n.DIR / "en.json"
    data = path.read_bytes() if path.exists() else b"{}"
    return Response(data, media_type="application/json", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


# O conector MCP (Claude chat/Cowork): /mcp, o OAuth (/authorize, /token, /register, /revoke) e os /.well-known.
# Montado em último lugar: só recebe os endereços que nenhuma rota da app reconhece.
app.mount("/", mcp_server.mcp_app())
