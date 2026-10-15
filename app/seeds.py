"""Valores iniciais. Só são inseridos se a chave ainda não existir, para não
sobrepor o que for editado em Configuração."""

from sqlmodel import Session, select

from app.models import GlobalConfig, OptionList

# Cada item: (rótulo PT, texto EN para os prompts ou None).
OPTION_LISTS: dict[str, list[tuple[str, str | None]]] = {
    "project_type": [("Curta / spot", None), ("Série", None), ("Filme", None)],
    "genre": [
        ("Aventura", "adventure"), ("Ação", "action"), ("Comédia", "comedy"), ("Drama", "drama"),
        ("Fantasia", "fantasy"), ("Ficção científica", "science fiction"), ("Mistério", "mystery"),
        ("Terror", "horror"), ("Documental", "documentary"), ("Publicidade", "commercial advertising"),
        ("Educativo", "educational"),
    ],
    "tone": [
        ("Leve", "light-hearted"), ("Divertido", "fun, playful"), ("Épico", "epic"), ("Tenso", "tense"),
        ("Sombrio", "dark, moody"), ("Emotivo", "emotional"), ("Inspirador", "inspiring, uplifting"),
        ("Informativo", "informative"),
    ],
    "audience": [
        ("Infantil", "for young children"), ("Juvenil", "for teenagers"),
        ("Geral", "for a general audience"), ("Adulto", "for adults"),
    ],
    "dialogue_language": [
        ("Português (PT)", "European Portuguese"), ("Português (BR)", "Brazilian Portuguese"),
        ("Inglês", "English"), ("Espanhol", "Spanish"), ("Sem diálogo", "no dialogue"),
    ],
    "render_style": [
        ("3D animado estilizado", "stylized 3D animation"), ("Fotorrealista", "photorealistic"),
        ("2D animado", "2D animation"), ("Stop-motion", "stop-motion animation"),
        ("Anime", "anime style"), ("Aguarela", "watercolor illustration"),
    ],
    "proportions": [
        ("Realistas", "realistic proportions"), ("Estilizadas", "stylized proportions"),
        ("Caricaturais", "caricatured proportions, oversized heads"),
    ],
    "palette_light": [
        ("Quente · tungsténio", "warm tungsten lighting"),
        ("Quente · tungsténio + crepúsculo frio", "warm tungsten practical lights mixed with a cool dusk fill"),
        ("Fria · azulada", "cool bluish lighting"),
        ("Natural · luz do dia", "natural daylight"),
        ("Hora dourada", "golden hour sunlight"),
        ("Crepúsculo · hora azul", "blue hour dusk light"),
        ("Noturna · néon", "night scene lit by neon signs"),
        ("Pastel suave", "soft pastel palette, gentle diffused light"),
        ("Alto contraste", "high-contrast lighting, deep shadows"),
        ("Monocromática", "monochrome palette"),
    ],
    "aspect_ratio": [
        ("9:16", "9:16 vertical frame"), ("16:9", "16:9 horizontal frame"), ("1:1", "1:1 square frame"),
    ],
    "target_duration": [(str(s), None) for s in (15, 30, 45, 60, 90, 120, 180)],
    "target_duration_long": [(str(m), None) for m in (1, 2, 3, 5, 7, 11, 15, 22, 30, 45, 60, 90, 120)],
    "time_of_day": [
        ("Madrugada", "before dawn"), ("Manhã", "morning"), ("Meio-dia", "midday"),
        ("Tarde", "afternoon"), ("Pôr do sol", "sunset"), ("Noite", "night"),
    ],
    "shot_type": [
        ("Plano geral", "wide establishing shot"),
        ("Plano de conjunto", "full shot, whole group visible"),
        ("Plano médio", "medium shot, framed from the waist up"),
        ("Plano americano", "medium-long shot, framed from the knees up"),
        ("Grande plano", "close-up, face fills the frame"),
        ("Plano de pormenor", "extreme close-up detail shot"),
        ("Plano sobre o ombro", "over-the-shoulder shot"),
        ("POV", "point-of-view shot"),
    ],
    "camera_angle": [
        ("Nível dos olhos", "eye-level"), ("Picado", "high angle, looking down"),
        ("Contrapicado", "low angle, looking up"), ("Zenital", "top-down overhead view"),
        ("Drone", "high aerial drone view"), ("Holandês", "dutch angle, tilted horizon"),
    ],
    "camera_movement": [
        ("Estática", "static camera"), ("Pan", "slow pan"), ("Tilt", "slow tilt"),
        ("Dolly in", "slow dolly in"), ("Dolly out", "slow dolly out"), ("Travelling", "tracking shot"),
        ("Grua", "crane shot"), ("Câmara ao ombro", "handheld camera"),
    ],
    "voice_type": [
        ("Grave", "deep voice"), ("Média", "mid-range voice"), ("Aguda", "high-pitched voice"),
        ("Infantil", "child's voice"), ("Idosa", "elderly voice"), ("Rouca", "raspy voice"),
    ],
    "timbre": [
        ("Quente", "warm tone"), ("Clara", "clear tone"), ("Aveludada", "velvety tone"),
        ("Metálica", "metallic tone"), ("Nasal", "nasal tone"), ("Sussurrada", "whispery tone"),
        ("Enérgica", "energetic delivery"), ("Calma", "calm delivery"),
    ],
}

