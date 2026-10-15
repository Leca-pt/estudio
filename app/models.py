from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampedModel(SQLModel):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=utcnow, nullable=False, sa_column_kwargs={"onupdate": utcnow}
    )


PROJECT_TYPES = {"short": "Curta / spot", "series": "Série", "film": "Filme"}
ASSET_KINDS = {"character": "Personagem", "location": "Local", "prop": "Adereço", "element": "Elemento do mundo"}
ASSET_KINDS_PLURAL = {"character": "Personagens", "location": "Locais", "prop": "Adereços", "element": "Elementos do mundo"}
MEDIA_KINDS = ("image", "video")
MEDIA_STATUSES = ("pending", "approved", "rejected")


class Project(TimestampedModel, table=True):
    name: str
    prefix: str = Field(unique=True, index=True)
    type: str  # short | series | film
    # Projeto de origem (ex.: um spot da série): a bíblia visual vem dele e os assets podem ser importados dele.
    parent_id: Optional[int] = Field(default=None, foreign_key="project.id", ondelete="SET NULL")

    # Identidade
    genre: Optional[str] = None
    tone: Optional[str] = None
    audience: Optional[str] = None
    dialogue_language: Optional[str] = None
    references: Optional[str] = None

    # Estilo visual
    render_style: Optional[str] = None
    proportions: Optional[str] = None
    palette_light: Optional[str] = None
    style_block: Optional[str] = None

    # Formato
    aspect_ratio: Optional[str] = None
    target_duration_s: Optional[int] = None
    video_model: Optional[str] = None  # modelo de vídeo padrão do projeto (nome); None = o da Configuração
    video_resolution: Optional[str] = None  # 480p | 720p | 1080p; None = a da Configuração
    image_resolution: Optional[str] = "2K"  # 1K | 2K | 4K: as imagens geradas (cada modelo usa a mais próxima que aceita)

    # Regras fixas
    exclusions: Optional[str] = None
    project_rules: Optional[str] = None

    # História
    world: Optional[str] = None  # o mundo (worldbuilding): o que é verdade neste mundo (PT); não entra nos prompts
    world_prompt: Optional[str] = None  # o mundo nos prompts (EN, curto): o que se vê; entra nos prompts de ambiente
    premise: Optional[str] = None  # a ideia de partida, em texto livre (PT); não entra nos prompts
    logline: Optional[str] = None
    synopsis: Optional[str] = None

    # Capa (imagem do cartão na lista de projetos)
    cover_media_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    cover_prompt: Optional[str] = None  # EN; escrito pelo escritor de prompts (ou editado à mão)
    archived_at: Optional[datetime] = None  # arquivado: sai da lista e do seletor (fica em «Arquivados»)


class Season(TimestampedModel, table=True):
    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    number: int
    title: Optional[str] = None
    planned_episodes: Optional[int] = None
    arc: Optional[str] = None
    summary: Optional[str] = None


class Act(TimestampedModel, table=True):
    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    number: int
    title: Optional[str] = None
    arc: Optional[str] = None
    summary: Optional[str] = None


class Episode(TimestampedModel, table=True):
    season_id: int = Field(foreign_key="season.id", ondelete="CASCADE", index=True)
    number: int
    title: Optional[str] = None
    logline: Optional[str] = None
    synopsis: Optional[str] = None
    target_duration_s: Optional[int] = None


class AssetSceneLink(SQLModel, table=True):
    asset_id: int = Field(foreign_key="asset.id", ondelete="CASCADE", primary_key=True)
    scene_id: int = Field(foreign_key="scene.id", ondelete="CASCADE", primary_key=True)


class ShotLineLink(SQLModel, table=True):
    """Guião marcado: as linhas do guião da cena que um plano cobre (uma linha pode estar em vários planos)."""

    shot_id: int = Field(foreign_key="shot.id", ondelete="CASCADE", primary_key=True)
    line_id: int = Field(foreign_key="sceneline.id", ondelete="CASCADE", primary_key=True)


CHECK_CATEGORIES = {"props": "Adereços", "geography": "Geografia", "gazes": "Olhares"}


