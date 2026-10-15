# Estúdio — app de produção de vídeo IA

Especificação para construir a app no VPS. Lê este ficheiro todo antes de começar.

## Regras de trabalho (obrigatórias)

- Trabalha **uma fase de cada vez**. No fim de cada fase, pára, resume o que foi feito e espera aprovação explícita antes de avançar.
- Não acrescentes funcionalidades que não estejam nesta especificação. Se faltar informação, pergunta.
- Interface em **português europeu**. Prompts gerados para os modelos em **inglês**.
- O VPS já corre outros serviços (ex.: OpenClaw). **Não mexas neles.** Antes de instalar seja o que for, verifica as portas 80/443 e se já existe um reverse proxy (nginx, Traefik, Caddy). Se existir, integra a app nele em vez de instalar outro.

## Objetivo

Uma app web, de um só utilizador, para organizar a produção de vídeos gerados com IA (curtas/spots, séries e filmes), do conceito até aos clips aprovados. A geração de imagens e vídeos é feita manualmente no Artlist; a app organiza, gera os prompts, guarda os resultados e mantém a continuidade.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLModel (SQLite), migrações com Alembic.
- **Frontend:** Jinja2 + HTMX, CSS simples. Sem framework JS pesado.
- **Ficheiros:** imagens e clips num volume em disco (`/data/media`), organizados por projeto; a base de dados guarda só o caminho.
- **Deploy:** Docker Compose. HTTPS num subdomínio com certificado automático (Caddy, ou o proxy que já existir).
- **Chat LLM:** Claude Code CLI já instalado no VPS, chamado em modo não interativo (fase 5).

## Segurança

- Login obrigatório em todas as páginas e ficheiros (incluindo `/media`).
- Um utilizador; hash da password (argon2) em variável de ambiente. Sessão por cookie `HttpOnly`, `Secure`, `SameSite=Lax`.
- Limite de tentativas de login. Proteção CSRF nos formulários.
- Uploads: só imagem (png, jpg, webp) e vídeo (mp4, mov); validar o tipo real do ficheiro e limitar o tamanho.
- Segredos num `.env` fora do repositório. O contentor corre como utilizador não-root.

## Conceitos

### Hierarquia

O projeto tem um **tipo**, que decide os níveis entre o projeto e a cena:

| Tipo | Hierarquia |
|---|---|
| Curta / spot | Projeto → Cena → Plano |
| Série | Projeto → Temporada → Episódio → Cena → Plano |
| Filme | Projeto → Ato → Cena → Plano |

Cena e Plano são iguais em todos os tipos. Personagens, locais e adereços pertencem ao **projeto** (partilhados entre episódios).

### As sete etapas

A página de cada projeto organiza-se nestas etapas, visíveis como separadores com o estado de cada uma:

1. **Projeto e bíblia**
2. **História** — logline, sinopse, arcos, guião por cena
3. **Desglose** — que personagens, locais e adereços o guião pede
4. **Design de assets** — fichas e folhas de referência
5. **Storyboard** — blocking e imagem-mestra por cena; frame a frame por plano
6. **Geração de vídeo** — prompt de vídeo e clip por plano
7. **Montagem** — fora da app (CapCut); a app só exporta os clips aprovados por ordem

## Modelo de dados

Todas as tabelas têm `id`, `created_at`, `updated_at`.

**Project**
- `name`, `prefix` (ex.: `AVT`, único), `type` (`short` | `series` | `film`)
- Identidade: `genre`, `tone`, `audience`, `dialogue_language`, `references` (texto, nota interna)
- Estilo visual: `render_style`, `proportions`, `palette_light` (texto), `style_block` (texto em inglês que entra em todos os prompts)
- Formato: `aspect_ratio`, `target_duration_s`, `working_resolution`
- Regras fixas: `exclusions` (texto em inglês que entra em todos os prompts), `project_rules` (texto)
- História: `logline`, `synopsis`

**Season** (só séries): `project_id`, `number`, `title`, `arc`, `summary`
**Act** (só filmes): `project_id`, `number`, `title`, `arc`, `summary`
**Episode** (só séries): `season_id`, `number`, `title`, `logline`, `synopsis`, `target_duration_s`

