# Estúdio — PT→EN UI translation guide

The app is "Estúdio", a film/series pre-production and AI generation studio (storyboards, 3D previs, image and
video generation). Translate the Portuguese (pt-PT) UI text into natural, concise UI English (US spelling is fine).
Match the tone: short, plain, friendly, imperative for buttons ("Save", "Generate"), sentence case for labels.

## Rules
- Output ONLY a JSON object mapping each input string (exactly as given, unchanged key) to its English translation.
- Keep placeholders like {1}, {2} exactly (each must appear in the translation; you may reorder them).
- Keep codes and tokens untouched: @TOKENS, P01, C07, T1E02, file names, model names (GPT Image, Nano Banana,
  Seedance, Flux, Artlist, fal.ai, OpenRouter, Claude, Codex, ByteDance, BytePlus), units (s, mm, m, MB, 2K, 4K).
- Keep typographic symbols: · → … — ⚠ ✓ ⓘ ×. Portuguese «quotes» become “curly quotes”.
- Keep leading/trailing punctuation the same (e.g. a trailing ":" or "…" stays).
- If a string is already English, a code, a proper name, or not UI text, map it to itself.
- Plural hints like "plano(s)" → "shot(s)".
- Do not add explanations. Do not translate inside @tokens or {n}.

## Glossary (use consistently)
Projeto=Project · Série=Series · Filme=Film · Curta / spot=Short / spot · Temporada=Season · Episódio=Episode · Ato=Act
Cena=Scene · Plano=Shot · Take=Take · Guião=Script · linha do guião=script line · Fala=Line (dialogue) · Ação (do guião)=Action
Bíblia=Bible · Mundo=World · Premissa=Premise · Logline=Logline · Sinopse=Synopsis · Sala de argumentistas=Writers' room
Personagem=Character · Elenco=Cast · Local=Location · Adereço=Prop · Elemento do mundo=World element · Figurante=Extra
Folha (de personagem/asset)=Sheet · Retrato=Portrait · Look=Look · Voz de referência=Reference voice
Maquete 3D / maquete=3D set · Encenação=Staging · Câmara=Camera · Momento (da câmara)=Moment · Previs=Previs
Vídeo-guia=Guide video · Vista (do local)=View · Referência da zona=Zone reference · Zona=Zone · Render=Render
Frame-chave=Key frame · Storyboard final=Final storyboard · Animatic=Animatic · Montagem=Edit · Etapa=Stage
Prompt=Prompt · Escritor de prompts=Prompt writer · Assistente=Assistant · Modelo=Model · Gerar=Generate
Reescrever=Rewrite · Escrever=Write · Aprovar=Approve · Rejeitar=Reject · Rejeitados=Rejected · Por decidir=Pending
Desatualizado=Outdated · Guardar=Save · Gravar (vídeo)=Record · Apagar=Delete · Reciclagem=Recycle bin
Carregar (ficheiro)=Upload · Descarregar=Download · Configuração=Settings · Listas=Lists · Ligações=Connections
Chaves de API=API keys · Conta=Account · Créditos=Credits · Agenda=Agenda · Lacuna=Gap · Continuidade=Continuity
Verificação=Check · Antes de gerar=Before generating · Sem take=No take · Ver seguido=Play all
Plano geral=Wide shot · Grande plano=Close-up · Plano médio=Medium shot · Plano de pormenor=Detail shot / insert
Contracampo=Reverse shot · Por cima do ombro=Over-the-shoulder · Nível dos olhos=Eye level · Picado=High angle
Contrapicado=Low angle · Estática=Static · Travelling=Tracking · Panorâmica=Pan
Taberna=Tavern · arrumos=storeroom · sala=main room · cozinha=kitchen