class ShotCheck(TimestampedModel, table=True):
    """Checklist de continuidade do plano."""

    shot_id: int = Field(foreign_key="shot.id", ondelete="CASCADE", index=True)
    order: int = 0
    category: str = "props"  # props | geography | gazes
    text: str = ""
    note: Optional[str] = None
    checked: bool = False


class Scene(TimestampedModel, table=True):
    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    episode_id: Optional[int] = Field(default=None, foreign_key="episode.id", ondelete="CASCADE", index=True)
    act_id: Optional[int] = Field(default=None, foreign_key="act.id", ondelete="CASCADE", index=True)
    number: int
    title: Optional[str] = None
    location_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="SET NULL")
    time_of_day: Optional[str] = None
    description: Optional[str] = None  # o que acontece na cena (PT), antes de a partir em ações e diálogos

    # Storyboard
    blocking: Optional[str] = None
    looks: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # guarda-roupa: {"<asset_id>": <look_id>}
    # Encenação 3D da cena (app/scene3d.py): na maquete do local (set3d_asset_id), quem está onde e o que se mexe ao
    # longo de toda a cena ({"duration", "objects", "actors", "lines"}); o elenco tem uma cor cada (cast3d). Os planos
    # são câmaras sobre esta encenação, cada um num troço de tempo.
    set3d_asset_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="SET NULL")
    stage3d: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    cast3d: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # {código do asset: cor}


SCENE_LINE_KINDS = {"action": "Ação", "dialogue": "Diálogo"}


class SceneLine(TimestampedModel, table=True):
    """Guião da cena: linhas de ação ou diálogo (com personagem), por ordem."""

    scene_id: int = Field(foreign_key="scene.id", ondelete="CASCADE", index=True)
    order: int = 0
    kind: str = "action"  # action | dialogue
    character_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="SET NULL")
    text: str = ""


class Asset(TimestampedModel, table=True):
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    kind: str  # character | location | prop | element
    name: str  # código, sempre com prefixo (ex.: AVT_VERA); fixo depois de criado
    label: str = ""  # nome visível (ex.: Rita)
    group_name: Optional[str] = None  # grupo criado pelo utilizador (ex.: Cidade, Equipa) para organizar a grelha
    canonical_description: Optional[str] = None
    profile: Optional[str] = None  # só personagens: quem é, passado, objetivo, forças e fraquezas (PT); não entra nos prompts
    voice_type: Optional[str] = None  # só personagens (lista voice_type)
    voice_timbre: Optional[str] = None  # só personagens (lista timbre)
    voice: Optional[str] = None  # descrição da voz para o prompt (EN)
    key_features: Optional[str] = None  # só personagens: features-chave para o vídeo (EN, uma frase curta)
    sheet_prompt: Optional[str] = None  # EN; escrito pelo escritor de prompts (ou editado à mão); None = ainda por escrever
    approved_image_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    # Só personagens: retrato (cabeça e ombros), referência de cara separada da folha de vistas.
    portrait_prompt: Optional[str] = None  # EN; escrito pelo escritor de prompts (ou editado à mão)
    portrait_image_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    # Só personagens: voz de referência (áudio aprovado), que segue nos pedidos de vídeo dos planos onde a personagem fala.
    voice_ref_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    # modelo 3D importado (GLB, MediaFile role "model3d"): nas maquetes desenha-se esta forma, em cinzento
    model3d_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    voice_note: Optional[str] = None  # EN; como fala (ex.: "speaking English with a clear Spanish accent"): vai com a voz em cada fala
    voice_sample: Optional[str] = None  # só personagens: o que a voz diz ao gerar a voz de referência (língua dos diálogos)
    layout3d: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # só locais: a maquete 3D (zonas, peças, câmaras das vistas)
    zone_notes: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # só locais com maquete: {zona: descrição (EN)}
    zone_refs: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # só locais com maquete: {zona: [ids de imagens]}
    # Importado de outro projeto (cópia): o asset de origem e o que já foi copiado dele ({"media": {id origem: id
    # cópia}, "views": {…}, "looks": {…}}), para «Atualizar a partir do original» não duplicar ficheiros.
    source_asset_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="SET NULL")
    source_map: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # Outros assets cujas imagens aprovadas vão como referência ao gerar a folha (ids de Asset, por ordem).
    sheet_refs: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))

    def ref_image_ids(self) -> list[int]:
        """Imagens aprovadas deste asset que servem de referência: folha e, nas personagens, retrato."""
        return [i for i in (self.approved_image_id, self.portrait_image_id if self.kind == "character" else None) if i]


