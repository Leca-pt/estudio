"""Campos que o Assistente pode propor: lista branca, validação e aplicação.

Chaves: "project.<attr>", "<entidade>:<id>.<attr>" e as especiais
"new_asset", "new_scene:<grupo>", "new_season", "new_episode:<season_id>", "new_act", "new_shot:<scene_id>", "new_check:<shot_id>", "link:<asset_id>:<scene_id>",
"new_look:<asset_id>" (look novo no guarda-roupa) e "scene_look:<scene_id>:<asset_id>" (o look de uma personagem numa cena).
"""

import json
import re
from dataclasses import dataclass, field

from sqlmodel import Session, select, update

from app.models import (
    SCENE_LINE_KINDS,
    ASSET_KINDS, CHECK_CATEGORIES, Act, Asset, AssetSceneLink, CharacterLook, Episode, Project, Scene, SceneLine,
    Season, Shot, ShotCheck,
)
from app.options import get_options

TEXT, EN, SELECT, INT, PROMPT = "text", "en", "select", "int", "prompt"

# entidade → (modelo, {atributo: (tipo, rótulo, lista de opções)})
ENTITIES = {
    "project": (Project, {
        "premise": (TEXT, "Premissa", None), "logline": (TEXT, "Logline", None), "synopsis": (TEXT, "Sinopse", None),
        "genre": (SELECT, "Género", "genre"), "tone": (SELECT, "Tom", "tone"), "audience": (SELECT, "Público", "audience"),
        "dialogue_language": (SELECT, "Língua dos diálogos", "dialogue_language"),
        "references": (TEXT, "Referências", None), "render_style": (SELECT, "Estilo de render", "render_style"),
        "proportions": (SELECT, "Proporções", "proportions"), "palette_light": (SELECT, "Paleta e luz", "palette_light"),
        "aspect_ratio": (SELECT, "Formato (proporção da imagem e do vídeo)", "aspect_ratio"),
        "style_block": (EN, "Bloco de estilo", None), "exclusions": (EN, "Exclusões", None),
        "project_rules": (TEXT, "Regras dos prompts", None), "world": (TEXT, "O mundo", None), "world_prompt": (EN, "O mundo nos prompts", None), "cover_prompt": (PROMPT, "Prompt da capa", None),
    }),
    "season": (Season, {"title": (TEXT, "Título", None), "arc": (TEXT, "Arco", None), "summary": (TEXT, "Resumo", None)}),
    "episode": (Episode, {"title": (TEXT, "Título", None), "logline": (TEXT, "Logline", None), "synopsis": (TEXT, "Sinopse", None)}),
    "act": (Act, {"title": (TEXT, "Título", None), "arc": (TEXT, "Arco", None), "summary": (TEXT, "Resumo", None)}),
    "scene": (Scene, {
        "title": (TEXT, "Título", None), "time_of_day": (SELECT, "Hora do dia", "time_of_day"),
        "description": (TEXT, "Descrição da cena", None),
        "blocking": (TEXT, "Notas de blocking", None),
    }),
    "line": (SceneLine, {"text": (TEXT, "Texto", None)}),
    "asset": (Asset, {
        "label": (TEXT, "Nome", None), "group_name": (TEXT, "Grupo", None),
        "canonical_description": (EN, "Descrição canónica", None), "profile": (TEXT, "Perfil / notas", None),
        "voice_type": (SELECT, "Tipo de voz", "voice_type"), "voice_timbre": (SELECT, "Timbre", "timbre"),
        "voice": (EN, "Descrição da voz", None), "key_features": (EN, "Features-chave para o vídeo", None),
        "voice_sample": (TEXT, "O que a voz diz (amostra da voz de referência)", None),
        "voice_note": (EN, "Como fala nas falas (EN)", None),
        "sheet_prompt": (PROMPT, "Prompt da folha", None), "portrait_prompt": (PROMPT, "Prompt do retrato", None),
    }),
    "look": (CharacterLook, {"name": (TEXT, "Nome do look", None), "description": (EN, "Roupa e acessórios", None),
                             "prompt": (PROMPT, "Prompt da folha do look", None)}),
    "shot": (Shot, {
        "title": (TEXT, "Nome do plano", None), "description": (TEXT, "O que acontece no plano", None), "shot_type": (SELECT, "Tipo de plano", "shot_type"),
        "camera_angle": (SELECT, "Ângulo", "camera_angle"), "camera_movement": (SELECT, "Movimento de câmara", "camera_movement"),
        "sound_effects": (EN, "Efeitos sonoros", None), "ambience": (EN, "Ambiente", None), "music": (EN, "Música", None), "video_prompt": (PROMPT, "Prompt de vídeo", None),
        "key_prompt": (PROMPT, "Prompt do frame-chave", None),
    }),
}


class ProposalError(ValueError):
    pass


class AlreadySet(ProposalError):
    """A proposta não muda nada (já está assim): ignora-se sem aviso."""


@dataclass
class FieldSpec:
    key: str
    label: str  # caminho legível, ex.: "Estilo visual › Bloco de estilo"
    kind: str
    current: str | None = None
    options: list[str] = field(default_factory=list)
    help: str = ""  # instruções específicas para o LLM


def parse_key(key: str) -> tuple[str, int | None, str]:
    """"shot:3.title" → ("shot", 3, "title"); "project.logline" → ("project", None, "logline")."""
    head, _, attr = key.partition(".")
    entity, _, raw_id = head.partition(":")
    return entity, (int(raw_id) if raw_id.isdigit() else None), attr


def _owned(session: Session, project: Project, entity: str, obj) -> bool:
    if obj is None:
        return False
    if entity == "project":
        return obj.id == project.id
    if entity in ("season", "act", "scene", "asset", "look"):
        return obj.project_id == project.id
    if entity == "episode":
        season = session.get(Season, obj.season_id)
        return bool(season and season.project_id == project.id)
    if entity == "line":
        scene = session.get(Scene, obj.scene_id)
        return bool(scene and scene.project_id == project.id)
    if entity == "shot":
        scene = session.get(Scene, obj.scene_id)
        return bool(scene and scene.project_id == project.id)
    return False


def resolve(session: Session, project: Project, key: str):
    """(objeto, atributo, tipo, opções) de uma chave simples; ProposalError se não for válida."""
    entity, obj_id, attr = parse_key(key)
    if entity not in ENTITIES or attr not in ENTITIES[entity][1]:
        raise ProposalError("Campo desconhecido.")
    model, attrs = ENTITIES[entity]
    obj = project if entity == "project" else (session.get(model, obj_id) if obj_id else None)
    if not _owned(session, project, entity, obj):
        raise ProposalError("O elemento já não existe.")
    kind, _label, options_key = attrs[attr]
    options = get_options(session).get(options_key, []) if options_key else []
    return obj, attr, kind, options


