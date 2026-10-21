# Decisões do produto

Registo das decisões do utilizador que mudam a forma da app. Lê isto antes de mexer em fluxos. Quando uma decisão
muda, atualiza a entrada (não apagues o histórico: marca-a como substituída).

## 2026-10-02 — Fluxo simplificado: tudo pela maquete 3D, todos os prompts pelo Assistente

1. **Acabam as vistas A–D.** Todos os locais usam a Maquete 3D. A maquete dá mais vistas, cria e define o ambiente à
   volta, e permite pôr a câmara onde for preciso. As vistas A–D (e o método "A partir da folha") são removidas,
   incluindo os dados que já existiam (o utilizador escolheu apagar, não esconder).
2. **Nenhum prompt é montado pela app.** Todos os prompts (folhas, retratos, vistas da maquete, vídeo, capa) são
   escritos pelo escritor de prompts (Assistente). Os prompts montados por regras falhavam e acabavam sempre refeitos
   pelo Assistente. Ao carregar em Gerar sem prompt escrito (ou com o prompt desatualizado), o escritor escreve e a
   geração entra na fila logo a seguir, num só clique. O utilizador pode ver e editar o prompt antes ou depois.
   A app só troca os marcadores `@CÓDIGO` pela sintaxe do modelo no envio (ver `ref_tokens.py`).
3. **O modelo do escritor de prompts é configurável** (Configuração → Assistente → "Modelo para escrever prompts":
   Claude ou OpenRouter).
4. **O storyboard usa sempre a maquete 3D do local.** Deixa de usar as vistas A–D.
5. **Acabam os painéis.** O vídeo de cada plano recebe:
   - a vista do local (gerada a partir da maquete) mais próxima da câmara do plano em cada momento;
   - as folhas do elenco;
   - o vídeo-guia da cena 3D, quando o modelo aceita vídeo.

   Se nenhuma vista serve, o plano cria uma vista nova do local a partir da sua câmara. Fica guardada na maquete do
   local e serve para outros planos. As imagens vão indexadas (marcador único por imagem) e anexadas ao prompt de
   vídeo. Painéis, imagens de painéis e imagens mestras existentes são apagados.
6. **O escritor de prompts tem o contexto completo.** Recebe a bíblia (ideia, tom, estilo, exclusões, formato), o
   mundo, as regras de prompt, a cena (guião, local, luz), o plano (enquadramento, câmara, falas, som), as descrições
   de quem e o que aparece, e as imagens que vão ser enviadas, por ordem e com o marcador de cada uma.

Também nesta data: as 23 lacunas de `ARCHITECTURE.md` §8 são para resolver (remover ou corrigir). As que deixam de
existir com os pontos acima ficam resolvidas por eles.

## 2 de outubro de 2026 (noite): prompts de vídeo por secções, pelo modelo de cada plano

Depois de ler os guias oficiais (Seedance 2.0 e 2.5 da ByteDance, Veo 3.1 da Google, Kling 3.0):

- O prompt de vídeo monta-se **por secções**, sempre pela mesma ordem: References (cada marcador com o seu papel),
  Summary (uma frase), Scene and look (sítio, luz, cor, estilo), Action, Dialogue, Sound, Constraints (as negativas
  só aqui).
- **O formato vem do modelo do plano**, não do modelo por omissão: etiquetas ou parágrafo, linha de tempo ou não, a
  forma das falas e do som, e os limites de palavras e caracteres (`model_profiles.video_format`). Mudar o modelo de
  um plano deixa o prompt desatualizado; o escritor reescreve-o ao Gerar.
- **Tempos só no Seedance 2.5** (o guia oficial diz que os segue ao segundo), sempre como o mesmo take contínuo. No
  Seedance 2.0 e nos outros, sem tempos: no 2.0 fizeram cortar a meio do take.

## 3 de outubro de 2026: as falas de um plano são as linhas do guião marcadas para ele

As ações e falas que vão num plano marcam-se no Storyboard (linhas do guião → plano) e isso é respeitado em todo o
lado: o escritor de prompts, o Assistente, as vozes de referência que seguem e a página Vídeo (que as mostra só para
leitura) usam essas linhas (`prompt_context.shot_talk`). A antiga escolha de uma só «Fala» na página Vídeo deixou de
existir; só um plano sem linhas marcadas usa a fala antiga que tinha guardada.

## 3 de outubro de 2026: guarda-roupa por personagem, escolhido por cena

- Cada personagem tem um guarda-roupa de **looks** (`CharacterLook`): nome, roupa e acessórios (EN) e uma folha. Os
  acessórios fazem parte do look; os adereços ficam para objetos usados na ação.
- A folha de um look gera-se **a partir da folha aprovada e do retrato** (guia «Folha de look»): o rosto, o corpo e
  o cabelo ficam, muda a roupa.
- O look escolhe-se **por cena** (`Scene.looks`, na página da cena no Storyboard) e vale para todos os planos dela.
  Sem escolha (ou sem folha aprovada do look), segue a folha base.
- Nos vídeos, a folha do look substitui a folha base (marcador `@CÓDIGO.look<id>`); o retrato mantém-se. O escritor
  recebe a roupa do look e o Assistente pode criar looks e escolhê-los por cena.

## 3 de outubro de 2026: maquetes com formas inclinadas, mais objetos e sempre editadas por partes

- Tipos novos na maquete: `prism` (prisma triangular: `gable` para telhados de duas águas e empenas, `ramp` para
  rampas e telhados de uma água) e `extrude` (qualquer polígono em planta levantado: prédios de esquina, praças).
- Limite de objetos por maquete: 1500 (era 400).
- Uma maquete que já existe muda-se sempre por partes (só o que muda). A reescrita completa só é oferecida ao
  Assistente quando o utilizador pede para refazer a maquete de raiz (`contexts.limit_rewrites`).
- O equipamento de telhado dos quarteirões (`roof_bits`) só entra quando o utilizador o pede.

## 3 de outubro de 2026: projetos ligados e importação de assets (cópias)

- Um projeto pode ser **parte de** outro (`Project.parent_id`), ex.: um spot da série. Ao criar, herda a bíblia
  visual, o formato e o mundo (não a história); o Assistente vê a logline e a sinopse do projeto de origem.
- **Importar de outro projeto** (etapa dos assets) faz **cópias** (decisão do utilizador: copiar, não ligar): ficha,
  imagens aprovadas, voz, looks, maquete, vistas e fotos, com os ficheiros copiados e os códigos (também nos prompts
  e nos marcadores @CÓDIGO.v/.r/.look) passados ao prefixo do destino (`app/asset_import.py`).
- Cada cópia guarda a origem (`Asset.source_asset_id`, `source_map`) e tem «Atualizar a partir do original», que traz
  as mudanças sem duplicar ficheiros; o que só existe na cópia fica.

## 3 de outubro de 2026: a ação 3D é da cena; os planos são câmaras sobre ela

- A **encenação 3D** (quem está onde e o que se mexe, ao longo de toda a cena) pertence à **cena**, com o local da
  maquete e as cores do elenco. Monta-se uma vez (o Assistente, a partir do guião e das notas de blocking).
- Cada **plano** é uma câmara sobre essa encenação num **troço de tempo** (início + duração); mapeia para uma ou mais
  linhas do guião. Vários planos podem mostrar o mesmo troço (plano geral, campo/contracampo, grande plano).
- Cada plano **gera o seu vídeo**, sem cortes dentro da geração (decisão do utilizador); a montagem junta-os.
- Seguem para o vídeo as folhas de **quem a câmara vê** nesse plano; as vozes de quem fala nas linhas dele.
- Dados antigos: a encenação de cada cena passou a ser a do primeiro plano com cena 3D; cada plano ficou com a sua
  câmara a começar no segundo 0 (migração 0045).

## 4 de outubro de 2026: sem blocos de tempo no vídeo, e o texto nunca contradiz o vídeo-guia

- Testado com o Seedance 2.5 (Artlist): os blocos de tempo no prompt («0s-1s: …») fazem cortar, mesmo com «same
  continuous take». Nenhum perfil usa blocos de tempo; a ação é um movimento contínuo.
- O escritor recebe, para cada momento da câmara, o que o vídeo-guia mostra de cada personagem (de frente ou de
  costas, lado do ecrã, para onde anda — `scene3d.moment_notes`) e não pode contradizê-lo: com texto e vídeo-guia em
  conflito, o modelo segue o texto.
- O editor da câmara avisa das passagens bruscas (rodar > 45°/s ou andar > 3 m/s), que os modelos leem como cortes.

## 5 de outubro de 2026: o vídeo-guia é normalizado no servidor

- O que o navegador grava (MP4 fragmentado ou WebM, com ritmo variável e uns décimos a mais) é convertido com o
  ffmpeg para MP4 H.264 a 24 imagens/s, sem som e cortado à duração exata do plano (`media.normalize_guide`). Uma
  gravação mais curta do que o plano é recusada.
- Um guia acabado de gravar aparece à frente do aprovado, para se poder ver e aprovar. A versão da encenação fica
  guardada por guia (`moments.guide_hash`): o aviso de «versão anterior» diz respeito ao guia que segue.