**Scene**
- Pertence a projeto (curta), episódio (série) ou ato (filme): `project_id` obrigatório + `episode_id` / `act_id` opcionais
- `number`, `title`, `location_id`, `time_of_day`, `script` (ação e falas)
- Storyboard: `blocking` (texto: posições, câmaras, regra dos 180°), `master_prompt`, `master_image_id`

**Asset**
- `project_id`, `kind` (`character` | `location` | `prop`), `name` (sempre com prefixo, ex.: `AVT_Vera`)
- `canonical_description` (inglês; marcas distintivas com número e posição exatos)
- `voice` (só personagens), `sheet_prompt`, `approved_image_id`
- Ligação N:N com `Scene` (em que cenas aparece), preenchida no desglose

**Shot** (plano)
- `scene_id`, `number`, `shot_type`, `camera_angle`, `camera_movement`
- `visible_assets` (N:N com Asset), `state` (texto: posições, adereços, poses no início do plano)
- Storyboard: `image_prompt`, `reference_ids` (lista de MediaFile), `start_frame_id`, `end_frame_id` (opcional)
- Checklist de continuidade: `check_props`, `check_geography`, `check_gazes` (booleanos)
- Vídeo: `video_prompt`, `model`, `duration_s`, `mode` (`frames` | `references`), `approved_clip_id`

**MediaFile**
- `project_id`, `path`, `kind` (`image` | `video`), `status` (`pending` | `approved` | `rejected`)
- Ligado a um alvo: asset, cena (mestra) ou plano (frame/clip)
- Um ficheiro rejeitado é **apagado** do disco e nunca pode ser escolhido como referência

**OptionList** — listas editáveis dos drop-downs (`key`, `value`, `order`)
**GlobalConfig** — configuração geral: modelos permitidos e definições, receitas e regras de prompt (ver abaixo)

## Geração de prompts

Os prompts são **montados pela app** a partir das partes guardadas, nunca escritos de raiz:

- **Folha de asset:** `style_block` + `canonical_description` + instruções de folha + `exclusions`
- **Imagem-mestra / imagem de plano:** `style_block` + local + descrição canónica de cada asset visível + `state` + tipo de plano e câmara + "exactly N people, each appearing once" + `exclusions`. Descreve sempre um **instante parado**, nunca movimento.
- **Vídeo:** receita da configuração geral (abaixo) preenchida com os dados do plano

Cada prompt aparece num bloco com botão **Copiar**, ao lado da lista de referências a anexar no Artlist (com miniaturas e botão de download). O prompt pode ser editado à mão; a app guarda a versão final.

Aviso quando um prompt passa de 2000 caracteres (limite do Artlist Studio).

## Configuração geral (valores iniciais, editáveis)

**Modelos**
- Nano Banana 2 Lite — imagem 1K
- Seedream 5.0 Pro — imagem 1K
- Seedance 2.0 Mini — vídeo 480p, clips de 4 ou 5 s
- Wan 3.0 — vídeo
- Nota: no Seedance 2.0 Mini e no Wan 3.0, por geração, só Start/End Frame **ou** imagens de referência, nunca os dois.

**Receita do prompt de vídeo (Seedance 2.0 Mini, Start Frame)**
1. "Animate the start frame exactly as it is, keeping the same framing, lighting…"
2. Features-chave de cada personagem visível (cabelo, cicatrizes, adereços, roupa)
3. Ação com tempos explícitos ("in the first two seconds…"), depois pose parada até ao fim
4. Fala entre aspas, descrição da voz, "clear natural lip movement"
5. Os outros personagens quietos e de boca fechada
6. Câmara
7. Som: voz, ambiente, música
8. "exactly N people, no text, no subtitles"

**Regras de prompt**
- Olhares e posições em direções de ecrã ("toward the right side of the frame"), nunca esquerda/direita do veículo ou da personagem; em planos de grupo, "nobody looks at the camera"
- Tamanho e zona do enquadramento para objetos distantes
- "only one <objeto>" quando houver risco de duplicados
- Sem campo de prompt negativo: exclusões dentro do prompt principal

