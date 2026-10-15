# Índice do código — Estúdio

Mapa de onde está cada coisa. Para o porquê e os fluxos, ver [ARCHITECTURE.md](ARCHITECTURE.md).
Atualizado a 2026-10-02 (migração 0040, fluxo simplificado: ver [DECISOES.md](DECISOES.md)). Caminhos relativos a `app/`.

## Raiz da app

| Ficheiro | O que faz |
|---|---|
| `main.py` | Cria a app FastAPI, monta `/static`, inclui os routers, arranca as filas de geração (`jobs.start`), trata login/erros. Rotas públicas: `/healthz`, `/sw.js`, `/manifest.webmanifest`, `/pub/ref/<token>.png\|.mp4\|.webm` (links temporários para o Artlist). |
| `config.py` | Definições do `.env` (segredo, admin, token do Claude, pasta de dados). |
| `db.py` | Motor SQLite (WAL, pool 20+30) e `get_session` (assíncrono de propósito: evita o bloqueio de 2026-10-02). |
| `models.py` | Tabelas SQLModel (ver ARCHITECTURE §3) e constantes (`ASSET_KINDS`, `IMAGE_JOB_ROLES`…). |
| `security.py` | Password (argon2), sessão, limite de tentativas de login, CSRF. |
| `forms.py` | Leitura de campos de formulário (`text`, `integer`, `lines`). |
| `options.py` | Listas editáveis (tipos de plano, tons…) e `get_config`/`set_config` (tabela GlobalConfig). |
| `seeds.py` | Valores iniciais: listas, guias do escritor de prompts (`DEFAULT_PROMPT_GUIDES`), regras, validações. |
| `cli.py` | `python -m app.cli`: init, hash/reset da password, backup da base de dados. |
| `templating.py` | Ambiente Jinja: filtros e funções globais (`image_generators`, `writer_status`, `thumb`…). |
| `stages.py` | Etapas 1–7, ordem por tipo de projeto, estado (Pendente/Em curso/Aprovada; `shot_board_state`, `asset_done`), árvore lateral, episódio atual, `render_stage`. |

## Geração de imagem e vídeo

| Ficheiro | O que faz |
|---|---|
| `jobs.py` | Filas de geração: uma por fornecedor (gpt, artlist, fal), pedidos `ImageJob`, `_image` (um pedido a qualquer fornecedor), `_locview` (vistas da maquete, pelo que o modelo sabe fazer), `_fal_video`, guarda o resultado. |
| `image_models.py` | Capacidades dos modelos (`caps_of`, `fit`, `max_inputs`), lista de fornecedores (`PROVIDER_PARALLEL`), modelos fal de imagem (recomendados + catálogo) e de vídeo (`FAL_VIDEO`). |
| `gpt_image.py` | GPT Image pela subscrição ChatGPT (Codex CLI): login, `generate`, `BASE_NOTE` (o render é a base). |
| `artlist.py` | Artlist pelo servidor MCP (Claude Code como cliente): login OAuth, `generate`, `generate_video`, saldo, links públicos temporários. Catálogos `MODELS`/`VIDEO_MODELS`. |
| `fal.py` | fal.ai REST: fila (`run`), envio para o CDN (`upload`), preços. |
| `fal_catalog.py` | Catálogo completo da fal e leitura do esquema de cada modelo (o que sabe fazer e como se pede). |
| `voice_models.py` | Modelos de voz (Artlist e fal) por capacidade: voz de catálogo ou a partir da descrição; campos do pedido à fal; catálogo de vozes do Artlist guardado. |
| `model_profiles.py` | Limites de palavras e sintaxe das referências por família de modelo (Seedance `@image{n}`, etc.). |

## Prompts

| Ficheiro | O que faz |
|---|---|
| `prompt_targets.py` | Os alvos de prompt (folha, retrato, vista, vídeo, capa): imagens que seguem, imagens só para o escritor, contexto, guia, estado (por escrever / escrito / desatualizado / editado), pedir a geração. |
| `prompt_context.py` | O contexto completo do escritor: bíblia, mundo, regras, cena, plano, assets. |
| `prompt_writer.py` | O escritor de prompts: tarefas `PromptTask` em segundo plano, pedido ao modelo (Claude ou OpenRouter), grava com versão e impressão digital, gera a seguir; mini chat de revisão. |
| `routes/prompt_ops.py` | O bloco de prompt das páginas (`panel`) e as ações Gerar / Escrever / repor versão (`save`, `run`). |
| `ref_tokens.py` | Marcadores `@CÓDIGO` / `@CÓDIGO.v20` (vista) / `@CÓDIGO.r20` (render) → sintaxe do modelo no envio; códigos sem imagem passam a nome. |
| `prompt_checks.py` | Avisos sobre prompts (palavras, movimento, "exactly N people"…). |
| `prompt_origin.py` | Avisos da bíblia (luz/hora do dia no bloco de estilo e exclusões). |
| `prompt_versions.py` | Versões dos prompts; gravar o editado e repor uma versão. |
| `prompts.py` | Só as palavras de luz/hora do dia (a app não monta prompts). |
| `location3d.py` | Zonas da maquete, vistas automáticas, "o que esta vista mostra" (`seen_from`), referências de zona, imagens que seguem para gerar uma vista (`view_inputs`). |
| `moments.py` | Momentos da câmara de um plano: vista do local mais parecida em cada um, criar uma vista a partir da câmara, imagens do vídeo (vistas e elenco). |
| `asset_refs.py` | Fotos e referências de um asset; a imagem que representa um local. |
| `shot_script.py` | Guião marcado (linhas cobertas por cada plano) e formato dos segundos. |

