"""O contexto completo que o escritor de prompts recebe (docs/DECISOES.md, ponto 6): a bíblia, o mundo, as regras de
prompt, a cena, o plano e quem e o que aparece. Texto simples, por blocos com título; os campos vazios não aparecem.

Os blocos servem também de impressão digital: quando um dado muda, o prompt escrito com ele fica desatualizado."""

from sqlmodel import Session, select

from app.models import Asset, Episode, Project, Scene, SceneLine, Shot, ShotLineLink
from app.options import get_config, prompt_texts

KIND_EN = {"character": "character", "location": "location", "prop": "prop", "element": "world element"}
TYPE_EN = {"short": "short film / spot", "series": "series", "film": "feature film"}


def _lines(title: str, rows: list[tuple[str, object]]) -> str:
    body = [f"- {label}: {str(value).strip()}" for label, value in rows if value is not None and str(value).strip()]
    return f"{title}\n" + "\n".join(body) if body else ""


def _en(texts: dict, key: str, value: str | None) -> str | None:
    if not value:
        return None
    en = texts.get((key, value))
    return f"{value} ({en})" if en and en != value else value


def project_block(session: Session, project: Project) -> str:
    """A bíblia e o mundo. O texto do mundo (PT) é contexto; o "mundo nos prompts" (EN) é o aspeto que se vê."""
    t = prompt_texts(session)
    return _lines("PROJETO (bíblia)", [
        ("Título", project.name), ("Tipo", TYPE_EN.get(project.type, project.type)),
        ("Género", _en(t, "genre", project.genre)), ("Tom", _en(t, "tone", project.tone)),
        ("Público", _en(t, "audience", project.audience)),
        ("Língua dos diálogos", _en(t, "dialogue_language", project.dialogue_language)),
        ("Referências", project.references),
        ("Estilo de render", _en(t, "render_style", project.render_style)),
        ("Proporções", _en(t, "proportions", project.proportions)),
        ("Paleta e luz por omissão", _en(t, "palette_light", project.palette_light)),
        ("Bloco de estilo (EN; entra em todos os prompts de imagem)", project.style_block),
        ("Exclusões (EN; entram em todos os prompts)", project.exclusions),
        ("Regras fixas do projeto", project.project_rules),
        ("Formato", _en(t, "aspect_ratio", project.aspect_ratio)),
        ("Logline", project.logline), ("Sinopse", project.synopsis),
        ("O mundo (PT; contexto, não é uma lista do que pôr em cada imagem)", project.world),
        ("O mundo nos prompts (EN; o aspeto do mundo nos ambientes)", project.world_prompt),
    ])


def rules_block(session: Session) -> str:
    """Regras de prompt ativas (Configuração → Prompts) — valem para todos os prompts."""
    rules = [r.get("text") for r in (get_config(session, "prompt_rules", []) or []) if r.get("active") and r.get("text")]
    return "REGRAS DE PROMPT (cumpre todas)\n" + "\n".join(f"- {r}" for r in rules) if rules else ""


def asset_block(asset: Asset, title: str | None = None) -> str:
    rows = [("Código (usa-o tal como está para referir o asset)", asset.name), ("Nome", asset.label),
            ("Tipo", KIND_EN.get(asset.kind, asset.kind)), ("Descrição canónica", asset.canonical_description)]
    if asset.kind == "character":
        rows += [("Traços-chave para o vídeo (EN)", asset.key_features), ("Voz (EN)", asset.voice),
                 ("Quem é (PT; contexto, não descrever)", asset.profile)]
    return _lines(title or f"ASSET {asset.name}", rows)


