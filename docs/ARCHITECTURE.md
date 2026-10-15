# Arquitetura — Estúdio

App de uma só pessoa para produzir vídeo com IA: da ideia (bíblia, mundo, guião) às imagens de referência, às maquetes 3D
dos locais, ao storyboard em 3D, à geração de vídeo e à montagem. Interface em português de Portugal; prompts em inglês.
Mapa dos ficheiros: [CODEBASE_INDEX.md](CODEBASE_INDEX.md). Decisões do produto: [DECISOES.md](DECISOES.md) (ler antes de
mudar fluxos). Atualizado a 2026-10-02 (migração 0040, fluxo simplificado).

## 1. Visão geral

```
Browser (htmx + idiomorph, three.js)
   │  páginas HTML (POST → 303 → página atualizada por morph, sem saltos)
   ▼
FastAPI (app/routes/*)  ──►  serviços (app/*.py)  ──►  SQLite (WAL) + ficheiros em /data/media
   │                              │
   │                              ├─► escritor de prompts (prompt_writer.py, tarefas PromptTask)
   │                              ├─► filas de geração (jobs.py): gpt · artlist · fal
   │                              └─► Assistente (routes/assistant.py, em segundo plano)
   ▼
Serviços externos: Claude Code CLI (Assistente/escritor) · OpenRouter · Codex CLI (GPT Image) ·
                   Artlist (MCP via Claude Code) · fal.ai (REST)
```

- **Um processo**, um contentor Docker (`director-app`), SQLite em `/data/db`, media em `/data/media`.
- **Deploy** só por `scripts/deploy.sh`: constrói a imagem e recusa reiniciar enquanto houver pedidos na fila.
- **Sessão da base de dados por pedido** (`db.get_session`) é assíncrona de propósito (incidente de 2026-10-02).

## 2. Interface

- `hx-boost` em toda a app + extensão **morph**: só muda o que mudou; numa ação na mesma página o scroll não salta;
  modais abertos ficam abertos; campos mexidos e por gravar não são repostos; a maquete 3D montada só é recarregada
  quando a cena, os momentos ou as vistas por tirar mudam.
- "Página atualiza-se sozinha" = evento `estudio-refresh` (pedido + morph).
- Cartões "A gerar…" perguntam o estado de 4 em 4 s; o chat de 2 em 2 s; o escritor de prompts de 3 em 3 s.
- O visualizador 3D só desenha quando algo muda e quando está à vista; tira sozinho os renders que faltam (vistas do
  local, previs dos momentos, vistas para o Assistente).

## 3. Dados (resumo)

- **Project** (bíblia, `world` PT, `world_prompt` EN, capa) → **Season → Episode** (série) ou **Act** (filme) → **Scene**
  (guião em **SceneLine**, local principal, notas de blocking) → **Shot** (local `set3d_asset_id`, cena 3D `scene3d`,
  elenco `cast3d`, `moments` = renders dos momentos e vistas escolhidas à mão, vídeo-guia, som, prompt de vídeo, take).
- **Asset** (`character`, `location`, `prop`, `element`): descrição canónica, folha e retrato (não nos locais), fotos;
  locais: maquete `layout3d` + `zone_notes` + `zone_refs`.
- **LocationView**: posição de câmara numa zona da maquete de um local (automática, guardada na vista livre ou criada a
  partir da câmara de um plano); render, mapa de profundidade, imagem aprovada, prompt.
- **MediaFile**: qualquer ficheiro, com papel (`sheet`, `portrait`, `photo`, `locview`, `locrender`, `locdepth`,
  `render` = previs de um momento, `guide`, `clip`, `frame`, `cover`, `preview3d`), estado e um alvo.
- **ImageJob**: pedido de geração. **PromptTask**: trabalho do escritor de prompts (sobrevive a reinícios).
- **PromptVersion** (versões; as do escritor têm `basis` = impressão digital dos dados com que escreveu),
  **ChatMessage/ChatProposal** (Assistente), **GlobalConfig**, **OptionList**.

## 4. Etapas e ordem por tipo de projeto

