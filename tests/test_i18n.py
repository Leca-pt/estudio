"""Interface em inglês (app/i18n.py): traduz o texto da app, nunca o conteúdo dos projetos nem as caixas de texto."""

import unittest

from app import i18n


class Lookup(unittest.TestCase):
    def test_exact_and_spaces(self):
        self.assertEqual(i18n.tr("Antes de gerar"), "Before generating")
        self.assertEqual(i18n.tr("  Guardar \n"), "  Save \n")

    def test_patterns(self):
        self.assertEqual(i18n.tr("Gerar frame-chave do momento 2"), "Generate key frame for moment 2")
        self.assertEqual(i18n.tr("Apagar o P05?"), "Delete P05?")
        self.assertEqual(i18n.tr("P23 cobre a linha 56"), "P23 covers line 56")

    def test_composites_and_dates(self):
        self.assertEqual(i18n.tr("Short · 6 scene(s) · 14 plano(s)"), "Short · 6 scene(s) · 14 shot(s)")
        self.assertEqual(i18n.tr("Sáb 3 jun 2028"), "Sat 3 Jun 2028")
        self.assertEqual(i18n.tr("· Curta"), "· Short")

    def test_content_untouched(self):
        for text in ("A Milena troca moedas físicas por medicamentos no mercado paralelo.",
                     "As fotografias reais da zona", "Joel varre o chão da taberna"):
            self.assertEqual(i18n.tr(text), text)


class Html(unittest.TestCase):
    def test_text_attrs_and_protected(self):
        html = ('<html lang="pt-PT"><button title="Guardar">Guardar</button><textarea placeholder="Guardar">Guardar</textarea>'
                '<span translate="no">Guardar</span><script>var a = "Guardar";</script></html>')
        out = i18n.translate_html(html)
        self.assertIn('<html lang="en">', out)
        self.assertIn('<button title="Save">Save</button>', out)
        self.assertIn('<textarea placeholder="Save">Guardar</textarea>', out)  # o conteúdo de uma caixa nunca muda
        self.assertIn('<span translate="no">Guardar</span>', out)
        self.assertIn('var a = "Guardar";', out)


if __name__ == "__main__":
    unittest.main()