class Shot(TimestampedModel, table=True):
    """Um plano = um take (vídeo contínuo): uma câmara sobre a encenação 3D da cena, num troço de tempo dela (do
    segundo "start" durante duration_s). Em cada momento da câmara, a vista do local mais parecida (ou uma criada a
    partir dessa câmara) dá o aspeto do sítio ao vídeo, com as folhas de quem aparece e o vídeo-guia."""

    scene_id: int = Field(foreign_key="scene.id", ondelete="CASCADE", index=True)
    number: int
    title: Optional[str] = None  # nome do plano (ex.: "Flyby até à rua")
    description: Optional[str] = None  # PT: o que acontece no plano, do início ao fim
    shot_type: Optional[str] = None
    camera_angle: Optional[str] = None
    camera_movement: Optional[str] = None
    # A câmara do plano sobre a encenação da cena: {"camera": [posições com "t" desde o início do plano]}; o plano
    # mostra a encenação de stage_start a stage_start + duration_s (app/scene3d.py)
    scene3d: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    stage_start: float = 0
    guide_video_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")  # vídeo-guia aprovado
    # Momentos da câmara (app/moments.py): {"renders": {"<n>": id do render}, "hash": versão da cena dos renders,
    # "views": {"<n>": id da LocationView escolhida à mão}}
    moments: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Vídeo: som
    dialogue: Optional[str] = None  # fala copiada do guião
    dialogue_character_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="SET NULL")
    sound_effects: Optional[str] = None  # EN
    ambience: Optional[str] = None  # EN
    music: Optional[str] = None  # EN

    # Vídeo: definições e resultado
    video_prompt: Optional[str] = None  # EN; escrito pelo escritor de prompts (ou editado à mão)
    video_refs: Optional[list] = Field(default=None, sa_column=Column(JSON))  # imagens escolhidas; None = automático
    # o que segue no pedido além das imagens (app/video_inputs.py): {"guide": false, "voices_off": [ids],
    # "modes": {"<id>": "start_frame" | "end_frame"}}; None = tudo como a app escolhe
    video_inputs: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    model: Optional[str] = None  # modelo escolhido no plano (nome); None = o padrão do projeto
    video_resolution: Optional[str] = None  # escolhida no plano; None = a padrão do projeto
    video_prompt_model: Optional[str] = None  # o modelo para que o escritor escreveu o prompt de vídeo
    duration_s: Optional[int] = None
    approved_clip_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    # Frame-chave (app/keyframe.py): a imagem do início do take com as personagens no sítio, gerada a partir do previs
    # do primeiro momento; aprovada, segue como referência no pedido de vídeo
    key_prompt: Optional[str] = None  # EN; escrito pelo escritor de prompts (ou editado à mão)
    key_image_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")