# O que cabe em cada caixa (o Assistente arruma o texto no sítio certo quando revê um destes campos).
DESCRIPTION_HELP = {
    "character": "só a aparência, em frases: idade, corpo e postura, cabelo, cara e olhos, marcas distintivas com número e "
                 "posição exatos, roupa e acessórios",
    "location": "só o local: disposição, materiais, elementos marcantes e onde estão no enquadramento",
    "prop": "só o objeto: forma, tamanho, materiais, cores, número exato de partes",
    "element": "a família de coisas (ex.: os táxis autónomos deste mundo): a linguagem de design que todas partilham "
               "(formas, proporções, materiais, cores, marcas visuais) e o que varia entre variantes; sem marcas reais",
}
NOT_IN_DESCRIPTION = ("NUNCA: vistas, poses de folha, grande plano/retrato, fundo, enquadramento, luz, estilo de render, "
                      "aspect ratio/formato, restrições (\"no text\", \"exactly one\"…) — isso vem das guias do escritor de "
                      "prompts, do bloco de estilo e das exclusões. Se a descrição atual tiver alguma destas instruções, retira-as sem "
                      "perder nenhum pormenor de aparência e diz em reason o que saiu.")


PROMPT_NOTE = ("Os prompts são escritos pelo escritor de prompts (botões Gerar e Escrever, com a guia de cada tipo na "
               "Configuração). Propõe aqui só alterações pedidas pelo utilizador: o prompt COMPLETO, com os marcadores "
               "@CÓDIGO das imagens que seguem tal como estão, o bloco de estilo e as exclusões.")


def field_help(session: Session, obj, attr: str) -> str:
    if isinstance(obj, CharacterLook):
        if attr == "description":
            return ("A roupa, o calçado e os acessórios deste look, em inglês e concretos (peças, cores, materiais, "
                    "desgaste, onde fica cada acessório: relógio, óculos, cinto). Só a roupa: nada da cara, do cabelo "
                    "nem do corpo, que vêm da folha da personagem.")
        if attr == "prompt":
            return f"Folha do look: a mesma pessoa da folha aprovada, só com esta roupa. {PROMPT_NOTE}"
        return "Nome curto do look, em português (ex.: Rua, Pijama, Fato de gala)."
    if attr == "canonical_description" and isinstance(obj, Asset):
        return f"Descrição canónica ({DESCRIPTION_HELP.get(obj.kind, '')}). {NOT_IN_DESCRIPTION}"
    if attr == "sheet_prompt" and isinstance(obj, Asset):
        extra = (" Só as vistas de corpo inteiro; sem grande plano da cara nem retrato (isso é o prompt do retrato)."
                 if obj.kind == "character" else "")
        return f"Folha de referência.{extra} {PROMPT_NOTE}"
    if attr == "portrait_prompt":
        return f"Retrato: cabeça e ombros, de frente, uma só pessoa, fundo liso. {PROMPT_NOTE}"
    if attr == "cover_prompt":
        return f"Capa do projeto (key art do cartão na lista de projetos, 16:9, sem texto). {PROMPT_NOTE}"
    if attr == "video_prompt":
        return f"Prompt de vídeo do plano (um take contínuo). {PROMPT_NOTE}"
    if attr == "key_prompt":
        return ("Frame-chave do plano: uma fotografia do primeiro instante do take com as personagens no sítio (a partir "
                f"do previs do 1.º momento, se houver cena 3D). {PROMPT_NOTE}")
    if attr == "voice_note":
        return ("EN, curto: como a personagem fala nos vídeos, ex. \"speaking English with a clear Spanish accent\". A "
                "app acrescenta-o sozinha a seguir à voz de referência em cada fala do prompt de vídeo (não o escrevas "
                "no prompt).")
    if attr == "voice_sample":
        return ("O que a personagem diz na amostra da voz de referência (gerada para manter a voz igual em todos os "
                "vídeos): 1 a 2 frases, 4 a 6 s de fala, na língua dos diálogos do projeto, com o jeito de falar da "
                "personagem (do perfil). Só o texto falado, sem indicações.")
    if attr == "voice":
        return ("Descrição da voz (EN), usada para criar a voz de referência e nos prompts de vídeo: idade aparente, "
                "timbre, sotaque, ritmo e energia, numa frase.")
    if attr == "blocking":
        return ("Notas de blocking da cena, em português: quem está onde, para onde olha e de que lado fica a câmara "
                "(regra dos 180°). Servem para montar as cenas 3D dos planos.")
    if attr == "world":
        return ("O mundo (worldbuilding), em português e texto livre: o que é verdade neste mundo e se mantém em todos os "
                "episódios — época e lugar, tecnologia, sociedade e poder, regras do fantástico, aspeto visual recorrente. "
                "Frases curtas e concretas, fáceis de verificar. Mantém o que o utilizador escreveu e organiza ou "
                "acrescenta; não inventes factos contrários.")
    if attr == "world_prompt":
        return ("O mundo nos prompts (EN, 15–30 palavras, uma ou duas frases): só o que SE VÊ do mundo em qualquer rua ou "
                "interior deste projeto — época e lugar no aspeto, tecnologia visível, estado das ruas, o que NÃO existe "
                "(ex.: \"no holograms\"). Nada de história, poder ou economia que não se veja. O escritor de prompts usa-o "
                "nos prompts de ambiente (vistas dos locais, capa, vídeo); não o repitas nas descrições. Sai do texto de "
                "O MUNDO.")
    if attr == "premise":
        return ("Premissa do projeto, em português e texto livre: a ideia de partida (e se…?, onde se passa, personagens "
                "principais, tom, o que a torna diferente). Não entra em prompts. Mantém o que o utilizador escreveu e "
                "organiza ou acrescenta; não inventes factos contrários.")
    if attr == "logline" and isinstance(obj, Project):
        return "Uma só frase, em português: quem é o protagonista, o que quer e o que está em jogo. Parte da premissa."
    if attr == "synopsis" and isinstance(obj, Project):
        return ("A história do princípio ao fim em poucos parágrafos, em português, coerente com a premissa e a logline"
                ". Numa série: a série inteira; cada temporada tem o seu arco e cada episódio a sua sinopse.")
    if attr == "profile" and isinstance(obj, Asset) and obj.kind != "character":
        return ("Notas do " + ("local" if obj.kind == "location" else "objeto") + ", em português e texto livre: o que é na "
                "história, para que serve, quem o usa ou frequenta, o que lá aconteceu, detalhes que têm de se manter. "
                "Não entra em prompts. Não descrevas a aparência aqui (isso é a descrição canónica).")
    if attr == "profile":
        return ("Perfil da personagem, em português e texto livre: quem é, o que faz, passado, o que quer, pontos fortes e "
                "fracos, maneira de falar e de se mexer. Não entra em prompts; serve para escrever a personagem. Não "
                "descrevas a aparência aqui (isso é a descrição canónica). Mantém o que o utilizador já escreveu e acrescenta "
                "ou organiza; não inventes factos contrários ao que está lá.")
    if attr == "group_name":
        return ("Grupo para organizar a grelha de assets (ex.: Cidade, Carrinha, Equipa, Vilões). Reutiliza os grupos que "
                "já existem, com a mesma grafia; cria um novo só quando nenhum serve.")
    if attr == "style_block":
        return ("Entra em TODOS os prompts, que têm limite de 80 palavras (imagem): no máximo 20 palavras. Só o essencial "
                "do estilo e do tratamento de imagem (ex.: render, paleta, grão). Nada de luz nem hora do dia (twilight, "
                "blue hour, night…): a luz de cada cena vem da hora do dia da cena. "
                "Sem aspect ratio, resolução nem formato.")
    if attr == "exclusions":
        return ("Entra em TODOS os prompts, que têm limite de 80 palavras: no máximo 15 palavras. Pela positiva quando "
                "possível; só o que o modelo costuma errar neste estilo. Não repitas o que a app já acrescenta sozinha "
                "(\"Exactly N people\", \"No text\" nas capas, \"nobody looks at the camera\" nos grupos) nem adjetivos vazios. "
                "Nada de luz nem hora do dia (ex.: \"no midday sun\"): isso contradiz as cenas com essa hora.")
    return ""