## Drop-downs (valores iniciais, editáveis em Configuração)

- **Tipo de projeto:** Curta / spot, Série, Filme
- **Género:** Aventura, Ação, Comédia, Drama, Fantasia, Ficção científica, Mistério, Terror, Documental, Publicidade, Educativo
- **Tom:** Leve, Divertido, Épico, Tenso, Sombrio, Emotivo, Inspirador, Informativo
- **Público:** Infantil, Juvenil, Geral, Adulto
- **Língua dos diálogos:** Português (PT), Português (BR), Inglês, Espanhol, Sem diálogo
- **Estilo de render:** 3D animado estilizado, Fotorrealista, 2D animado, Stop-motion, Anime, Aguarela
- **Proporções:** Realistas, Estilizadas, Caricaturais
- **Aspect ratio:** 9:16, 16:9, 1:1, 9:16 + 16:9
- **Resolução de trabalho:** 480p, 720p, 1080p
- **Momento do dia:** Madrugada, Manhã, Meio-dia, Tarde, Pôr do sol, Noite
- **Tipo de plano:** Plano geral, Plano de conjunto, Plano médio, Plano americano, Grande plano, Plano de pormenor, Plano sobre o ombro, POV
- **Ângulo:** Nível dos olhos, Picado, Contrapicado, Zenital, Drone, Holandês
- **Movimento de câmara:** Estática, Pan, Tilt, Dolly in, Dolly out, Travelling, Grua, Câmara ao ombro

## Fases

### Fase 1 — Fundação
- Estrutura do projeto, Docker Compose, `.env.example`, README de instalação
- Integração com o reverse proxy e HTTPS no subdomínio
- Login e sessão
- Base de dados, migrações e seeds (drop-downs e configuração geral)
- Layout base: lista de projetos, página do projeto com os 7 separadores (vazios exceto a etapa 1)
- Backup diário da base de dados e de `/data/media` (cron dentro do contentor ou no host)

**Pára e espera aprovação.**

### Fase 2 — Projeto, bíblia e história
- Criar/editar projeto com todos os campos da bíblia (drop-downs onde indicado)
- Hierarquia conforme o tipo: temporadas/episódios ou atos
- Cenas com guião
- Página de configuração: editar drop-downs e configuração geral

**Pára e espera aprovação.**

### Fase 3 — Desglose e design de assets
- Lista de assets por tipo; ligar assets às cenas
- Ficha de asset com prompt da folha gerado, botão Copiar
- Upload de resultados (arrastar e largar), aprovar/rejeitar; rejeitados apagados

**Pára e espera aprovação.**

### Fase 4 — Storyboard e vídeo
- Cena: blocking, prompt da mestra, upload e aprovação da mestra
- Plano: campos, prompt de imagem, referências sugeridas (mestra, folhas dos assets visíveis, imagens aprovadas da mesma cena), checklist, Start/End Frame
- Plano: prompt de vídeo pela receita, definições, upload e aprovação do clip
- Aviso quando uma referência escolhida foi rejeitada ou não é coerente com o estado do plano (marcação manual)
- Exportar clips aprovados de uma cena/episódio num zip, nomeados por ordem (`S01_P01.mp4`…)

**Pára e espera aprovação.**

### Fase 5 — Chat com LLM
- Painel de chat lateral em cada etapa, com contexto automático do sítio onde estás (projeto, bíblia, cena, plano)
- Backend chama o Claude Code CLI em modo não interativo (`claude -p`), com timeout, sem ferramentas de sistema (sem bash, sem escrita de ficheiros), a correr com um utilizador sem privilégios
- O LLM devolve **propostas** estruturadas (JSON) para campos concretos; a app mostra-as como sugestões com botão **Aplicar**. Nada é gravado sem aprovação.
- O LLM responde só ao que foi pedido na etapa atual; não avança para outras etapas.
- Histórico de conversa guardado por projeto.

**Pára e espera aprovação.**

## O que o dono faz antes de começar

- Criar o registo DNS do subdomínio a apontar para o VPS
- Definir a password de acesso (a app gera o hash com um comando indicado no README)