class CharacterLook(TimestampedModel, table=True):
    """Um look do guarda-roupa de uma personagem (roupa e acessórios). A folha do look parte da folha aprovada da
    personagem (o rosto, o corpo e o cabelo ficam; muda a roupa). Escolhe-se por cena (Scene.looks); sem escolha, vale
    a folha da personagem."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    asset_id: int = Field(foreign_key="asset.id", ondelete="CASCADE", index=True)
    name: str = ""  # PT, ex.: "Rua", "Pijama"
    description: Optional[str] = None  # EN: a roupa e os acessórios
    prompt: Optional[str] = None  # EN; o prompt da folha do look (escritor de prompts)
    image_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    order: int = 0


class LocationView(TimestampedModel, table=True):
    """Vista de um local com Maquete 3D: uma posição de câmara na maquete (cam = {pos, look_at, lens}), numa divisão.
    kind "ref" = a imagem de referência da divisão (a "folha" dela); "view" = uma vista. O render da posição dá a
    geometria; a imagem gerada (image_id) parte do render e da referência da divisão (ou da folha do local)."""

    asset_id: int = Field(foreign_key="asset.id", ondelete="CASCADE", index=True)
    zone: Optional[str] = None  # nome da divisão (None = o espaço todo)
    kind: str = "view"  # ref | view
    order: int = 0
    label: str = ""
    cam: dict = Field(sa_column=Column(JSON, nullable=False))
    render_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    render_hash: Optional[str] = None  # versão da maquete em que o render foi tirado
    image_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    prompt: Optional[str] = None  # EN; escrito pelo escritor de prompts (ou editado à mão)


class MediaFile(TimestampedModel, table=True):
    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    path: str
    kind: str  # image | video | audio
    role: str = ""  # sheet | portrait | look (folha de um look) | voice (áudio) | photo | locrender/locdepth/locview (vistas da maquete) | render (momento de um plano) | preview3d | guide (vídeo-guia) | clip | frame | cover
    source: str = "upload"  # upload | gpt-image | artlist
    status: str = "pending"  # pending | approved | rejected
    duration_ms: Optional[int] = None  # vídeos e áudios

    # Alvo (apenas um preenchido)
    asset_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="CASCADE", index=True)
    scene_id: Optional[int] = Field(default=None, foreign_key="scene.id", ondelete="CASCADE", index=True)
    shot_id: Optional[int] = Field(default=None, foreign_key="shot.id", ondelete="CASCADE", index=True)
    locview_id: Optional[int] = Field(default=None, foreign_key="locationview.id", ondelete="CASCADE")
    look_id: Optional[int] = Field(default=None, foreign_key="characterlook.id", ondelete="CASCADE")


class OptionList(TimestampedModel, table=True):
    key: str = Field(index=True)
    value: str  # rótulo (PT) que aparece nos drop-downs e fica gravado nos campos
    prompt_text: Optional[str] = None  # texto (EN) que entra nos prompts
    order: int = 0


class GlobalConfig(TimestampedModel, table=True):
    key: str = Field(unique=True, index=True)
    value: Any = Field(sa_column=Column(JSON, nullable=False))


class Credential(TimestampedModel, table=True):
    """Credencial do único utilizador. A password inicial vem do .env; depois de
    alterada, o hash novo fica aqui."""

    email: str
    password_hash: str
    must_change_password: bool = True
    session_version: int = 1


class ChatMessage(TimestampedModel, table=True):
    """Histórico do Assistente, por projeto. context_key identifica o ecrã (ex.: sb_shot:3)."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    context_key: str = Field(index=True)
    context_label: str = ""
    role: str  # user | assistant | error
    text: str = ""


class ChatProposal(TimestampedModel, table=True):
    """Proposta do Assistente para um campo concreto. Só é gravada com "Aplicar"."""

    message_id: int = Field(foreign_key="chatmessage.id", ondelete="CASCADE", index=True)
    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    field_key: str  # ex.: project.logline, shot:3.title, new_asset
    field_label: str = ""
    value: str = ""
    old_value: Optional[str] = None
    reason: Optional[str] = None
    status: str = "pending"  # pending | applied | ignored | failed


class PromptVersion(TimestampedModel, table=True):
    """Versões de um prompt editado (corrigir uma coisa de cada vez e poder voltar atrás)."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    field_key: str = Field(index=True)  # ex.: shot:3.video_prompt
    text: str
    source: str = "manual"  # manual | assistente
    basis: Optional[str] = None  # só do Assistente: impressão digital dos dados com que escreveu


class PromptTask(TimestampedModel, table=True):
    """O escritor de prompts a trabalhar num alvo (kind/ref, ver app/prompt_targets.py). status: running | failed |
    done. generate = o pedido de geração a fazer logo a seguir ({"gen": "<modelo>", "audio": bool}). Na revisão de um
    prompt (mini chat), request = {"prompt", "instruction"} e result = {"prompt", "reply"}."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    kind: str
    ref: str
    status: str = "running"
    model: str = ""
    generate: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    request: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    error: Optional[str] = None


