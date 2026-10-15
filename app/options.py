from sqlalchemy import update
from sqlmodel import Session, func, select

from app.models import PROJECT_TYPES, Asset, Episode, GlobalConfig, OptionList, Project, Scene, Shot
from app.memo import memo

# Listas editáveis, por grupo, pela ordem em que aparecem em Configuração.
OPTION_GROUPS: list[tuple[str, dict[str, str]]] = [
    ("Projeto", {
        "project_type": "Tipos de projeto",
        "dialogue_language": "Línguas dos diálogos",
        "target_duration": "Durações de spot (s)",
        "target_duration_long": "Durações de episódio/filme (min)",
    }),
    ("Bíblia", {
        "genre": "Géneros",
        "tone": "Tons",
        "audience": "Públicos",
        "render_style": "Estilos de render",
        "proportions": "Proporções",
        "palette_light": "Paletas e luz",
        "aspect_ratio": "Aspect ratios",
    }),
    ("Cenas e planos", {
        "time_of_day": "Horas do dia",
        "shot_type": "Tipos de plano",
        "camera_angle": "Ângulos",
        "camera_movement": "Movimentos de câmara",
    }),
    ("Personagens", {
        "voice_type": "Tipos de voz",
        "timbre": "Timbres",
    }),
]
OPTION_KEYS: dict[str, str] = {k: v for _, group in OPTION_GROUPS for k, v in group.items()}

# Onde cada lista é usada (para contar "em uso" e propagar mudanças de nome).
OPTION_USAGE = {
    "genre": [(Project, "genre")],
    "tone": [(Project, "tone")],
    "audience": [(Project, "audience")],
    "dialogue_language": [(Project, "dialogue_language")],
    "render_style": [(Project, "render_style")],
    "proportions": [(Project, "proportions")],
    "palette_light": [(Project, "palette_light")],
    "aspect_ratio": [(Project, "aspect_ratio")],
    # Curtas: duração total em segundos. Séries (por episódio) e filmes: em minutos.
    "target_duration": [(Project, "target_duration_s", Project.type == "short")],
    "target_duration_long": [(Project, "target_duration_s", Project.type != "short"), (Episode, "target_duration_s", None)],
    "time_of_day": [(Scene, "time_of_day")],
    "shot_type": [(Shot, "shot_type")],
    "camera_angle": [(Shot, "camera_angle")],
    "camera_movement": [(Shot, "camera_movement")],
    "voice_type": [(Asset, "voice_type")],
    "timbre": [(Asset, "voice_timbre")],
}
# Listas numéricas: rótulo = número na unidade da lista; o campo guarda segundos.
NUMERIC_KEYS = {"target_duration": 1, "target_duration_long": 60}


def list_labels(session: Session) -> dict[str, str]:
    overrides = get_config(session, "option_list_labels", {}) or {}
    return {k: overrides.get(k, v) for k, v in OPTION_KEYS.items()}


def get_option_rows(session: Session, key: str) -> list[OptionList]:
    return list(session.exec(select(OptionList).where(OptionList.key == key).order_by(OptionList.order, OptionList.id)).all())


def get_options(session: Session) -> dict[str, list[str]]:
    rows = session.exec(select(OptionList).order_by(OptionList.key, OptionList.order, OptionList.id)).all()
    options: dict[str, list[str]] = {key: [] for key in OPTION_KEYS}
    for row in rows:
        options.setdefault(row.key, []).append(row.value)
    return options


def option_counts(session: Session) -> dict[str, int]:
    return dict(session.exec(select(OptionList.key, func.count()).group_by(OptionList.key)).all())


def _stored(key: str, value: str):
    return int(value) * NUMERIC_KEYS[key] if key in NUMERIC_KEYS else value


def _usages(key: str):
    for entry in OPTION_USAGE.get(key, []):
        model, column, *cond = entry
        yield model, column, (cond[0] if cond else None)


def duration_key(project_type: str) -> str:
    return "target_duration" if project_type == "short" else "target_duration_long"


def duration_choices(session: Session, project_type: str) -> list[tuple[int, str]]:
    """(segundos, rótulo) da lista de durações que se aplica ao tipo de projeto."""
    from app.templating import format_duration

    key = duration_key(project_type)
    out = []
    for row in get_option_rows(session, key):
        if row.value.isdigit():
            seconds = int(row.value) * NUMERIC_KEYS[key]
            out.append((seconds, format_duration(seconds)))
    return out


def usage_count(session: Session, key: str, value: str) -> int:
    if key == "project_type":
        rows = get_option_rows(session, key)
        codes = list(PROJECT_TYPES)
        idx = next((i for i, r in enumerate(rows) if r.value == value), None)
        if idx is None or idx >= len(codes):
            return 0
        return session.exec(select(func.count()).select_from(Project).where(Project.type == codes[idx])).one()
    total = 0
    for model, column, cond in _usages(key):
        stmt = select(func.count()).select_from(model).where(getattr(model, column) == _stored(key, value))
        if cond is not None:
            stmt = stmt.where(cond)
        total += session.exec(stmt).one()
    return total


def rename_usages(session: Session, key: str, old: str, new: str) -> None:
    for model, column, cond in _usages(key):
        stmt = update(model).where(getattr(model, column) == _stored(key, old))
        if cond is not None:
            stmt = stmt.where(cond)
        session.exec(stmt.values({column: _stored(key, new)}))


def project_type_labels(session: Session) -> dict[str, str]:
    """Os três tipos têm comportamento fixo (códigos); os nomes vêm da lista editável."""
    labels = get_options(session).get("project_type", [])
    codes = list(PROJECT_TYPES)
    if len(labels) != len(codes):
        return dict(PROJECT_TYPES)
    return dict(zip(codes, labels))


@memo
def get_config(session: Session, key: str, default=None):
    row = session.exec(select(GlobalConfig).where(GlobalConfig.key == key)).first()
    return row.value if row else default


def set_config(session: Session, key: str, value) -> None:
    row = session.exec(select(GlobalConfig).where(GlobalConfig.key == key)).first()
    if row is None:
        row = GlobalConfig(key=key, value=value)
    else:
        row.value = value
    session.add(row)


def prompt_texts(session: Session) -> dict[tuple[str, str], str]:
    """(lista, rótulo) → texto EN para os prompts."""
    rows = session.exec(select(OptionList).where(OptionList.prompt_text.is_not(None))).all()
    return {(r.key, r.value): r.prompt_text for r in rows if r.prompt_text}


def video_settings(session: Session) -> dict:
    """Definições da geração de vídeo, para qualquer fornecedor (Configuração → Modelos e geração → Vídeo):
    resolução, som por omissão e tempo máximo. Antes ficavam na configuração do Artlist: lê-se de lá se faltar."""
    cfg = get_config(session, "video_generator", {}) or {}
    old = get_config(session, "artlist", {}) or {}
    return {"quality": str(cfg.get("quality") or old.get("video_quality") or "480p"),
            "audio": bool(cfg.get("audio", old.get("video_audio", True))),
            "timeout_s": int(cfg.get("timeout_s") or old.get("video_timeout_s") or 1500)}