| Tipo | Ordem |
|---|---|
| Curta | 1 Bíblia → 2 História → 4 Design de assets → 5 Storyboard → 6 Vídeo → 7 Montagem |
| Série | 1 → 4 Mundo → 2 Temporadas e episódios → Cenas do episódio → 5 → 6 → 7 (as últimas, por episódio) |
| Filme | 1 → 4 Mundo → 2 Atos → 5 → 6 → 7 |

"Por cena" é uma vista de Assets (`/etapa/4?vista=cenas`); `/etapa/3` redireciona para lá.

## 5. Fluxos principais

### 5.1 Prompts — escritos pelo escritor de prompts
Nenhum prompt é montado pela app. Cada alvo (`prompt_targets.py`: folha, retrato, vista, vídeo, capa) sabe que imagens
seguem para o gerador (pela ordem, cada uma com o seu marcador `@CÓDIGO`), que imagens o escritor vê só para si (fotos
reais, previs), o contexto (`prompt_context.py`: bíblia, mundo, regras, cena, plano, elenco) e a guia do tipo
(Configuração → Prompts). **Gerar**: se o prompt falta ou está desatualizado, o escritor escreve e a geração segue
sozinha; se foi escrito com os dados atuais ou editado à mão, gera logo. **Escrever** reescreve sem gerar. O mini chat
pede uma alteração ao prompt. No envio, `ref_tokens` troca os marcadores pela sintaxe do modelo.

### 5.2 Assets
Personagens, adereços, elementos: descrição canónica (+ fotos, + outros assets como referência) → Gerar folha → Aprovar.
Personagens: retrato a partir da folha aprovada.

### 5.3 Locais — sempre pela maquete 3D
Descrição e fotos → maquete (o Assistente monta-a; ou "Começar com um espaço simples") → zonas com "Como é" e
referências visuais → vistas (automáticas, guardadas na vista livre, ou criadas a partir da câmara de um plano) → o
browser tira render + mapa de profundidade → Gerar (escreve o prompt e gera numa só passagem):
- modelo de estrutura → mapa de profundidade como controlo;
- modelo que aceita imagens → render como base + referência da zona + folhas de design da zona;
- modelo só de texto → só o prompt (o seletor avisa).
Fotografias reais nunca vão para o gerador; o escritor vê-as e descreve o aspeto delas.

### 5.4 Storyboard
**Cena** (blocking, quem aparece, local da maquete) → **encenação 3D da cena** (`Scene.stage3d`: o elenco e os
figurantes com as posições ao longo de toda a cena, as cores do elenco em `Scene.cast3d` e o segundo em que começa
cada linha do guião em `lines`) → **planos = câmaras sobre a encenação**, cada um num troço de tempo
(`Shot.stage_start` + `duration_s`) com a sua câmara (`Shot.scene3d.camera`, momentos marcados com `moment`). Todos os
planos veem as mesmas posições: campo/contracampo, plano geral e grandes planos da mesma ação. O plano desenha o troço
da encenação (`scene3d.compose` com `slice_actors`); quem a câmara vê (`scene3d.in_frame`) decide as folhas que seguem
para o vídeo e o «exactly N people», e `frame_notes` diz ao escritor quem está de costas em primeiro plano. Para cada
**momento**: previs e a vista do local mais parecida (`moments.py`), escolhida à mão, ou uma vista nova a partir desta
câmara → Gerar a imagem da vista → **gravar e aprovar o vídeo-guia** (marcado com a versão da encenação; fica
desatualizado se ela mudar). Cada plano gera o seu vídeo; a montagem junta-os. A câmara também se edita à mão na página do plano: as posições-chave (ver, editar com a Câmara à mão, juntar no
segundo atual, mudar o segundo, marcar momento, apagar), gravadas por `POST …/planos/{id}/camera`, com «Desfazer» para
a versão anterior (`Shot.moments.camera_prev`).

### 5.5 Vídeo
Modelo (Artlist ou fal; os que aceitam várias imagens primeiro) → som e fala → imagens do pedido (automático: as vistas
dos momentos sem repetir + as folhas do elenco; ou escolha à mão, incluindo outras vistas e fotogramas) → vídeo-guia
(modelos com `video_refs`) → prompt (escritor) → "O que vai ser enviado" → Gerar take → escolher. Modelos só com imagem
inicial recebem a vista do primeiro (e do último) momento, com aviso. Fotogramas de um take podem passar a ser a
primeira imagem do plano seguinte.