def spec(session: Session, project: Project, key: str, path: str) -> FieldSpec:
    obj, attr, kind, options = resolve(session, project, key)
    label = ENTITIES[parse_key(key)[0]][1][attr][1]
    current = getattr(obj, attr)
    return FieldSpec(key, f"{path} › {label}" if path else label, kind,
                     None if current is None else str(current), options, field_help(session, obj, attr))


def clean_value(kind: str, value: str, options: list[str]) -> str | int | None:
    value = (value or "").strip()
    if kind == SELECT:
        if value not in options:
            raise ProposalError(f"“{value}” não está na lista.")
        return value
    if kind == INT:
        if not value.isdigit() or not 1 <= int(value) <= 600:
            raise ProposalError("Tem de ser um número inteiro.")
        return int(value)
    if not value:
        raise ProposalError("Valor vazio.")
    return value


# ── Campos especiais ─────────────────────────────────────────

def _scene_by_code(session: Session, project: Project, code: str, scene_ids: list[int]) -> Scene | None:
    code = code.strip().upper()
    for sid in scene_ids:
        scene = session.get(Scene, sid)
        if scene and scene.project_id == project.id and f"C{scene.number:02d}" == code:
            return scene
    return None


def _asset_by_code(session: Session, project: Project, code: str) -> Asset | None:
    code = str(code or "").strip().lstrip("@").upper()
    return session.exec(select(Asset).where(Asset.project_id == project.id, Asset.name == code)).first() if code else None


def _stage_scene(session: Session, project: Project, key: str) -> Scene | None:
    """A cena de um campo stage3d:N (para conferir as variações da maquete com a maquete do local dela)."""
    raw = key.split(":", 1)[1] if ":" in key else ""
    scene = session.get(Scene, int(raw)) if raw.isdigit() else None
    return scene if scene is not None and scene.project_id == project.id else None