## Decisões anteriores que continuam em vigor

- **Capacidades, não modelos.** Nenhum processo é construído à volta de um modelo ou fornecedor. Decide-se pelo que
  o modelo declara (texto→imagem, referências, edição, estrutura, vídeo de referência). Os modelos que não suportam
  algo ficam só marcados ou escondidos.
- **Uma só passagem** nas vistas da maquete (o método de duas passagens foi rejeitado por ser caro).
- **As fotos reais não vão para o gerador** junto com um render: o escritor vê-as e descreve o aspeto em texto.
- **Custos:** gerações pagas só depois de o utilizador aprovar o custo indicado.
- **Deploy** só por `scripts/deploy.sh`, com a fila de geração vazia.
- **Sem saltos de scroll**: navegação por morph, seleções em modal, gravação em segundo plano.
- **Ordem das etapas por tipo de projeto**: a série e o filme fazem o mundo antes das cenas; a curta mantém a ordem
  clássica.

## 5 de outubro de 2026: duração dos takes

- Não há folga de tempo extra: o plano já tem margem para cortar. (Experimentou-se gravar 1 s antes e depois; o
  utilizador não quis.) «Folga» quer dizer margem criativa para o modelo, não segundos a mais.
- Um take mais comprido do que o plano não é erro (corta-se na montagem); só um take mais curto do que o plano é avisado.
- **Fixo e livre no prompt de vídeo.** Prende-se só a identidade das personagens, a câmara e o enquadramento (do
  vídeo-guia), os momentos principais da ação, as falas e a voz, um só plano sem cortes e sem texto. Os figurantes,
  os elementos do mundo (sem número nem posição), os gestos entre momentos, a luz e a vida de fundo ficam livres;
  as Constraints são curtas. Prompts que querem controlar tudo dão takes piores.

## 5 de outubro de 2026: o prompt de vídeo é verificado contra o vídeo-guia

- `guide_check.check` compara cada prompt de vídeo com o vídeo-guia: de frente / de perfil / de costas de cada
  personagem pela ordem do guia (`scene3d.moment_facts`), os marcadores de quem está em plano e de cada imagem que
  segue, o @Video1, e que não se contam figurantes nem se lhes chama manequins.
- Os três caminhos que escrevem o prompt usam-no: o escritor (Reescrever) e o mini chat voltam a tentar até 2 vezes
  com os erros; o Assistente do chat tem a revisão automática da app; a página do vídeo avisa antes de gerar.
- O escritor e o Assistente veem o próprio vídeo-guia numa tira de imagens (`media.guide_sheet`), além dos factos
  de cada momento.
- Testado com 27 escritas reais do plano P08 (escritor, mini chat, Assistente): todas acabaram de acordo com o guia.

## 5 de outubro de 2026 (tarde): de volta à receita do take 3

- Os takes com o prompt «fixo e livre» e com o «andar natural» ficaram piores (Seedance 2.0 e 2.5). A guia do vídeo
  voltou ao texto com que se escreveu o take 3 (4 de outubro): «@Video1 = camera movement, framing, lens, timing and
  the positions of everyone», figurantes «following exactly the positions and movements of @Video1», elementos do
  mundo pela cor e «exactly as in @X». Única diferença: «figures» em vez de «mannequins» (deu manequins no take 4).
- Fica o verificador do guia (ordem de frente/perfil/costas, marcadores, cores) e a ligação versão → take.
- Testado (takes 11–13): a rigidez vem do Seedance 2.0, que copia o andar do previs; o 2.5 fica fluido. O excesso de
  figurantes no guia não causou a rigidez. Afinações à receita do take 3: quem se mexe vai «moving with fluid,
  natural, realistic body motion» (a app junta-o na Action se faltar); nunca «stiff animation» (a app tira-o);
  figurantes «in varied, colourful everyday clothes» (sem isso vestem-se da cor da figura do guia).

## 5 de outubro de 2026: poucas figuras no vídeo-guia

- Teste controlado no Seedance 2.5 (mesmo prompt, só muda o guia): guia limpo (1 figura além do elenco) → 6 s, sem
  cortes, segue o caminho; guia cheio (9 figuras) → 14–15 s, manequins e cores do previs, ação repetida; sem guia →
  6 s, sem cortes.
- O utilizador põe poucos ou nenhuns figurantes na encenação. A app avisa (Storyboard e Vídeo do plano) quando a
  câmara vê mais de `scene3d.MAX_GUIDE_FIGURES` (3) figuras além das personagens principais.
- Gravar um guia novo já não apaga os anteriores; um take cujo ficheiro de entrada desapareceu falha antes de pagar.

## 5 de outubro de 2026: teste completo do prompt de vídeo (projeto «TESTE vídeo-guia (Claude)», TVG)

- 12 planos (de frente, de costas, de perfil, vira-se, a falar com voz e sotaque, modelo sem vídeo de referência,
  plano a dois, campo e contracampo com falas, grupo com alguém a entrar, rua cheia, sem 3D) × escritor (2×), mini chat
  (Revê, Encurta, pedido contra o guia) e Assistente: todas as escritas finais seguem o guia.
- Corrigido no caminho: quem a câmara vê passou a contar pessoas tapadas por outras (por cima do ombro, grupos), a
  câmara metida numa parede e a mobília alta (prateleiras); o verificador liga cada «de frente/de costas» a quem é
  nomeado (não aos figurantes nem a todos os nomes da frase) e verifica as vozes (@X.voice sem voz que siga = erro).
- Guias gravados num computador lento: até 0,5 s em falta completa-se com a última imagem.

## 5 de outubro de 2026: as pessoas da maquete são cápsulas (boneco B)

- Teste de luta no Seedance 2.0 com dois guias da mesma coreografia: com bonecos articulados (A) o modelo copiou o
  guia à letra, incluindo golpes no ar; com cápsulas (B) fez uma luta credível, com contacto, mantendo posições,
  quedas e tempos. Leitura do utilizador: quanto mais o boneco parece real, mais o modelo o segue como um corpo.
- Todas as pessoas (elenco, figurantes, elementos do mundo com corpo) são cápsulas lisas com viseira escura na frente
  (`scene3d_parts.mannequin`); elenco num tom da sua cor, figurantes cinzento-claro. Poses: stand, walk, run, sit,
  point, bend (dobrado), kneel (de joelhos), lie (deitado). A encenação marca só posições, orientação e momentos-chave;
  os golpes e gestos vão na descrição e no prompt (STAGE_HELP). RENDER_VERSION 3: os guias antigos ficam marcados.
- Correções da auditoria do P08: elementos do mundo com corpo (kind person com code) ficavam cinzentos como figurantes;
  agora têm a cor deles (tom mais forte, 70%). Cabeça da cápsula encostada ao corpo. Figuras com menos de 6% da altura
  do quadro não contam como «na imagem» (`scene3d.MIN_SCREEN`). A página do vídeo avisa a vermelho, com botão para
  repor a escolha automática, quando a câmara vê alguém cuja folha não segue no pedido (`video.left_out`).

## 6 de outubro de 2026: conector MCP (Claude chat e Cowork)

- A app é um servidor MCP em `<PUBLIC_URL>/mcp` (`app/mcp_server.py`, SDK `mcp` 2.3, HTTP sem
  estado, respostas JSON). Autenticação OAuth com registo dinâmico; a autorização faz-se na página `/mcp/autorizar`,
  com o login da app. Códigos e tokens guardados só em hash (`McpGrant`); acesso 8 h, renovação 90 dias.
- Ferramentas: list_projects, project_map, open_screen (o mesmo ecrã e os mesmos campos do Assistente), view_images,
  set_field (valida e grava como uma proposta aplicada). Sem gerações pagas, aprovações nem apagar.
- A app MCP está montada em `/` depois de todas as rotas: só recebe os endereços que a app não reconhece.
- O conector também cria projetos (create_project, opcionalmente ligado a outro, com a bíblia copiada), lista e copia
  assets de outros projetos (importable_assets, import_assets, a mesma importação da app) e apaga cenas e planos para a
  Reciclagem (delete_scene, delete_shot; `app/trash.py`): fotografia completa + ficheiros movidos para
  media/_reciclagem/<id>; a página /projetos/<id>/reciclagem recupera tudo (ids novos, ligações e versões acertadas);
  passados 30 dias apaga-se de vez. A ligação «Reciclagem (n)» aparece na barra de topo quando há algo.
- O guião de uma cena edita-se pelo conector no screen `scene:<id>` (o ecrã do episódio/ato/curta com a cena em foco,
  como na app): `new_lines:<id>` acrescenta linhas, `line:<id>.text` altera uma. O project_map mostra-o em cada cena.
- Campos novos do Assistente (e do conector, no screen `scene:<id>`): `scene_location:<id>` (o local do cabeçalho,
  pelo código do local) e `scene_order:<id>` (a posição da cena no episódio/ato/curta; as outras renumeram).
