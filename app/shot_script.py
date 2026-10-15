"""Guião marcado: as linhas do guião da cena que cada plano cobre, e o formato dos segundos."""

from sqlmodel import Session, select

from app.models import Scene, Shot


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return ""
    return (f"{value:.1f}".rstrip("0").rstrip(".")).replace(".", ",") + " s"


def script_coverage(session: Session, scene: Scene) -> list[dict]:
    """O guião da cena com os planos que cobrem cada linha (guião marcado)."""
    from app.models import Asset, SceneLine, ShotLineLink

    lines = session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order)).all()
    shots = {s.id: s for s in session.exec(select(Shot).where(Shot.scene_id == scene.id)).all()}
    links = session.exec(select(ShotLineLink).where(ShotLineLink.line_id.in_([ln.id for ln in lines]))).all() if lines else []
    by_line: dict[int, list[Shot]] = {}
    for link in links:
        if link.shot_id in shots:
            by_line.setdefault(link.line_id, []).append(shots[link.shot_id])
    out = []
    for n, ln in enumerate(lines, start=1):
        who = session.get(Asset, ln.character_id) if ln.character_id else None
        out.append({"n": n, "line": ln, "who": who,
                    "shots": sorted(by_line.get(ln.id, []), key=lambda s: s.number)})
    return out


def shot_line_ids(session: Session, shot: Shot) -> list[int]:
    from app.models import ShotLineLink

    return [link.line_id for link in session.exec(select(ShotLineLink).where(ShotLineLink.shot_id == shot.id)).all()]


def set_shot_lines(session: Session, shot: Shot, line_ids: list[int]) -> None:
    """Liga o plano a estas linhas (só as da cena dele). Se o plano ainda não tem descrição nem fala, parte delas."""
    from app.models import SceneLine, ShotLineLink

    lines = {ln.id: ln for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == shot.scene_id)).all()}
    wanted = [i for i in dict.fromkeys(line_ids) if i in lines]
    current = {link.line_id: link for link in session.exec(select(ShotLineLink).where(ShotLineLink.shot_id == shot.id)).all()}
    for line_id, link in current.items():
        if line_id not in wanted:
            session.delete(link)
    for line_id in wanted:
        if line_id not in current:
            session.add(ShotLineLink(shot_id=shot.id, line_id=line_id))
    chosen = sorted((lines[i] for i in wanted), key=lambda ln: ln.order)
    if chosen and not (shot.description or "").strip():
        from app.models import Asset

        parts = []
        for ln in chosen:
            who = session.get(Asset, ln.character_id) if ln.character_id else None
            parts.append(f"{who.label or who.name if who else 'Alguém'} diz: «{ln.text}»" if ln.kind == "dialogue" else ln.text)
        shot.description = " ".join(p.strip() for p in parts if p.strip())
    speech = next((ln for ln in chosen if ln.kind == "dialogue" and ln.text.strip()), None)
    if speech and not (shot.dialogue or "").strip():
        shot.dialogue, shot.dialogue_character_id = speech.text, speech.character_id
    session.add(shot)