def validate(session: Session, project: Project, key: str, value: str, allowed: dict[str, FieldSpec]) -> str:
    """Valida uma proposta contra os campos permitidos neste contexto. Devolve o valor normalizado."""
    if key not in allowed:
        raise ProposalError("Campo fora do contexto atual.")
    fs = allowed[key]
    if fs.kind.startswith("room_"):  # Sala de argumentistas
        from app.assistant import room_ctx

        from app.writing import WritingError

        try:
            return room_ctx.validate(session, project, fs.kind, key, value)
        except (ValueError, KeyError, WritingError) as exc:
            raise ProposalError(str(exc))
    if fs.kind == "new_lines":
        try:
            data = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError("Formato inválido.")
        items = data.get("lines") if isinstance(data, dict) else data
        if not isinstance(items, list) or not items:
            raise ProposalError("Sem linhas.")
        clean = []
        for it in items:
            if not isinstance(it, dict) or it.get("kind") not in SCENE_LINE_KINDS or not str(it.get("text", "")).strip():
                raise ProposalError("Linha sem tipo ou texto.")
            line = {"kind": it["kind"], "text": str(it["text"]).strip()}
            if it["kind"] == "dialogue":
                who = _asset_by_code(session, project, str(it.get("character") or ""))
                if not who or who.kind != "character":
                    raise ProposalError(f"Personagem desconhecida: {it.get('character')}.")
                line["character"] = who.name
            clean.append(line)
        return json.dumps({"lines": clean}, ensure_ascii=False)
    if fs.kind == "set3d_from":
        value = str(value).strip()
        if value not in fs.options:
            raise ProposalError("Esse local não tem maquete (ou não é desta cena).")
        if value == (fs.current or ""):
            raise AlreadySet("Já está assim.")
        return value
    if fs.kind == "key_moment":
        value = str(value).strip()
        if value not in fs.options:
            raise ProposalError(f"Tem de ser o número de um momento da câmara ({', '.join(fs.options)}).")
        if value == (fs.current or ""):
            raise AlreadySet("Já está assim.")
        return value
    if fs.kind == "moment_view":
        value = str(value).strip().lower()
        if value not in fs.options:
            raise ProposalError('Tem de ser o id de uma vista do local, "auto" ou "nova".')
        if value == (fs.current or "") and value != "nova":
            raise AlreadySet("Já está assim.")
        return value
    if fs.kind in ("scene3d", "stage3d", "layout3d"):
        from app import scene3d

        try:
            data = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError("O JSON 3D não é válido.")
        try:
            clean = (scene3d.validate_camera(data) if fs.kind == "scene3d"
                     else scene3d.validate_stage(data, session, project.id, _stage_scene(session, project, key)) if fs.kind == "stage3d"
                     else scene3d.validate_set(data))
        except scene3d.SceneError as exc:
            raise ProposalError(str(exc))
        return json.dumps(clean, ensure_ascii=False)
    if fs.kind == "layout3d_edit":  # maquete por partes: guarda a edição e a versão da maquete a que se aplica
        from app import scene3d

        try:
            edit = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError("A edição da maquete não é JSON válido.")
        asset = session.get(Asset, int(key.split(":")[1]))
        try:
            scene3d.apply_layout_edit(asset.layout3d if asset else None, edit)
        except scene3d.SceneError as exc:
            raise ProposalError(str(exc))
        keep = {k: edit[k] for k in ("update", "remove", "add", "ground", "seed") if k in edit}
        return json.dumps({"base": scene3d.layout_digest(asset.layout3d), **keep}, ensure_ascii=False)
    if fs.kind == "scene_look":
        value = str(value).strip()
        if value.lower() == "base":
            value = "Base"
        if value not in fs.options:
            raise ProposalError("Esse look não existe no guarda-roupa da personagem (usa o nome do look ou \"Base\").")
        if value == (fs.current or "Base"):
            raise AlreadySet("Já está assim.")
        return value
    if fs.kind == "sheet_ref":
        ref = _asset_by_code(session, project, value)
        asset = session.get(Asset, int(key.split(":")[1]))
        if not ref or not asset or ref.id == asset.id:
            raise ProposalError("Asset desconhecido.")
        if ref.id in (asset.sheet_refs or []):
            raise AlreadySet("Já está nas referências.")
        return ref.name
    if fs.kind in (TEXT, EN, PROMPT, SELECT, INT):
        return str(clean_value(fs.kind, value, fs.options))
    if fs.kind == "sheet_refs":  # a lista completa das referências da folha (códigos); [] = nenhuma
        try:
            codes = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError('Formato: ["PRJ_RUA", "PRJ_BAR"] (a lista completa; [] para nenhuma).')
        if not isinstance(codes, list):
            raise ProposalError('Formato: ["PRJ_RUA", "PRJ_BAR"] (a lista completa; [] para nenhuma).')
        own = session.get(Asset, int(key.split(":")[1]))
        names = []
        for c in codes:
            ref = _asset_by_code(session, project, str(c))
            if not ref or (own and ref.id == own.id):
                raise ProposalError(f"Asset desconhecido: {c}.")
            names.append(ref.name)
        clean = json.dumps(list(dict.fromkeys(names)), ensure_ascii=False)
        if clean == (fs.current or "[]"):
            raise AlreadySet("Já está assim.")
        return clean
    if fs.kind == "asset_code":  # mudar o código (o nome técnico) do asset
        value = str(value).strip().upper()
        if value == (fs.current or ""):
            raise AlreadySet("Já tem esse código.")
        if not value.startswith(f"{project.prefix}_") or not re.fullmatch(r"[A-Z0-9_]+", value) or value == f"{project.prefix}_":
            raise ProposalError(f"O código tem de começar por {project.prefix}_ e ter só maiúsculas, números e _.")
        if session.exec(select(Asset).where(Asset.project_id == project.id, Asset.name == value)).first():
            raise ProposalError(f"Já existe um asset com o código {value}.")
        return value
    if fs.kind == "asset_kind":
        value = str(value).strip()
        if value not in ASSET_KINDS:
            raise ProposalError(f"Tipo inválido: usa um de {', '.join(ASSET_KINDS)}.")
        if value == fs.current:
            raise AlreadySet("Já é desse tipo.")
        return value
    if fs.kind == "script":  # o guião completo da cena, pela ordem final
        try:
            data = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError("Formato inválido.")
        items = data.get("lines") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ProposalError('Formato: {"lines": [{"id": <id de uma linha que fica>, "kind": ..., "text": ...}, ...]}.')
        scene = session.get(Scene, int(key.split(":")[1]))
        mine = {ln.id for ln in session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)).all()}
        clean, seen = [], set()
        for it in items:
            if not isinstance(it, dict) or it.get("kind") not in SCENE_LINE_KINDS or not str(it.get("text", "")).strip():
                raise ProposalError("Cada linha precisa de kind (action|dialogue) e text.")
            row = {"kind": it["kind"], "text": str(it["text"]).strip()}
            if it.get("id") is not None:
                if int(it["id"]) not in mine or int(it["id"]) in seen:
                    raise ProposalError(f"A linha {it['id']} não é desta cena (ou repete-se).")
                row["id"] = int(it["id"])
                seen.add(row["id"])
            if it["kind"] == "dialogue":
                who = _asset_by_code(session, project, str(it.get("character") or ""))
                if not who or who.kind != "character":
                    raise ProposalError(f"Personagem desconhecida: {it.get('character')}.")
                row["character"] = who.name
            clean.append(row)
        return json.dumps({"lines": clean}, ensure_ascii=False)
    if fs.kind == "line_edit":  # uma linha do guião inteira: tipo, quem fala (diálogos) e texto
        try:
            data = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError("Formato inválido.")
        if not isinstance(data, dict) or data.get("kind") not in SCENE_LINE_KINDS or not str(data.get("text", "")).strip():
            raise ProposalError('Formato: {"kind": "action"|"dialogue", "character": "<código, só nos diálogos>", "text": "..."}.')
        line = {"kind": data["kind"], "text": str(data["text"]).strip()}
        if data["kind"] == "dialogue":
            who = _asset_by_code(session, project, str(data.get("character") or ""))
            if not who or who.kind != "character":
                raise ProposalError(f"Personagem desconhecida: {data.get('character')}.")
            line["character"] = who.name
        clean = json.dumps(line, ensure_ascii=False)
        if clean == (fs.current or ""):
            raise AlreadySet("Já está assim.")
        return clean
    if fs.kind == "scene_location":  # o local principal da cena: o código de um local do projeto ("" = nenhum)
        value = str(value).strip().upper()
        if value and value not in fs.options:
            raise ProposalError("Esse local não existe no projeto: usa o código de um local (ex.: PRJ_BAR).")
        if value == (fs.current or ""):
            raise AlreadySet("Já está assim.")
        return value
    if fs.kind == "scene_order":  # a posição da cena (1 = primeira) entre as do mesmo episódio/ato/curta
        if not str(value).strip().isdigit() or not 1 <= int(value) <= len(fs.options):
            raise ProposalError(f"A posição tem de ser um número de 1 a {len(fs.options)}.")
        if str(int(value)) == (fs.current or ""):
            raise AlreadySet("Já está nessa posição.")
        return str(int(value))
    if fs.kind == "shot_duration":  # segundos inteiros, das que o modelo do plano aceita (sem arredondar)
        v = str(value).strip().removesuffix("s").strip()
        if not v.isdigit():
            raise ProposalError(f"A duração é um número inteiro de segundos. Opções: {', '.join(fs.options)}.")
        if v not in fs.options:
            raise ProposalError(f"O modelo de vídeo do plano não gera {v} s. Opções: {', '.join(fs.options)}.")
        if v == (fs.current or ""):
            raise AlreadySet("Já tem essa duração.")
        return v
    if fs.kind == "scene_duration":
        from app.scene3d import STAGE_MAX_S

        try:
            v = round(float(str(value).strip().removesuffix("s").strip().replace(",", ".")), 2)
        except ValueError:
            raise ProposalError("A duração da encenação é um número de segundos.")
        floor = float(fs.options[0]) if fs.options else 0.0
        if v < floor - 1e-6:
            raise ProposalError(f"Os planos da cena acabam aos {floor:g} s: a encenação não pode ter menos do que isso.")
        if v <= 0 or v > STAGE_MAX_S:
            raise ProposalError(f"Entre 1 e {STAGE_MAX_S} s.")
        if f"{v:g}" == (fs.current or ""):
            raise AlreadySet("Já tem essa duração.")
        return f"{v:g}"
    if fs.kind == "video_inputs":  # o que segue no pedido de vídeo (app/video_inputs.py)
        from app import video_inputs as VI
        from app.routes.video import shot_model

        shot = session.get(Shot, int(key.split(":")[1].split(".")[0]))
        try:
            refs, cfg, notes = VI.parse(session, shot, shot_model(session, shot), value)
        except ValueError as exc:
            raise ProposalError(str(exc))
        if refs == shot.video_refs and cfg == shot.video_inputs:
            raise AlreadySet("Já está assim.")
        return json.dumps({"video_refs": refs, "video_inputs": cfg, "notes": notes}, ensure_ascii=False)
    if fs.kind in ("video_model", "video_resolution"):  # modelo/resolução de vídeo do plano ou do projeto
        value = str(value or "").strip()
        match = next((o for o in fs.options if o.lower() == value.lower()), None)
        if match is None:
            raise ProposalError(f"Valor inválido. Opções: {', '.join(fs.options)}.")
        if match == (fs.current or ""):
            raise AlreadySet("Já está assim.")
        return match
    if fs.kind == "zone_refs":  # as imagens de referência de uma zona da maquete (ids das opções)
        from app.location3d import MAX_ZONE_REFS

        try:
            ids = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            raise ProposalError("Formato: [12, 34].")
        if not isinstance(ids, list) or not all(str(i).isdigit() and str(i) in fs.options for i in ids):
            raise ProposalError(f"Só ids da lista: {', '.join(fs.options)}.")
        ids = list(dict.fromkeys(int(i) for i in ids))
        if len(ids) > MAX_ZONE_REFS:
            raise ProposalError(f"No máximo {MAX_ZONE_REFS} referências por zona.")
        if json.dumps(ids) == (fs.current or "[]"):
            raise AlreadySet("Já está assim.")
        return json.dumps(ids)
    if fs.kind == "link":
        value = str(value).strip()
        if value not in ("0", "1"):
            raise ProposalError('Valor tem de ser "0" ou "1".')
        if value == fs.current:
            raise AlreadySet("Já está assim.")
        return value
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        raise ProposalError("Formato inválido.")
    if not isinstance(data, dict):
        raise ProposalError("Formato inválido.")
    options = get_options(session)
    if fs.kind == "new_asset":
        if not str(data.get("label", "")).strip() or data.get("kind") not in ASSET_KINDS:
            raise ProposalError("Asset sem nome ou tipo.")
    elif fs.kind == "new_shot":
        for k, lst in (("shot_type", "shot_type"), ("camera_angle", "camera_angle"), ("camera_movement", "camera_movement")):
            if data.get(k) and data[k] not in options.get(lst, []):
                raise ProposalError(f"“{data[k]}” não está na lista.")
        if data.get("camera") is not None:
            from app import scene3d

            try:
                scene3d.validate_camera(data["camera"])
            except scene3d.SceneError as exc:
                raise ProposalError(f"Câmara do plano: {exc}")
    elif fs.kind in ("new_season", "new_act", "new_episode"):
        if fs.kind == "new_episode" and not str(data.get("title", "")).strip():
            raise ProposalError("Episódio sem título.")
        if any(not isinstance(v, str) for v in data.values()):
            raise ProposalError("Os campos são texto.")
    elif fs.kind == "new_scene":
        if not str(data.get("title", "")).strip():
            raise ProposalError("Cena sem título.")
        if data.get("time_of_day") and data["time_of_day"] not in options.get("time_of_day", []):
            raise ProposalError(f"“{data['time_of_day']}” não está na lista das horas do dia.")
        loc = _asset_by_code(session, project, str(data.get("location") or ""))
        if data.get("location") and (loc is None or loc.kind != "location"):
            raise ProposalError(f"{data.get('location')} não é um local do projeto.")
    elif fs.kind == "new_look":
        if not str(data.get("name", "")).strip() or not str(data.get("description", "")).strip():
            raise ProposalError("Look sem nome ou sem roupa.")
        asset = session.get(Asset, int(key.split(":")[1]))
        from app import looks as L

        if asset is not None and L.by_name(session, asset, str(data["name"])):
            raise ProposalError(f"Já existe um look «{data['name']}»: altera esse.")
    elif fs.kind == "new_check":
        if data.get("category") not in CHECK_CATEGORIES or not str(data.get("text", "")).strip():
            raise ProposalError("Verificação sem categoria ou texto.")
    elif fs.kind == "shot_lines":
        if not isinstance(data.get("lines"), list) or not all(str(x).isdigit() for x in data["lines"]):
            raise ProposalError('Formato: {"lines": [1, 2]}.')
    return json.dumps(data, ensure_ascii=False)


