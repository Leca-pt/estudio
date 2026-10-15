# Regras de construção de prompts

> **2026-10-02:** os prompts passaram a ser escritos pelo escritor de prompts, com estas regras como base (as guias de cada tipo estão em Configuração → Prompts). Acabaram as vistas A–D, os painéis, a imagem-mestra e as receitas montadas: as secções sobre isso ficam como histórico. O fluxo atual está em [DECISOES.md](DECISOES.md) e [ARCHITECTURE.md](ARCHITECTURE.md).

Complemento ao `SPEC.md`. Define como a app monta os prompts de imagem e de vídeo. Aplica-se ao gerador de prompts (fases 3 e 4) e ao chat com LLM (fase 5), que deve seguir estas regras sempre que propuser um prompt.

Baseado numa pesquisa de setembro de 2026 aos guias oficiais e de plataformas (fontes no fim).

## Prioridade

1. Regras confirmadas pelo utilizador na prática (receita e regras da **Configuração geral** do `SPEC.md`)
2. Este documento
3. Guias genéricos

Quando este documento contradiz uma regra confirmada na prática, a regra confirmada prevalece até o utilizador decidir o contrário. Os pontos em conflito estão marcados como **pendente de teste**.

## 1. Princípios gerais

- **Brief, não lista de palavras.** Frases descritivas com sujeito, ação, local, luz e disposição. Nada de listas de tags separadas por vírgulas.
- **O mais importante primeiro.** Os modelos dão mais peso ao início do prompt. O sujeito e a ação abrem o prompt; estilo, luz e ambiente vêm depois.
- **Comprimento controlado.** Prompts longos fazem o modelo "fazer a média" e ignorar instruções. Limites na secção 5.
- **Exclusões pela positiva, no próprio prompt.** Descrever o que se quer ("a plain grey background, empty of objects") em vez de só o que não se quer. Os modelos usados não têm campo de prompt negativo.
- **Adjetivos vazios não ajudam.** "beautiful", "stunning", "masterpiece", "8k", "high quality" não dão direção. Substituir por luz, materiais e composição concretos.
- **Aspect ratio e resolução nunca no texto.** São definições do modelo; o seletor prevalece sobre o que se escreve.
- **Idioma:** prompts sempre em inglês.
- **Corrigir uma coisa de cada vez.** Quando um resultado falha, altera-se só a instrução ligada à falha (a app deve permitir editar o prompt e guardar versões).

## 2. Prompts de imagem

### Ordem dos blocos

1. **Sujeito(s):** quem ou o quê, com a descrição canónica de cada asset visível. Com 2 ou mais personagens na imagem (capa, mestra, planos de grupo), cada personagem entra só com as **features-chave** (uma frase curta): as descrições completas não cabem no brief de 30–80 palavras e a identidade vem das imagens de referência anexadas (folha e retrato). Locais e adereços mantêm a descrição canónica.
2. **Posição e estado:** onde está cada um no enquadramento, pose, adereços (instante parado)
3. **Local:** ambiente e elementos de fundo
4. **Composição e câmara:** tipo de plano, ângulo, altura da câmara, profundidade de campo
5. **Luz:** fonte, direção e qualidade ("soft window light from the left", "single warm lamp above the table")
6. **Bloco de estilo** do projeto
7. **Restrições:** "exactly N people, each appearing once", "only one <objeto>", exclusões do projeto

### Regras

- **Instante parado.** Nunca movimento de câmara, trajetórias ou ações em curso. Verbos de movimento ("walks toward", "pans", "zooms") geram aviso.
- **A luz e os materiais pesam mais do que o estilo.** Nomear a luz e os materiais relevantes (couro gasto, metal escovado, madeira envernizada).
- **Realismo:** quando o estilo é fotorrealista, pequenas imperfeições ("slight film grain", "natural skin texture") funcionam melhor do que palavras de qualidade.
- **Texto na imagem:** entre aspas, com fonte, cor e posição. Curto (3–5 palavras). Por defeito, o projeto exclui texto.
- **Direções de ecrã:** posições e olhares sempre em direções de ecrã ("on the right side of the frame"), conforme a Configuração geral.
- **Edição de uma imagem existente:** descrever só a alteração e dizer explicitamente o que se mantém ("change only the jacket colour; keep the face, pose, lighting and background exactly as they are").

### Modelos

- **Seedream 5.0 Pro:** brief de 30–80 palavras; lê bem hierarquia e relações entre elementos; texto entre aspas.
- **Nano Banana 2 / 2 Lite:** bons para edição rápida e transferência de estilo; mesma estrutura sujeito → ação → contexto.

