"""Imagens fixas das maquetes 3D com o visualizador three.js da app, num Chromium sem ecrã dentro do contentor.

O servidor guarda o pedido (a cena e as vistas) com uma chave de uso único, o Chromium abre /interno/maquete/<chave>
em 127.0.0.1 (a página carrega static/js/maquete_still.js, que usa o mesmo buildLayer/actorMesh do visualizador) e
devolve as imagens. Usado pela ferramenta view_layout do conector MCP.
"""

import asyncio
import base64
import json
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()
_jobs: dict[str, tuple[dict, float]] = {}
_lock = asyncio.Semaphore(1)  # um Chromium de cada vez (o servidor tem 2 CPUs)
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


@router.get("/interno/maquete/{token}", include_in_schema=False)
def still_page(request: Request, token: str):
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=404)
    item = _jobs.pop(token, None)
    if item is None or item[1] < time.time():
        raise HTTPException(status_code=404)
    payload = json.dumps(item[0]).replace("</", "<\\/")
    if item[0].get("viewer"):  # o próprio visualizador das páginas (mount), para os renders e a gravação do conector
        return HTMLResponse(VIEWER_PAGE.replace("__ASPECT__", item[0]["aspect"]).replace("__SPEC__", payload))
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8"><style>body{{margin:0}}</style></head><body>
<script type="application/json" id="job">{payload}</script>
<script type="module">
import {{ renderStills }} from '/static/js/maquete_still.js?v=3';
const job = JSON.parse(document.getElementById('job').textContent);
try {{ window.__result = await renderStills(job.spec, job.opts); }} catch (e) {{ window.__error = String(e && e.stack || e); }}
</script></body></html>""")


async def render(spec: dict, opts: dict, timeout_s: int = 120) -> list[dict]:
    """[{view, png (bytes)} ou {view, error}] para cada vista pedida."""
    from playwright.async_api import async_playwright

    token = secrets.token_urlsafe(24)
    _jobs[token] = ({"spec": spec, "opts": opts}, time.time() + timeout_s)
    async with _lock:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=ARGS)
            try:
                page = await browser.new_page()
                await page.goto(f"http://127.0.0.1:8000/interno/maquete/{token}", timeout=timeout_s * 1000)
                await page.wait_for_function("window.__result || window.__error", timeout=timeout_s * 1000)
                error = await page.evaluate("window.__error || null")
                if error:
                    raise RuntimeError(error.splitlines()[0])
                result = await page.evaluate("window.__result")
            finally:
                await browser.close()
    out = []
    for r in result:
        if r.get("png"):
            out.append({"view": r["view"], "png": base64.b64decode(r["png"].split(",", 1)[1])})
        else:
            out.append({"view": r["view"], "error": r.get("error") or "falhou"})
    return out


# ── O visualizador das páginas (scene3d.js, mount) num Chromium sem ecrã ──────────
# Para o conector fazer o que a página do local ou do plano faz no navegador do utilizador: os renders das vistas (com a
# profundidade), os previs dos momentos e a gravação do vídeo-guia. Os mesmos quadros, tirados pelo mesmo código
# (el.m3dCapture, el.m3dFrames); o servidor guarda-os como os pedidos da página.
VIEWER_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0}.m3d-stage{width:1280px}</style></head><body>
<script type="application/json" id="job">__SPEC__</script>
<div class="m3d" data-scene3d data-aspect="__ASPECT__"><script type="application/json" class="m3d-data"></script>
<div class="m3d-stage"></div><div class="m3d-bar row"></div><div class="m3d-chips row"></div><div class="m3d-legend row"></div>
<div class="m3d-actions row"><span class="hint m3d-status"></span></div></div>
<script type="module">
const job = JSON.parse(document.getElementById('job').textContent);
const el = document.querySelector('[data-scene3d]');
el.querySelector('.m3d-data').textContent = JSON.stringify(job.spec);
try { const { mount } = await import('/static/js/scene3d.js?v=40'); mount(el); window.__ready = true; }
catch (e) { window.__error = String(e && e.stack || e); }
</script></body></html>"""


async def capture(spec: dict, aspect: str = "16:9", views: list[dict] | None = None, moments: list[float] | None = None,
                  fps: int = 0, timeout_s: int = 1200, progress=None) -> dict:
    """Com o visualizador da app: {"views": {id: (png, depth)}} para cada vista {id, cam, outdoor}, {"moments": [png]}
    para cada segundo pedido e, com fps, {"video": mp4} (a câmara do plano do início ao fim, quadro a quadro)."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from playwright.async_api import async_playwright

    def raw(url: str) -> bytes:
        return base64.b64decode(url.split(",", 1)[1])

    token = secrets.token_urlsafe(24)
    _jobs[token] = ({"viewer": True, "spec": spec, "aspect": aspect if re.fullmatch(r"\d+:\d+", aspect or "") else "16:9"},
                    time.time() + timeout_s)
    out: dict = {"views": {}, "moments": [], "video": None}
    end = time.time() + timeout_s
    async with _lock:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=ARGS)
            tmp = Path(tempfile.mkdtemp(prefix="maquete-"))
            try:
                page = await browser.new_page(viewport={"width": 1400, "height": 1000})
                await page.goto(f"http://127.0.0.1:8000/interno/maquete/{token}", timeout=60_000)
                await page.wait_for_function("window.__ready || window.__error", timeout=60_000)
                error = await page.evaluate("window.__error || null")
                if error:
                    raise RuntimeError(error.splitlines()[0])
                el = "document.querySelector('[data-scene3d]')"
                await page.wait_for_timeout(800)  # texturas e etiquetas
                for v in views or []:
                    r = await page.evaluate(f"(lv) => {el}.m3dCapture.view(lv)", v)
                    out["views"][v["id"]] = (raw(r["png"]), raw(r["depth"]))
                for t in moments or []:
                    out["moments"].append(raw(await page.evaluate(f"(t) => {el}.m3dCapture.moment(t)", t)))
                if fps:
                    if not await page.evaluate(f"!!{el}.m3dFrames"):
                        raise RuntimeError("este plano não tem câmara para gravar")
                    duration = float(await page.evaluate(f"{el}.m3dFrames.duration"))
                    await page.evaluate(f"{el}.m3dFrames.start()")
                    times = [min(duration, i / fps) for i in range(int(round(duration * fps)) + 1)]
                    for b in range(0, len(times), 12):  # 12 quadros por pedido ao navegador
                        if time.time() > end:
                            raise RuntimeError("a gravação demorou demasiado")
                        urls = await page.evaluate(f"(ts) => ts.map((t) => {el}.m3dFrames.frame(t))", times[b:b + 12])
                        for i, url in enumerate(urls, start=b):
                            (tmp / f"f{i:05d}.jpg").write_bytes(raw(url))
                        if progress:
                            progress(min(len(times), b + 12), len(times))
                    mp4 = tmp / "guia.mp4"
                    run = await asyncio.to_thread(subprocess.run, [
                        "ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", str(tmp / "f%05d.jpg"),
                        "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(mp4)],
                        capture_output=True, text=True, timeout=300)
                    if run.returncode != 0 or not mp4.is_file():
                        raise RuntimeError("o ffmpeg não juntou os quadros: " + run.stderr[-300:])
                    out["video"] = mp4.read_bytes()
            finally:
                await browser.close()
                shutil.rmtree(tmp, ignore_errors=True)
    return out