IMAGE_JOB_ROLES = ("sheet", "portrait", "look", "locview", "key", "cover", "clip", "voice")


class ImageJob(TimestampedModel, table=True):
    """Geração ou edição de uma imagem com o GPT Image (Codex CLI), em segundo plano."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    role: str  # sheet | portrait | locview | cover | clip | voice
    asset_id: Optional[int] = Field(default=None, foreign_key="asset.id", ondelete="CASCADE")
    scene_id: Optional[int] = Field(default=None, foreign_key="scene.id", ondelete="CASCADE")
    shot_id: Optional[int] = Field(default=None, foreign_key="shot.id", ondelete="CASCADE")
    locview_id: Optional[int] = Field(default=None, foreign_key="locationview.id", ondelete="CASCADE")
    look_id: Optional[int] = Field(default=None, foreign_key="characterlook.id", ondelete="CASCADE")
    kind: str = "generate"  # generate | edit | video_refs | video_frames
    provider: str = "gpt"  # gpt (Codex, subscrição ChatGPT) | artlist (MCP, créditos Artlist)
    confirm_cost: bool = False  # o utilizador confirmou o custo pedido pelo Artlist
    credits: Optional[int] = None  # créditos Artlist gastos, quando indicados
    model_group_id: Optional[int] = None  # Artlist: modelo escolhido no botão Gerar (None = o da Configuração)
    stage: Optional[str] = None  # em que ponto está (passo, fila do fornecedor): mostrado no cartão "A gerar…"
    model: Optional[str] = None  # fornecedores por chave de API (fal…): id do modelo, ex.: "fal-ai/flux-pro/v1/depth"
    params: Optional[dict] = Field(default=None, sa_column=Column(JSON))  # ex.: voz: {"voice", "description"}
    duration_s: Optional[int] = None  # só vídeo (role "clip"; kind "video_frames" ou "video_refs")
    audio: bool = True  # só vídeo: gerar som
    prompt: str
    aspect_ratio: Optional[str] = None
    inputs: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))  # ids de MediaFile
    status: str = "queued"  # queued | running | done | failed
    error: Optional[str] = None
    result_media_id: Optional[int] = Field(default=None, foreign_key="mediafile.id", ondelete="SET NULL")
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class McpClient(TimestampedModel, table=True):
    """Um cliente MCP registado (ex.: o conector do Claude): os dados do registo OAuth tal como chegaram."""

    client_id: str = Field(index=True, unique=True)
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))


class McpGrant(TimestampedModel, table=True):
    """Autorizações do conector MCP: pedidos à espera de confirmação, códigos e tokens (guardados só como hash)."""

    kind: str = Field(index=True)  # request | code | access | refresh
    key_hash: str = Field(index=True, unique=True)
    client_id: str = Field(index=True)
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    expires_at: float = 0


class TrashItem(TimestampedModel, table=True):
    """Reciclagem: uma cena ou um plano apagado (pelo conector MCP), com tudo o que é preciso para o recuperar."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    kind: str  # scene | shot
    label: str = ""
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    expires_at: float = 0