### Templates

**Folha de personagem**
```
Character reference sheet of {name}: {canonical_description}.
{views, e.g. front view, three-quarter view, side view and back view}, full body, neutral standing pose, arms relaxed, arranged left to right on a plain light-grey background.
Even soft studio lighting from the front, no harsh shadows.
{style_block}
Exactly one character shown in each view, the same person in all views. {project_exclusions}
```

**Folha de local**
```
Environment reference of {name}: {canonical_description}.
{view, e.g. wide establishing view from eye level}, empty of people.
{lighting of the location at its default time of day}.
{style_block}
{project_exclusions}
```

**Folha de adereço**
```
Prop reference of {name}: {canonical_description}.
Shown from the front and at three-quarter angle, isolated on a plain light-grey background, true scale indicated by {scale reference if needed}.
Even soft studio lighting.
{style_block}
Only one {prop} in each view. {project_exclusions}
```

**Imagem-mestra / imagem de plano**
```
{for each visible asset: name + canonical description + position in screen directions + pose/state}.
{location and background elements consistent with the master image}.
{shot type}, {camera angle}, {lens / depth of field; for secondary characters in medium shots: "heavily blurred, face unreadable, partly cut by the frame edge"}.
{lighting}.
{style_block}
Exactly {N} people, each appearing once. {only one <object> where needed}. {project_exclusions}
```

## 3. Prompts de vídeo

### Ordem dos blocos (imagem-para-vídeo, com Start Frame)

A imagem já define o sujeito, a composição e o estilo. O prompt descreve sobretudo **o que muda**.

1. **Ação principal** com tempos explícitos ("in the first two seconds…"), depois pose parada até ao fim
2. **Features-chave** das personagens visíveis (curtas; ver pendente de teste B)
3. **Fala** entre aspas, com descrição da voz e "clear natural lip movement"
4. **Personagens secundárias** quietas e de boca fechada
5. **Câmara:** um só movimento, descrito em frase separada do movimento do sujeito
6. **Som:** voz, efeitos, ambiente, música (ou a ausência dela)
7. **Restrições:** "exactly N people", "no text, no subtitles"

### Regras

- **Uma câmara por plano.** Mais do que um termo de movimento de câmara no mesmo prompt gera aviso.
- **Separar câmara e sujeito.** "The dancer spins slowly. The camera holds a fixed framing." Nunca "spinning camera around a dancing person".
- **Evitar "fast".** É o termo que mais degrada a imagem. Se é preciso ritmo, só um elemento é rápido (a ação, a câmara ou o corte, nunca vários).
- **Tempo do princípio ao fim.** Descrever como o plano evolui: início → meio → fim.
- **Diálogo:** sempre entre aspas duplas. A app coloca as aspas automaticamente a partir do campo de fala.
- **Som descrito por camadas:** voz, efeitos, ambiente, música.
- **Referências numeradas:** quando o modo usa imagens de referência (e não Start Frame), a app numera-as pela ordem em que devem ser anexadas e o prompt refere-as como "Image 1", "Image 2"… Os modos Start/End Frame e referências nunca se misturam.
- **Olhares e posições** em direções de ecrã, com "nobody looks at the camera" em planos de grupo (Configuração geral).

### Modelos

- **Seedance 2.0 Mini:** as primeiras 20–30 palavras são as que mais pesam. Alvo de 50–70 palavras por plano (até 100–150 só com estrutura temporal).
- **Wan 3.0:** sem prompt negativo; imagem-para-vídeo foca-se no que muda; referências como "Image 1", "Video 1", "Audio 1"; Start/End Frame e referências são modos exclusivos.
- **Veo 3.1 / Kling 3.0** (futuro, via API): Veo aceita carimbos de tempo (`[00:00-00:02] …`) e secções de som (`SFX:`, `Ambient noise:`); Kling aceita até 6 planos etiquetados e falas com etiqueta de personagem (`[Character A: Mom, urgent voice]: "…"`).

### Templates

**Template A — receita atual (por defeito)**

É a receita da Configuração geral do `SPEC.md`, confirmada na prática com o Seedance 2.0 Mini. Mantém-se como template por defeito.

