"""Testes da renderização da Sala de argumentistas (app/reading.py), pelo mesmo percurso que a página usa
(reading.render_text: segmentação + markdown-it-py + composição do guião).

Correr no contentor: python -m unittest tests.test_reading -v
"""

import unittest
from html.parser import HTMLParser

from app import reading

VOID = {"br", "hr", "img", "input", "meta", "link"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], ""

    @property
    def cls(self):
        return (self.attrs.get("class") or "").split()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def all_text(self):
        return self.text + "".join(c.all_text() for c in self.children)

    def find_all(self, tag=None, cls=None):
        out = []
        for c in self.children:
            if (tag is None or c.tag == tag) and (cls is None or cls in c.cls):
                out.append(c)
            out += c.find_all(tag, cls)
        return out


class Tree(HTMLParser):
    """Uma árvore mínima do HTML (só a biblioteca padrão), para verificar a estrutura e não só o texto."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root
        self.feed(str(html))

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(Node("#text", {}, self.cur))
        self.cur.children[-1].text = data


def tree(text, target=None, names=None):
    return Tree(reading.render_text(text, names or {}, target)).root


def node_with_text(root, text, tag=None):
    hits = [n for n in root.find_all(tag) if text in n.all_text()]
    return min(hits, key=lambda n: len(n.all_text())) if hits else None


def inside(node, tag):
    return any(a.tag == tag for a in node.ancestors())


def visible_text_outside_code(root):
    return "".join(n.text for n in root.find_all("#text") if not inside(n, "code") and not inside(n, "pre"))


MINIMAL = """# Teste de renderização

Parágrafo antes da tabela.

| A | B |
| --- | --- |
| 1 | Dois |
| 2 | Três |

## Secção seguinte

Este texto deve aparecer fora da tabela.
"""

# Estrutura de um guião real (texto neutro): ficha com tabela e continuidades em «## …» antes das cenas do formato da sala.
E3_LIKE = """# Episódio de teste — Guião

**Temporada:** 1
**Episódio:** 3 de 6

## Ficha do episódio

Resumo do episódio, com acentos e travessões — tudo.

| # | Cena | Local | Momento | Personagens principais |
| ---:| --- | --- | --- | --- |
| 1 | Encontro | Lisboa — rua das flores | Noite | Clara, Tomás |
| 2 | À porta | Café do Tomás | Noite | Tomás, Ana |

## Continuidade anterior

Texto da continuidade anterior.

## Continuidade seguinte

Texto da continuidade seguinte.

## Cena 1 — Encontro
Local: Rua das flores
Hora: Noite

Ação da primeira cena.

TOMÁS
I was waiting for you.

## Cena 2 — À porta
Local: Café
Hora: Noite

CLARA
(baixinho)
Not now.
"""

# Estrutura de outro guião real: formato clássico; tabela seguida de parágrafos a negrito de continuidade.
E2_LIKE = """# Episódio de teste — Guião

## Ficha do episódio

Resumo.

| # | Cena | Local | Momento | Personagens principais |
|---:|---|---|---|---|
| 1 | Ronda | Armazém — cais de carga | Noite seguinte | Ana, Supervisor (V.O.) |
| 2 | Registo | Armazém — receção | Manhã | Ana |

**Continuidade anterior:** o Bruno está de baixa.

**Continuidade seguinte:** o E3 começa com o Tomás.

## Teaser

### CENA 1 — RONDA

**1. INT. ARMAZÉM — CAIS DE CARGA — NOITE**

A Ana faz a ronda.

**ANA** *(no rádio)*

> All clear here.

## Acto 1

**2. INT. ARMAZÉM — RECEÇÃO — MANHÃ**

