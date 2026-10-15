"""Versões dos prompts (escritos pelo Assistente ou editados à mão) e a lógica comum de gravar um prompt."""

from sqlmodel import Session, select

from app.forms import text
from app.models import PromptVersion


def record(session: Session, project_id: int, field_key: str, body: str, source: str = "manual",
           basis: str | None = None) -> None:
    latest = session.exec(
        select(PromptVersion).where(PromptVersion.project_id == project_id, PromptVersion.field_key == field_key)
        .order_by(PromptVersion.id.desc())
    ).first()
    if latest is None or latest.text != body or latest.source != source:
        session.add(PromptVersion(project_id=project_id, field_key=field_key, text=body, source=source, basis=basis))
    elif basis and latest.basis != basis:
        latest.basis = basis
        session.add(latest)


def versions(session: Session, project_id: int, field_key: str, limit: int = 20) -> list[PromptVersion]:
    return list(session.exec(
        select(PromptVersion).where(PromptVersion.project_id == project_id, PromptVersion.field_key == field_key)
        .order_by(PromptVersion.id.desc()).limit(limit)
    ).all())


def video_takes(session: Session, project_id: int, field_key: str, shot_id: int) -> dict[int, list[str]]:
    """{id da versão: ["Take 8 · Seedance 2.0", …]}: cada take gerado fica com a versão do prompt que estava em vigor
    quando foi pedido (a mais recente gravada antes do pedido; gerar grava sempre o prompt antes de pedir)."""
    from app.models import ImageJob, MediaFile
    from app.routes.video import take_labels

    vers = list(session.exec(select(PromptVersion).where(PromptVersion.project_id == project_id,
                                                         PromptVersion.field_key == field_key)
                             .order_by(PromptVersion.created_at)).all())
    jobs = session.exec(select(ImageJob).where(ImageJob.shot_id == shot_id, ImageJob.role == "clip",
                                               ImageJob.result_media_id.is_not(None))).all()
    clips = {m.id: m for m in session.exec(select(MediaFile).where(MediaFile.id.in_([j.result_media_id for j in jobs]))).all()} \
        if jobs else {}
    labels = take_labels(session, [m for m in clips.values() if m.status != "rejected"])
    out: dict[int, list[str]] = {}
    for j in sorted(jobs, key=lambda x: x.created_at):
        v = next((v for v in reversed(vers) if v.created_at <= j.created_at), None)
        m = clips.get(j.result_media_id)
        if v is None or m is None:
            continue
        if m.status == "rejected":
            import re

            n = re.search(r"v(\d+)\.\w+$", m.path)
            label = f"Take {n.group(1) if n else '?'} (rejeitado)"
        else:
            label = " · ".join(labels.get(m.id, "Take").split(" · ")[:2]) + (" · escolhido" if m.status == "approved" else "")
        out.setdefault(v.id, []).append(label)
    return out


def handle(session: Session, project_id: int, obj, attr: str, field_key: str, form, name: str, op: str,
           transform=None) -> None:
    """Grava o prompt do formulário (editado à mão → nova versão; vazio → fica por escrever).
    Operação: restore_version:<id> (repõe uma versão anterior)."""
    verb, _, arg = (op or "").partition(":")
    if verb == "restore_version" and arg.isdigit():
        v = session.get(PromptVersion, int(arg))
        if v and v.project_id == project_id and v.field_key == field_key:
            setattr(obj, attr, v.text)
            record(session, project_id, field_key, v.text, v.source, v.basis)
        return
    if name not in form:
        return
    edited = text(form, name)
    if edited and transform:
        edited = transform(edited)
    if not edited:
        setattr(obj, attr, None)
        return
    if edited != getattr(obj, attr):
        record(session, project_id, field_key, edited, "manual")
        setattr(obj, attr, edited)