**Template B — ordem da pesquisa (pendente de teste A)**
```
{subject(s)} {main action with explicit timing: "in the first two seconds …"}, then {holds the final pose still until the end}.
{key features of each visible character, one short phrase each}.
{character} says "{line}" in a {voice description}, with clear natural lip movement.
{other characters stay still with closed mouths}.
{camera: one movement or "The camera stays static"}.
Sound: {voice}, {effects}, {ambience}, {music or "no music"}.
Exactly {N} people, no text, no subtitles. Keep the framing, lighting and look of the start frame.
```

## 4. Pontos pendentes de teste

Não alterar o comportamento por defeito até o utilizador decidir. Ambos os templates ficam disponíveis e editáveis na Configuração.

- **A. Abertura do prompt de vídeo.** A receita atual abre com "Animate the start frame exactly as it is…". A pesquisa diz que as primeiras palavras são as de maior peso e devem ser o sujeito e a ação. O Template B põe a instrução de manter o frame no fim.
- **B. Redescrever features no vídeo.** A receita atual redescreve as features-chave (confirmado que evita perdas no Seedance). O guia do Wan 3.0 avisa que repetir o que está na imagem cria desencontros. Compromisso: só as features-chave, uma frase curta por personagem, nunca a descrição canónica completa.
- **C. Comprimento.** Prompts de vídeo da receita atual chegam aos ~2000 caracteres (limite do Artlist Studio). A pesquisa recomenda 50–150 palavras. A app mostra o contador e avisa, sem bloquear.

## 5. Validações da app

Avisos visíveis junto ao bloco do prompt. **Nenhum bloqueia** a cópia ou a gravação.

| Verificação | Aplica-se a | Aviso quando |
|---|---|---|
| Palavras | Imagem | acima do limite do perfil em uso (secção 7) na mestra, planos e capa, e acima do limite de referências na folha, retrato e vistas |
| Palavras | Vídeo | acima do limite do perfil em uso (secção 7); há um limite maior com estrutura temporal |
| Caracteres | Todos | mais de 2000 (limite do Artlist Studio) |
| Movimento em imagem | Imagem | verbos de movimento de câmara ou de trajetória |
| Várias câmaras | Vídeo | mais de um termo de movimento de câmara |
| "fast" | Vídeo | "fast", "quickly", "rapid", "rapidly" |
| Adjetivos vazios | Todos | "beautiful", "stunning", "masterpiece", "8k", "4k", "high quality", "ultra detailed" |
| Formato no texto | Todos | "9:16", "16:9", "vertical", "1080p" e semelhantes no prompt |
| Contagem de pessoas | Imagem e vídeo | falta "exactly N people" quando há personagens visíveis |
| Diálogo | Vídeo | fala sem aspas (a app corrige automaticamente) |
| Modos misturados | Vídeo | Start/End Frame e imagens de referência no mesmo plano |

As listas de palavras ficam editáveis na Configuração.

## 6. Chat com LLM (fase 5)

Quando o LLM propõe ou corrige um prompt:

- Segue a ordem de blocos e os limites deste documento e a receita da Configuração geral.
- Usa as descrições canónicas (ou, em imagens com 2 ou mais personagens, as features-chave) dos assets tal como estão guardadas; nunca as reformula.
- Numa correção, altera só a parte ligada à falha descrita e diz qual foi a alteração.
- Devolve o prompt como proposta, com o contador de palavras, para aprovação.

## 7. Perfis por modelo

As regras das secções 1 a 5 valem para todos os modelos. O que muda de modelo para modelo é o comprimento e a sintaxe, agrupados em perfis. **O perfil em uso é o do modelo por omissão** escolhido em Configuração → Modelos e geração (um para imagem, outro para vídeo). Escolher outro modelo no botão Gerar não muda as regras nem obriga a refazer o prompt.

Perfis afinados por pesquisa de 29/09/2026 aos guias dos fabricantes (O = oficial, P = plataforma, C = comunidade).