class CharacterRegistration(TimestampedModel, table=True):
    """Registo de uma imagem de uma personagem (folha, retrato, look) na biblioteca de assets da ByteDance (BytePlus
    ModelArk): o Seedance aceita-a como asset://<remote_id> (as imagens com caras realistas enviadas por URL são
    recusadas). Um grupo de assets por personagem (group_id)."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    asset_id: int = Field(foreign_key="asset.id", ondelete="CASCADE", index=True)  # a personagem
    media_id: int = Field(foreign_key="mediafile.id", ondelete="CASCADE", index=True)
    provider: str = "byteplus"
    group_id: str = ""
    remote_id: str = ""
    status: str = "processing"  # processing | active | failed
    error: Optional[str] = None


class WritingFile(TimestampedModel, table=True):
    """Sala de argumentistas: um ficheiro de texto do projeto (sinopse, arco, escaleta, guião de um episódio, notas…),
    escrito pelo utilizador e pelas IAs ligadas ao conector MCP. Em escrita (draft) qualquer um o muda (cada mudança é
    uma versão); aprovado, o conteúdo passou para a app (target) e fica trancado até o utilizador o destrancar."""

    __table_args__ = (UniqueConstraint("project_id", "path"),)

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    path: str  # ex.: "Temporada 1/E01/guiao.md"
    content: str = ""
    version: int = 1
    status: str = "draft"  # draft | approved
    target: Optional[str] = None  # para onde vai ao aprovar (app/writing.py: TARGETS), ex.: "scenes:episode:3"
    approved_version: Optional[int] = None
    approved_at: Optional[datetime] = None
    last_author: str = ""
    deleted_at: Optional[datetime] = None  # apagado: fica em «Apagados» (o caminho passa a .apagados/<id>/…) até ser reposto
    deleted_by: Optional[str] = None


class WritingVersion(TimestampedModel, table=True):
    """Uma versão de um ficheiro da sala (o texto inteiro), com quem a fez e porquê."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    file_id: int = Field(foreign_key="writingfile.id", ondelete="CASCADE", index=True)
    version: int
    content: str = ""
    author: str = ""
    message: str = ""


class WritingProposal(TimestampedModel, table=True):
    """Uma proposta de alteração a um ficheiro da sala: o texto proposto (inteiro), feito sobre uma versão (base_version),
    com a justificação, os impactos na continuidade e o que precisa da decisão do autor. Os outros aprovam ou contestam
    (comentários com verdict); aplicar funde-a no ficheiro (fusão a três: base, atual, proposta)."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    file_id: int = Field(foreign_key="writingfile.id", ondelete="CASCADE", index=True)
    number: int  # #1, #2… por projeto
    title: str = ""
    rationale: str = ""  # porquê
    impacts: str = ""  # impactos na continuidade
    decisions: str = ""  # pontos que precisam da decisão do autor (o utilizador)
    content: str = ""
    base_version: int = 1
    revision: int = 1
    author: str = ""
    status: str = "open"  # open | applied | rejected | withdrawn
    alternative_to: Optional[int] = Field(default=None, foreign_key="writingproposal.id", ondelete="SET NULL")
    applied_version: Optional[int] = None
    closed_by: Optional[str] = None
    closed_at: Optional[datetime] = None


class WritingTask(TimestampedModel, table=True):
    """Fila de tarefas editoriais da sala (ex.: «rever o episódio 3»), com responsável, estado e resultado."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    number: int
    title: str = ""
    details: str = ""
    assignee: Optional[str] = None  # None = qualquer um
    status: str = "open"  # open | doing | done | cancelled
    result: str = ""
    created_by: str = ""
    file_id: Optional[int] = Field(default=None, foreign_key="writingfile.id", ondelete="SET NULL")
    proposal_id: Optional[int] = Field(default=None, foreign_key="writingproposal.id", ondelete="SET NULL")
    done_at: Optional[datetime] = None