### 5.5b Vozes de referência
Cada personagem pode ter uma voz de referência (`Asset.voice_ref_id`, áudio aprovado): gerada com um modelo de voz
(`voice_models.py`, pelas capacidades: voz do catálogo do fornecedor ou voz criada a partir da descrição; Artlist e fal)
ou carregada. Nos planos onde a personagem fala (a fala do plano e as falas do guião que ele cobre), a voz segue no pedido
de vídeo como áudio de referência, nos modelos que o aceitam (`audio_refs`), e o escritor refere-a por `@CÓDIGO.voice`
(→ `@Audio1` na fal, `@audio1` nos Seedance do Artlist; por palavras nos outros modelos do Artlist). O catálogo de vozes do Artlist lê-se dos resultados da ferramenta
(`artlist._tool_results`) e fica em `artlist_voices`. Cada voz pode ser cortada na ficha (botão Cortar: início e fim,
ouvir o trecho; `media.trim_audio` com o ffmpeg); o corte fica como versão nova e o modal mostra, por plano, os
segundos de voz que o modelo desse plano ainda aceita (`video.voice_budget`).

### 5.5c Guarda-roupa
Looks por personagem (`CharacterLook`, `app/looks.py`): a folha de cada look (alvo de prompt `look`, papel `look`,
`MediaFile.look_id`) parte da folha e do retrato aprovados. O look de cada personagem escolhe-se por cena
(`Scene.looks`, página da cena no Storyboard); `looks.ref_ids` dá as imagens que seguem para os vídeos dessa cena.

### 5.5d Projetos ligados e importação
Um projeto pode ser parte de outro (`Project.parent_id`: herda a bíblia visual e o mundo). Os assets de outro projeto
importam-se como cópias (`app/asset_import.py`: `import_assets`, `refresh`), com os ficheiros e os códigos passados ao
prefixo do destino; a cópia lembra a origem (`Asset.source_asset_id`, `source_map`).

### 5.6 Montagem
Takes escolhidos por ordem, lacunas assinaladas, "Ver seguido", exportar zip. Edição fina fora da app.

### 5.7 Assistente (chat)
Painel em todas as páginas; o contexto depende do ecrã. Propostas em cartões (Aplicar / Ignorar). Pode criar assets,
cenas, planos e verificações, montar maquetes e cenas 3D, e alterar textos. Nas páginas sem campos responde só em texto
e diz onde se faz a mudança.

## 6. Fornecedores, capacidades e filas

- **Capacidades** (`image_models.caps_of`): `t2i`, `refs`, `edit`, `structure`; vídeo: `refs`, `video_refs`, `end_frame`.
  A app decide pela capacidade, nunca pelo nome. Modelos que só geram com referências avisam e são recusados sem elas.
- **Filas**: uma por fornecedor (`PROVIDER_PARALLEL`: gpt 1, artlist configurável, fal 4).
- **Escritor de prompts**: Claude (subscrição) ou OpenRouter, escolhido em Configuração → Assistente.

| Fornecedor | Como liga | Imagem | Vídeo |
|---|---|---|---|
| GPT Image | Codex CLI, subscrição ChatGPT | sim | — |
| Artlist | MCP pelo Claude Code, OAuth | sim | sim (Seedance com vídeo-guia) |
| fal.ai | chave de API | recomendados + catálogo | Seedance 2.0 / Fast / Mini / 2.5 (com vídeo-guia) |
| Claude Code | token no `.env` | — (Assistente e escritor) | — |
| OpenRouter | chave de API | — (Assistente e escritor) | — |

## 7. Configuração (GlobalConfig)
`prompt_guides` (guias do escritor + proporções das folhas e retratos), `prompt_rules`, `prompt_checks`,
`prompt_max_chars`, `prompt_warn_enabled`, `assistant` (motor, modelo, `writer`), `gpt_image`, `artlist`,
`image_generator`, `video_generator` (modelo, resolução, som — para todos os fornecedores), `api_keys` (cifradas; só
OpenRouter e fal ligadas), `fal_catalog`, `fal_models`.