def _set_lines_by_number(session: Session, shot: Shot, scene: Scene, numbers: list) -> None:
    """Linhas do guião pelo número (1 = a primeira linha da cena)."""
    from app.shot_script import set_shot_lines

    lines = session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order)).all()
    ids = [lines[int(n) - 1].id for n in numbers if str(n).isdigit() and 1 <= int(n) <= len(lines)]
    set_shot_lines(session, shot, ids)


def apply(session: Session, project: Project, key: str, value: str, context_scene_ids: list[int]) -> None:
    """Aplica uma proposta aceite. Revalida contra o estado atual."""
    from app.routes.assets import asset_code
    from app.routes.storyboard import create_shot

    if key.startswith("room_"):  # Sala de argumentistas
        from app.assistant import room_ctx

        try:
            room_ctx.apply(session, project, key, value)
        except ValueError as exc:
            raise ProposalError(str(exc))
        return
    if key == "new_asset":
        data = json.loads(value)
        label, kind = str(data["label"]).strip(), data["kind"]
        if kind not in ASSET_KINDS:
            raise ProposalError("Tipo de asset inválido.")
        asset = Asset(project_id=project.id, kind=kind, label=label, name=asset_code(session, project, label),
                      canonical_description=(str(data.get("description") or "").strip() or None))
        session.add(asset)
        session.flush()
        for code in data.get("scenes") or []:
            scene = _scene_by_code(session, project, str(code), context_scene_ids)
            if scene and not session.get(AssetSceneLink, (asset.id, scene.id)):
                session.add(AssetSceneLink(asset_id=asset.id, scene_id=scene.id))
        refs = [a.id for a in (_asset_by_code(session, project, str(c)) for c in data.get("refs") or []) if a]
        if refs:
            asset.sheet_refs = list(dict.fromkeys(refs))
            session.add(asset)
        return
    if key.startswith("new_look:"):  # look novo no guarda-roupa de uma personagem
        asset = session.get(Asset, int(key.split(":")[1]))
        if not asset or asset.project_id != project.id or asset.kind != "character":
            raise ProposalError("A personagem já não existe.")
        data = json.loads(value)
        count = len(session.exec(select(CharacterLook).where(CharacterLook.asset_id == asset.id)).all())
        look = CharacterLook(project_id=project.id, asset_id=asset.id, name=str(data["name"]).strip()[:60],
                             description=str(data["description"]).strip(), order=count)
        session.add(look)
        session.flush()
        for code in data.get("scenes") or []:
            scene = _scene_by_code(session, project, str(code), context_scene_ids)
            if scene:
                from app import looks as L

                L.set_scene_look(scene, asset, look)
                session.add(scene)
        return
    if key.startswith("scene_look:"):  # o look de uma personagem numa cena
        from app import looks as L

        _k, raw_scene, raw_asset = key.split(":")
        scene, asset = session.get(Scene, int(raw_scene)), session.get(Asset, int(raw_asset))
        if not scene or not asset or scene.project_id != project.id or asset.project_id != project.id:
            raise ProposalError("A cena ou a personagem já não existe.")
        look = None if value == "Base" else L.by_name(session, asset, value)
        if value != "Base" and look is None:
            raise ProposalError("Esse look já não existe.")
        L.set_scene_look(scene, asset, look)
        session.add(scene)
        return
    if key.startswith("new_lines:"):
        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        count = len(session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id)).all())
        for n, it in enumerate(json.loads(value)["lines"]):
            who = _asset_by_code(session, project, it.get("character") or "") if it["kind"] == "dialogue" else None
            session.add(SceneLine(scene_id=scene.id, order=count + n, kind=it["kind"], text=it["text"],
                                  character_id=who.id if who else None))
        return
    m_dur = re.fullmatch(r"shot:(\d+)\.duration", key)
    if m_dur:  # a duração do plano: os troços seguintes andam com a diferença
        from app.routes.storyboard import change_duration

        shot = session.get(Shot, int(m_dur.group(1)))
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        try:
            session.info["duration_notes"] = change_duration(session, shot, int(value))
        except ValueError as exc:
            raise ProposalError(str(exc))
        return
    if key.startswith("scene_duration:"):  # alonga (ou encurta) a encenação; as chaves de tempo ficam
        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id or not scene.stage3d:
            raise ProposalError("A cena já não existe (ou ainda não tem encenação).")
        stage = dict(scene.stage3d)
        stage["duration"] = float(value)
        scene.stage3d = stage
        session.add(scene)
        return
    m_inputs = re.fullmatch(r"shot:(\d+)\.video_inputs", key)
    if m_inputs:
        shot = session.get(Shot, int(m_inputs.group(1)))
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        data = json.loads(value)
        shot.video_refs, shot.video_inputs = data["video_refs"], data["video_inputs"]
        session.add(shot)
        return
    m_video = re.fullmatch(r"shot:(\d+)\.video_(model|resolution)", key)
    if m_video:  # modelo/resolução do plano («default» = o padrão do projeto)
        from app.routes.video import RESOLUTIONS, choose_model

        shot = session.get(Shot, int(m_video.group(1)))
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        if m_video.group(2) == "model":
            try:
                choose_model(session, shot, value)
            except ValueError as exc:
                raise ProposalError(str(exc))
        else:
            shot.video_resolution = None if value == "default" else value if value in RESOLUTIONS else shot.video_resolution
            session.add(shot)
        return
    if key == "project.image_resolution":
        project.image_resolution = value
        session.add(project)
        return
    if key in ("project.video_model", "project.video_resolution"):
        from app.routes.video import apply_project_default

        if key == "project.video_model":
            project.video_model = value
            session.add(project)
            session.flush()
            apply_project_default(session, project)
        else:
            project.video_resolution = value
            session.add(project)
        return
    if key.startswith("zonerefs:"):  # as referências visuais de uma zona da maquete (como o modal da app)
        _z, a_raw, zone = key.split(":", 2)
        asset = session.get(Asset, int(a_raw))
        if not asset or asset.project_id != project.id:
            raise ProposalError("O local já não existe.")
        refs = dict(asset.zone_refs or {})
        ids = json.loads(value)
        if ids:
            refs[zone] = ids
        else:
            refs.pop(zone, None)
        asset.zone_refs = refs or None
        session.add(asset)
        return
    if key.startswith("zonenote:"):  # descrição de uma divisão/zona da maquete de um local
        _, raw_id, zone = key.split(":", 2)
        asset = session.get(Asset, int(raw_id))
        if not asset or asset.project_id != project.id:
            raise ProposalError("O local já não existe.")
        notes = dict(asset.zone_notes or {})
        if value.strip():
            notes[zone] = value.strip()[:1500]
        else:
            notes.pop(zone, None)
        asset.zone_notes = notes or None
        session.add(asset)
        return
    if key.startswith("lvprompt:"):  # prompt de uma vista da maquete de um local
        from app.models import LocationView
        from app.prompt_versions import record

        view = session.get(LocationView, int(key.split(":")[1]))
        asset = session.get(Asset, view.asset_id) if view else None
        if not asset or asset.project_id != project.id:
            raise ProposalError("A vista já não existe.")
        view.prompt = value.strip() or None
        if view.prompt:
            record(session, project.id, key, view.prompt, "assistente")
        session.add(view)
        return
    if key.startswith("key_moment:"):  # de que momento da câmara se gera o frame-chave
        from app import keyframe

        shot = session.get(Shot, int(key.split(":")[1]))
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        keyframe.choose_moment(shot, int(value))
        session.add(shot)
        return
    if key.startswith("moment_view:"):  # a vista do local num momento da câmara: id, "auto" ou "nova"
        from app import moments

        _k, raw_shot, raw_n = key.split(":")
        shot = session.get(Shot, int(raw_shot))
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        if value == "nova":
            try:
                moments.create_view(session, shot, int(raw_n))
            except ValueError as exc:
                raise ProposalError(str(exc))
        else:
            moments.choose_view(shot, int(raw_n), int(value) if value.isdigit() else None)
        session.add(shot)
        return
    if key.startswith("sheet_refs:"):
        asset = session.get(Asset, int(key.split(":")[1]))
        if not asset or asset.project_id != project.id:
            raise ProposalError("O asset já não existe.")
        asset.sheet_refs = [a.id for a in (_asset_by_code(session, project, c) for c in json.loads(value)) if a] or None
        session.add(asset)
        return
    if key.startswith("asset_code:"):
        from app.routes.assets import rename_asset

        asset = session.get(Asset, int(key.split(":")[1]))
        if not asset or asset.project_id != project.id:
            raise ProposalError("O asset já não existe.")
        rename_asset(session, project, asset, value)  # pastas, ficheiros e o código em todos os textos do projeto
        return
    if key.startswith("asset_kind:"):
        asset = session.get(Asset, int(key.split(":")[1]))
        if not asset or asset.project_id != project.id:
            raise ProposalError("O asset já não existe.")
        if asset.kind == "location":  # deixa de poder ser usado onde o tipo antigo era exigido (como na app)
            session.exec(update(Scene).where(Scene.location_id == asset.id).values(location_id=None))
        if asset.kind == "character":
            session.exec(update(SceneLine).where(SceneLine.character_id == asset.id).values(character_id=None))
        asset.kind = value
        session.add(asset)
        return
    if key.startswith("script:"):
        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        set_script(session, project, scene, json.loads(value)["lines"])
        return
    if key.startswith("line_edit:"):
        line = session.get(SceneLine, int(key.split(":")[1]))
        scene = session.get(Scene, line.scene_id) if line else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("A linha já não existe.")
        data = json.loads(value)
        who = _asset_by_code(session, project, data.get("character") or "") if data["kind"] == "dialogue" else None
        line.kind, line.text, line.character_id = data["kind"], data["text"], who.id if who else None
        session.add(line)
        return
    if key.startswith("scene_location:"):
        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        loc = _asset_by_code(session, project, value) if value else None
        if value and (loc is None or loc.kind != "location"):
            raise ProposalError("Esse local não existe no projeto.")
        scene.location_id = loc.id if loc else None
        if loc and not session.get(AssetSceneLink, (loc.id, scene.id)):
            session.add(AssetSceneLink(asset_id=loc.id, scene_id=scene.id))  # o local principal conta como "aparece"
        session.add(scene)
        return
    if key.startswith("scene_order:"):
        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        siblings = list(session.exec(select(Scene).where(Scene.project_id == project.id, Scene.episode_id == scene.episode_id,
                                                         Scene.act_id == scene.act_id, Scene.id != scene.id).order_by(Scene.number)).all())
        siblings.insert(min(int(value) - 1, len(siblings)), scene)
        for n, sc in enumerate(siblings, start=1):
            if sc.number != n:
                sc.number = n
                session.add(sc)
        return
    if key.startswith("set3d_from:"):  # o local (com maquete) onde a encenação da cena se passa: "asset:<id>"
        from app.routes.storyboard import set_location

        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        set_location(session, project, scene, value.partition(":")[2])
        session.add(scene)
        return
    if key.startswith("stage3d:"):  # a encenação da cena
        from app import scene3d

        scene = session.get(Scene, int(key.split(":")[1]))
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        try:
            scene.stage3d = scene3d.validate_stage(json.loads(value), session, project.id, scene)
        except scene3d.SceneError as exc:
            raise ProposalError(str(exc))
        scene.cast3d = scene3d.scene_cast(session, scene) or None
        session.add(scene)
        return
    if key in ("new_season", "new_act") or key.startswith("new_episode:"):  # temporada, ato ou episódio novo (no fim)
        from sqlalchemy import func

        data = json.loads(value)
        text = {k: (str(data.get(k) or "").strip() or None) for k in ("title", "arc", "summary", "logline", "synopsis")}
        if key == "new_season":
            n = session.exec(select(func.max(Season.number)).where(Season.project_id == project.id)).one() or 0
            session.add(Season(project_id=project.id, number=n + 1, title=text["title"], arc=text["arc"], summary=text["summary"]))
        elif key == "new_act":
            n = session.exec(select(func.max(Act.number)).where(Act.project_id == project.id)).one() or 0
            session.add(Act(project_id=project.id, number=n + 1, title=text["title"], arc=text["arc"], summary=text["summary"]))
        else:
            season = session.get(Season, int(key.split(":")[1]))
            if not season or season.project_id != project.id:
                raise ProposalError("A temporada já não existe.")
            n = session.exec(select(func.max(Episode.number)).where(Episode.season_id == season.id)).one() or 0
            session.add(Episode(season_id=season.id, number=n + 1, title=text["title"], logline=text["logline"],
                                synopsis=text["synopsis"], target_duration_s=project.target_duration_s))  # herda o formato da série
        session.flush()
        return
    if key.startswith("new_scene:"):  # cena nova (no fim da curta, do episódio ou do ato)
        group = key.split(":", 1)[1]
        data = json.loads(value)
        episode = act = None
        if group.startswith("episode-") and group[8:].isdigit():
            episode = session.get(Episode, int(group[8:]))
            season = session.get(Season, episode.season_id) if episode else None
            if not season or season.project_id != project.id:
                raise ProposalError("O episódio já não existe.")
            where = Scene.episode_id == episode.id
        elif group.startswith("act-") and group[4:].isdigit():
            act = session.get(Act, int(group[4:]))
            if not act or act.project_id != project.id:
                raise ProposalError("O ato já não existe.")
            where = Scene.act_id == act.id
        else:
            where = (Scene.project_id == project.id) & Scene.episode_id.is_(None) & Scene.act_id.is_(None)
        from sqlalchemy import func

        number = (session.exec(select(func.max(Scene.number)).where(where)).one() or 0) + 1
        loc = _asset_by_code(session, project, str(data.get("location") or ""))
        scene = Scene(project_id=project.id, episode_id=episode.id if episode else None, act_id=act.id if act else None,
                      number=number, title=str(data["title"]).strip(),
                      description=(str(data.get("description") or "").strip() or None),
                      time_of_day=data.get("time_of_day") or None,
                      location_id=loc.id if loc is not None and loc.kind == "location" else None)
        session.add(scene)
        session.flush()
        if scene.location_id:
            session.add(AssetSceneLink(asset_id=scene.location_id, scene_id=scene.id))
        return
    if key.startswith("layout3dedit:"):
        from app import scene3d

        asset = session.get(Asset, int(key.split(":")[1]))
        if not asset or asset.project_id != project.id or asset.kind != "location":
            raise ProposalError("O local já não existe.")
        edit = json.loads(value)
        if edit.get("base") != scene3d.layout_digest(asset.layout3d):
            raise ProposalError("A maquete mudou desde esta proposta (outra alteração já foi aplicada). Pede ao "
                                "Assistente para a refazer sobre a maquete atual.")
        try:
            asset.layout3d = scene3d.apply_layout_edit(asset.layout3d, edit)
        except scene3d.SceneError as exc:
            raise ProposalError(str(exc))
        session.add(asset)
        return
    if key.startswith("layout3d:"):
        from app import scene3d

        asset = session.get(Asset, int(key.split(":")[1]))
        if not asset or asset.project_id != project.id or asset.kind != "location":
            raise ProposalError("O local já não existe.")
        try:
            asset.layout3d = scene3d.validate_set(json.loads(value))
        except scene3d.SceneError as exc:
            raise ProposalError(str(exc))
        session.add(asset)
        return
    if key.startswith("scene3d:"):
        from app import scene3d

        data = json.loads(value)
        try:
            shot = session.get(Shot, int(key.partition(":")[2]))
            scene = session.get(Scene, shot.scene_id) if shot else None
            if not scene or scene.project_id != project.id:
                raise ProposalError("O plano já não existe.")
            clean = scene3d.validate_camera(data)
            if not shot.duration_s:
                from app.routes.storyboard import shot_durations

                allowed = shot_durations(session, shot)
                last = max((k["t"] for k in clean["camera"]), default=5)
                shot.duration_s = min(allowed, key=lambda d: (abs(d - last), d))
            scene3d.set_camera(shot, clean)
            session.add(shot)
        except scene3d.SceneError as exc:
            raise ProposalError(str(exc))
        return
    if key.startswith("sheet_ref:"):
        asset = session.get(Asset, int(key.split(":")[1]))
        ref = _asset_by_code(session, project, value)
        if not asset or asset.project_id != project.id or not ref or ref.id == asset.id:
            raise ProposalError("O asset já não existe.")
        if ref.id not in (asset.sheet_refs or []):
            asset.sheet_refs = [*(asset.sheet_refs or []), ref.id]
            session.add(asset)
        return
    entity, obj_id, _attr = parse_key(key)
    if entity == "link":
        _l, a_raw, s_raw = key.split(":")
        asset, scene = session.get(Asset, int(a_raw)), session.get(Scene, int(s_raw))
        if not asset or not scene or asset.project_id != project.id or scene.project_id != project.id:
            raise ProposalError("O elemento já não existe.")
        existing = session.get(AssetSceneLink, (asset.id, scene.id))
        if value.strip() in ("1", "true", "sim") and not existing:
            session.add(AssetSceneLink(asset_id=asset.id, scene_id=scene.id))
        elif value.strip() in ("0", "false", "não") and existing:
            session.delete(existing)
        return
    if entity == "new_shot":
        scene = session.get(Scene, obj_id)
        if not scene or scene.project_id != project.id:
            raise ProposalError("A cena já não existe.")
        data = json.loads(value)
        shot = create_shot(session, scene)
        for k in ("title", "description", "shot_type", "camera_angle", "camera_movement"):
            if data.get(k):
                setattr(shot, k, str(data[k]).strip())
        session.flush()
        if isinstance(data.get("lines"), list):
            _set_lines_by_number(session, shot, scene, data["lines"])
            from app import scene3d as _s3

            t0 = _s3.start_for_lines(scene, [int(x) for x in data["lines"] if str(x).isdigit()])
            if t0 is not None:  # o troço do plano começa na primeira linha que cobre (a câmara pode dizer outro)
                shot.stage_start = round(t0, 2)
        if data.get("duration"):
            from app.routes.storyboard import shot_durations

            allowed = shot_durations(session, shot)
            shot.duration_s = min(allowed, key=lambda d: (abs(d - float(data["duration"])), d))
        if isinstance(data.get("camera"), dict):  # a câmara do plano sobre a encenação (start + posições)
            from app import scene3d

            try:
                scene3d.set_camera(shot, scene3d.validate_camera(data["camera"]))
            except scene3d.SceneError as exc:
                raise ProposalError(str(exc))
        elif data.get("start") is not None:
            shot.stage_start = max(0.0, float(data["start"]))
        session.add(shot)
        return
    if entity == "shot_lines":
        shot = session.get(Shot, obj_id)
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        _set_lines_by_number(session, shot, scene, json.loads(value).get("lines") or [])
        return
    if entity == "new_check":
        shot = session.get(Shot, obj_id)
        scene = session.get(Scene, shot.scene_id) if shot else None
        if not scene or scene.project_id != project.id:
            raise ProposalError("O plano já não existe.")
        data = json.loads(value)
        count = len(session.exec(select(ShotCheck).where(ShotCheck.shot_id == shot.id)).all())
        session.add(ShotCheck(shot_id=shot.id, order=count, category=data["category"], text=str(data["text"]).strip(),
                              note=(str(data.get("note") or "").strip() or None)))
        return
    obj, attr, kind, options = resolve(session, project, key)
    new_value = clean_value(kind, value, options)
    if kind == PROMPT:
        from app.prompt_versions import record

        # A página guarda as versões da capa como "project:<id>.cover_prompt"; aplicado do chat, fica como está (o Gerar
        # não o reescreve: ver prompt_targets.state).
        record(session, project.id, f"project:{project.id}.{attr}" if key.startswith("project.") else key, new_value, "assistente")
    setattr(obj, attr, new_value)
    if entity == "shot" and attr == "video_prompt":
        from app.routes.video import shot_model

        obj.video_prompt_model = (shot_model(session, obj) or {}).get("name")
    session.add(obj)


