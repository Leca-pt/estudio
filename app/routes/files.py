"""Ficheiros de resultado: upload, aprovar, rejeitar, descarregar e zip."""

import io
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlmodel import Session, func, select

from app.db import get_session
from app.media import (
    MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, UploadError, image_extension, media_root, mp4_duration_ms,
    read_limited, safe_segment, video_extension, write_file,
)
from app.media_service import approve_media, back_url, download_name, reject_media
from app.models import MediaFile, Project
from app.security import csrf_protect, require_login
from app.stages import get_project

router = APIRouter(prefix="/projetos/{project_id}", dependencies=[Depends(require_login), Depends(csrf_protect)])


def flash(request: Request, message: str) -> None:
    request.session["flash_error"] = message


def flash_info(request: Request, message: str) -> None:
    request.session["flash_info"] = message


def store_bytes(session: Session, project: Project, folder: str, kind: str, role: str, data: bytes, ext: str,
                status: str = "pending", duration_ms: int | None = None, **target) -> MediaFile:
    """Grava um ficheiro já validado na pasta do projeto (vN.ext, a seguir aos que já existem) e cria o MediaFile."""
    base = PurePosixPath(safe_segment(project.prefix), *[safe_segment(p) for p in folder.split("/")])
    stmt = select(func.count()).select_from(MediaFile).where(MediaFile.role == role)
    for field, value in target.items():
        stmt = stmt.where(getattr(MediaFile, field) == value)
    n = session.exec(stmt).one() + 1
    rel = base / f"v{n}.{ext}"
    while (media_root() / rel).exists():
        n += 1
        rel = base / f"v{n}.{ext}"
    write_file(str(rel), data)
    media = MediaFile(project_id=project.id, path=str(rel), kind=kind, role=role, status=status, duration_ms=duration_ms,
                      **target)
    session.add(media)
    session.flush()
    return media


async def save_uploads(
    request: Request, session: Session, project: Project, folder: str, kind: str, role: str, status: str = "pending",
    **target
) -> int:
    """Grava os ficheiros enviados em `files` (por omissão como resultados por decidir). Devolve quantos gravou."""
    form = await request.form()
    files = [f for f in form.getlist("files") if hasattr(f, "read") and f.filename]
    if not files:
        flash(request, "Escolhe pelo menos um ficheiro.")
        return 0
    errors, saved = [], 0
    for upload in files:
        try:
            if kind == "image":
                data = await read_limited(upload, MAX_IMAGE_BYTES)
                ext, duration = image_extension(data, upload.filename), None
            elif kind == "audio":
                from app.media import MAX_AUDIO_BYTES, audio_extension, normalize_audio

                data = await read_limited(upload, MAX_AUDIO_BYTES)
                audio_extension(data, upload.filename)
                data, ext, duration = normalize_audio(data)
            else:
                data = await read_limited(upload, MAX_VIDEO_BYTES)
                ext, duration = video_extension(data, upload.filename), mp4_duration_ms(data)
        except UploadError as exc:
            errors.append(str(exc))
            continue
        store_bytes(session, project, folder, kind, role, data, ext, status=status, duration_ms=duration, **target)
        saved += 1
    session.commit()
    if errors:
        flash(request, " ".join(errors))
    return saved


def get_media(session: Session, project: Project, media_id: int) -> MediaFile:
    media = session.get(MediaFile, media_id)
    if media is None or media.project_id != project.id:
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")
    return media


def media_available(media: MediaFile | None) -> bool:
    return media is not None and media.status != "rejected" and (media_root() / media.path).is_file()


def kept_take(media: MediaFile | None) -> bool:
    """Um rejeitado que ainda tem o ficheiro (takes, imagens, vozes e fotos rejeitados ficam guardados)."""
    return media is not None and (media_root() / media.path).is_file()


def _return_to(request: Request, project: Project, media: MediaFile) -> str:
    """Volta à página onde se carregou no botão (ex.: aprovar a imagem de uma vista no plano não leva à página do
    local); sem ela, à página do ficheiro."""
    from urllib.parse import urlsplit

    ref = urlsplit(request.headers.get("referer") or "")
    if ref.path.startswith(f"/projetos/{project.id}/"):  # só o caminho: o redirecionamento fica sempre nesta app
        return ref.path + (f"?{ref.query}" if ref.query and "guardado" not in ref.query else "")
    return back_url(project, media)


@router.post("/media/{media_id}/aprovar")
def media_approve(request: Request, project_id: int, media_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    media = get_media(session, project, media_id)
    if media.status == "rejected" and not kept_take(media):
        raise HTTPException(status_code=400, detail="Um ficheiro rejeitado não pode ser aprovado.")
    approve_media(session, media)
    session.commit()
    return RedirectResponse(_return_to(request, project, media), status_code=303)


@router.post("/media/{media_id}/rejeitar")
def media_reject(request: Request, project_id: int, media_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    media = get_media(session, project, media_id)
    reject_media(session, media)
    session.commit()
    return RedirectResponse(_return_to(request, project, media), status_code=303)


@router.post("/media/{media_id}/repor")
def media_restore(request: Request, project_id: int, media_id: int, session: Session = Depends(get_session)):
    """Um take rejeitado volta a «por decidir»."""
    from app.media_service import restore_media

    project = get_project(session, project_id)
    media = get_media(session, project, media_id)
    if media.status != "rejected" or not kept_take(media):
        raise HTTPException(status_code=400, detail="Só se repõem takes rejeitados que ainda têm o ficheiro.")
    restore_media(session, media)
    session.commit()
    return RedirectResponse(_return_to(request, project, media), status_code=303)


@router.get("/media/{media_id}/descarregar")
def media_download(project_id: int, media_id: int, session: Session = Depends(get_session)):
    project = get_project(session, project_id)
    media = get_media(session, project, media_id)
    if not (media_available(media) or kept_take(media)):
        raise HTTPException(status_code=404, detail="Ficheiro não encontrado.")
    return FileResponse(media_root() / media.path, filename=download_name(session, project, media))


def zip_response(filename: str, entries: list[tuple[str, MediaFile]]) -> StreamingResponse:
    """Zip em memória com (nome no zip, ficheiro). Vídeos já vêm comprimidos: ZIP_STORED."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, media in entries:
            zf.write(media_root() / media.path, arcname=name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
