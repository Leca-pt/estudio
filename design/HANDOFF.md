# Estúdio — design da UI (handoff para implementação)

Design da interface do Estúdio. A app vai ser implementada em FastAPI + SQLite, com HTML renderizado no servidor, Jinja2 e HTMX.

## Conteúdo desta pasta

| Pasta / ficheiro | O que é | Como usar |
|---|---|---|
| `estudio.css` | Estilos completos da UI: tokens de cor, layout, componentes | Copiar para `static/css/estudio.css` e usar tal como está |
| `ecras/*.html` | 30 ecrãs em HTML estático, que abrem diretamente no browser | Referência de estrutura e markup para os templates Jinja2 |
| `png/*.png` | Imagem de cada ecrã | Referência visual: comparar a implementação com estas imagens |
| `mockup-dc/*.dc.html` | Ficheiros originais do editor de design | Só para arquivo. **Não usar** (têm runtime próprio: `<x-dc>`, `support.js`) |

## Regras gerais

- **Idioma:**
  - Toda a interface em português europeu.
  - Os prompts mostrados na app são em inglês.
- **Tema:**
  - Escuro por omissão.
  - O tema claro é a classe `light` no elemento `.app` (ex.: `<div class="app light">`).
  - Guardar a preferência do utilizador.
- **Cor só para estado:**
  - Pendente: cinzento, `.st` / `.dot.pend`.
  - Em curso: âmbar, `.st.wip` / `.dot.wip`.
  - Aprovado: verde, `.st.ok` / `.dot.ok`.
  - Rejeitado: vermelho, `.st.no` / `.dot.no`.
  - Não usar cor para mais nada.
- **Sem drag-and-drop:**
  - Reordenar faz-se com botões ↑/↓.
  - O upload aceita arrastar ficheiros, com um botão "Escolher ficheiros" como alternativa.
- **Dropdowns** em todos os campos com lista predefinida. Texto livre só em logline, sinopse, descrições, notas e guião.
- **Nada é guardado pelo Assistente sem "Aplicar":**
  - Cada sugestão é um cartão `.sug` que mostra o campo-alvo, o valor proposto e o valor antigo riscado.
  - "Aplicar" faz um POST por HTMX que atualiza o campo.
  - "Ignorar" descarta a sugestão.

## Layout global → `templates/base.html`

```
.app > .shell
  header.top          logo · seletor de projeto (.psw) · Configuração · Sair
  .body               grelha: 232px | 1fr | 316px
    aside.side        árvore do projeto (.tree > .tn, níveis .l2 .l3)
    main.main
      nav.stepper     7 etapas (.step, .step.on = atual)
      .content        conteúdo do ecrã
    aside.asst        Assistente (contexto .ctx, .msgs, .sug, .composer)
```

- **Variantes do `.body`:**
  - `.no-asst`: sem Assistente (Configuração).
  - `.solo`: sem árvore (Projetos).
  - Assistente recolhido: coluna de 52px com a classe `.rail` (ver `E7-Montagem.html`).
- **Árvore por tipo de projeto:**
  - Curta / spot: Cenas → Planos
  - Série: Temporadas → Episódios → Cenas → Planos
  - Filme: Atos → Cenas → Planos
- **Stepper:** cada etapa mostra o número, o nome e o estado (`Aprovada`, `Em curso · 7/10`, `Pendente`).
- **Contexto do Assistente:** muda com o ecrã (ex.: "Cena 1 · Plano 3"). Deve vir do servidor.

## Componentes → macros Jinja2 (`templates/macros/`)

| Macro | Classe CSS | Exemplo em |
|---|---|---|
| `status(estado)` | `.st` / `.dot` | todos |
| `prompt_block(titulo, texto, modelo)` | `.pb` | `E5-Plano.html` |
| `ref_strip(refs)` | `.refs > .ref` | `E5-Plano.html` |
| `upload_area(resultados)` | `.drop` + `.res` | `E4-Asset.html`, `E5-Plano.html` |
| `suggestion(campo, valor, antigo)` | `.sug` | todos os ecrãs com Assistente |
| `empty_state(titulo, texto, acao)` | `.empty` | ecrãs `*-vazio.html` |
| `frame_slot(tipo, imagem, opcional)` | `.slot` / `.ph` | `E5-Plano.html`, `E6-Video.html` |

