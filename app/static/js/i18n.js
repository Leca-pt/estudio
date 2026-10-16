// Interface em inglês (Configuração → Idioma): o servidor já traduz as páginas; aqui traduz-se o texto que o
// navegador escreve sozinho (a maquete, avisos, contadores…), com o mesmo dicionário (app/i18n/en.json). Não toca no
// conteúdo de caixas de texto, scripts, nem no que está marcado translate="no".
(function () {
  const me = document.currentScript;
  let strings = null, patterns = [];
  const norm = (s) => s.replace(/\s+/g, ' ').trim();
  const WORDY = /[A-Za-zÀ-ÿ]{2,}/;
  function lookup(text) {
    const key = norm(text);
    if (!key || !strings) return null;
    if (Object.prototype.hasOwnProperty.call(strings, key)) return strings[key];
    for (const [rx, repl, anchor, frags] of patterns) {
      if ((anchor && !key.includes(anchor)) || !frags.every((f) => key.includes(f))) continue;
      const m = rx.exec(key);
      if (m) return repl.replace(/\\(\d+)/g, (_, n) => { const g = m[+n] || ''; return lookup(g) ?? g; });
    }
    for (const sep of [' · ', ' › ', ' — ']) {  // linhas feitas de partes: parte a parte
      if (key.includes(sep)) {
        const parts = key.split(sep), got = parts.map((p) => lookup(p));
        if (got.some((g) => g != null)) return parts.map((p, i) => got[i] ?? p).join(sep);
      }
    }
    return null;
  }
  function t(text) {
    if (typeof text !== 'string' || !WORDY.test(text)) return text;
    const hit = lookup(text);
    if (hit == null) return text;
    return text.match(/^\s*/)[0] + hit + text.match(/\s*$/)[0];
  }
  window.estudioT = t;
  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt', 'data-tip', 'data-confirm', 'data-confirm-text', 'data-confirm-ok', 'data-busy-label', 'data-busy-text', 'label'];
  const skip = (el) => !el || el.closest('textarea, script, style, [translate="no"], [contenteditable="true"]');
  function fixText(node) {
    if (skip(node.parentElement)) return;
    const v = node.nodeValue, out = t(v);
    if (out !== v) node.nodeValue = out;
  }
  function fixEl(el) {
    if (el.nodeType !== 1 || skip(el)) return;
    for (const a of ATTRS) { const v = el.getAttribute(a); if (v) { const out = t(v); if (out !== v) el.setAttribute(a, out); } }
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (n.nodeType === 3) fixText(n);
      else if (!skip(n)) for (const a of ATTRS) { const v = n.getAttribute(a); if (v) { const out = t(v); if (out !== v) n.setAttribute(a, out); } }
    }
  }
  function start() {
    fixEl(document.body);
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === 'characterData') fixText(m.target);
        else if (m.type === 'attributes') { if (!skip(m.target)) { const v = m.target.getAttribute(m.attributeName); if (v) { const out = t(v); if (out !== v) m.target.setAttribute(m.attributeName, out); } } }
        else m.addedNodes.forEach((n) => (n.nodeType === 3 ? fixText(n) : fixEl(n)));
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  const url = me && me.dataset.dict;
  let cached = null;
  try { cached = JSON.parse(localStorage.getItem('estudio-i18n') || 'null'); } catch (e) { /* sem cache */ }
  const install = (d) => {
    strings = d.strings || {};
    patterns = (d.patterns || []).map(([rx, repl, anchor, frags]) => { try { return [new RegExp('^(?:' + rx + ')$', 's'), repl, anchor || '', frags || []]; } catch (e) { return null; } }).filter(Boolean);
    const norm2 = {};
    for (const k in strings) norm2[norm(k)] = strings[k];
    strings = norm2;
  };
  const go = () => { if (document.body) start(); else document.addEventListener('DOMContentLoaded', start); };
  if (cached && cached.url === url) { install(cached.data); go(); }
  else fetch(url).then((r) => r.json()).then((d) => {
    install(d); go();
    try { localStorage.setItem('estudio-i18n', JSON.stringify({ url, data: d })); } catch (e) { /* grande demais: fica só nesta página */ }
  }).catch(() => {});
})();
