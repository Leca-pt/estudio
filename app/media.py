"""Ficheiros de media: validação do tipo real, gravação no volume e remoção."""

import io
import re
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.config import settings

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
VIDEO_BRANDS_MOV = {b"qt  "}
Image.MAX_IMAGE_PIXELS = 80_000_000  # proteção contra "bombas" de descompressão


class UploadError(ValueError):
    pass


def media_root() -> Path:
    return Path(settings.media_dir)


def safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


async def read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise UploadError(f"{upload.filename}: ficheiro demasiado grande (máximo {limit // (1024 * 1024)} MB).")
    if not data:
        raise UploadError(f"{upload.filename}: ficheiro vazio.")
    return data


def image_extension(data: bytes, filename: str) -> str:
    """Valida que é mesmo uma imagem PNG, JPEG ou WEBP (pelo conteúdo, não pelo nome)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise UploadError(f"{filename}: não é uma imagem válida (aceita-se PNG, JPG ou WEBP).")
    if fmt not in IMAGE_FORMATS:
        raise UploadError(f"{filename}: formato {fmt} não aceite (aceita-se PNG, JPG ou WEBP).")
    return IMAGE_FORMATS[fmt]


def video_extension(data: bytes, filename: str) -> str:
    """Valida MP4/MOV pela caixa 'ftyp' no início do ficheiro."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise UploadError(f"{filename}: não é um vídeo válido (aceita-se MP4 ou MOV).")
    return "mov" if data[8:12] in VIDEO_BRANDS_MOV else "mp4"


MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg", "flac": "audio/flac"}


def audio_kind(data: bytes) -> str | None:
    """Extensão de um áudio pelo conteúdo (MP3, WAV, M4A, OGG, FLAC), ou None."""
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "m4a"
    return None


def audio_extension(data: bytes, filename: str) -> str:
    ext = audio_kind(data)
    if ext is None:
        raise UploadError(f"{filename}: não é um áudio válido (aceita-se MP3, WAV, M4A, OGG ou FLAC).")
    return ext


def normalize_audio(data: bytes) -> tuple[bytes, str, int | None]:
    """Áudio para MP3 (o formato que os modelos de vídeo aceitam) e a duração em ms, com o ffmpeg. MP3 e WAV ficam como
    estão (só se mede a duração). Sem ffmpeg, fica como veio."""
    import shutil
    import subprocess
    import tempfile

    ext = audio_kind(data) or "mp3"
    if not shutil.which("ffmpeg"):
        return data, ext, None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in.{ext}"
        src.write_bytes(data)
        if ext not in ("mp3", "wav"):
            dst = Path(tmp) / "out.mp3"
            run = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1",
                                  "-b:a", "128k", str(dst)], capture_output=True, timeout=120)
            if run.returncode != 0 or not dst.is_file():
                raise UploadError("Não foi possível converter o áudio para MP3.")
            data, ext, src = dst.read_bytes(), "mp3", dst
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True, timeout=60)
        try:
            duration = int(float(probe.stdout.strip()) * 1000)
        except ValueError:
            duration = None
    return data, ext, duration


def trim_audio(relative: str, start_s: float, end_s: float) -> tuple[bytes, int | None]:
    """Um trecho de um áudio (de start_s a end_s, em segundos), em MP3, com a duração em ms. Não mexe no original."""
    import shutil
    import subprocess
    import tempfile

    src = (media_root() / relative).resolve()
    if not src.is_relative_to(media_root().resolve()) or not src.is_file():
        raise UploadError("O ficheiro de áudio já não existe.")
    if not shutil.which("ffmpeg"):
        raise UploadError("O servidor não tem o ffmpeg: não é possível cortar o áudio.")
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "out.mp3"
        run = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                              "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}", "-ac", "1", "-b:a", "128k", str(dst)],
                             capture_output=True, timeout=120)
        if run.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
            raise UploadError("Não foi possível cortar o áudio.")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dst)],
                               capture_output=True, text=True, timeout=60)
        try:
            duration = int(float(probe.stdout.strip()) * 1000)
        except ValueError:
            duration = None
        return dst.read_bytes(), duration