**Detalhes de comportamento:**
- **Bloco de prompt:**
  - Monoespaçado e editável, com contador de caracteres.
  - Acima de 2000 caracteres o contador passa a `.cnt.warn` e aparece a barra `.warnbar` (ver `Componentes.html`).
  - O limite vem da Configuração (Regras de prompt).
  - O botão "Copiar" usa a Clipboard API (JS mínimo).
- **Rejeitar um resultado:**
  - Abre sempre a confirmação `.scrim > .dlg` (ver `Componentes.html`).
  - O ficheiro é apagado no servidor e nunca pode ser usado como referência.
- **Aprovar** um resultado substitui o aprovado atual.
- **End Frame** é opcional e tem a marca `.opt`.
- **Campos inseridos em todos os prompts** ("Bloco de estilo" e "Exclusões") usam a classe `.inj` e a etiqueta `.injtag`.

## Ecrãs

Cada ecrã tem uma versão vazia (`-vazio`) e uma preenchida com o projeto de exemplo AVT.

| Ecrã | Ficheiros | Rota sugerida |
|---|---|---|
| Login | `Login.html` (escuro), `Login-claro.html` (claro + erro) | `/login` |
| Projetos | `Projetos.html`, `Projetos-vazio.html` | `/` |
| 1 · Projeto e bíblia | `E1-Biblia*.html` | `/p/{pfx}/biblia` |
| 2 · História | `E2-Historia*.html` (o vazio mostra uma série) | `/p/{pfx}/historia` |
| 3 · Desglose | `E3-Desglose*.html` | `/p/{pfx}/desglose` |
| 4 · Assets (grelha) | `E4-Assets*.html` | `/p/{pfx}/assets?tipo=` |
| 4 · Asset (detalhe) | `E4-Asset.html`, `E4-Asset-vazio.html` | `/p/{pfx}/assets/{codigo}` |
| 5 · Storyboard, cena | `E5-Cena*.html` | `/p/{pfx}/storyboard/{cena}` |
| 5 · Storyboard, plano | `E5-Plano*.html` | `/p/{pfx}/storyboard/{cena}/{plano}` |
| 6 · Vídeo, plano | `E6-Video*.html` | `/p/{pfx}/video/{cena}/{plano}` |
| 7 · Montagem | `E7-Montagem*.html` | `/p/{pfx}/montagem` (+ `/export.zip`) |
| Configuração | `Config.html`, `Config-vazio.html`, `Config-prompts.html` | `/config/listas`, `/config/modelos` |
| Componentes | `Componentes.html` | só referência |

## Responsivo

Os ecrãs estão desenhados com larguras fixas: desktop 1440, tablet 834 e telemóvel 390. Na app, os mesmos templates devem adaptar-se com media queries. Não criar páginas separadas.

- **Abaixo de 1024px** (ver `Tablet-Plano.html`):
  - A árvore passa a gaveta, aberta pelo botão de menu na barra de topo.
  - O Assistente passa a gaveta, aberta pelo botão "Assistente".
  - O stepper fica compacto: número, ponto de estado e nome curto.
  - Os campos passam a 44px de altura.
- **Abaixo de 600px** (ver `Tel-*.html`):
  - Barra de topo `.m-top`.
  - Stepper como `<select>` com a barra `.bar7` por baixo.
  - Tudo numa coluna, com alvos de toque de pelo menos 44px.
  - A árvore é uma gaveta à esquerda (ver `Tel-Menu.html`).
  - Barra fixa em baixo com os botões Assistente e Guardar.

## A remover na implementação

- `.ph`, `.fig`, `.hz` e `.lb` são placeholders de imagem do mockup. Na app passam a `<img>` com os ficheiros reais (`object-fit: cover`), mantendo as proporções `.r916`, `.r43` e `.r169`.
- Os ecrãs têm altura fixa e `overflow: hidden` no `.app`. Na app, o `.content` deve fazer scroll (`overflow: auto`).
- Os valores de exemplo ("Savry", "Pequenos Inventores", "O Farol", contagens) são fictícios.

## Ordem de implementação sugerida

1. `estudio.css` + `base.html` + Login + Projetos
2. Etapa 1 e Configuração → listas (os dropdowns dependem delas)
3. Etapas 2 e 3
4. Etapa 4 (upload, aprovar e rejeitar)
5. Etapa 5 (cena e plano)
6. Etapas 6 e 7 (exportação zip)
7. Assistente (cartões Aplicar/Ignorar via HTMX)
8. Responsivo (tablet e telemóvel)