- Campo `line_edit:<id>` (Assistente e conector): muda o tipo de uma linha do guião (ação/diálogo), quem fala e o
  texto, mantendo o número. Apagar linhas fica de fora: os números das linhas estão na encenação e nos planos.
- Mais campos (Assistente e conector): `script:<cena>` (o guião completo pela ordem final; as linhas com id mantêm os
  planos e os tempos da encenação, as sem id são novas, as que faltam apagam-se), `sheet_refs:<asset>` (lista completa),
  `asset_code:<asset>` (via `rename_asset`, que agora também troca o código na encenação 3D e nas cores da cena) e
  `asset_kind:<asset>`. Ferramenta `delete_asset`: o asset vai para a Reciclagem com os mesmos ids e com o registo de
  onde era usado; recuperar repõe tudo.

## 6 de outubro de 2026: imagens pelo conector MCP (`app/mcp_images.py`)

- A) gerar na app: write_prompt (o escritor, como «Escrever»), generate_image (prompt_targets.enqueue, como «Gerar»;
  count até 4; confirm_cost só depois de o utilizador aceitar o custo do Artlist), generation_status, list_models.
  B) gerar fora: get_reference_images (prompt_targets.inputs, só aprovadas; endereços /pub/ref de 1 h ou base64),
  upload_image (png/jpg/webp até 20 MB, convertida para PNG, guardada por jobs.store_result como «por decidir», com um
  ImageJob provider «mcp» que regista o prompt e o modelo). Comum: list_candidates, view_candidates, approve_image
  (approve_media) e reject_image (reject_media: apaga o ficheiro).
- Tipos: sheet, portrait, look (look_id), view (vista de um local, view_id), previs (vista do local num momento do
  plano, sb_shot:N + view_id: a app não gera imagens de planos à parte), cover. Os locais não têm folha.
- Diferença em relação ao botão Gerar: com o prompt por escrever ou desatualizado, o botão escreve-o e gera; o
  generate_image pede write_prompt primeiro (para o cliente ver o prompt antes de gastar).
- Testado no projeto 1: pedido e candidata do MCP iguais aos do botão Gerar, campo a campo; aprovar pelo MCP e pela
  interface com o mesmo efeito; rejeitadas fora das referências e das imagens.

## 6 de outubro de 2026: nada fica perdido no disco

- `media.delete_file` apaga também os derivados sem registo (`media.companions`: a tira do vídeo-guia) e remove as
  pastas que ficam vazias, sem subir acima da pasta do projeto (`media.prune_empty_dirs`). A reciclagem faz o mesmo ao
  mudar ficheiros. Vale para rejeitar, apagar planos, cenas e assets, e para a reciclagem.
- Limpeza feita: 6 tiras órfãs do antigo P08 e 82 pastas vazias acumuladas; nenhum ficheiro sem registo no disco.

## 6 de outubro de 2026: ver e verificar maquetes pelo conector