def normalize_guide(data: bytes, ext: str, seconds: float | None) -> tuple[bytes, int | None]:
    """Vídeo-guia gravado no navegador para um MP4 limpo: H.264 a 24 imagens/s constantes, sem som, com o índice no
    início e cortado à duração do plano (o Chrome entrega MP4 fragmentado ou WebM, com ritmo variável e uns décimos a
    mais no fim; num computador lento, uns décimos a menos, que se completam com a última imagem). Devolve os bytes e a
    duração em ms do que foi gravado (antes de completar). Sem ffmpeg, fica como veio."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("ffmpeg"):
        return data, None
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / f"in.{ext}", Path(tmp) / "out.mp4"
        src.write_bytes(data)
        raw = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                             capture_output=True, text=True, timeout=60)
        cut = ["-t", f"{seconds:.3f}"] if seconds else []
        run = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), *cut, "-an",
                              "-vf", "fps=24,format=yuv420p,tpad=stop_mode=clone:stop_duration=0.6", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                              "-movflags", "+faststart", str(dst)], capture_output=True, timeout=300)
        if run.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
            raise UploadError("Não foi possível preparar o vídeo-guia (o ficheiro gravado parece danificado).")
        try:
            duration = int(round(float(raw.stdout.strip()) * 1000))
        except ValueError:
            duration = None
        return dst.read_bytes(), duration


def guide_sheet(relative: str, frames: int = 8) -> tuple[str, list[float]] | None:
    """Uma tira com imagens do vídeo-guia a intervalos iguais (da esquerda para a direita), para o escritor ver o que o
    modelo vai receber. Fica ao lado do vídeo (<nome>_tira.jpg) e só se faz uma vez. Devolve (caminho, segundos)."""
    import shutil
    import subprocess

    src = (media_root() / relative).resolve()
    if not src.is_relative_to(media_root().resolve()) or not src.is_file() or not shutil.which("ffmpeg"):
        return None
    rel = str(Path(relative).with_name(Path(relative).stem + "_tira.jpg"))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                           capture_output=True, text=True, timeout=60)
    try:
        dur = float(probe.stdout.strip())
    except ValueError:
        return None
    times = [round(dur * (i + 0.5) / frames, 1) for i in range(frames)]
    dst = media_root() / rel
    if not dst.is_file():
        cols = 4
        run = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vf",
                              f"fps={frames / dur:.4f},scale=480:-1,tile={cols}x{(frames + cols - 1) // cols}:padding=6",
                              "-frames:v", "1", "-q:v", "4", str(dst)], capture_output=True, timeout=120)
        if run.returncode != 0 or not dst.is_file():
            return None
    return rel, times


def write_file(relative: str, data: bytes) -> None:
    target = (media_root() / relative).resolve()
    if not target.is_relative_to(media_root().resolve()):
        raise UploadError("Caminho inválido.")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)


def companions(relative: str) -> list[str]:
    """Ficheiros derivados que vivem ao lado de um ficheiro e não têm registo próprio (ex.: a tira do vídeo-guia que o
    escritor de prompts vê): apagam-se e mudam-se com ele."""
    p = Path(relative)
    return [str(p.with_name(p.stem + "_tira.jpg")), str(p.with_name(p.stem + "_thumb.jpg"))]


def prune_empty_dirs(target: Path) -> None:
    """Remove as pastas que ficaram vazias acima de um ficheiro apagado, sem subir acima da pasta do projeto."""
    root = media_root().resolve()
    folder = target.parent
    while folder != root and folder.parent != root and folder.is_relative_to(root):
        try:
            folder.rmdir()  # só apaga se estiver vazia
        except OSError:
            break
        folder = folder.parent


def delete_file(relative: str) -> None:
    root = media_root().resolve()
    for rel in (relative, *companions(relative)):
        target = (media_root() / rel).resolve()
        if target.is_relative_to(root) and target.is_file():
            target.unlink()
            prune_empty_dirs(target)


def media_url(relative: str) -> str:
    return f"/media/{relative}"


def mp4_duration_ms(data: bytes) -> int | None:
    """Duração de um MP4/MOV a partir da caixa moov/mvhd (sem ffprobe)."""

    def boxes(buf: bytes, start: int, end: int):
        i = start
        while i + 8 <= end:
            size = int.from_bytes(buf[i:i + 4], "big")
            kind = buf[i + 4:i + 8]
            header = 8
            if size == 1:
                size = int.from_bytes(buf[i + 8:i + 16], "big")
                header = 16
            elif size == 0:
                size = end - i
            if size < header:
                return
            yield kind, i + header, min(i + size, end)
            i += size

    try:
        for kind, s, e in boxes(data, 0, len(data)):
            if kind != b"moov":
                continue
            for k2, s2, _e2 in boxes(data, s, e):
                if k2 != b"mvhd":
                    continue
                version = data[s2]
                if version == 1:
                    timescale = int.from_bytes(data[s2 + 20:s2 + 24], "big")
                    duration = int.from_bytes(data[s2 + 24:s2 + 32], "big")
                else:
                    timescale = int.from_bytes(data[s2 + 12:s2 + 16], "big")
                    duration = int.from_bytes(data[s2 + 16:s2 + 20], "big")
                return round(duration * 1000 / timescale) if timescale else None
    except (IndexError, ValueError):
        return None
    return None



def video_thumb(relative: str) -> Path | None:
    """Miniatura JPG de um vídeo (um fotograma a meio segundo, 480 px), guardada ao lado dele (<nome>_thumb.jpg) e
    feita uma só vez. Serve de capa aos vídeos nas páginas (que já não carregam os vídeos todos ao abrir)."""
    import shutil
    import subprocess

    src = (media_root() / relative).resolve()
    if not src.is_relative_to(media_root().resolve()) or not src.is_file() or not shutil.which("ffmpeg"):
        return None
    out = src.with_name(src.stem + "_thumb.jpg")
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    run = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", str(src), "-frames:v", "1", "-vf",
                          "scale=480:-2", "-q:v", "4", str(out)], capture_output=True, timeout=60)
    if run.returncode != 0 or not out.is_file():  # vídeo com menos de meio segundo
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
                        str(out)], capture_output=True, timeout=60)
    return out if out.is_file() else None