## 8. As 23 lacunas de 2026-10-02 — o que lhes aconteceu

| # | Lacuna | Agora |
|---|---|---|
| 1 | Carregar take sempre desativado | Corrigido: o carregamento está sempre disponível |
| 2 | Maquete só criável pelo Assistente | Corrigido: "Começar com um espaço simples" (e o Assistente continua a montar) |
| 3 | O Assistente não criava cenas | Corrigido: campo `new_scene` (curta, episódio, ato) |
| 4 | 17 chaves de API sem uso | Corrigido: a página mostra só OpenRouter e fal; as outras guardadas podem ser apagadas |
| 5 | Lista "Modelos" antiga | Removida |
| 6 | Imagem mestra enganadora | Removida (com os dados) |
| 7 | Modo início/fim inalcançável | Resolvido: o modo é o do modelo; os modelos só de imagem inicial recebem as vistas, com aviso |
| 8 | Vídeo ignorava as vistas da maquete | Resolvido: as vistas dos momentos são as imagens do vídeo |
| 9 | Painéis 3D sem profundidade | Resolvido: acabaram os painéis; as vistas novas têm render e profundidade |
| 10 | Resolução e som só com o Artlist | Corrigido: `video_generator`, para qualquer fornecedor |
| 11 | Resolução de trabalho sem uso | Removida |
| 12 | "9:16 + 16:9" | Removido (projetos passam a 9:16) |
| 13 | Ordenar episódios sem interface | Corrigido: setas em cada episódio |
| 14 | Página "Ainda não disponível" | Removida |
| 15 | Escritor não cobria tudo | Resolvido: escreve todos os prompts |
| 16 | Assistente sem campos nas visões gerais | Corrigido: Storyboard propõe planos; nas outras responde em texto e diz onde se faz |
| 17 | Modelos Artlist só com referências | Corrigido: avisados no seletor e recusados sem referências |
| 18 | Takes da fal sem modelo | Corrigido: "Take 3 · Seedance 2.0 · fal.ai" |
| 19 | Estado do escritor em memória | Corrigido: `PromptTask` na base de dados; a meio de um reinício fica como falhado |
| 20 | Renders só com a página aberta | Resolvido: a página onde se cria a vista (plano ou local) tira o render logo, e o Gerar só aparece com render |
| 21 | Textos "Start/End Frame" | Removidos |
| 22 | Prompt das vistas fixo no código | Resolvido: guia "Vistas da maquete" editável |
| 23 | Regras só chegavam ao Assistente | Resolvido: o escritor recebe as regras da Configuração e as da bíblia |

## 9. Dívida técnica (que ficou)
- Nomes legados: rota `/desglose`, template `stage_3.html`, contexto `breakdown`; a chave de campo `set3d_from` (é o
  local do plano).
- Duplicações: chamada ao Claude CLI em `artlist.generate`/`_run_agent`/`balance`; redução de imagens em vários sítios;
  login por PTY em `artlist.py` e `gpt_image.py`.
- Módulos de serviço ainda importam rotas (`prompt_targets` → `routes.gpt`/`routes.video`); `routes/production.py` não tem rotas.
- `image_models.FAL` é um dicionário global alterado em cada pedido (`sync`).
- Chamadas HTTP síncronas dentro de pedidos (`llm.models`, `openrouter.models`, `fal_catalog`).
- A impressão digital dos prompts (`prompt_targets.basis`) calcula o contexto a cada página: nas maquetes com muitas
  vistas pode pesar.

## 10. Regras do projeto (resumo)
- Nunca processos presos a um modelo ou fornecedor: decide-se pelas capacidades.
- Nenhum prompt montado pela app: escreve-os o escritor de prompts, com o contexto completo.
- Uma só geração por imagem. Fotografias reais não vão para o gerador junto com um render.
- Nada pago corre sem aprovação explícita do custo; deploy só com a fila vazia.
- Atualizações da página nunca mexem no scroll.