- view_layout (`app/mcp_layout.py`): imagens tiradas com o mesmo visualizador da app (buildLayer/actorMesh exportados
  de scene3d.js e usados por `static/js/maquete_still.js`) num Chromium sem ecrã dentro do contentor (Playwright 1.63,
  instalado no Dockerfile; `app/maquete_render.py` abre /interno/maquete/<chave>, só de 127.0.0.1). Vistas: top
  (grelha de 1 m, mais forte a cada 5 m, eixos, zonas, #n, seta da vista A), A/B… (vistas do local), eye, orbit,
  floor:<z> (corte acima do piso), camera; focus aproxima as plantas a uma divisão ou retângulo. Nas perspetivas os #n
  têm tamanho fixo e escondem-se atrás do que os tapa.
- check_layout (`app/layout_check.py`, só lê): sobreposições (mobília a mais de 10 cm dentro de paredes finas),
  portas tapadas por paredes (falta o vão) e portas/janelas soltas em divisões interiores, peças fora das divisões e
  divisões sem área, peças a flutuar ou enterradas (as encostadas a paredes contam como penduradas), assentos com
  frente virada para o lado errado de uma mesa/TV/balcão próximo, medidas (portas fora de 0,7–1,5 m, pé-direito < 2,3 m).
- Instruções do conector: depois de gravar uma maquete, view_layout ["top", "A"] e check_layout antes de reportar.

## 2026-10-07 — MCP: criar temporadas, episódios e atos
O ecrã `story` passa a ter `new_season` e `new_episode:<season_id>` (séries) e `new_act` (filmes), como os botões da app
(ficam no fim; o episódio herda a duração-alvo da série). A resposta do set_field diz o ecrã onde continuar
(`episode:<id>` para criar cenas com new_scene). Também disponível no Assistente da app.

## 2026-10-07 — MCP: vistas, renders e vídeo-guia
O conector faz nas maquetes o que o utilizador faz na app: auto_views, add_view (= «Guardar vista aqui»), rename_view,
delete_view, campo zonerefs:<asset>:<zona> (referências de cada divisão), render_maquete (asset:N → renders das vistas
com profundidade; sb_shot:N → previs dos momentos e vistas novas do local), record_guide (em segundo plano, quadro a
quadro a 24 fps, ~2 min para 15 s), list_guides, approve_guide, reject_guide. Os renders e a gravação usam o próprio
visualizador (scene3d.js mount, ganchos el.m3dCapture / el.m3dFrames) na página interna /interno/maquete/<chave> (só
127.0.0.1, chave de uso único) e são guardados pelas mesmas funções das rotas (save_view_render, save_moment_render,
save_guide). Sem sessão de login para o Chromium. write_prompt/generate_image de uma vista recusam com render desatualizado.

## 2026-10-07 — ByteDance (BytePlus ModelArk): Seedance direto e registo de personagens
Fornecedor de vídeo «bytedance» (app/bytedance.py), fila própria, pago (não se repete sozinho; depois de um reinício
retoma a espera pelo task_id guardado em job.params, sem pagar outra vez). Modelos: Seedance 2.0, 2.0 Fast, 2.0 Mini e
2.5 (ids dreamina-seedance-…; têm de estar ativados na consola). Chaves em Configuração → Chaves de API → BytePlus:
chave de API (Bearer, gera), Access Key + Secret Key (assinatura OpenAPI V4, biblioteca de assets).
Registo de personagens (app/character_registry.py, tabela characterregistration, migração 0049): cada imagem aprovada
de uma personagem (folha, retrato, looks) vira um asset AIGC num grupo por personagem e segue como asset://<id> — o
Seedance recusa caras realistas por URL. Regista-se à mão (página da personagem → «Registo na ByteDance») ou sozinho
quando um vídeo da ByteDance as leva; as outras imagens, o vídeo-guia e as vozes vão por links /pub/ref (6 h).
Custo mostrado é estimativa (tokens ≈ L×A×24×s/1024; 2.0 $7/M, 2.5 $10.7/M — por confirmar na consola).

## 2026-10-08 — Navegação da cena
Barra igual no guião (cena aberta no episódio/ato/curta), no storyboard (cena e plano) e no vídeo (plano):
cena anterior/seguinte no mesmo contentor, separadores Guião · Storyboard · Vídeo da mesma cena/plano e os planos
da cena com o estado do passo atual (app/scene_nav.py, templates/_scene_nav.html). Com uma cena aberta, o bloco do
episódio/ato fica dobrado. Desempenho: o estado dos planos no progresso das etapas já não monta a cena 3D
(moments.rows(renders=False)) e é calculado uma vez por pedido.

## 2026-10-08 — Modelo e resolução de vídeo por plano
Projeto: video_model + video_resolution (padrão; etapa Vídeo → «Padrão do projeto»; MCP: project.video_model /
project.video_resolution no screen bible). Plano: Shot.model None = padrão do projeto; Shot.video_resolution None =
padrão (limitada às resoluções do modelo). Migração 0050 pôs os planos com o modelo da Configuração a seguir o padrão.
A Configuração passa a ser só o padrão dos projetos novos. Cada take guarda resolução, modelo e (Artlist) o
generationId em job.params; o rótulo do take mostra modelo · resolução · créditos, e «Prompt e definições».
Custo: app/video_cost.py — Artlist get_generation_cost (exato, guardado 7 dias em video_quotes), fal pela API de
preços, ByteDance estimativa. Rascunho: take Seedance 2.5 Artlist a 480p com generationId, até 7 dias → «Passar a
1080p (mesmo take)» (render_draft, ImageJob kind draft_render). Shot.video_prompt_model avisa «escrito para outro
modelo» com Reescrever. Páginas: campos data-only-if-changed (prompt, som) só seguem se mexidos — uma página antiga
apagava o que o MCP gravara entretanto.

## 2026-10-08 — O que segue no pedido de vídeo, por plano
app/video_inputs.py: cada plano escolhe o vídeo-guia (ligado/desligado), cada imagem candidata (ligada, como
referência, frame inicial ou final) e cada voz. Guardado em Shot.video_refs (imagens ligadas; None = automática) e
Shot.video_inputs (migração 0051: guide=false, voices_off, modes). Com frame inicial o pedido passa a
imagem-para-vídeo (kind video_frames): seguem só as frames e o prompt — não se mistura com referências, guia ou vozes
(conservador: nenhum fornecedor confirmou a mistura). Página: secção «O que segue no pedido» (só segue se mexida —
data-unless-touched). MCP: lista com estado e modo no open_screen e campo shot:N.video_inputs (JSON; erros com as
opções válidas; notas quando a escolha implica algo). O escritor só usa o que segue (sem guia descreve a câmara);
guide_check avisa quando o prompt cita algo desligado, com Reescrever. Cada take guarda params.sent e params.guide.

## 2026-10-08 — Resolução das imagens
Project.image_resolution (1K/2K/4K, migração 0052, 2K por omissão; etapa 1 e MCP project.image_resolution). Seletor
ao lado do modelo em cada Gerar de imagem (só as resoluções do modelo; GPT Image pela subscrição e modelos sem
chave: tamanho fixo, seletor desligado). Job.params.resolution. Artlist: artlist.RESOLUTIONS (chave e valores de cada
modelo, lidos do get_model_config — antes ia sempre «1k»); fal: image_models.request(res=…) põe "resolution" (Nano
Banana) ou image_size em píxeis dentro dos limites de cada família. MCP: generate_image(resolution) com erro e opções
válidas; list_models com as resoluções e o custo por resolução (cotações do Artlist numa só passagem, guardadas).
As imagens guardam-se no tamanho que o modelo devolve (a v243 tem 1088×608 porque o pedido era ~1K); o MCP mostra
miniaturas de 768 px. Nos vídeos da fal, as imagens seguem como o ficheiro original.

## 2026-10-08 — Custo do take com os inputs reais
O Artlist cota pela variante que os inputs escolhem (com vídeo de referência é «multi-to-video», bem mais caro) e
só cota com os ficheiros: video_cost.shot_args/shot_quote sobem as mesmas imagens, guia e vozes do pedido
(artlist.quote_request, sem gerar) e cotam com e sem o vídeo → «1340 créditos (460 + vídeo-guia 880)». Chave da
cotação = modelo, resolução, duração, som, tipo de pedido e inputs (nunca valor antigo). Ecrã e MCP usam a mesma.
A confirmação do Artlist avisa se o pedido difere do ecrã; o take mostra «o ecrã mostrava X». Links /pub/ref passam a
ficar também em disco (servem noutro processo e depois de um reinício; só ficheiros de media).

## 2026-10-08 — Duração dos planos pelo MCP
storyboard.change_duration: só durações que o modelo do plano aceita (erro com as opções, sem arredondar); os planos
seguintes da cena andam com a diferença (stage_start); erro com os segundos em falta se o fim passar a encenação.
Campos: shot:N.duration (video_shot e sb_shot) e scene_duration:N (sb_scene; só muda o fim, as chaves das
personagens ficam). O open_screen mostra a duração, o troço, as durações aceites e o estado do vídeo-guia (aprovado ou
por aprovar) e dos previs; list_guides marca os guias com outra duração. A página de vídeo usa a mesma regra; os
seletores de modelo, resolução e duração só seguem se mexidos (uma página antiga não desfaz o que o MCP gravou).

## 2026-10-08 — MCP: takes, frames, campos inteiros, vários campos, voz
app/mcp_takes.py: list_takes (estado, modelo, resolução, duração, créditos, links de 2 h para o vídeo e a miniatura
`<take>_thumb.jpg`, prompt e o que seguiu), frame_from_take (ffmpeg; shot_ref:N → MediaFile role «shotref», token
@SHOT<N>.f<id>, aparece nas video_inputs do plano, desligado; view:a:v → imagem aprovada da vista), get_field (valor
inteiro), set_fields (uma transação: valida tudo, grava tudo ou nada), quote_shot. set_field passa pela mesma
transação e as gravações do mesmo projeto fazem-se uma de cada vez. list_guides devolve dados. Asset.voice_note (EN,
migração 0053): a app junta-o à voz em cada fala no envio («…@X.voice, speaking English with a clear Spanish accent»).

## 2026-10-08 — Planos pela ordem na encenação; páginas ao vivo
Shot.number segue o início do troço (stage_start; empate pelo id): um before_flush (models._renumber_shots) renumera a
cena sempre que um plano é criado, apagado ou muda de início (migração 0054 renumerou o que havia). Vale em toda a app
(storyboard, vídeo, montagem, MCP); os ids, ficheiros, takes e referências (@SHOT<id>) não mudam; os botões de mover
planos saíram. Páginas ao vivo (app/live.py): cada commit sobe o contador do projeto (exceto estados de pedidos, chat,
configuração); a página pergunta /interno/versao/<projeto> a cada 3 s (só visível, sem pedido em curso) e faz morph
se mudou — os campos mexidos não são tocados.

## 2026-10-09 — Takes rejeitados ficam guardados
Rejeitar um take (role clip) já não apaga o ficheiro: sai de escolhido e de por decidir e fica em «Rejeitados» na
página do plano (Repor → por decidir; Escolher). As imagens rejeitadas continuam a ser apagadas. list_takes mostra os
rejeitados com o link do vídeo. Os takes rejeitados antes disto já tinham o ficheiro apagado.

## 2026-10-09 — Desempenho (definitivo)
app/memo.py: @memo guarda por pedido (na sessão) as contas caras que as páginas repetiam dezenas de vezes
(video_candidates, request_plan, guide_for_video, moments.rows/location/view_images/cast_images/render_hash,
scene3d.compose/scene_cast/in_frame, video_inputs.pool/frames/items, get_config, model_profiles.active, video_models);
limpa-se a cada flush/commit/rollback e não se usa com mudanças por gravar. memo.across guarda entre páginas, pela
versão do projeto (app/live.py): o estado dos planos no progresso das etapas e o resumo do painel do Assistente.
Vídeos nas páginas: preload="none" com capa JPG (/media/capa/<vídeo>, <nome>_thumb.jpg); só o leitor do take
escolhido carrega (para guardar fotogramas). Páginas GET com mais de 1 s ficam no log («página lenta»).

## 2026-10-12 — Sala de argumentistas
/projetos/<id>/sala (app/writing.py, app/routes/room.py, app/mcp_writing.py): ficheiros de texto livres do projeto
(caminhos com pastas) onde o utilizador e as IAs do conector MCP (ChatGPT, Claude — o autor é o client_name do registo
OAuth) escrevem e discutem a história e os guiões sem tocar nos dados de produção. Cada gravação é uma versão (texto
inteiro, autor, mensagem); write_file exige base_version (sem sobrepor o trabalho do outro), edit_file muda partes
(texto exato e único). Comentários por ficheiro e conversa geral. Só o utilizador aprova: o texto passa para o destino
(campos de texto: premissa, logline, sinopse, mundo, arcos/resumos, sinopse de episódio, perfil de personagem; ou
scenes:<episódio/ato/projeto> = cenas e linhas do guião) e o ficheiro fica trancado; só o utilizador destranca, e a app
fica com a versão aprovada até nova aprovação. Guião em texto (writing.SCRIPT_FORMAT): «## Título», Local/Hora/Resumo,
parágrafos de ação, NOME + fala. Ao aprovar, as cenas emparelham-se por semelhança (a ordem não conta) e as linhas
iguais, mudadas ou só movidas mantêm o id (planos, encenação); antes de aprovar mostra-se o plano (novas, mudadas,
para a Reciclagem, planos que perdem falas); personagens desconhecidas impedem aprovar.

## 2026-10-12 — MCP: vídeo, takes e vozes
app/mcp_video.py: generate_video (o mesmo pedido que «Gerar vídeo»; sem confirm_cost só devolve o custo cotado, com
confirm_cost=true o pedido segue com confirmCost — só depois de o utilizador aceitar), write_video_prompt (o escritor da
app), upload_take (link https ou base64, MP4/MOV; fica por decidir, com o prompt e o modelo indicados num ImageJob
provider "mcp"), approve_take / reject_take / restore_take (como Escolher / Rejeitar / Repor), list_voices,
upload_voice (áudio para a ficha da personagem, normalizado como no carregamento da app), approve_voice / reject_voice.

## 2026-10-12 — Sala de argumentistas: discussão, propostas, conflitos e tarefas
Participantes com nome: o utilizador assina com AUTHOR_NAME (writing.USER), as IAs o client_name do conector (Claude,
ChatGPT). Comentários em fios (reply_to), menções (@Claude, @ChatGPT, @<utilizador>, @todos) e notificações por participante
(WritingNotice; my_notifications no MCP; «Notificações» na sala e contador na barra de topo). Propostas (WritingProposal,
#n por projeto): título, justificação, impactos na continuidade, decisões para o utilizador, texto proposto sobre uma versão
de base; pareceres approve/contest por revisão (uma revisão nova anula os anteriores); alternativas (alternative_to);
uma IA aplica só com parecer favorável de outro participante e sem contestações; o utilizador aplica ou rejeita sempre.
Conflitos: fusão a três (writing.merge3) em write_file com base antiga e ao aplicar propostas; mudanças no mesmo sítio
são recusadas com as linhas em choque. Tarefas (WritingTask): título, detalhes, responsável (ou quem pegar), estado
(por fazer, em curso, feita com resultado obrigatório, cancelada), ligadas a um ficheiro ou proposta, com discussão.

## 2026-10-12 — Sala: modo de leitura
Os ficheiros abrem em «Ler» (app/reading.py): markdown composto em Literata (markdown-it-py, sem HTML do texto) e os
guiões com aspeto de guião de cinema em Courier Prime (cabeçalhos de cena, nome da personagem ao centro, indicação entre
parênteses, falas numa coluna estreita, transições à direita). Reconhece o formato da sala («## Título» + Local/Hora) e
o clássico em markdown («**1. EXT. …**», «**NOME** *(indicação)*» e a fala como citação «> …»). Os códigos dos assets
aparecem pelo nome. «Editar» (em escrita) e «Texto simples» mostram o texto tal como está.

## 2026-10-12 — Sala: correção das tabelas na leitura dos guiões
Causas: (1) o renderizador dos guiões deitava fora as linhas em branco do texto antes da primeira cena, e o markdown
recebia a tabela colada aos parágrafos seguintes (no GFM, uma linha não vazia a seguir a uma tabela é mais uma linha):
as continuidades do E2 entravam na tabela; (2) num ficheiro com «Local:/Hora:», qualquer «## …» era tratado como cena:
a ficha e as continuidades do E3 viravam cenas e o conteúdo era composto como ação, com os «|» à vista. Correção em
app/reading.py: segmentação explícita (reading.segment) que não perde linhas; uma cena só começa num cabeçalho clássico
ou num «## Título» seguido de Local/Hora/Resumo (ou em qualquer «##» quando o destino é «cenas», como na aprovação);
um título markdown que não é cena fecha a cena; o que não é cena vai inteiro ao parser; dentro das cenas é o parser que
classifica tabelas, listas, código e citações; os códigos dos assets trocam-se pelos nomes só no texto corrido (não no
código). Testes: tests/test_reading.py (python -m unittest tests.test_reading).
Falas soltas: numa cena, uma citação «> …» sem nome antes (a seguir a uma linha de ação) continua a fala de quem falou
por último na cena e aparece com o nome e «(cont.)», como num guião; sem ninguém antes, é uma fala sem nome. Um ficheiro
é guião se tiver pelo menos uma cena fora de blocos de código (antes eram precisos dois cabeçalhos clássicos).

## 2026-10-13 — Sala: apagar ficheiros e scroll da barra
Apagar (botão no cabeçalho do ficheiro; MCP delete_file) manda o ficheiro para «Apagados» (deleted_at; o caminho passa a
.apagados/<id>/…, para o original ficar livre), com versões, discussão e propostas (as abertas ficam retiradas), e
notifica os participantes. As IAs só apagam ficheiros em escrita; o utilizador apaga também os aprovados (o que passou para
a app fica na app). Repor (app ou MCP restore_file; com o caminho ocupado fica «… (reposto)»). Apagar de vez só na app,
a partir de Apagados. Um ficheiro apagado só se lê. A barra dos ficheiros rola sozinha quando não cabe no ecrã.
Sala: a barra mostra os ficheiros numa árvore de pastas (subpastas incluídas) que abrem e fecham; cada pasta lembra o
estado neste browser (localStorage, por projeto); a pasta do ficheiro aberto abre sempre; botões para abrir/fechar
todas; o morph da página ao vivo não mexe no «open» das pastas.

## 2026-10-13 — Agenda
/agenda (fora dos projetos; ligação no topo com o número de itens para hoje e atrasados): itens = blocos de tempo
(start → due) ou um dia (só due), de qualquer projeto ou de nenhum, sem subtarefas (decisão do utilizador: «alocar
tempo para um episódio»; o resto é microgestão). Tipos: episódio, guiões, short, publicação, outro. Vistas: Hoje
(atrasado, hoje, próximos 7 dias, feito hoje), Quadro (por fazer / em curso / feito, arrastar), Linha do tempo (faixa
por projeto, barras e pontos, 4–26 semanas, abre em hoje) e Visão geral (progresso por projeto, atrasos, a decorrer,
próximo prazo). Publicações recorrentes (dias da semana, período, numeradas {n}; apagar tira as por fazer). MCP:
agenda_today, list_agenda, add_agenda_item, update_agenda_item, delete_agenda_item, add_recurring_posts (app/mcp_agenda.py).
Agenda › Calendário: modo Mês (semanas de segunda a domingo; os blocos atravessam os dias e continuam na semana
seguinte; os itens de um dia são etiquetas com ponto da cor do tipo) e modo Ano (12 meses pequenos; em cada dia as
cores do que lá há, com o resumo ao passar o rato; clicar abre o mês). Setas para o mês/ano anterior e seguinte e
«Hoje». Linha do tempo: opção «1 ano» (52 semanas).
Sala: limite de cada ficheiro subiu de 400 000 para 2 000 000 caracteres (o maior já tinha 394 989) e o de cada
comentário de 20 000 para 100 000. Com ~1,9 M caracteres: leitura 0,9 s, fusão e diff instantâneos (diff_rows usa
autojunk nos ficheiros grandes: 2 s → 0,04 s).

## 2026-10-14 — Sala: PDF dos ficheiros
Botão de download no cabeçalho de cada ficheiro (também numa versão antiga aberta): /projetos/<id>/sala/<f>/pdf[?ver=N]
(app/room_pdf.py). Usa o HTML do modo de leitura com uma folha de estilos de papel (A4; texto em Literata; guião em
Courier Prime 12 pt com as margens de um argumento, nome da personagem ao centro, falas recuadas) e imprime com o
Chromium do servidor (Playwright), com o rodapé «prefixo · ficheiro · versão» e «página / total». ~2 s por ficheiro.

## 2026-10-15 — Assistente na Sala de argumentistas
O painel do Assistente aparece na sala (app/assistant/room_ctx.py). Contexto «room» (conversa geral: ficheiros,
propostas, tarefas, comentários recentes) e «room_file:<id>» (o ficheiro inteiro até 150 000 caracteres, a discussão e
as propostas dele). Pode propor, assinado «Assistente» e só gravado com «Aplicar»: comentários (gerais ou do ficheiro,
com menções), ficheiros novos, tarefas, mudanças por partes, o texto inteiro (ficheiros até 60 000 caracteres) e
propostas formais para os outros participantes darem parecer. A versão vai na chave do campo (room_edit:<f>:<v>…) e
serve de base à fusão a três quando o ficheiro mudou entre a proposta e o «Aplicar».

## 2026-10-15 — Revisão de UI/UX (rede de segurança, erros, navegação, custos, Agenda, Sala, nomes)
- Apagar na app (cena, temporada, episódio, ato, plano, asset) vai para a Reciclagem, como pelo MCP
  (trash.trash_container para temporadas/episódios/atos, com as cenas); a Reciclagem aparece sempre no topo.
- «Rejeitar» deixa de apagar o que o utilizador gerou ou carregou (takes, folhas, retratos, looks, vistas, capa, vozes,
  fotos): fica em «Rejeitados», com Repor/Aprovar (media_service.KEEP_ON_REJECT); os ficheiros técnicos (vídeo-guia,
  renders) apagam-se. Confirmação em todos os Rejeitar; textos certos.
- Erros: aviso (toast) quando um pedido falha ou não há rede; erros das gerações em linguagem de utilizador com «Ver
  detalhes»; «Repetir» pede confirmação (pode ter custo). Aviso de alterações por guardar em todos os formulários (e
  Ctrl/Cmd+S); na Sala, um conflito ao guardar mantém o texto no editor com as diferenças; comentários com erro não se perdem.
- Navegação: a app lembra o último projeto e a última página de cada um (cartão, seletor, «Voltar a …» nas páginas
  globais, «Voltar ao projeto» na Sala); o cartão abre a etapa que mostra; a árvore e os títulos da Montagem ficam na
  etapa; o episódio atual segue a cena/plano aberto; «Seguinte: …» no fim de cada etapa; a barra da cena passa de um
  episódio/ato para o seguinte; Assistente no telemóvel na Sala.
- Custos: custo de cada modelo de vídeo nas listas; confirmação com o custo antes de gerar um take.
- Agenda: filtros por tipo e pesquisa em todas as vistas; vista Lista com ações em massa (feitos, estado, mudar datas,
  apagar); recorrências editáveis (refaz as por fazer de hoje em diante); diálogos não perdem o que se escreveu;
  ligação aceita o endereço inteiro; «Agendar» no topo de qualquer página de projeto; «Parar» volta a por fazer.
- Sala: pesquisa nos ficheiros e na discussão; o utilizador também faz propostas a partir do editor; aviso enquanto
  prepara o PDF; mensagens sem as dicas para as IAs.
- Nomes: «escritor de prompts» (os prompts) ≠ «Assistente» (o chat); take em vez de clip; Guardar; português claro
  (Primeira/Última imagem, parte da encenação, notas de encenação, Traços marcantes, Formato); sem «painéis».
- Projetos: arquivar (sai da lista e do seletor; «Arquivados»), pesquisa na lista; criar um asset abre a ficha nova.
- Guião da cena (História): acrescentar, mover e tirar linhas faz-se no browser (sem pedidos); guardar mantém o id das
  linhas que já existiam (via fields.set_script) — antes, guardar apagava e recriava as linhas e desligava os planos
  das falas que cobriam (ShotLineLink) e os tempos da encenação.
- Ficha do asset: os resultados da folha, do retrato e dos looks usam os blocos comuns (m.results, m.upload_zone).
- Saldo do Artlist junto aos botões de gerar (guardado 5 min no servidor; uma chamada de cada vez).

## 2026-10-15 — Frame-chave do plano (o storyboard final)
Teste do utilizador: gerar primeiro uma imagem do take (personagens no sítio, a partir das folhas e do local) e mandá-la
como referência ao modelo de vídeo, com as folhas, deu bons takes com menos tentativas. Sem vídeo-guia (o Seedance 2.0 e
outros seguem-no mal), só com referências o modelo não sabia onde estava cada personagem.
Fluxo, sem voltas atrás (pedido do utilizador): o frame-chave é o ÚLTIMO PASSO DO PLANO NO STORYBOARD («5 · Frame-chave»,
depois da câmara, das vistas e do vídeo-guia) — gera-se e aprova-se só aí. As etapas seguintes só o usam: a página Vídeo
mostra-o em «Do Storyboard» (com «Mudar no Storyboard») e segue no pedido; a Montagem mostra-o nos planos sem take.
- Shot.key_prompt + Shot.key_image_id (migração 0060); app/keyframe.py; alvo de prompt «chave» (prompt_targets), guia
  «key» (Configuração → Prompts → Frame-chave de um plano).
- Com cena 3D: parte do previs do 1.º momento (render cinzento, a geometria; ImageJob.params base + hash da cena), a
  vista do local desse momento e as folhas (do look da cena) de quem a câmara vê — o mesmo caminho das vistas da maquete
  (jobs._locview). O escritor recebe a cor da cápsula de cada personagem e o que o previs mostra nesse instante. Sem cena
  3D: compõe pela descrição do plano, com as folhas das personagens da cena e a imagem do local.
- O frame-chave aprovado do plano anterior da cena só o escritor vê (luz, roupa, sítio); não vai para o gerador (copiava
  a composição, como as fotos reais).
- No vídeo: aprovado, segue como 1.ª referência (@P<nn>.key) na escolha automática; a vista do 1.º momento deixa de
  seguir (com vídeo-guia fica só a do fim). Sem cena 3D seguem também as folhas das personagens da cena. O vídeo-guia
  continua a ser escolha do plano. Nos modelos só com frames, o frame-chave é a imagem inicial. O escritor do vídeo
  descreve a ação a partir das posições dele.
- Storyboard da cena: «Storyboard final» = a tira dos frames-chave aprovados pela ordem dos planos (só aparece quando há
  algum; os que faltam levam ao passo 5 do plano).
- Montagem: plano sem take com frame-chave mostra-o («sem take»); «Ver seguido» mostra-o durante a duração do plano —
  um animatic até haver takes. O zip continua só com takes.
- Mudar a câmara ou a encenação depois marca o frame-chave como desatualizado (Storyboard e Vídeo). Rejeitar guarda-o;
  as candidatas podem ser editadas. MCP: write_prompt/generate_image/list_candidates com kind keyframe e screen
  sb_shot:N; campo shot:N.key_prompt no ecrã sb_shot.
- Telemóvel: a barra do topo alargava a página em todas as vistas (.shell sem coluna limitada); agora cabe (só o código
  do projeto no seletor, sem o logótipo ao lado do menu).

## 2026-10-15 — Menos estímulo visual (regras de desenho das páginas)
Pedido do utilizador: as páginas tinham demasiada informação visível ao mesmo tempo (imagens, vídeos, prompts, avisos,
explicações). Regras (revelação progressiva, carga cognitiva, uma ação principal por bloco):
- Secções que se fecham (macro m.fold, details[data-fold]): título + resumo numa linha. Abertas por omissão só quando há
  coisa a fazer — no plano do Storyboard, só o primeiro passo por fazer (e a câmara quando é preciso gravar o
  vídeo-guia); o que o utilizador abre ou fecha fica nessa página até fechar o separador (sessionStorage); um link
  para dentro (#frame-chave, os passos no topo) abre a secção. O morph não mexe no «open».
- Prompts recolhidos (m.prompt_panel): uma linha com o estado, o início do texto e os avisos; o Escrever e o Gerar
  ficam sempre à vista. Abrem-se para ler ou editar. Nos modais de prompt («Ver o prompt») ficam abertos (open=True).
- Ajuda a pedido: as explicações longas passam a um «?» (m.tip_btn) com balão; fica no máximo uma frase visível.
- Avisos num só sítio: na página Vídeo, «Antes de gerar» junta os avisos do plano (erros primeiro) junto ao Gerar take.
- Listas longas recolhidas: as posições da câmara (com mais de 4) e as passagens de movimento brusco abrem-se a pedido.
- Os takes (o resultado) ficam sempre à vista; o espaço de «nenhum take escolhido» já não ocupa meio ecrã.
Resultado (projeto de teste, P01): página Vídeo de 4746 px para 1750 px; plano no Storyboard de 4179 px para 1726 px.
Alargado (mesmo dia) às outras páginas: etapa 1 (as 7 secções da bíblia fecham-se com resumo; abertas só as
incompletas), cena do Storyboard (notas, quem aparece, local, encenação 3D e guião marcado; os planos ficam sempre à
vista), ficha do asset (referências, fotos, resultados sem nada por decidir, retrato, guarda-roupa, voz e traços, voz de
referência, ByteDance), Configuração → Prompts (cada guia do escritor numa linha; regras e validações fechadas) e a
grelha de assets (cartões de 240 px: 3 por linha em vez de 2 — o utilizador tinha dito «podes fazer os cards maiores
se quiseres» em 29/09). Um campo obrigatório numa secção fechada abre-a ao gravar (evento invalid).
Antes → depois: etapa 1 2989 → cabe no ecrã; Mundo 4392 → 2445; cena 2543 → 1321; personagem 2392 → 1512;
Configuração → Prompts 4773 → cabe no ecrã.
Consistência: todas as secções que se fecham usam a mesma seta à esquerda (.fold-chev, roda ao abrir) e o mesmo resumo
(«O mundo», os grupos de assets, o Episódio/Ato da História passaram a este padrão); explicações nos títulos só pelo «?»;
um subtítulo de uma frase curta é aceite (Configuração). As setas para baixo que ficam são botões de mover, não secções.
Estáticos: ao mudar app.css/app.js/scene3d.js sobe-se o ?v= (base.html e o import em app.js).

## 2026-10-15 — Maquetes: relevo, natureza, mais interiores e modelos 3D importados
Pedido do utilizador: maquetes mais intricadas (canyons, rochas, paisagens, interiores com mais detalhe). Regra mantida:
o Assistente escreve parâmetros (JSON), nunca geometria; tudo cinzento e liso (a forma), o aspeto vem das folhas e das
vistas (detalhe a mais é copiado pelos modelos de vídeo, como os bonecos articulados).
- Relevo (app/terrain.py ≡ static/js/terrain.js, a mesma conta nas duas linguagens — teste tests/test_terrain.py com
  alturas calculadas pelo navegador): objeto "terrain" {from, to, z, relief, scale, seed, color, features} com
  canyon (path, floor, width, depth, jagged, terraces), hill, mesa, ridge e flat. Fora dos terrenos o chão está a 0; o
  chão plano à volta tem um buraco à medida dos terrenos e fica à altura média da borda, e o terreno tem uma «saia»
  até lá (sem degraus nem buracos para o céu).
- "rock" (rocha irregular, center [x,y] assenta no chão, shape round|sharp|slab|spire) e "scatter" (rochas, árvores,
  pinheiros, arbustos, erva, cactos espalhados num retângulo, desenhados em lote; max_slope, avoid; até 6000 no total).
- Pessoas e veículos: com relevo, o z conta a partir do chão (andam a seguir as subidas e descidas, no navegador e nas
  contas do servidor). Câmara: z absoluto, ou "ground": true numa posição (convertido em absoluto ao gravar).
  A visibilidade (quem a câmara vê, notas dos momentos) conta com o terreno a tapar (parede do canyon).
  O Assistente recebe o resumo do relevo com um quadriculado de alturas e o fundo do canyon ao longo do caminho; as
  vistas automáticas do local ficam à altura dos olhos acima do chão.
- Interiores: +21 peças (bookshelf, wall_cabinet, toilet, bathtub, shower, washbasin, nightstand, dresser,
  coffee_table, rug, curtain, frame, mirror, floor_lamp, fireplace, piano, washer, tv_stand, radiator, office_chair,
  boxes); as de parede penduram-se sozinhas à altura certa (center z = chão).
- Modelos 3D importados (gerar com o Artlist fica para quando houver créditos): .glb na ficha de um adereço ou elemento
  do mundo (secção «Modelo 3D», com pré-visualização; Asset.model3d_id, migração 0061; MediaFile role model3d, o
  anterior vai para «Rejeitados»). Na maquete/encenação {"type": "model", "asset", "center", "rot_z", "height"|"size"};
  um ator com esse código também aparece com o modelo. Em cinzento, frente do glTF (+Z) virada para a frente da cena
  (+Y), normais calculadas se faltarem; sem ficheiro, uma caixa com o nome. Os renders, o vídeo-guia e os renders do
  servidor esperam pelos modelos; o Chromium do servidor lê-os por /interno/modelo/<id> (só 127.0.0.1).
- Medido: 2 renders de um canyon de 300 × 400 m com 420 rochas/arbustos no servidor (sem placa gráfica): 3,3 s.
- Montado na encenação da cena (2026-10-16), só no ecrã, com uma etiqueta do plano no canto e as falas do guião por
  baixo (as linhas de diálogo nos segundos que a encenação lhes marca em "lines"):
  - «Montado»: a ação corre uma vez (0 → duração da encenação) e a câmara é a do plano que começou mais recentemente;
    planos que começam juntos (cobertura do mesmo troço) dividem-no em partes iguais, pela ordem dos números; onde
    nenhum plano cobre a ação fica a câmara anterior e a etiqueta diz «sem plano».
  - «Planos inteiros» (só quando difere, i.e. há sobreposições ou buracos): cada plano todo, pela ordem dos números,
    repetindo a ação dos que se sobrepõem — como montar os takes tal como estão.
  - Clicar num chip P.., na vista livre ou na câmara à mão sai do montado.

## Variações da maquete por cena (2026-10-16)
- Uma cena pode mudar a maquete do local só para si, na encenação: `stage3d.set_changes = {"hide": [#n], "update":
  [{"i": #n, campos}], "add": [objetos], "note": "o que muda"}`. Os #n são os da maquete do local («Alterar por
  partes»); a maquete do local e as outras cenas não mudam. Os juntados continuam a contagem (#total, #total+1…).
- `scene3d.scene_set` devolve a maquete já com as variações (cada objeto leva "n", o número no local): o visualizador
  da cena e dos planos, os previs, os vídeos-guia, a visibilidade, a planta para o Assistente, view_layout e
  check_layout sb_scene:N seguem-na. `scene_base` é a do local, sem variações. Os #n são conferidos ao gravar a
  encenação; se a maquete do local mudar depois e deixarem de bater, fica a do local e aparece um aviso.
- Peças e caixas inclinam-se: rot_x, rot_y (ou "rot": [x, y, z]); também "pos" por "center". Peça inclinada: z é onde
  assenta o ponto mais baixo e x, y o meio da pegada; um assento de pernas para o ar assenta pelo assento (as costas
  pendem para fora da mesa). Caixa: roda à volta do meio. As contas são as mesmas no servidor (tilted_size,
  flipped_seat_h) e no navegador.
- Vistas do local: escolhida a opção b do pedido (aviso + nota automática). As imagens das vistas mostram o local
  habitual; o ecrã do plano (passo 3 e «Antes de gerar» no Vídeo) avisa e os contextos do prompt de vídeo e do
  frame-chave levam «O SÍTIO NESTA CENA NÃO ESTÁ COMO NAS VISTAS DO LOCAL…» com a nota e o resumo. Os renders das vistas
  novas criadas a partir de um plano tiram-se da maquete do local (spec.loc_layer), nunca da versão da cena.
  Vistas próprias da cena (opção a) ficam para depois.
- check_layout: uma peça a flutuar só conta como «pendurada» numa parede ou fachada se estiver à mesma altura (antes,
  um prédio por cima em planta escondia o aviso).

## Frames-chave por momento (2026-10-16)
- O frame-chave só leva quem a câmara vê nesse instante (keyframe.in_shot, scene3d.codes_at): as folhas e o «quem é
  quem» do previs. Quem só entra depois, já saiu ou está tapado vai no contexto como «FORA DESTA IMAGEM (…entra aos X
  s)», para o gerador não o pôr lá. O pedido de vídeo continua com as folhas de todos os que aparecem no plano.
- Um frame-chave aprovado por momento da câmara (storyboard final e animatic). «Gerar deste momento» escolhe de onde
  se gera o próximo (shot.moments["key"] = moment_key; os pedidos guardam hash, moment, n e t). Aprovar substitui só o
  do mesmo momento. Shot.key_image_id é o que segue no pedido de vídeo: o primeiro aprovado, ou o que o utilizador
  escolher com «Usar no vídeo» (de preferência um em que já estão todos nas posições).
- Frame-chave do meio do plano no vídeo: o prompt diz «a imagem deste plano aos X s (NÃO é o início)» e não serve de
  imagem inicial nos modelos só com frames. A vista do local desse momento deixa de seguir à parte.
- Animatic (Montagem, «Ver seguido»): sem take, cada frame-chave fica no ecrã de meio caminho entre o momento dele e os
  vizinhos (a soma dá a duração do plano). O «Storyboard final» da cena mostra-os todos.
- Pela conversa: campo «Frame-chave › Momento» (key_moment:N) no ecrã do plano; aprovar e escolher o que segue no
  vídeo é sempre do utilizador.

## Frame-chave com o ângulo do previs (2026-10-16)
- Problema visto (T1E02 C06 P02 e outros): os modelos de edição com várias referências (GPT Image 2.5) copiam o
  enquadramento da vista do local quando esta foi tirada de outro ângulo («aproximada»/«diferente») e perdem o do previs.
- Testados dois passos (estrutura com flux-pro-depth e depois as personagens): o 1.º passo transformava a cápsula
  colada à câmara num barril e o 2.º ignorava-o e seguia a vista. Retirado.
- O que resultou: uma vista do local tirada da câmara exata do momento («Vista desta câmara»). Por isso: com a vista
  do momento de outro ângulo, ela não segue no frame-chave (keyframe.view_far), o escritor descreve o sítio pela nota
  da zona, e o passo 5 mostra «Antes de gerar» com o botão «Criar a vista desta câmara» (vai para o passo 3, onde se
  gera a imagem da vista). Sem imagem na vista do momento, o passo 5 avisa.
- Proporções: a imagem da vista (gerada do render) abre um pouco o espaço; aceite por agora.

## Nada desatualizado segue em silêncio (2026-10-16)
- Pedido do utilizador: as alterações têm de propagar e ver-se; assinalar não chega — se o pedido continuar a levar o
  conteúdo antigo, gera-se em vão e em silêncio.
- app/outdated.py junta o que fica para trás num plano e PARA o pedido (no servidor, em routes/video.queue_video e
  prompt_targets.enqueue do frame-chave — vale para o botão, o escritor de prompts e o conector MCP) até o utilizador
  decidir. Cada problema traz as saídas como botões, no «Antes de gerar» (página Vídeo e passo 5):
  - frame-chave que segue no vídeo de uma câmara antiga ou feito antes de a câmara/encenação mudarem → gerar de novo /
    seguir sem frame-chave (vai para Rejeitados) / manter (só se não é de uma câmara antiga; params "hash_ok");
  - vídeo-guia aprovado de outra duração ou gravado antes da mudança → gravar de novo / seguir sem vídeo-guia /
    manter (guide_hashes passa a esta versão);
  - imagem de uma vista do local que segue no pedido e foi feita antes de a maquete mudar NO QUE A VISTA MOSTRA →
    refazer / manter (params "render_ok"). Quando chega um render novo da vista compara-se com o anterior
    (render_replaced, antes de o apagar): igual → a imagem continua a valer; diferente → "render_changed". As imagens
    anteriores a esta verificação não têm como ser comparadas (os renders antigos eram apagados) e não contam.
    No frame-chave, uma vista assim simplesmente não segue (o passo 5 diz porquê);
  - prompt editado à mão para uma versão anterior do plano (a versão do último prompt com "basis" ≠ a atual) →
    reescrever / manter este texto (grava a versão atual como basis). Os prompts do escritor já se reescreviam
    sozinhos ao Gerar.
- As listas (cena: linha do plano; Montagem: o cartão sem take) mostram «⚠ desatualizado: frame-chave, vídeo-guia».

## Imagem do sítio para gerar uma vista: a mais parecida (2026-10-16)
- Antes, todas as vistas de uma zona levavam como Image 2 a referência da zona, para onde quer que a câmara olhasse
  (nos arrumos, a vista 11 olha ao longo da divisão; a do P03 olha para o lado oposto e via outras paredes).
- location3d.look_reference: a imagem aprovada da mesma zona tirada com a câmara mais parecida (moments.score, até
  «aproximada»); nunca a da própria vista nem uma desatualizada. Sem nenhuma, a referência da zona, como antes. O
  escritor sabe qual segue e para quê ("Imagem do sítio que segue"); o cartão da vista (ficha do local e passo 3)
  mostra «Aspeto a partir da vista …» ou «… da referência da zona».

## Interface em inglês (2026-10-16)
- Configuração → «Idioma · Language» (GlobalConfig ui_language; português por omissão). O Assistente e o escritor de
  prompts respondem ao utilizador nessa língua (i18n.assistant_note no system prompt); o conteúdo dos campos segue as
  regras de cada campo (os prompts para os modelos continuam em inglês).
- Tradução à saída, não no código: a app continua a gerar as páginas em português e app/i18n.py (TranslateMiddleware)
  traduz o texto visível das respostas HTML com o dicionário app/i18n/en.json — frases exatas, padrões com partes
  variáveis (expressões estritas, com âncora e pedaços fixos obrigatórios; partes nas pontas curtas; uma parte que
  pareça frase de conteúdo por traduzir faz o padrão não servir), linhas compostas (« · », « › ») parte a parte,
  frases separadas só se todas tiverem tradução, datas palavra a palavra. Nunca se traduz <textarea>, <script>,
  <style> nem translate="no"; os valores gravados ficam como estão (as listas mostram-se traduzidas).
- O texto que o navegador escreve sozinho (maquete, avisos, alert/confirm) traduz-se em static/js/i18n.js com o mesmo
  dicionário (MutationObserver), carregado de /i18n/en.json com versão.
- O dicionário fez-se a partir do código (templates, mensagens do servidor, JS, listas) e do texto visto pelo crawler,
  traduzido com um glossário (scratchpad i18n/GLOSSARY.md, copiado para app/i18n/GLOSSARY.md). Ao acrescentar texto
  à app, acrescentar a tradução ao en.json (o crawler com I18N_COLLECT lista o que falta).
- Custo: ~40 ms na página mais pesada (cena com guião longo) com a cache de frases; nada em português.

## 20 de outubro de 2026: fotos de referência dos assets pelo conector MCP

- list_reference_photos (photo_id, tamanho, zonas que as usam, endereço /pub/ref de 1 h para o original; base64 opcional),
  upload_reference_photo (image_url https ou image_base64, png/jpg/webp até 20 MB; guardada como na ficha: em
  assets/<código>/fotos, logo aprovada, porque as fotos não têm aprovar/rejeitar; num local, zone=<divisão> junta-a às
  referências dessa zona, até ao máximo da app) e remove_reference_photo (reject_media, como «Tirar»: vai para
  «Rejeitados»).
- A gravação dos ficheiros carregados passou para files.store_bytes, usada pelo formulário da app e pelo conector.

## 20 de outubro de 2026: áudio das falas e downloads para montagem (MCP)

- Áudio carregado (ex.: a narração de um documentário gerada no ElevenLabs) liga-se a falas do guião, por escolha do
  utilizador: um ficheiro cobre UMA OU MAIS linhas seguidas da cena (as falas podem estar partidas e o áudio ser um só).
  MediaFile role line_audio com scene_id, lines (ids pela ordem) e basis (hash do texto das linhas ao carregar);
  app/line_audio.py. Uma linha só está num áudio: carregar outro que a apanhe manda o anterior para «Rejeitados».
- Desatualizado (regra de propagação): linha apagada, linhas já não seguidas ou texto mudado → aviso no guião com
  «Carregar outro» e «Manter» (só quando só o texto mudou); no export do MCP vem outdated com o motivo; no zip da
  Montagem o ficheiro leva _DESATUALIZADO no nome. Não entra em nenhum pedido de geração (por agora).
- Guião: botão de carregar em cada linha (abre a janela com essa linha escolhida; «Até à linha» para várias); o
  leitor aparece na primeira linha do intervalo, as outras dizem «no áudio da linha N»; «Áudios tirados» com Repor.
- Zip da Montagem: os áudios vão em audio/ (PRJ_T1E01_C02_L03-05_audio.mp3).
- MCP (app/mcp_media.py): export_media(scope projeto/ep-N/ato-N/cena-N/plano-N/asset-N, kinds video/audio/image) dá
  links de 6 h do que está aprovado, por ordem da montagem, e as lacunas; upload_line_audio, remove_line_audio e
  keep_line_audio. open_screen da cena mostra os áudios como [audio:N].

## 20 de outubro de 2026: formato do projeto obrigatório para gerar

- Um projeto sem formato (Bíblia › 05 Formato) gerava em 9:16 sem avisar (o Graywake, uma série 16:9, saiu com
  frames-chave verticais). Agora não se adivinha: gpt.project_aspect devolve None e queue_image, a edição de imagens,
  o pedido de vídeo e outdated.blockers (com a ação «Escolher o formato») recusam com FORMAT_MISSING. Folhas, retratos
  e capa têm proporção própria e não dependem disto.
- O formato passa a ser um campo da bíblia para o Assistente e o MCP (project.aspect_ratio) e o create_project do MCP
  aceita aspect_ratio.
- O GPT Image (ChatGPT) respeita a proporção pedida no texto: dos frames-chave pedidos em 9:16, 18 de 23 saíram verticais.

## 20 de outubro de 2026: modelos padrão por projeto

- Três níveis, o mais específico ganha: Configuração (padrão da app) → projeto (Bíblia › 05 Formato e modelos:
  gerar imagens, editar imagens, vozes, vídeo e resolução do vídeo; vazio = «Igual à Configuração») → o seletor de cada
  botão (uma geração). Campos novos em Project: image_model, image_edit_model, voice_model (o vídeo já tinha
  video_model/video_resolution, cujo formulário saiu da etapa 6, que agora só mostra o padrão com um link).
- gpt.generator_default(session, use, project) devolve (modelo, origem); o seletor diz no rótulo «padrão do projeto» ou
  «padrão da Configuração». A memória do browser do último escolhido passa a ser por projeto e só vale enquanto o padrão
  for o mesmo (mudar o padrão volta a pô-lo em todos os botões).
- Vozes: o padrão do projeto ganha ao «último usado» (que continua a ser guardado na Configuração).
- Assistente e MCP: project.image_model, project.image_edit_model e project.voice_model ("default" = Configuração),
  além de project.video_model e project.video_resolution.

## 20 de outubro de 2026: mais modelos de vídeo da fal.ai

- FAL_VIDEO passa a ter famílias (image_models.fal_video_request monta o pedido no formato de cada uma) e capacidades
  (fal_video_caps): Seedance (como antes: referências, vídeo-guia e vozes), Flux 3 / Flux 3 Draft (imagem inicial; com
  imagem final vai ao endpoint first-last-frame), Flux 3 Max, Kling 3 Pro e Standard, Kling O3 Pro (por referências: até
  4 imagens @Image1…; ou imagem inicial e final), Wan 3.0 e LTX 2.5 Pro. Só os Seedance recebem vídeo-guia e vozes.
- Os pedidos foram validados contra o OpenAPI de cada endpoint da fal (campos obrigatórios, nomes, enums) e o percurso
  completo (botão → fila → pedido) testado numa cópia com a fal simulada; nenhum gerado a sério.
- Custos: os Seedance pela tabela de tokens (API de preços da fal); os outros pela tabela da página de cada modelo
  (por segundo e resolução, ou por segundo com/sem som no Kling), datada de 20 out. 2026. O Flux 3 Max está em
  promoção até 7 nov. (75 % mais barato); a app mostra o preço normal.
- Perfis de prompt: o Kling ganha ref_format @Image{n} (o O3 por referências); o Flux 3 usa o perfil curto (sem guia
  publicado pela BFL).

## 21 de outubro de 2026: Kling 4 e catálogo de vídeo da fal

- Kling 4 (kling/v4/reference-to-video) embutido: um só endpoint, por referências (até 10 imagens @Image1…, até 5
  vídeos @Video1… — recebe o vídeo-guia) ou, sem referências, com imagem inicial (não tem imagem final nem som).
  Preço por segundo por resolução; com vídeo de referência a fal cobra entrada + saída (no mínimo 2× a saída): a app
  mostra o maior dos dois.
- «Todos os modelos da fal» como catálogo, igual ao das imagens: Configuração → Modelos de vídeo da fal.ai → Atualizar
  catálogo (imagem→vídeo e vídeo→vídeo, sem efeitos, avatares, lip-sync, ampliar…; 275 em 21 out.) e escolher os que
  entram nos seletores. Ao ativar, app/fal_video_catalog.py lê o esquema (imagem inicial/final, lista de referências e
  máximo, vídeos e áudios de referência, durações e se vão como número ou texto, resoluções 480p/720p/1080p, proporções,
  som, sintaxe @Image1) — 142 dos 275 são utilizáveis; os outros ficam de fora com o motivo (sem imagens, campos que a
  app não preenche, exigem sempre imagem final). Os pedidos dos 142 validados contra o esquema de cada um.
- Preço dos do catálogo: o texto publicado na página de cada modelo (por resolução, com/sem som, por segundo ou por
  vídeo) — a API de preços só dá o escalão mais baixo; se o texto não se percebe ou dá menos do que esse escalão,
  fica o da API como «a partir de». Mostrado como «estimativa», com o texto original (Configuração e list_models).
- image_models.fal_video() junta embutidos e catálogo; tudo o que listava ou chamava FAL_VIDEO passa por aí, e os
  seletores, a Bíblia, a Configuração, o Assistente e o MCP (list_models) mostram os ativados.