O ecrã mostra o registo.
"""


class MinimalDocument(unittest.TestCase):
    def check(self, root):
        self.assertTrue(root.find_all("h1"), "falta o título")
        tables = root.find_all("table")
        self.assertEqual(len(tables), 1, "tem de haver exatamente uma tabela")
        t = tables[0]
        self.assertEqual(len(t.find_all("th")), 2)
        self.assertEqual(len(t.find_all("tbody")[0].find_all("tr")), 2)
        h2 = node_with_text(root, "Secção seguinte", "h2")
        self.assertIsNotNone(h2, "o título «Secção seguinte» tem de ser um h2")
        self.assertFalse(inside(h2, "table"))
        p = node_with_text(root, "Este texto deve aparecer fora da tabela.", "p")
        self.assertIsNotNone(p)
        self.assertFalse(inside(p, "table"))
        self.assertNotIn("Este texto", t.all_text())
        self.assertNotIn("|", visible_text_outside_code(root))
        self.assertTrue(node_with_text(root, "Parágrafo antes da tabela.", "p"))

    def test_plain_markdown(self):
        self.check(tree(MINIMAL))

    def test_same_content_inside_a_script(self):
        # o mesmo bloco antes das cenas de um guião clássico e de um guião da sala
        self.check(tree(MINIMAL + "\n**1. INT. CASA — NOITE**\n\nAção.\n\n**2. EXT. RUA — DIA**\n\nAção.\n"))
        self.check(tree(MINIMAL + "\n## Cena 1\nLocal: Casa\nHora: Noite\n\nAção.\n"))


class CaseA_E3Table(unittest.TestCase):
    def setUp(self):
        self.root = tree(E3_LIKE)

    def test_table_is_a_table(self):
        tables = self.root.find_all("table")
        self.assertEqual(len(tables), 1)
        self.assertFalse(inside(tables[0], "pre") or inside(tables[0], "code"))
        self.assertEqual([th.all_text() for th in tables[0].find_all("th")],
                         ["#", "Cena", "Local", "Momento", "Personagens principais"])
        self.assertIn("Lisboa — rua das flores", tables[0].all_text())
        self.assertNotIn("|", visible_text_outside_code(self.root))

    def test_sections_are_not_scenes(self):
        scenes = [s.all_text() for s in self.root.find_all("div", "rd-h")]
        self.assertEqual(scenes, ["Cena 1 — Encontro", "Cena 2 — À porta"])
        self.assertEqual([n.all_text() for n in self.root.find_all("div", "rd-n")], ["C01", "C02"])
        for title in ("Ficha do episódio", "Continuidade anterior", "Continuidade seguinte"):
            self.assertIsNotNone(node_with_text(self.root, title, "h2"), title)

    def test_dialogue_still_formatted(self):
        self.assertEqual([c.all_text() for c in self.root.find_all("div", "rd-cue")], ["TOMÁS", "CLARA"])
        self.assertEqual([c.all_text() for c in self.root.find_all("div", "rd-par")], ["(baixinho)"])

    def test_with_scenes_target_every_h2_is_a_scene(self):
        # com destino «cenas» na app, a aprovação trata qualquer «## …» como cena; a leitura mostra o mesmo
        root = tree(E3_LIKE, target="scenes:episode:1")
        self.assertEqual(len(root.find_all("div", "rd-scene")), 5)


class CaseB_E2Continuity(unittest.TestCase):
    def setUp(self):
        self.root = tree(E2_LIKE)

    def test_continuity_outside_table(self):
        t = self.root.find_all("table")[0]
        self.assertEqual(len(t.find_all("tbody")[0].find_all("tr")), 2)
        self.assertNotIn("Continuidade", t.all_text())
        for label in ("Continuidade anterior:", "Continuidade seguinte:"):
            p = node_with_text(self.root, label, "p")
            self.assertIsNotNone(p, label)
            self.assertFalse(inside(p, "table"), label)

    def test_classic_scenes(self):
        self.assertEqual([n.all_text() for n in self.root.find_all("div", "rd-n")], ["1", "2"])
        self.assertEqual(self.root.find_all("div", "rd-h")[0].all_text(), "INT. ARMAZÉM — CAIS DE CARGA — NOITE")
        self.assertEqual([c.all_text() for c in self.root.find_all("div", "rd-cue")], ["ANA"])
        self.assertEqual(self.root.find_all("div", "rd-line")[0].all_text(), "All clear here.")
        self.assertIsNotNone(node_with_text(self.root, "Acto 1", "h2"))


class Boundaries(unittest.TestCase):
    TABLE = "| A | B |\n| --- | --- |\n| 1 | Dois |\n"

    def table_then(self, after):
        root = tree("Intro.\n\n" + self.TABLE + "\n" + after)
        t = root.find_all("table")[0]
        self.assertEqual(len(t.find_all("tbody")[0].find_all("tr")), 1)
        return root, t

    def test_headings_of_each_level(self):
        for level in range(1, 7):
            root, t = self.table_then("#" * level + " Depois\n\nTexto.\n")
            h = node_with_text(root, "Depois", f"h{level}")
            self.assertIsNotNone(h)
            self.assertFalse(inside(h, "table"))

    def test_paragraph_and_list(self):
        root, t = self.table_then("Um parágrafo.\n\n- um\n- dois\n")
        self.assertFalse(inside(node_with_text(root, "Um parágrafo.", "p"), "table"))
        self.assertEqual(len(root.find_all("li")), 2)
        self.assertNotIn("dois", t.all_text())

    def test_accents_and_dashes_in_cells(self):
        root = tree("| Local | Nota |\n| --- | --- |\n| Café do Tomás — arrumos | «Ação», pontuação; ç, ã, é! |\n")
        tds = [td.all_text() for td in root.find_all("td")]
        self.assertEqual(tds, ["Café do Tomás — arrumos", "«Ação», pontuação; ç, ã, é!"])

    def test_no_tables(self):
        root = tree("# Notas\n\nSó texto, *itálico* e **negrito**.\n")
        self.assertEqual(root.find_all("table"), [])
        self.assertTrue(root.find_all("em") and root.find_all("strong"))

    def test_code_blocks_stay_code(self):
        code = "```\n| não | é tabela |\n| --- | --- |\nINT. NÃO É CENA — NOITE\n```\n"
        for doc in (code, "**1. INT. CASA — NOITE**\n\nAção.\n\n" + code + "\n**2. EXT. RUA — DIA**\n\nFim.\n"):
            root = tree(doc)
            pre = root.find_all("pre")
            self.assertEqual(len(pre), 1)
            self.assertIn("| não | é tabela |", pre[0].all_text())
            self.assertEqual(root.find_all("table"), [])
            self.assertNotIn("NÃO É CENA", "".join(n.all_text() for n in root.find_all("div", "rd-h")))

    def test_table_inside_a_scene(self):
        root = tree("**1. INT. CASA — NOITE**\n\nAção.\n\n" + self.TABLE + "\nMais ação.\n\n**2. EXT. RUA — DIA**\n\nFim.\n")
        t = root.find_all("table")
        self.assertEqual(len(t), 1)
        self.assertNotIn("Mais ação", t[0].all_text())
        self.assertNotIn("|", visible_text_outside_code(root))

    def test_codes_become_names_but_not_in_code(self):
        root = tree("TST_ANA entra.\n\n```\nTST_ANA\n```\n", names={"TST_ANA": "Ana"})
        self.assertIn("Ana entra.", root.all_text())
        self.assertIn("TST_ANA", root.find_all("pre")[0].all_text())

    def test_no_line_is_lost(self):
        # o texto fora das cenas chega ao parser tal como está (com as linhas em branco que fecham as tabelas)
        for doc in (E2_LIKE, E3_LIKE, MINIMAL):
            for seg in reading.segment(doc):
                if seg["kind"] == "md":
                    self.assertIn("\n".join(seg["lines"]).strip("\n"), doc)
            md_blank = sum(1 for s in reading.segment(doc) if s["kind"] == "md" for x in s["lines"] if not x.strip())
            self.assertGreater(md_blank, 0)

class DialogueContinuation(unittest.TestCase):
    DOC = """**1. INT. CAFÉ — NOITE**

