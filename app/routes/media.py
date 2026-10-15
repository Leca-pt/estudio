from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.security import require_login

router = APIRouter(dependencies=[Depends(require_login)])


THUMB_WIDTHS = (640, 1600)


def _source(file_path: str) -> Path:
    root = Path(settings.media_dir).resolve()
    target = (root / file_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404)
    return target


@router.get("/media/capa/{file_path:path}")
def media_poster(file_path: str):
    """Capa (JPG) de um vídeo: as páginas mostram-na em vez de carregar o vídeo antes de o pôr a tocar."""
    from fastapi.concurrency import run_in_threadpool  # noqa: F401 — (a rota já corre numa thread)

    from app.media import video_thumb

    src = _source(file_path)
    root = Path(settings.media_dir).resolve()
    thumb = video_thumb(str(src.relative_to(root)))
    if thumb is None:
        raise HTTPException(status_code=404)
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


@router.get("/media/thumb/{width}/{file_path:path}")
def media_thumb(width: int, file_path: str):
    """Miniatura WebP (largura máx. 640 ou 1600) das imagens: as originais têm ~2 MB. Fica em cache fora dos backups."""
    if width not in THUMB_WIDTHS:
        raise HTTPException(status_code=404)
    source = _source(file_path)
    cache = Path(settings.media_dir).resolve().parent / "cache" / "thumbs" / str(width) / (file_path + ".webp")
    if not cache.is_file() or cache.stat().st_mtime < source.stat().st_mtime:
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(source) as im:
                im = im.convert("RGB")
                im.thumbnail((width, width * 3))
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                im.save(tmp, "WEBP", quality=82, method=4)
                tmp.replace(cache)
        except (UnidentifiedImageError, OSError):
            return FileResponse(source, headers={"Cache-Control": "private, max-age=86400"})
    return FileResponse(cache, media_type="image/webp", headers={"Cache-Control": "private, max-age=604800"})


@router.get("/media/{file_path:path}")
def media(file_path: str):
    return FileResponse(_source(file_path), headers={"Cache-Control": "private, max-age=86400"})