class WritingComment(TimestampedModel, table=True):
    """A discussão da sala: sobre um ficheiro, uma proposta, uma tarefa ou geral; respostas encadeadas (parent_id),
    menções (@Claude, @ChatGPT, @<utilizador>) e, nas propostas, o parecer (verdict) sobre uma revisão."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    file_id: Optional[int] = Field(default=None, foreign_key="writingfile.id", ondelete="CASCADE", index=True)
    version: Optional[int] = None  # a versão do ficheiro de que se fala
    author: str = ""
    text: str = ""
    parent_id: Optional[int] = Field(default=None, foreign_key="writingcomment.id", ondelete="SET NULL")
    proposal_id: Optional[int] = Field(default=None, foreign_key="writingproposal.id", ondelete="CASCADE", index=True)
    task_id: Optional[int] = Field(default=None, foreign_key="writingtask.id", ondelete="CASCADE", index=True)
    mentions: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))
    verdict: Optional[str] = None  # approve | contest (pareceres sobre propostas)
    revision: Optional[int] = None  # a revisão da proposta a que o parecer se refere


class WritingNotice(TimestampedModel, table=True):
    """Notificações da sala para cada participante (menções, respostas, propostas, pareceres, tarefas)."""

    project_id: int = Field(foreign_key="project.id", ondelete="CASCADE", index=True)
    recipient: str = Field(index=True)
    kind: str = ""  # mention | reply | proposal | review | applied | task | comment
    text: str = ""
    author: str = ""
    file_id: Optional[int] = Field(default=None, foreign_key="writingfile.id", ondelete="CASCADE")
    proposal_id: Optional[int] = Field(default=None, foreign_key="writingproposal.id", ondelete="CASCADE")
    task_id: Optional[int] = Field(default=None, foreign_key="writingtask.id", ondelete="CASCADE")
    comment_id: Optional[int] = Field(default=None, foreign_key="writingcomment.id", ondelete="CASCADE")
    read: bool = False


# ── Numeração dos planos pela ordem na encenação ─────────────────────────────
# P01, P02… seguem o início do troço de cada plano na encenação da cena (stage_start); em empate, o id. Sempre que um
# plano é criado, apagado ou muda de início, a cena é renumerada antes de gravar. O número é só a ordem: os ids (e os
# ficheiros, takes e referências, ligados ao id) não mudam.
from sqlalchemy import event as _event  # noqa: E402
from sqlalchemy.orm import Session as _OrmSession  # noqa: E402


def order_shots(shots: list) -> list:
    return sorted(shots, key=lambda x: (float(x.stage_start or 0), x.id if x.id is not None else 10**12))


@_event.listens_for(_OrmSession, "before_flush")
def _renumber_shots(session, _ctx, _instances) -> None:
    touched = [o for o in list(session.new) + list(session.dirty) + list(session.deleted) if isinstance(o, Shot)]
    scenes = {o.scene_id for o in touched if o.scene_id}
    if not scenes:
        return
    gone = {id(o) for o in session.deleted if isinstance(o, Shot)}
    from sqlmodel import select as _select

    with session.no_autoflush:
        for sid in scenes:
            have = {id(x): x for x in session.exec(_select(Shot).where(Shot.scene_id == sid)).all()}
            for o in session.new:
                if isinstance(o, Shot) and o.scene_id == sid:
                    have[id(o)] = o
            for n, x in enumerate(order_shots([x for k, x in have.items() if k not in gone]), 1):
                if x.number != n:
                    x.number = n


PLAN_KINDS = {"episode": "Episódio", "script": "Guiões", "short": "Short", "post": "Publicação", "other": "Outro"}
PLAN_STATUS = {"todo": "Por fazer", "doing": "Em curso", "done": "Feito"}


class PlanItem(TimestampedModel, table=True):
    """Agenda: um bloco de trabalho (ex.: «E04» de 1 a 15/11) ou um dia (ex.: «Postar Short 12» a 4/11), de qualquer
    projeto ou de nenhum. start = quando começa (vazio = só o dia do prazo); due = o prazo / o dia."""

    project_id: Optional[int] = Field(default=None, foreign_key="project.id", ondelete="SET NULL", index=True)
    title: str
    kind: str = "other"  # PLAN_KINDS
    start: Optional[date] = None
    due: date
    status: str = "todo"  # PLAN_STATUS
    done_at: Optional[datetime] = None
    notes: Optional[str] = None
    link: Optional[str] = None  # caminho na app (ex.: /projetos/2/historia/episodios/3)
    position: int = 0  # ordem no quadro
    recurrence_id: Optional[int] = Field(default=None, foreign_key="planrecurrence.id", ondelete="SET NULL", index=True)


class PlanRecurrence(TimestampedModel, table=True):
    """Publicações recorrentes (ex.: um Short às terças e quintas): criam itens de um dia, numerados."""

    project_id: Optional[int] = Field(default=None, foreign_key="project.id", ondelete="SET NULL")
    title: str  # com {n} para o número (ex.: «Postar Short {n}»)
    kind: str = "post"
    weekdays: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]"))  # 0 = segunda
    start: date
    end: date
    first_number: int = 1