DEFAULT_RULES = [
    {"text": "Acrescentar \"exactly N people, each appearing once\"", "active": True},
    {"text": "Olhares e posições em direções de ecrã (\"toward the right side of the frame\"), nunca "
             "esquerda/direita do veículo ou da personagem; em planos de grupo, \"nobody looks at the camera\"",
     "active": True},
    {"text": "Marcas distintivas com número e posição exatos", "active": True},
    {"text": "Tamanho e zona do enquadramento para objetos distantes", "active": True},
    {"text": "\"only one <objeto>\" quando houver risco de duplicados", "active": True},
    {"text": "Prompts de imagem descrevem um instante parado; movimento só no vídeo", "active": True},
    {"text": "Sem campo de prompt negativo: exclusões da bíblia no fim de cada prompt", "active": True},
]

# Guias do escritor de prompts (Configuração → Prompts): como escrever cada tipo de prompt. O escritor escreve o
# prompt completo; a app não lhe junta nada (só troca os marcadores @CÓDIGO pela sintaxe do modelo no envio).
DEFAULT_PROMPT_GUIDES = {
    "sheet_aspect": "16:9",
    "portrait_aspect": "1:1",
    "character": (
        "Folha de personagem (fundo neutro, várias vistas da mesma pessoa).\n"
        "1. Abre com \"Character sheet of <nome>:\" e o aspeto essencial da descrição canónica (idade, corpo, cara, "
        "cabelo, roupa, marcas com posição exata).\n"
        "2. \"Front, three-quarter, side and back views, full body, neutral pose, plain light-grey background, soft even "
        "studio light.\"\n"
        "3. Fotos de referência (se seguirem): a roupa e o estilo vêm das fotos; a cara e o corpo vêm da descrição. "
        "Outros assets como referência: diz o papel de cada um pelo marcador.\n"
        "4. O bloco de estilo da bíblia.\n"
        "5. Fecha com \"One person per view, the same person in all views.\" e as exclusões.\n"
        "Sem ambiente, sem luz de cena, sem o mundo."),
    "portrait": (
        "Retrato de personagem (cabeça e ombros). A folha aprovada segue como referência: a mesma pessoa.\n"
        "\"Head-and-shoulders portrait of <nome>, the same person as <marcador da folha> (face, hair, clothing):\" e o "
        "essencial da cara. \"Facing the camera, neutral expression, plain light-grey background, soft even studio "
        "light.\" Depois o bloco de estilo, \"Exactly one person.\" e as exclusões."),
    "prop": (
        "Folha de adereço. \"Prop reference of <nome>:\" e a forma, proporções, materiais e marcas da descrição. "
        "\"Front and three-quarter views, plain light-grey background, soft even studio light.\" Fotos de um objeto "
        "real: manter a forma, as proporções e os materiais. Bloco de estilo; \"Only one <objeto> per view.\"; "
        "exclusões."),
    "element": (
        "Folha de elemento do mundo (uma família de design: carros, ecrãs, drones…). \"World element design sheet of "
        "<nome>:\" e a linguagem de design da descrição. \"Several distinct variants of the same design family side by "
        "side, each fully visible, at the same scale, plain light-grey background, soft even studio light.\" Bloco de "
        "estilo; \"No people.\"; exclusões."),
    "view": (
        "Imagem de uma vista de um local, gerada a partir do render da maquete 3D desta posição de câmara (o marcador "
        "@<LOCAL>.r<id>; é a primeira imagem).\n"
        "1. Linha 1, sempre: \"Turn <marcador do render>, a rough grey block-out (low-detail 3D mock-up) of this exact "
        "camera position, into a real photograph: same framing, camera height, angle and lens; every building, wall, "
        "door, window and stair, and every object, stays where it is and about as big; each simple block becomes the "
        "real, fully detailed thing it stands for (real cars, real street furniture, real furniture), never a box.\"\n"
        "2. A referência larga de uma zona (tipo «referência») começa por \"Wide reference image of <zona> of "
        "<local>.\"; uma vista por \"<nome da vista> of <zona> of <local>, at eye level.\" (zonas ao ar livre: "
        "\"the outdoor area <zona> outside <local> (open air, no ceiling)\").\n"
        "3. O que esta vista mostra: segue a pista da maquete e o render (esquerda, centro, direita; perto, ao fundo), "
        "com materiais, cores e luz do «Como é» da zona; o aspeto das fotografias reais da zona descrito por palavras.\n"
        "4. A segunda imagem, se seguir: se for a referência aprovada desta mesma zona, \"<marcador> is the reference "
        "image of this same room: the same materials, colours, furniture and light.\"; se for de outra zona ou a imagem "
        "geral, só estilo (\"…only a style reference: materials, colours, lighting mood, era; do not copy its space, "
        "layout or furniture\"); numa zona ao ar livre com uma referência de interior, só a época, a paleta e o "
        "ambiente.\n"
        "5. As folhas de design da zona (se seguirem): \"<marcadores> are design sheets of things in this view: give "
        "those things exactly that design and look, never copy their layout, viewpoint or background; add nothing that "
        "is not in the render or in this description.\"\n"
        "6. \"Empty of people.\" Do mundo nos prompts, só o que se vê nesta vista. Bloco de estilo e exclusões no fim.\n"
        "Cada frase sobre imagens numa linha própria (os modelos que seguem só a geometria recebem o prompt sem essas "
        "linhas)."),
    "look": (
        "Folha de um look do guarda-roupa de uma personagem: a MESMA pessoa da folha aprovada (@<CÓDIGO>) e do retrato "
        "(@<CÓDIGO>.portrait), com outra roupa. Edita a partir da folha: mantém exatamente o rosto, o cabelo, o corpo, "
        "as proporções, as poses, o fundo e a disposição da folha; muda só a roupa, o calçado e os acessórios para os "
        "deste look (descrição do look, em pormenor: peças, cores, materiais, desgaste, onde fica cada acessório). "
        "Diz o que sai (\"replace the apron, waistcoat and shirt with …\"). Só a roupa do look, nada dos outros looks. "
        "Bloco de estilo da bíblia; \"No text, no labels.\"; exclusões."),
    "video": (
        "Prompt de vídeo de um plano: UM take contínuo, sem cortes. O modelo recebe as vistas do local (sem pessoas: o "
        "aspeto do sítio em cada momento), as folhas do elenco, as vozes de referência de quem fala e, quando o modelo "
        "aceita, o vídeo-guia @Video1 (previs cinzento com a câmara, o enquadramento, o ritmo e as posições de toda a "
        "gente).\n"
        "Monta o prompt POR SECÇÕES, nesta ordem, no FORMATO DO PERFIL do modelo deste plano (secções com etiqueta ou "
        "parágrafo; com ou sem linha de tempo; a forma das falas e do som):\n"
        "1. References — cada marcador com o seu papel, um por frase: as folhas (\"@PRJ_ANA is Ana, the bartender: "
        "keep her exact face, hair and clothes\"), as vistas do local (\"@PRJ_BAR.v20 shows how the street outside "
        "looks; it is a place along the camera path, not a separate shot\"), a voz (\"@PRJ_ANA.voice is Ana's "
        "voice\") e, com vídeo-guia, \"@Video1 = camera movement, framing, lens, timing and the positions of everyone "
        "only; never show its grey previs look\" (só isto: não fales de animação rígida — «stiff animation» chama a "
        "atenção do modelo para ela). Usa cada marcador que segue.\n"
        "2. Summary — uma frase: quem, onde, o que acontece, o género e a câmara (\"One single continuous shot: …\").\n"
        "3. Scene and look — o sítio, a hora, a luz (fonte, direção, cor), a paleta, a época e o estilo da bíblia; os "
        "traços-chave de cada personagem visível.\n"
        "4. Action — a ação pela ordem do previs, SEM contradizer o vídeo-guia (quem está de frente ou de costas, de "
        "que lado do ecrã e para onde anda: segue «O QUE O VÍDEO-GUIA MOSTRA EM CADA MOMENTO»), com a câmara em cada "
        "passo (enquadramento, movimento, para onde olha), cada personagem pelo marcador e cada vista do local quando a "
        "câmara lá passa; quem anda ou se mexe vai sempre \"moving with fluid, natural, realistic body motion\". Com linha de tempo (só se o perfil a pedir), cobre a duração toda em segundos inteiros e cada "
        "bloco continua o mesmo movimento (\"(same continuous take)\"); sem ela, liga os passos com \"then, without a "
        "cut…\".\n"
        "5. Dialogue — uma linha por fala, na forma do perfil: quem fala (marcador), o tom, o gesto, a fala entre aspas e "
        "lábios naturais; os outros de boca fechada. Com voz de referência: \"in exactly the voice of @<CÓDIGO>.voice\" "
        "(o áudio dá o timbre: não descrevas o timbre, a altura nem a idade da voz) e, se a voz da ficha tiver sotaque, "
        "junta o sotaque e a forma de falar com dois ou três traços concretos da ficha (\"speaking English with a strong "
        "European Portuguese accent: rolled r's, open vowels\"); sem sotaque na ficha, nada mais. Sem voz de referência, "
        "descreve a voz da ficha. Sem falas, omite a secção.\n"
        "6. Sound — o ambiente e os efeitos com nome (porta, passos, copos, rua), a música ou \"no background music\".\n"
        "7. Constraints — \"One continuous take, no cuts, no transitions\", o número exato de personagens do elenco em plano "
        "(a CONTAGEM do contexto: \"exactly N main characters, each appearing once\") e, com figurantes, que são pessoas "
        "genéricas que seguem o vídeo-guia, \"no subtitles, no text, no logos\" e as exclusões da bíblia. As negativas "
        "ficam só aqui; no resto descreve pela positiva.\n"
        "COM VÍDEO-GUIA (@Video1), O PROMPT É CURTO (150–250 palavras): a câmara, o enquadramento, o caminho e as posições "
        "vêm do vídeo, não do texto. References numa frase por imagem; Action em 2 a 4 frases — o que acontece, pela "
        "ordem, e quem está de frente ou de costas — sem descrever lentes, distâncias nem movimentos de câmara passo a "
        "passo; Scene and look em 2 a 3 frases. Mais texto não ajuda: dá ao modelo motivos para inventar planos.\n"
        "Concreto e no presente; o essencial primeiro em cada secção. Respeita o limite de palavras do perfil."),
    "key": (
        "Frame-chave de um plano: UMA fotografia do primeiro instante do take, com as personagens já nos sítios certos. "
        "Segue para o modelo de vídeo como referência (o vídeo começa desta composição): tem de parecer um fotograma "
        "real do filme.\n"
        "COM PREVIS (o render cinzento do momento, @P<nn>.m<n>, é a primeira imagem):\n"
        "1. Linha 1, sempre: \"Turn <marcador do previs>, a rough grey 3D previs of the first frame of this shot, into a "
        "real film still: exactly the same framing, camera height, angle and lens; every person stands exactly where "
        "their capsule figure is, at the same size, facing the same way (the dark visor marks the face); every wall, "
        "door, window, piece of furniture and object stays where it is and becomes the real, fully detailed thing.\"\n"
        "2. Quem é quem, uma frase por personagem, pela cor da cápsula (NO PREVIS, QUEM É QUEM): \"The <cor> figure is "
        "<marcador da folha>: exactly that face, hair, body and clothes\" (com o look da cena, a folha do look).\n"
        "3. O sítio: \"<marcador da vista> shows how this place really looks: the same materials, colours, furniture "
        "and light\" (se a vista seguir).\n"
        "4. O instante: o que cada um faz nesse segundo (pose, gesto, expressão, para onde olha), pela ação da cena e "
        "por NESSE INSTANTE; a luz da cena (fonte, direção, cor) e a hora.\n"
        "5. Figurantes (figuras cinzento-claro), se houver: pessoas comuns do sítio e da época, roupa variada e com cor.\n"
        "6. Bloco de estilo da bíblia; \"Exactly N people\" (o elenco em plano + figurantes); \"no capsules, no grey "
        "figures, no mannequins, no statues, no text\"; exclusões.\n"
        "SEM PREVIS: compõe a imagem pela descrição do plano (tipo de plano, ângulo, lente, quem está onde e de frente ou "
        "de costas), cada personagem pelo marcador da folha, o sítio pelo marcador da imagem do local, e os pontos 4 a "
        "6.\n"
        "Cada frase sobre imagens numa linha própria. Um instante parado: sem movimento descrito como sequência."),
    "cover": (
        "Capa do projeto (16:9, para o cartão da lista de projetos): uma imagem-chave que resume a ideia — as "
        "personagens principais (pelas folhas, cada uma pelo marcador) no local principal, numa composição de cartaz, "
        "com o tom e a logline. Um instante parado. Bloco de estilo; \"No text, no title, no logos.\"; exclusões."),
}
GUIDE_LABELS = {"character": "Folha de personagem", "portrait": "Retrato", "look": "Folha de look (guarda-roupa)", "prop": "Folha de adereço",
                "element": "Folha de elemento do mundo", "view": "Vistas da maquete (locais)",
                "video": "Vídeo de um plano", "key": "Frame-chave de um plano", "cover": "Capa do projeto"}


