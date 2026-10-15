"""PDF dos ficheiros da Sala de argumentistas: o mesmo HTML do modo de leitura (app/reading.py) numa folha de estilos de
papel (A4, guião em Courier Prime 12 pt com as margens de um argumento, texto em Literata), impresso pelo Chromium do
servidor (Playwright) com o título e o número da página."""

import asyncio
from html import escape

from markupsafe import Markup

PRINT_CSS = """
@page { size: A4; margin: 22mm 20mm 20mm 20mm; }
html, body { margin: 0; background: #fff; color: #111; }
body { font-family: 'Literata', 'Liberation Serif', Georgia, serif; }
.doc-h { font-family: 'IBM Plex Sans', 'Liberation Sans', sans-serif; font-size: 8.5pt; color: #777; margin-bottom: 10mm;
         border-bottom: 0.5pt solid #ccc; padding-bottom: 2mm; display: flex; justify-content: space-between; }
/* texto (markdown) */
.rd-md { font-family: 'Literata', 'Liberation Serif', Georgia, serif; font-size: 11pt; line-height: 1.55; }
.rd-md h1 { font-size: 20pt; line-height: 1.25; margin: 0 0 6mm; }
.rd-md h2 { font-size: 14.5pt; margin: 8mm 0 3mm; break-after: avoid; }
.rd-md h3 { font-size: 12pt; margin: 6mm 0 2mm; break-after: avoid; }
.rd-md p { margin: 0 0 3.5mm; orphans: 3; widows: 3; }
.rd-md ul, .rd-md ol { margin: 0 0 3.5mm; padding-left: 7mm; }
.rd-md blockquote { margin: 0 0 3.5mm; padding-left: 5mm; border-left: 1.5pt solid #bbb; color: #444; font-style: italic; }
.rd-md hr { border: 0; border-top: 0.5pt solid #bbb; margin: 6mm 25%; }
.rd-md code { font-family: 'Liberation Mono', monospace; font-size: 9.5pt; background: #f2f2f2; padding: 0 2pt; }
.rd-md pre { background: #f4f4f4; padding: 3mm; font-size: 9pt; white-space: pre-wrap; break-inside: avoid; }
.rd-md pre code { background: none; padding: 0; }
.rd-md table { border-collapse: collapse; width: 100%; font-size: 9.5pt; margin: 0 0 4mm; break-inside: auto; }
.rd-md th, .rd-md td { border: 0.5pt solid #999; padding: 1.5mm 2mm; text-align: left; vertical-align: top; }
.rd-md th { background: #eee; }
.rd-md tr { break-inside: avoid; }
/* guião: Courier 12 pt, margens de argumento (texto a ~1,5" da esquerda, falas a ~2,5", nome a ~3,7") */
.rd-script { font-family: 'Courier Prime', 'Liberation Mono', 'Courier New', monospace; font-size: 12pt; line-height: 1.2; }
.rd-script .rd-md { font-size: 11pt; }
.rd-scene { margin: 7mm 0 4mm; break-after: avoid; }
.rd-n { display: none; }
.rd-h { font-weight: 700; text-transform: uppercase; break-after: avoid; }
.rd-slug { text-transform: uppercase; color: #333; break-after: avoid; }
.rd-sum { margin: 2mm 0 0; font-style: italic; color: #444; font-size: 11pt; }
.rd-act { margin: 0 0 4.2mm; orphans: 2; widows: 2; }
.rd-dlg { margin: 0 0 4.2mm 25mm; width: 90mm; break-inside: avoid; }
.rd-cue { margin-left: 30mm; font-weight: 400; text-transform: uppercase; }
.rd-ext { color: #111; }
.rd-par { margin-left: 15mm; font-style: normal; color: #111; }
.rd-line { }
.rd-trans { text-align: right; margin: 0 0 4.2mm; text-transform: uppercase; }
.rd-sec { font-family: 'IBM Plex Sans', 'Liberation Sans', sans-serif; font-weight: 600; font-size: 10pt; color: #555;
          margin: 8mm 0 3mm; break-after: avoid; }
.rd-note { font-style: italic; color: #8a5a00; }
.rd-ref { color: inherit; }
"""

FONTS = ("https://fonts.googleapis.com/css2?family=Courier+Prime:ital,wght@0,400;0,700;1,400"
         "&family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;1,7..72,400&family=IBM+Plex+Sans:wght@400;600&display=swap")


def page_html(title: str, subtitle: str, body: Markup) -> str:
    return (f'<!doctype html><html lang="pt"><head><meta charset="utf-8"><title>{escape(title)}</title>'
            f'<link rel="stylesheet" href="{FONTS}"><style>{PRINT_CSS}</style></head><body>'
            f'<div class="doc-h"><span>{escape(title)}</span><span>{escape(subtitle)}</span></div>{body}</body></html>')


async def to_pdf(html: str, footer: str, timeout_s: int = 60) -> bytes:
    from playwright.async_api import async_playwright

    async def go() -> bytes:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="networkidle")
                await page.evaluate("document.fonts.ready")
                return await page.pdf(
                    format="A4", print_background=True, prefer_css_page_size=True, display_header_footer=True,
                    header_template="<span></span>",
                    footer_template=('<div style="font-size:8px;color:#888;width:100%;padding:0 20mm;'
                                     'font-family:sans-serif;display:flex;justify-content:space-between">'
                                     f'<span>{escape(footer)}</span><span><span class="pageNumber"></span> / '
                                     '<span class="totalPages"></span></span></div>'))
            finally:
                await browser.close()
    return await asyncio.wait_for(go(), timeout_s)