| Perfil | Modelos | Limite (por omissão) | Dicas | Evidência |
|---|---|---|---|---|
| Imagem · Brief curto | Flux, Wan, Grok | 80; 120 nas referências | 30–80 palavras, o início pesa mais | Flux O (BFL); Wan e Grok sem guia |
| Imagem · Brief médio | Seedream, Qwen | 100; 150 nas referências | sujeito primeiro; longos dispersam; "Image 1" | Seedream O (teto 600 palavras); Qwen O/P |
| Imagem · Instruções longas | GPT Image, Nano Banana, Kling O3 Image, Meta Muse | 150; 200 nas referências | narrativo, mais detalhe por personagem; "Image 1: papel" | GPT e Nano Banana O; Kling e Muse P |
| Vídeo · Prompt curto | Grok Imagine Video, Luma Ray | 100; 150 com tempos | ação no presente, uma câmara; Luma sem frases negativas | Luma O; Grok sem guia |
| Vídeo · Seedance | Seedance 2.0 / Mini / 2.5 | 150; 200 com tempos | essencial primeiro; tempos só no 2.5; @image1 | O (teto 1000 palavras); resto P/C |
| Vídeo · Wan | Wan 3.0 | 150; 200 com tempos | só movimento e câmara; falas `[Rita, tom]: 'line'`; "Image 1" | O (Alibaba) |
| Vídeo · LTX | LTX 2.5 Pro | 150; 200 com tempos | um parágrafo, começa pela ação | O (até 200 palavras) |
| Vídeo · MiniMax H3 | MiniMax H3 / H3 Max | 300; 450 com tempos | ancora a imagem e depois a ação; `Soundscape:` / `Music:`; falas `<d>…</d>`; câmara em prosa | O (350–500 palavras; 7000 caracteres) |
| Vídeo · Veo | Veo 3.1 / Fast / Lite | 150; 220 com tempos | `[00:00-00:02]`, `SFX:`, `Ambient noise:`; Lite sem referências | O (1024 tokens) |
| Vídeo · Kling | Kling v3 / O3 | 150; 220 com tempos | falas com etiqueta única, ação antes da fala | O; limite de 2500 caracteres via P |

Limites de caracteres dos prompts de vídeo (confirmados no Artlist por `get_model_config`, 29/09/2026, ou do fabricante quando o Artlist não indica): Kling 2500, Grok 4096 (perfil curto: 4000), Wan e LTX 5000, Luma 6000, Seedance 6000 (≈ 1000 palavras, fabricante), Veo 4000 (≈ 1024 tokens, fabricante), MiniMax 7000 (fabricante; o Artlist aceita 50000). Os prompts de imagem mantêm o limite geral da Configuração.

No Artlist, só o Seedance e o Wan 3.0 aceitam várias imagens de referência; os outros usam só Start/End Frame. Nas referências, o Seedance usa `@image1`, `@image2`… e o Wan "Image 1", "Image 2"… (confirmado em teste real a 29/09/2026: as duas formas ligam bem as identidades no Seedance; fica `@image1`, a do fabricante). Os prompts nunca guardam estes números: ver secção 8.

Modelos sem perfil próprio usam o Brief curto (imagem) ou o Prompt curto (vídeo). Os limites são editáveis em Configuração → Prompts → Validações.

### Vídeo por secções (2 de outubro de 2026)

O prompt de vídeo de um plano monta-se por secções (References → Summary → Scene and look → Action → Dialogue →
Sound → Constraints) no formato do perfil do modelo desse plano: `layout` (secções com etiqueta ou parágrafo),
`timeline` (blocos "0s-3s:" só no Seedance 2.5), `dialogue` e `sound` (a sintaxe de cada modelo). Ver
`app/model_profiles.py` e a guia «Vídeo de um plano» em `app/seeds.py`. Decisão em `docs/DECISOES.md`.

## 8. Marcadores de referência

Os prompts nunca guardam números fixos de imagem ("Image 2", "@image2", "<Picture 2>"): se as referências mudarem depois de o prompt ser escrito, ou se uma imagem deixar de existir, o número passava a apontar para a imagem errada. Em vez disso, o prompt usa marcadores ligados à origem da imagem:

| Marcador | Imagem |
|---|---|
| `@AVT_VERA` | folha do asset (ou a primeira imagem desse asset que seguir) |
| `@AVT_VERA.portrait` | retrato |
| `@LOC_BAR.viewB` | vista B de um local |
| `@C01.master` | imagem-mestra da cena 1 |
| `@P03.start` | frame de outro plano |
| `@START`, `@END` | Start e End Frame do próprio plano (modo Frames) |

Só no envio (`app/jobs.py`), com a lista exata das imagens que seguem, cada marcador passa à sintaxe do modelo escolhido nesse pedido: "Image n" nas imagens (GPT Image e Artlist numeram assim as imagens anexadas), `@imagen` no Seedance, "Image n" no Wan. No vídeo em modo Referências, também os códigos escritos sem @ (ex.: "AVT_VERA walks") passam à sintaxe do modelo. Nos modelos sem referências numeradas e no modo Frames, os marcadores entram como nomes.

A página do plano mostra como cada marcador vai seguir, avisa quando o prompt refere uma imagem que não vai nas referências ou usa números fixos, e a tecla @ na caixa do prompt lista os marcadores disponíveis.