# Validações (docs/PROMPT_RULES.md, secção 5). Só avisam; nunca bloqueiam.
DEFAULT_PROMPT_CHECKS = {
    "image_max_words": 80,
    "reference_max_words": 120,
    "video_max_words": 100,
    "video_max_words_timed": 150,
    "motion_words": ["walks", "walking", "runs", "running", "pans", "panning", "zooms", "zooming", "zoom in",
                     "zoom out", "dolly", "dollies", "tracking", "tilts", "tilting", "orbits", "moves toward", "moving",
                     "flies", "flying", "spins", "turns around", "camera moves", "rushes"],
    "camera_words": ["static camera", "camera stays static", "fixed framing", "pan", "pans", "panning", "tilt",
                     "tilts", "tilting", "dolly in", "dolly out", "dollies", "dollying", "push in", "pushes in",
                     "pull out", "pulls out", "zoom", "zooms", "zooming", "tracking shot", "tracks", "crane",
                     "cranes", "orbit", "orbits", "orbiting", "handheld", "truck left", "truck right"],
    "fast_words": ["fast", "quickly", "rapid", "rapidly"],
    "empty_adjectives": ["beautiful", "stunning", "masterpiece", "8k", "4k", "high quality", "ultra detailed"],
    "format_words": ["9:16", "16:9", "1:1", "4:3", "vertical frame", "vertical format", "vertical video",
                     "horizontal frame", "portrait orientation", "landscape orientation", "480p", "720p", "1080p"],
}