## Maquete 3D

| Ficheiro | O que faz |
|---|---|
| `scene3d.py` | Formato da maquete do local e da cena do plano (câmara com momentos, elenco), validação, edição por partes, `compose` para o browser, pré-visualizações para o Assistente, `BLANK_ROOM`. |
| `static/js/scene3d.js` | Visualizador three.js: anima a cena, câmara do plano/vista livre, "Ver por dentro", renders das vistas (+ profundidade, também das vistas novas criadas num plano), previs dos momentos, vídeo-guia; desenha só quando muda. |
| `static/js/scene3d_parts.js` | Peças (mobília, carros), manequins com poses, janelas dos prédios, etiquetas. |

## Ficheiros e media

| Ficheiro | O que faz |
|---|---|
| `media.py` | Escrita segura de ficheiros, validação de imagens/vídeos, duração de MP4. |
| `media_service.py` | Aprovar/rejeitar resultados, para onde voltar, nomes de download, códigos C01/P03. |
| `api_keys.py` | Chaves de API cifradas; `CONNECTED` = as que a app usa (OpenRouter, fal). |

## Assistente (`assistant/`)

| Ficheiro | O que faz |
|---|---|
| `contexts.py` | O que o Assistente vê em cada ecrã e que campos pode propor (`build`). |
| `fields.py` | Campos permitidos, validação e aplicação das propostas (incl. cenas, planos, maquete, zonas, cena 3D). |
| `llm.py` | Chamada ao Claude Code CLI (`ask`), lista de modelos Claude. |
| `openrouter.py` | Alternativa pelo OpenRouter. |

## Rotas (`routes/`)

| Ficheiro | Prefixo | Ecrãs e ações |
|---|---|---|
| `auth.py` | — | login, logout, mudar password |
| `projects.py` | — | lista de projetos, criar, bíblia, `/etapa/N` (despacha cada etapa) |
| `story.py` | `/projetos/{id}/historia` | etapa 2: temporadas, episódios, atos, cenas, linhas do guião |
| `assets.py` | `/projetos/{id}` | etapa 4: mundo, assets, "Por cena" (`/desglose`), página do asset (folha, retrato, fotos; locais: maquete, zonas, vistas, renders, referências) |
| `storyboard.py` | `/projetos/{id}` | etapa 5: cena (blocking, planos), plano (local, cena 3D, elenco, momentos e vistas, vídeo-guia, continuidade); renders dos momentos |
| `video.py` | `/projetos/{id}` | etapa 6 por plano: modelo, som, imagens do pedido, "O que vai ser enviado", take, fotogramas; `video_models`, `send_refs`, `guide_for_video`, `queue_video` |
| `montage.py` | `/projetos/{id}` | etapas 6 (visão geral) e 7 (montagem, exportar zip) |
| `gpt.py` | `/projetos/{id}` | pedidos de imagem: seletor de modelos, `queue_image`, estado/repetir/cancelar, editar imagem, capa |
| `writer.py` | `/projetos/{id}` | estado do escritor de prompts (o bloco pergunta enquanto escreve), mini chat |
| `assistant.py` | `/projetos/{id}/assistente` | chat (envio, resposta em segundo plano), propostas (aplicar/ignorar) |
| `files.py` | `/projetos/{id}` | aprovar, rejeitar, descarregar; `save_uploads`, `flash` |
| `media.py` | — | servir ficheiros e miniaturas |
| `config_page.py` | `/configuracao` | listas, modelos e geração (imagem, fal, vídeo), ligações (Codex, Artlist), prompts (guias, regras, validações), Assistente (e modelo do escritor), chaves, conta |
| `production.py` | — | *sem rotas*: funções partilhadas (`get_shot`, `approved_media`, `scene_assets`…) |

## Interface

- `templates/` — `base.html` (htmx + idiomorph), `projects/shell.html` (barra lateral, etapas, painel do Assistente), uma pasta por área (`stages/`, `story/`, `assets/`, `storyboard/`, `video/`, `montage/`, `assistant/`, `gpt/`, `writer/`, `config/`). `_macros.html`: bloco de prompt (`prompt_panel`), seletores (`picker`), resultados, botão do escritor de um grupo.
- `static/js/app.js` — navegação por morph (sem saltos de scroll), chat, mini chat, modais, lightbox, fotogramas, seletores, gravação em segundo plano de escolhas.
- `static/css/estudio.css` (base) + `app.css` (tudo o resto).

## Outros

- `migrations/versions/0001…0040` — histórico do esquema (Alembic). A 0040 apaga A–D, painéis e mestras.
- `scripts/deploy.sh` — único caminho de deploy; recusa reiniciar com pedidos na fila.
- `docs/PROMPT_RULES.md` — regras de escrita de prompts (o Assistente segue-as).
- `docs/DECISOES.md` — decisões do produto (a ler antes de mudar fluxos).
- `spec.md`, `design/` — especificação e design originais (parte já ultrapassada: ver DECISOES.md).