### Teste real (29/09/2026)

Plano de teste com a folha do Dash (1.ª), a folha da Rita (2.ª) e o Bar (3.ª), prompt só com marcadores e sem descrever as personagens: Seedance 2.0 Mini (`@image1` e "Image 1") e Wan 3.0 ("Image 1") puseram cada personagem no papel certo, no local certo, com a fala e o som pedidos. MiniMax H3 Max (Start Frame vazio do Bar, falas `<d>…</d>`, `Soundscape:`): gerou voz e ambiente, embora o Artlist não tenha interruptor de som; não respeitou a entrada "pela direita" e acrescentou texto num letreiro apesar de "no text".

## 9. Storyboard com painéis

Um plano é um enquadramento contínuo e dá **um take** (um vídeo). A cena é a sequência dos takes dos seus planos. O plano define só o enquadramento (tipo, ângulo, movimento de câmara); a ação conta-se em **painéis**, os momentos do plano (o 1 é o início, o último o fim do take).

Cada painel tem: a legenda (em inglês, um instante parado), o segundo do take, o **elenco** desse momento (personagens e adereços, com notas), o **local e a vista**, a **iluminação** (vazio = a hora do dia da cena), referências extra (a mestra, outras vistas, painéis ou fotogramas de outros planos), o prompt (montado a partir destes campos, editável) e a imagem. Um painel novo parte do anterior (mesmo elenco, local e luz). A imagem gera-se com as referências do painel (e o painel anterior, para continuidade) ou a partir da imagem do painel anterior (edição: mesmo enquadramento, o momento seguinte).

No vídeo, o modo é o do modelo:
- **Referências** (Seedance, Wan 3.0): seguem as imagens dos painéis (`@P03.panel1`, `@P03.panel2`…) e as referências de cada painel, cada uma com a indicação dos painéis onde entra; a ação do prompt é a linha de tempo dos painéis ("The clip starts as @P03.panel1 (…); at 4 s, as @P03.panel2 (…); it ends at 9.5 s as @P03.panel3 (…)").
- **Início/fim** (os restantes): o painel 1 é o start frame, o último o end frame; os intermédios entram na linha de tempo.

O plano guarda ainda o som (fala, efeitos, ambiente, música), o modelo e a duração.

## Fontes

- Google Cloud — Ultimate prompting guide for Veo 3.1: https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1
- Google — Nano Banana Pro prompt tips: https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/
- Leonardo — Nano Banana Prompt Guide: https://leonardo.ai/news/nano-banana-prompt-guide
- Google AI (DEV) — Nano-Banana Pro Prompting Guide: https://dev.to/googleai/nano-banana-pro-prompting-guide-strategies-1h9n
- Invideo — Seedance 2.0 Prompt Guide: https://invideo.io/blog/seedance-2-0-prompt-guide/
- Seedance 2.0 Full Prompting Guide: https://theseanclaude.substack.com/p/seedance-20-prompt-guide-how-to-get
- APIYI — Seedance 2.0 Official Prompt Guide: https://help.apiyi.com/en/seedance-2-0-prompt-guide-video-generation-camera-style-tips-en.html
- fal — Seedance 2.0 Prompting Guide: https://fal.ai/learn/tools/seedance-2-0-prompting-guide
- OpenArt — Seedream 5.0 Pro Prompt Guide: https://openart.ai/blog/seedream-5-0-pro-prompt-guide/
- Atlas Cloud — Seedream 5.0 Pro Prompt Guide: https://www.atlascloud.ai/blog/tips/seedream-5-pro-prompt-guide
- Morphic — Seedream 5.0 Pro guide: https://morphic.com/resources/how-to/seedream-5-pro-guide
- JXP — Seedream 5.0 Pro Prompt Guide: https://www.jxp.com/seedream/blog/seedream-5-0-pro-prompt-guide
- WaveSpeed — Wan 3.0 Prompt Guide: https://wavespeed.ai/blog/ai-guides/wan-3-0-prompt-guide/
- Oxen — WAN 3.0 reference: https://docs.oxen.ai/inference-api/reference/models/wan-v3-0-video
- LTX — Veo 3.1 Prompt Guide: https://ltx.io/blog/veo-prompt-guide
- fal — Kling 3.0 Prompting Guide: https://blog.fal.ai/kling-3-0-prompting-guide/
- Atlabs — Kling 3.0 Prompt Guide: https://www.atlabs.ai/blog/kling-3-0-prompting-guide-master-ai-video-generation