GLOBAL_CONFIG: dict[str, object] = {
    "prompt_guides": DEFAULT_PROMPT_GUIDES,
    "prompt_checks": DEFAULT_PROMPT_CHECKS,
    "prompt_rules": DEFAULT_RULES,
    "prompt_max_chars": 2000,
    "prompt_warn_enabled": True,
    "option_list_labels": {},
    "assistant": {"model": "sonnet", "timeout_s": 300},
    "gpt_image": {"enabled": True, "timeout_s": 300},
    # Artlist por MCP: Seedream 5.0 Pro (grupo 513) a 1K; a ligação à conta faz-se na Configuração.
    "artlist": {"enabled": True, "model_group_id": 513, "model_name": "Seedream 5.0 Pro", "quality": "1k", "timeout_s": 300},
}


def run_seeds(session: Session) -> None:
    rows = session.exec(select(OptionList)).all()
    existing_keys = {r.key for r in rows}
    for key, items in OPTION_LISTS.items():
        if key in existing_keys:
            continue
        for i, (value, prompt_text) in enumerate(items):
            session.add(OptionList(key=key, value=value, prompt_text=prompt_text, order=i))

    # Preenche o texto EN dos itens originais que ainda não o têm.
    defaults = {(k, v): p for k, items in OPTION_LISTS.items() for v, p in items}
    for row in rows:
        if row.prompt_text is None and defaults.get((row.key, row.value)):
            row.prompt_text = defaults[(row.key, row.value)]
            session.add(row)

    existing_cfg = set(session.exec(select(GlobalConfig.key)).all())
    for key, value in GLOBAL_CONFIG.items():
        if key not in existing_cfg:
            session.add(GlobalConfig(key=key, value=value))

    session.commit()