**DUARTE**

> Someone was here.

Ela olha para a porta.

> This one got the update before the others.

**ANA**

> And?

Pausa.

> Let's go.

**TOMÁS (O.S.)** *(lá de dentro)*

> Go where?

**2. EXT. RUA — DIA**

Fim.
"""

    def test_quote_after_action_continues_last_speaker(self):
        root = tree(self.DOC)
        cues = [c.all_text() for c in root.find_all("div", "rd-cue")]
        self.assertEqual(cues, ["DUARTE", "DUARTE (cont.)", "ANA", "ANA (cont.)", "TOMÁS (O.S.)"])
        self.assertEqual(root.find_all("blockquote"), [])
        self.assertIn("This one got the update before the others.", [d.all_text() for d in root.find_all("div", "rd-line")])
        self.assertEqual([p.all_text() for p in root.find_all("div", "rd-par")], ["(lá de dentro)"])

    def test_quote_without_previous_speaker(self):
        root = tree("**1. INT. CASA — NOITE**\n\nAção.\n\n> Uma voz.\n\n**2. EXT. RUA — DIA**\n\nFim.\n")
        self.assertEqual(root.find_all("div", "rd-cue"), [])
        self.assertEqual([d.all_text() for d in root.find_all("div", "rd-line")], ["Uma voz."])

    def test_single_scene_is_a_script(self):
        root = tree("**1. INT. CASA — NOITE**\n\n**ANA**\n\n> Olá.\n")
        self.assertEqual([c.all_text() for c in root.find_all("div", "rd-cue")], ["ANA"])

    def test_scene_heading_only_inside_code_is_not_a_script(self):
        root = tree("Notas.\n\n```\nINT. CASA — NOITE\n```\n")
        self.assertEqual(root.find_all("div", "rd-scene"), [])
        self.assertTrue(root.find_all("pre"))

    def test_speaker_does_not_leak_into_next_scene(self):
        root = tree(self.DOC + "\n> Sozinha.\n")
        self.assertNotIn("TOMÁS (O.S.) (cont.)", [c.all_text() for c in root.find_all("div", "rd-cue")])


if __name__ == "__main__":
    unittest.main()