def set_script(session: Session, project: Project, scene: Scene, items: list[dict]) -> None:
    """O guião da cena passa a ser esta lista, por esta ordem. As linhas com "id" continuam a ser as mesmas (os planos que
    as cobrem e o segundo em que começam na encenação acompanham-nas); as sem "id" são novas; as que faltam apagam-se."""
    old = list(session.exec(select(SceneLine).where(SceneLine.scene_id == scene.id).order_by(SceneLine.order)).all())
    old_number = {ln.id: n for n, ln in enumerate(old, 1)}
    by_id = {ln.id: ln for ln in old}
    keep = {it["id"] for it in items if it.get("id")}
    for ln in old:
        if ln.id not in keep:
            session.delete(ln)  # as ligações aos planos vão em cascata
    session.flush()
    new_number: dict[int, int] = {}  # número antigo → número novo
    for n, it in enumerate(items, 1):
        who = _asset_by_code(session, project, it.get("character") or "") if it["kind"] == "dialogue" else None
        ln = by_id.get(it.get("id")) if it.get("id") else None
        if ln is None:
            ln = SceneLine(scene_id=scene.id, order=n - 1, kind=it["kind"], text=it["text"], character_id=who.id if who else None)
        else:
            new_number[old_number[ln.id]] = n
            ln.order, ln.kind, ln.text, ln.character_id = n - 1, it["kind"], it["text"], who.id if who else None
        session.add(ln)
    if scene.stage3d and scene.stage3d.get("lines"):  # os tempos da encenação seguem as linhas que ficam
        stage = dict(scene.stage3d)
        stage["lines"] = sorted(({"n": new_number[x["n"]], "t": x["t"]} for x in stage["lines"] if x["n"] in new_number),
                                key=lambda x: x["t"])
        scene.stage3d = stage
        session.add(scene)