def scene_block(session: Session, scene: Scene) -> str:
    t = prompt_texts(session)
    loc = session.get(Asset, scene.location_id) if scene.location_id else None
    ep = session.get(Episode, scene.episode_id) if scene.episode_id else None
    lines = session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id, SceneLine.text != "")
                         .order_by(SceneLine.order)).all()
    who = {a.id: a.name for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    script = "\n".join(f"  {n}. " + (f"{who.get(ln.character_id, '?')}: \"{ln.text}\"" if ln.kind == "dialogue" else ln.text)
                       for n, ln in enumerate(lines, 1))
    head = _lines(f"CENA {scene.number}", [
        ("Episódio", f"E{ep.number} {ep.title or ''}" if ep else None), ("Título", scene.title),
        ("O que acontece", scene.description), ("Local principal", f"{loc.name} ({loc.label})" if loc else None),
        ("Hora do dia (a luz da cena vem daqui)", _en(t, "time_of_day", scene.time_of_day)),
        ("Notas de blocking", scene.blocking),
    ])
    return head + (f"\n- Guião da cena:\n{script}" if script else "")


def window_lines(session: Session, shot: Shot, covered: list) -> list:
    """Das linhas marcadas para o plano, as que acontecem no troço da encenação que ele mostra (com a encenação a
    marcar o tempo das linhas; sem isso, todas)."""
    from app import scene3d

    scene = session.get(Scene, shot.scene_id)
    if not covered or not (scene and (scene.stage3d or {}).get("lines")):
        return covered
    order = [ln.id for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)
                                          .order_by(SceneLine.order)).all()]
    number = {lid: i + 1 for i, lid in enumerate(order)}
    keep = set(scene3d.in_window(scene, shot, [number[ln.id] for ln in covered if ln.id in number]))
    return [ln for ln in covered if number.get(ln.id) in keep]


def shot_talk(session: Session, shot: Shot, covered: list | None = None) -> list[tuple[int | None, str]]:
    """As falas do plano, por ordem: as das linhas do guião marcadas para este plano (no Storyboard). Só um plano sem
    linhas marcadas usa a fala antiga guardada no plano (Shot.dialogue). [(id da personagem, texto)]"""
    if covered is None:
        covered = session.exec(select(SceneLine).join(ShotLineLink, ShotLineLink.line_id == SceneLine.id)
                               .where(ShotLineLink.shot_id == shot.id).order_by(SceneLine.order)).all()
    if covered:  # com linhas marcadas, só contam as que acontecem no troço do plano (nunca a fala antiga do plano)
        covered = window_lines(session, shot, covered)
        return [(ln.character_id, ln.text) for ln in covered if ln.kind == "dialogue" and (ln.text or "").strip()]
    return [(shot.dialogue_character_id, shot.dialogue)] if (shot.dialogue or "").strip() else []


def shot_block(session: Session, shot: Shot) -> str:
    t = prompt_texts(session)
    covered = window_lines(session, shot, session.exec(
        select(SceneLine).join(ShotLineLink, ShotLineLink.line_id == SceneLine.id)
        .where(ShotLineLink.shot_id == shot.id).order_by(SceneLine.order)).all())
    who = {}
    if covered or shot.dialogue_character_id:
        scene = session.get(Scene, shot.scene_id)
        who = {a.id: a.name for a in session.exec(select(Asset).where(Asset.project_id == scene.project_id)).all()}
    lines = "; ".join((f"{who.get(ln.character_id, '?')}: \"{ln.text}\"" if ln.kind == "dialogue" else ln.text)
                      for ln in covered)
    talk = shot_talk(session, shot, covered)
    talk_text = " / ".join(f"{who.get(c, '?')}: \"{txt}\"" for c, txt in talk)
    return _lines(f"PLANO P{shot.number:02d}", [
        ("Título", shot.title), ("O que acontece (do início ao fim)", shot.description),
        ("Linhas do guião que cobre", lines or None),
        ("Tipo de plano", _en(t, "shot_type", shot.shot_type)), ("Ângulo", _en(t, "camera_angle", shot.camera_angle)),
        ("Movimento de câmara", _en(t, "camera_movement", shot.camera_movement)),
        ("Duração (s)", shot.duration_s),
        (f"Falas neste plano ({len(talk)}, todas, por esta ordem; cada uma com a sua linha Dialogue)" if len(talk) > 1
         else "Fala neste plano", talk_text or None),
        ("Efeitos sonoros (EN)", shot.sound_effects), ("Ambiente sonoro (EN)", shot.ambience), ("Música (EN)", shot.music),
    ])


def join(*blocks: str) -> str:
    return "\n\n".join(b for b in blocks if b and b.strip())
