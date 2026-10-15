// Estúdio — JS mínimo: tema, gaveta, copiar, contadores, confirmação, diálogos.
(function () {
  const app = () => document.querySelector('.app');
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };

  document.documentElement.dataset.rd = store.get('estudio-rd') === 'paper' ? 'paper' : 'dark';

  function applyTheme() {
    const theme = store.get('estudio-theme') || 'dark';
    const a = app();
    if (a) a.classList.toggle('light', theme === 'light');
    document.querySelectorAll('[data-theme-set]').forEach(b => b.classList.toggle('on', b.dataset.themeSet === theme));
  }

  function updateCounter(el) {
    const max = parseInt(el.dataset.count, 10);
    const n = el.value.length;
    const cnt = document.getElementById(el.id + '-cnt');
    if (cnt) {
      const words = (el.value.match(/[A-Za-zÀ-ÿ0-9][\w'’-]*/g) || []).length;
      cnt.textContent = (max ? `${n} / ${max}` : `${n}`) + ` · ${words} palavras`;
      cnt.classList.toggle('warn', !!max && n > max);
    }
    const bar = document.getElementById(el.id + '-warn');
    if (bar) {
      bar.hidden = !(max && n > max);
      bar.textContent = `${n - max} caracteres acima do limite de ${max}. Encurta antes de gerar ou copiar.`;
    }
  }

  // Chat do Assistente: só desce quando chega uma mensagem nova e quem lê já estava no fundo (ou quando envia a sua).
  // Atualizações que não trazem mensagens (o "A pensar…", cartões a gerar, gravar a página) não lhe mexem.
  // O estado fica em propriedades do elemento (o morph mantém o elemento, mas acertaria os atributos data-*).
  function chatCount(box) {  // assinatura da última mensagem (a lista mostra sempre as N últimas: contar não chega)
    const all = box.querySelectorAll('.msg:not(.asst-thinking)');
    const last = all[all.length - 1];
    return last ? `${all.length}|${last.textContent.trim().slice(-200)}` : '0';
  }
  function scrollChat(force) {
    const box = document.getElementById('asst-msgs');
    if (!box) return;
    if (!box._chatWatch) {
      box._chatWatch = true;
      box._stick = true;
      box.addEventListener('scroll', () => { box._stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80; }, { passive: true });
    }
    const n = chatCount(box);
    if (force || box._seen === undefined || (n !== box._seen && box._stick)) {
      box.scrollTop = box.scrollHeight;
      box._stick = true;
      const until = Date.now() + 800;  // fica no fundo enquanto as caixas e as imagens acertam a altura (até mexeres)
      const pin = () => { if (!box._stick) return; box.scrollTop = box.scrollHeight; if (Date.now() < until) requestAnimationFrame(pin); };
      requestAnimationFrame(pin);
    }
    box._seen = n;
  }

  let lbLinks = [], lbIndex = 0;
  function lightboxLinks(from) {
    // As imagens do sítio onde se clicou: dentro de um modal (ex.: uma zona da maquete), as desse modal; fora, as da
    // página (sem as dos modais fechados).
    const scope = (from && from.closest('dialog:not(#lightbox)')) || null;
    const seen = new Set();
    return [...(scope || document).querySelectorAll('a[href^="/media/"]')].filter(a => {
      const h = a.getAttribute('href');
      if (!/\.(png|jpe?g|webp|mp4|mov)$/i.test(h) || seen.has(h) || (!scope && a.closest('dialog'))) return false;
      seen.add(h);
      return true;
    });
  }
  function showLightbox() {
    const d = document.getElementById('lightbox');
    const a = lbLinks[lbIndex];
    const href = a.getAttribute('href');
    const label = (a.querySelector('.lb') || {}).textContent || a.querySelector('img')?.alt || href.split('/').pop();
    const isVideo = /\.(mp4|mov)$/i.test(href);
    const img = d.querySelector('[data-lb-img]'), vid = d.querySelector('[data-lb-video]');
    img.hidden = isVideo; vid.hidden = !isVideo;
    if (isVideo) {  // vídeo: toca no modal, com controlos
      img.removeAttribute('src');
      if (vid.getAttribute('src') !== href) vid.src = href;
      vid.play().catch(() => {});
    } else {
      vid.pause(); vid.removeAttribute('src'); vid.load();
      img.src = href.replace(/^\/media\//, '/media/thumb/1600/');  // descarregar/abrir = original
      img.alt = label;
    }
    d.querySelector('[data-lb-name]').textContent = label;
    d.querySelector('[data-lb-count]').textContent = lbLinks.length > 1 ? `${lbIndex + 1} / ${lbLinks.length}` : '';
    d.querySelector('[data-lb-download]').href = href;
    d.querySelector('[data-lb-download]').setAttribute('download', label.trim() || '');
    d.querySelector('[data-lb-open]').href = href;
    d.querySelectorAll('.lb-nav').forEach(b => { b.hidden = lbLinks.length < 2; });
    const fb = d.querySelector('[data-lb-frame]');
    if (fb) { fb.hidden = !(isVideo && a.dataset.framePost); fb.disabled = false; }
  }
  function openLightbox(link) {
    lbLinks = lightboxLinks(link);
    lbIndex = Math.max(0, lbLinks.findIndex(a => a.getAttribute('href') === link.getAttribute('href')));
    showLightbox();
    const d = document.getElementById('lightbox');
    if (!d.open) d.showModal();
  }
  // Fotogramas dos takes: o browser desenha o fotograma visível num PNG e envia-o (sem ffmpeg no servidor).
  function csrfToken() {
    try { return JSON.parse(document.querySelector('.app').getAttribute('hx-headers'))['X-CSRF-Token']; } catch (e) { return ''; }
  }
  function saveFrame(video, url, take, btn) {
    if (!video || !video.videoWidth) { alert('O vídeo ainda não carregou. Carrega em play e tenta outra vez.'); return; }
    const c = document.createElement('canvas');
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
    if (btn) btn.disabled = true;
    c.toBlob(blob => {
      const fd = new FormData();
      fd.append('frame', blob, 'frame.png'); fd.append('take', take); fd.append('t', video.currentTime.toFixed(3));
      fetch(url, { method: 'POST', body: fd, headers: { 'X-CSRF-Token': csrfToken() }, credentials: 'same-origin' })
        .then(r => { if (!r.ok) throw new Error(r.status); const d = document.getElementById('lightbox'); if (d && d.open) d.close(); window.estudioRefresh(); })
        .catch(() => { if (btn) btn.disabled = false; alert('Não foi possível guardar o fotograma.'); });
    }, 'image/png');
  }
  function captureFrom(btn) {
    const video = btn.closest('.col').querySelector('.player video');
    const go = () => saveFrame(video, btn.dataset.framePost, btn.dataset.take, btn);
    if (btn.dataset.frameCapture !== 'last') { video.pause(); go(); return; }
    const seekEnd = () => { video.pause(); video.addEventListener('seeked', go, { once: true }); video.currentTime = Math.max(0, video.duration - 0.05); };
    if (video.readyState >= 1) seekEnd(); else { video.addEventListener('loadedmetadata', seekEnd, { once: true }); video.load(); }
  }

  // Modal de seleção: pesquisa, contador e repor ao cancelar
  function pickerCount(dlg) {
    const out = dlg.querySelector('.pk-count'); if (!out) return;
    const n = dlg.querySelectorAll('.pk-item input:checked').length, max = +dlg.dataset.max || 0;
    out.textContent = max ? `${n} de ${max}` : `${n} escolhido(s)`;
    out.classList.toggle('over', !!max && n > max);
  }
  function resetPicker(dlg) {
    dlg.querySelectorAll('.pk-item input').forEach(i => { i.checked = i.defaultChecked; });
    const q = dlg.querySelector('.pk-search'); if (q) { q.value = ''; q.dispatchEvent(new Event('input', { bubbles: true })); }
    pickerCount(dlg);
  }
  document.addEventListener('input', e => {
    if (!e.target.matches('.pk-search')) return;
    const q = e.target.value.trim().toLowerCase(), dlg = e.target.closest('dialog');
    dlg.querySelectorAll('.pk-item').forEach(it => it.classList.toggle('hide', !!q && !it.dataset.label.includes(q)));
  });
  document.addEventListener('change', e => { const d = e.target.closest('dialog[data-picker]'); if (d) pickerCount(d); });
  document.addEventListener('cancel', e => { if (e.target.matches && e.target.matches('dialog[data-picker]')) resetPicker(e.target); }, true);

  // "Ver seguido" (Montagem): toca os takes escolhidos por ordem, um atrás do outro; um plano sem take mas com
  // frame-chave (data-hold) fica no ecrã os segundos do plano — um animatic até haver takes.
  let lbPlayAll = false, lbHold = null;
  function playAll() {
    lbLinks = lightboxLinks().filter(a => a.closest('.clips') && (/\.(mp4|mov)$/i.test(a.getAttribute('href')) || a.dataset.hold));
    if (!lbLinks.length) return;
    lbIndex = 0; lbPlayAll = true;
    showPlaying();
    const d = document.getElementById('lightbox');
    if (!d.open) d.showModal();
  }
  function showPlaying() {
    clearTimeout(lbHold);
    showLightbox();
    const a = lbLinks[lbIndex];
    if (lbPlayAll && a && a.dataset.hold) lbHold = setTimeout(nextPlaying, (+a.dataset.hold || 4) * 1000);
  }
  function nextPlaying() {
    if (lbIndex < lbLinks.length - 1) { lbIndex += 1; showPlaying(); } else lbPlayAll = false;
  }
  document.addEventListener('ended', e => {
    if (!lbPlayAll || !e.target.matches('[data-lb-video]')) return;
    nextPlaying();
  }, true);
  function stepLightbox(delta) {
    if (lbLinks.length < 2) return;
    lbIndex = (lbIndex + delta + lbLinks.length) % lbLinks.length;
    showPlaying();
  }
  document.addEventListener('close', e => {
    if (e.target.id === 'lightbox') { lbPlayAll = false; clearTimeout(lbHold); const v = e.target.querySelector('[data-lb-video]'); if (v) v.pause(); }
  }, true);
  document.addEventListener('keydown', e => {
    const d = document.getElementById('lightbox');
    if (!d || !d.open) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); stepLightbox(-1); }
    if (e.key === 'ArrowRight') { e.preventDefault(); stepLightbox(1); }
  });

  // Caixas de texto crescem com o conteúdo (vê-se sempre o texto todo).
  function autoGrow(el) {
    if (!el || el.dataset.noGrow !== undefined || !el.offsetParent) return;
    const cs = getComputedStyle(el);
    const border = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    const max = el.closest('.composer') ? 200 : Infinity;
    el.style.height = 'auto';
    const h = Math.min(el.scrollHeight + border, max);
    el.style.height = h + 'px';
    el.style.overflowY = el.scrollHeight + border > max ? 'auto' : 'hidden';
  }
  function growAll(root) {
    (root || document).querySelectorAll('textarea').forEach(autoGrow);
  }
  document.addEventListener('input', e => { if (e.target.tagName === 'TEXTAREA') autoGrow(e.target); });
  document.addEventListener('toggle', e => growAll(e.target), true);  // <details> e <dialog> que abrem
  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => growAll(), 150); });

  function scrollToHash() {
    if (!location.hash) return;
    const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (el && el.tagName === 'DETAILS') el.open = true;
    if (el) el.scrollIntoView({ block: 'start' });
  }

  // Posição do scroll: um botão que grava e volta à mesma página fica onde estava (mesmo que o endereço traga uma
  // âncora). Guarda-se a secção que estava no topo do ecrã (o elemento com id mais próximo) e a distância a ela: assim a
  // posição acerta mesmo que o que está por cima mude de altura (imagens, a maquete 3D a montar).
  const SCROLL_KEY = 'estudio-scroll';
  function topAnchor(c) {
    const box = c.getBoundingClientRect();
    let el = document.elementFromPoint(box.left + Math.min(40, box.width / 2), box.top + 8);
    while (el && el !== c && !(el.id && c.contains(el))) el = el.parentElement;
    return el && el !== c ? { id: el.id, off: el.getBoundingClientRect().top - box.top } : null;
  }
  function saveScroll() {
    const c = document.querySelector('.content');
    if (!c) return;
    const a = topAnchor(c);
    try { sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ p: location.pathname, y: c.scrollTop, a, t: Date.now() })); } catch (e) {}
  }
  let scrollPending = null;  // {y, a}: reaplicado enquanto a página acerta as alturas, até o utilizador mexer no scroll
  function stopRestore() { scrollPending = null; }
  ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(ev => window.addEventListener(ev, stopRestore, { passive: true, capture: true }));
  function applyPending() {
    const c = document.querySelector('.content');
    if (!c || !scrollPending) return;
    const el = scrollPending.a && document.getElementById(scrollPending.a.id);
    if (el && c.contains(el)) c.scrollTop += el.getBoundingClientRect().top - c.getBoundingClientRect().top - scrollPending.a.off;
    else c.scrollTop = scrollPending.y;
  }
  function restoreScroll() {  // devolve true se havia uma posição desta página para repor
    let s = null;
    try { s = JSON.parse(sessionStorage.getItem(SCROLL_KEY) || 'null'); sessionStorage.removeItem(SCROLL_KEY); } catch (e) {}
    if (!s || s.p !== location.pathname || Date.now() - s.t > 60000) return false;
    scrollPending = { y: s.y, a: s.a };
    applyPending();
    const until = Date.now() + 3000;
    const tick = () => { if (!scrollPending) return; applyPending(); if (Date.now() < until) requestAnimationFrame(tick); else scrollPending = null; };
    requestAnimationFrame(tick);
    return true;
  }
  document.addEventListener('submit', e => {
    if (!e.target.closest('[hx-post], [hx-get]')) saveScroll();  // formulários normais (não o Assistente)
  }, true);
  window.addEventListener('beforeunload', saveScroll);  // inclui a atualização quando uma imagem fica pronta

  let hashDone = null;  // a âncora do endereço só se segue uma vez por página (não a cada atualização parcial)
  // Conteúdo que demora (ex.: o custo de um take, pedido ao fornecedor): pede-se uma vez por endereço e a resposta vai
  // para os elementos que estiverem na página quando chega — a página pode ter sido redesenhada entretanto (morph), e um
  // hx-get ficava a escrever num elemento que já não existia. A resposta fica guardada para os redesenhos seguintes.
  const lazyDone = new Map(), lazyBusy = new Set();
  function lazyFill() {
    document.querySelectorAll('[data-lazy]').forEach((el) => {
      const url = el.dataset.lazy;
      if (lazyDone.has(url)) { if (el.innerHTML !== lazyDone.get(url)) el.innerHTML = lazyDone.get(url); return; }
      if (lazyBusy.has(url)) return;
      lazyBusy.add(url);
      fetch(url, { credentials: 'same-origin' })
        .then((r) => (r.ok ? r.text() : null))
        .then((html) => { lazyDone.set(url, html !== null ? html : '<span class="dim">indisponível</span>'); })
        .catch(() => { lazyDone.set(url, '<span class="dim">indisponível</span>'); })
        .finally(() => { lazyBusy.delete(url); lazyFill(); });
    });
  }
  document.addEventListener('htmx:afterSettle', lazyFill);

  // Agenda, Quadro: arrastar um cartão para outra coluna (ou para antes de outro cartão) muda o estado e a ordem.
  let agDrag = null;
  document.addEventListener('dragstart', (e) => {
    const c = e.target.closest && e.target.closest('[data-kanban] [data-item]');
    if (!c) return;
    agDrag = c; c.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', c.dataset.item); } catch (err) { /* nada */ }
  });
  document.addEventListener('dragend', () => {
    if (agDrag) agDrag.classList.remove('dragging');
    document.querySelectorAll('.ag-col.over').forEach((x) => x.classList.remove('over'));
    agDrag = null;
  });
  document.addEventListener('dragover', (e) => {
    const col = agDrag && e.target.closest && e.target.closest('[data-kanban] [data-col]');
    if (!col) return;
    e.preventDefault();
    document.querySelectorAll('.ag-col.over').forEach((x) => { if (x !== col) x.classList.remove('over'); });
    col.classList.add('over');
    const body = col.querySelector('.ag-col-b');
    const after = [...body.querySelectorAll('[data-item]:not(.dragging)')].find((x) => {
      const r = x.getBoundingClientRect(); return e.clientY < r.top + r.height / 2;
    });
    if (after) body.insertBefore(agDrag, after); else body.appendChild(agDrag);
  });
  document.addEventListener('drop', async (e) => {
    const col = agDrag && e.target.closest && e.target.closest('[data-kanban] [data-col]');
    if (!col) return;
    e.preventDefault();
    const board = col.closest('[data-kanban]');
    const next = agDrag.nextElementSibling && agDrag.nextElementSibling.dataset ? agDrag.nextElementSibling.dataset.item : '';
    const body = new URLSearchParams({ status: col.dataset.col, before: next || '' });
    try {
      const r = await fetch(`/agenda/itens/${agDrag.dataset.item}/mover`, { method: 'POST', body, credentials: 'same-origin',
        headers: { 'x-csrf-token': board.dataset.csrf, 'content-type': 'application/x-www-form-urlencoded' } });
      if (!r.ok) throw new Error(r.status);
    } catch (err) { alert('Não foi possível mover o cartão.'); }
    if (window.estudioRefresh) window.estudioRefresh();
  });

  // Agenda, Linha do tempo: abre com o dia de hoje à vista (no telemóvel a grelha é mais larga do que o ecrã).
  function tlToday() {
    document.querySelectorAll('[data-tl-today]').forEach((w) => {
      const col = parseInt(w.dataset.tlToday, 10);
      if (!col || w._scrolled) return;
      const day = w.querySelector('.ag-tl-days .today');
      const lab = w.querySelector('.ag-tl-lab');
      if (day) {
        const x = day.getBoundingClientRect().left - w.getBoundingClientRect().left + w.scrollLeft;
        w.scrollLeft = Math.max(0, x - (lab ? lab.offsetWidth : 0) - 3 * day.offsetWidth);
      }
      w._scrolled = true;
    });
  }
  tlToday();
  document.addEventListener('htmx:afterSettle', tlToday);

  // Guião da cena (História): acrescentar, mover e tirar linhas no próprio browser, sem pedidos ao servidor.
  // Guardar grava tudo pela ordem que se vê (as linhas que já existiam levam o id e não perdem os planos).
  function scriptDirty(el) { const f = el.closest('form'); if (f && guarded(f)) { dirtyForms.add(f); f.classList.add('dirty'); } }
  document.addEventListener('click', (e) => {
    const add = e.target.closest && e.target.closest('[data-line-add]');
    if (add) {
      const box = add.closest('.f').querySelector('[data-script]');
      const tpl = add.closest('.f').querySelector('template[data-line-tpl]');
      if (!box || !tpl) return;
      const row = tpl.content.firstElementChild.cloneNode(true);
      const kind = add.dataset.lineAdd;
      row.querySelector('[name=line_kind]').value = kind;
      row.classList.toggle('k-action', kind === 'action'); row.classList.toggle('k-dialogue', kind === 'dialogue');
      const empty = box.querySelector('.script-empty'); if (empty) empty.remove();
      box.appendChild(row);
      const ta = row.querySelector('textarea'); ta.focus();
      scriptDirty(box);
      return;
    }
    const mv = e.target.closest && e.target.closest('[data-line-move]');
    if (mv) {
      const row = mv.closest('.ln'); const step = +mv.dataset.lineMove;
      const other = step < 0 ? row.previousElementSibling : row.nextElementSibling;
      if (other && other.classList.contains('ln')) {
        if (step < 0) row.parentNode.insertBefore(row, other); else row.parentNode.insertBefore(other, row);
        scriptDirty(row);
      }
      return;
    }
    const del = e.target.closest && e.target.closest('[data-line-del]');
    if (del) { const row = del.closest('.ln'); const box = row.parentNode; scriptDirty(box); row.remove(); }
  });

  // Procurar numa grelha de cartões (ex.: a lista de projetos), sem ir ao servidor.
  document.addEventListener('input', (e) => {
    const t = e.target; if (!t.matches || !t.matches('[data-filter-cards]')) return;
    const q = t.value.trim().toLowerCase();
    document.querySelectorAll(t.dataset.filterCards).forEach((c) => { c.hidden = q && !c.textContent.toLowerCase().includes(q); });
  });

  // Agenda › Lista: escolher todos e contar os escolhidos.
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (t.matches && t.matches('[data-check-all]')) {
      t.closest('form').querySelectorAll('[data-check-one]').forEach((c) => { c.checked = t.checked; });
    }
    if (t.matches && (t.matches('[data-check-all]') || t.matches('[data-check-one]'))) {
      const f = t.closest('form'); const n = f.querySelectorAll('[data-check-one]:checked').length;
      const out = f.querySelector('[data-checked-n]'); if (out) out.textContent = `${n} escolhido${n === 1 ? '' : 's'}`;
    }
  });
  // Agenda › Publicações recorrentes: quantas publicações vão ser criadas, antes de criar.
  function recPreview(dlg) {
    const out = dlg.querySelector('[data-rec-preview]'); if (!out) return;
    const a = dlg.querySelector('[name=start]').value, b = dlg.querySelector('[name=end]').value;
    const days = [...dlg.querySelectorAll('[name=weekdays]:checked')].map((x) => +x.value);
    if (!a || !b || !days.length) { out.textContent = ''; return; }
    let n = 0; const d = new Date(a + 'T12:00:00'), end = new Date(b + 'T12:00:00');
    while (d <= end && n < 1000) { if (days.includes((d.getDay() + 6) % 7)) n += 1; d.setDate(d.getDate() + 1); }
    out.textContent = n ? `Vão ser criadas ${n} publicações.` : 'Nenhum destes dias cai no período.';
  }
  document.addEventListener('input', (e) => { const d = e.target.closest && e.target.closest('[data-rec-count]'); if (d) recPreview(d); });
  document.addEventListener('change', (e) => { const d = e.target.closest && e.target.closest('[data-rec-count]'); if (d) recPreview(d); });
  // Datas: o prazo não pode ser antes do início (avisa já no formulário).
  document.addEventListener('change', (e) => {
    const t = e.target; if (!t.matches || !t.matches('[data-min-for]')) return;
    const due = document.getElementById(t.dataset.minFor); if (due) due.min = t.value || '';
  });

  // Sala de argumentistas: árvore de pastas na barra. Cada pasta lembra-se de estar aberta ou fechada (por projeto, neste
  // browser); a pasta do ficheiro aberto abre sempre. O morph não mexe no «open» destas pastas.
  const treeKey = (nav) => 'estudio-tree-' + nav.dataset.tree;
  const treeState = (nav) => { try { return JSON.parse(store.get(treeKey(nav)) || '{}'); } catch (e) { return {}; } };
  function applyTree() {
    document.querySelectorAll('[data-tree]').forEach((nav) => {
      const st = treeState(nav);
      nav.querySelectorAll('details.rm-dir').forEach((d) => {
        const want = d.hasAttribute('data-has-cur') || (d.dataset.dir in st ? st[d.dataset.dir] : true);
        if (d.open !== want) { d._applying = true; d.open = want; }
      });
    });
  }
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (!d.matches || !d.matches('details.rm-dir')) return;
    if (d._applying) { d._applying = false; return; }
    const nav = d.closest('[data-tree]');
    if (!nav) return;
    const st = treeState(nav);
    st[d.dataset.dir] = d.open;
    store.set(treeKey(nav), JSON.stringify(st));
  }, true);
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('[data-tree-all]');
    if (!b) return;
    const nav = b.closest('.rm-side') && b.closest('.rm-side').querySelector('[data-tree]');
    if (!nav) return;
    const open = b.dataset.treeAll === 'open', st = {};
    nav.querySelectorAll('details.rm-dir').forEach((d) => { st[d.dataset.dir] = open; d._applying = d.open !== open; d.open = open; });
    store.set(treeKey(nav), JSON.stringify(st));
  });
  applyTree();
  document.addEventListener('htmx:afterSettle', applyTree);

  // Secções que se fecham (details[data-fold]): abertas por omissão só quando há coisa a fazer (data-default-open, do
  // servidor). O que o utilizador abre ou fecha fica nesta página até fechar o separador; um link para dentro de uma
  // secção fechada (#frame-chave, #resultados…) abre-a. O morph não mexe no «open» delas.
  const foldKey = () => 'estudio-fold:' + location.pathname;
  const foldState = () => { try { return JSON.parse(sessionStorage.getItem(foldKey()) || '{}'); } catch (e) { return {}; } };
  const setOpen = (d, want) => { if (d.open !== want) { d._applying = true; d.open = want; } };
  function openTo(el) { for (let d = el && el.closest('details[data-fold]'); d; d = d.parentElement && d.parentElement.closest('details[data-fold]')) setOpen(d, true); }
  function applyFolds() {
    const st = foldState();
    document.querySelectorAll('details[data-fold]').forEach((d) => {
      setOpen(d, d.id in st ? st[d.id] : d.dataset.defaultOpen === '1');
    });
    if (location.hash.length > 1) { try { openTo(document.querySelector(location.hash)); } catch (e) { /* hash que não é um seletor */ } }
  }
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (!d.matches || !d.matches('details[data-fold]')) return;
    if (d._applying) { d._applying = false; return; }
    if (!d.id) return;
    const st = foldState(); st[d.id] = d.open;
    try { sessionStorage.setItem(foldKey(), JSON.stringify(st)); } catch (err) { /* sem armazenamento: só não se lembra */ }
  }, true);
  window.addEventListener('hashchange', () => { try { openTo(document.querySelector(location.hash)); } catch (e) { /* nada */ } });
  document.addEventListener('click', (e) => {  // ligações para uma secção desta página (ex.: os passos do plano)
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || a.getAttribute('href').length < 2) return;
    try { openTo(document.querySelector(a.getAttribute('href'))); } catch (err) { /* nada */ }
  });
  document.addEventListener('invalid', (e) => openTo(e.target), true);
  applyFolds();
  document.addEventListener('htmx:afterSettle', applyFolds);

  // Ajuda a pedido: o «?» mostra a explicação num balão (fecha ao clicar fora ou com Esc).
  let tipPop = null;
  const closeTip = () => { if (tipPop) { tipPop.remove(); tipPop = null; } };
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('.tip-btn');
    if (!b) { if (tipPop && !tipPop.contains(e.target)) closeTip(); return; }
    e.preventDefault(); e.stopPropagation();  // dentro de um <summary>: não abre nem fecha a secção
    const same = tipPop && tipPop._for === b;
    closeTip();
    if (same) return;
    tipPop = document.createElement('div');
    tipPop.className = 'tip-pop'; tipPop.setAttribute('role', 'tooltip'); tipPop.textContent = b.dataset.tip; tipPop._for = b;
    document.body.appendChild(tipPop);
    const r = b.getBoundingClientRect(), w = Math.min(340, window.innerWidth - 16);
    tipPop.style.width = w + 'px';
    tipPop.style.left = Math.max(8, Math.min(r.left - 12, window.innerWidth - w - 8)) + 'px';
    const below = r.bottom + 8 + tipPop.offsetHeight < window.innerHeight;
    tipPop.style.top = (below ? r.bottom + 6 : r.top - tipPop.offsetHeight - 6) + 'px';
  }, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeTip(); });
  document.addEventListener('scroll', closeTip, true);
  document.addEventListener('htmx:beforeSwap', closeTip);

  // Alterações por gravar: qualquer formulário onde se escreveu e que ainda não foi submetido. Antes de sair da página
  // (ligações, seletores de navegação, fechar o separador) pergunta-se; Ctrl/Cmd+S grava o formulário principal.
  // Fora: o Assistente (mensagens) e formulários marcados data-no-guard.
  const dirtyForms = new Set();
  const guarded = (f) => f && f.method && f.method.toLowerCase() === 'post' && !f.hasAttribute('data-no-guard')
    && !f.closest('#asst') && !f.matches('#asst-form');
  function markDirty(e) {
    const el = e.target;
    if (!el || !el.form || el.type === 'file' || el.type === 'hidden' || el.hasAttribute('data-autosubmit')) return;
    if (!guarded(el.form)) return;
    dirtyForms.add(el.form);
    el.form.classList.add('dirty');
  }
  document.addEventListener('input', markDirty, true);
  document.addEventListener('submit', (e) => { dirtyForms.delete(e.target); e.target.classList.remove('dirty'); }, true);
  const anyDirty = () => { for (const f of [...dirtyForms]) { if (!f.isConnected) dirtyForms.delete(f); } return dirtyForms.size > 0; };
  window.estudioLeaveOk = () => !anyDirty() || confirm('Tens alterações por guardar nesta página. Sair sem guardar?')
    && (dirtyForms.clear(), true);
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || !anyDirty() || a.hasAttribute('download') || a.target === '_blank' || a.getAttribute('href').startsWith('#')) return;
    if (a.closest('form') && dirtyForms.has(a.closest('form'))) return;
    const to = new URL(a.href, location.href);
    if (to.origin !== location.origin || (to.pathname === location.pathname && to.search === location.search)) return;
    if (!window.estudioLeaveOk()) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  window.addEventListener('beforeunload', (e) => { if (anyDirty()) { e.preventDefault(); e.returnValue = ''; } });
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      const f = document.querySelector('[data-edit-guard]') || [...dirtyForms].find((x) => x.isConnected)
        || document.querySelector('.content form[method=post]');
      if (f) { e.preventDefault(); f.requestSubmit(); }
    }
  });

  // Erros: um pedido que falha (servidor ou rede) diz-o, em vez de falhar em silêncio.
  function toast(msg, kind) {
    let t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; t.setAttribute('role', 'alert'); document.body.appendChild(t); }
    t.textContent = msg; t.classList.toggle('info', kind === 'info'); t.classList.add('on');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 8000);
  }
  window.estudioToast = toast;
  // Descargas que demoram (ex.: o PDF da sala): diz que está a preparar.
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[data-busy-label]');
    if (a) toast(a.dataset.busyLabel + ' Começa a descarregar dentro de segundos.', 'info');
  });
  document.addEventListener('htmx:responseError', (e) => {
    const st = e.detail.xhr ? e.detail.xhr.status : 0;
    toast(st >= 500 ? 'Não foi possível concluir: o servidor deu um erro. O que escreveste continua na página; tenta outra vez daqui a pouco.'
      : `Não foi possível concluir (erro ${st}). Recarrega a página e tenta outra vez.`);
  });
  document.addEventListener('htmx:sendError', () => toast('Sem ligação ao servidor. Verifica a internet; o que escreveste continua na página.'));

  // Ao vivo: se alguém mudou o projeto (o conector MCP, o Assistente, um pedido que acabou), a página redesenha-se
  // sozinha. Não durante um pedido da própria página; o que se está a escrever fica (o morph não mexe nos campos mexidos).
  setInterval(async () => {
    const el = document.getElementById('live');
    if (!el || document.hidden || document.querySelector('.htmx-request') || !window.estudioRefresh) return;
    try {
      const r = await fetch(`/interno/versao/${el.dataset.p}`, { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) return;
      const v = String((await r.json()).v);
      const now = document.getElementById('live');
      if (now && v !== now.dataset.v) { now.dataset.v = v; window.estudioRefresh(); }
    } catch (e) { /* sem rede: tenta outra vez */ }
  }, 3000);

  // Resolução das imagens: só as que o modelo escolhido ao lado aceita (data-res nas opções do modelo); sem nenhuma,
  // o modelo tem tamanho fixo e o seletor fica desligado.
  const RES = ['1K', '2K', '4K'];
  function syncRes(sel) {
    const model = sel.parentElement && sel.parentElement.querySelector('select.gen-select:not(.res-select)');
    const opt = model && model.selectedOptions[0];
    const allowed = ((opt && opt.dataset.res) || '').split(',').filter(Boolean);
    [...sel.options].forEach((o) => { o.hidden = !allowed.includes(o.value); });
    sel.disabled = !allowed.length;
    sel.title = allowed.length ? 'Resolução da imagem' : 'Este modelo não tem escolha de resolução (gera no tamanho dele)';
    // a escolhida à mão fica (se o modelo a aceitar); senão a do projeto, ou a mais próxima dela
    const keep = sel._picked && allowed.includes(sel.value);
    if (allowed.length && !keep) {
      const want = RES.indexOf(sel.dataset.default || '2K');
      sel.value = allowed.reduce((a, b) => (Math.abs(RES.indexOf(b) - want) < Math.abs(RES.indexOf(a) - want) ? b : a));
    }
  }
  document.addEventListener('change', (e) => {
    const t = e.target;
    if (t && t.matches && t.matches('select.res-select')) t._picked = true;
    if (t && t.matches && t.matches('select.gen-select:not(.res-select)') && t.parentElement) {
      t.parentElement.querySelectorAll('select.res-select').forEach(syncRes);
    }
  });

  function init(root) {
    applyTheme();
    lazyFill();
    document.querySelectorAll('select.res-select').forEach(syncRes);
    growAll(root);
    const page = !root || root === document.body || (root.querySelector && root.querySelector('.content')) ||
      (root.matches && root.matches('.content, .app'));
    if (page) {
      const restored = scrollPending !== null || restoreScroll();
      if (!restored && hashDone !== location.href) scrollToHash();
      hashDone = location.href;
    }
    scrollChat();
    (root || document).querySelectorAll('dialog[data-open-on-load]').forEach(d => { if (!d.open) d.showModal(); });
    (root || document).querySelectorAll('textarea[data-count]').forEach(updateCounter);
    // Maquete 3D: o three.js só se carrega nas páginas que têm uma
    const models = document.querySelectorAll('[data-scene3d]:not([data-mounted])');
    if (models.length) import('/static/js/scene3d.js?v=45').then(m => models.forEach(el => m.mount(el)))
      .catch(err => models.forEach(el => { const s = el.querySelector('.m3d-status'); if (s) s.textContent = 'Não foi possível abrir o 3D: ' + err.message; }));
    // Modelo 3D importado de um asset (ficha do asset): pré-visualização a rodar
    const glbs = document.querySelectorAll('[data-model-url]:not([data-mounted])');
    if (glbs.length) import('/static/js/model3d.js?v=2').then(m => glbs.forEach(el => m.preview(el))).catch(() => {});
  }

  document.addEventListener('DOMContentLoaded', () => init());
  // As alturas dependem da fonte: recalcula quando as fontes da página terminam de carregar.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { growAll(); applyPending(); });
  window.addEventListener('load', () => { growAll(); applyPending(); });
  document.addEventListener('htmx:load', e => init(e.target));

  // Mini chat no modal de um prompt: o pedido vai ao modelo de escrever prompts, que devolve o prompt alterado; o texto
  // muda na caixa (fica por guardar até carregar em Guardar) e cada pedido fica na lista, com "Desfazer".
  function pchatSend(box) {
    const input = box.querySelector('.pchat-row input'), btn = box.querySelector('[data-pchat-send]');
    const ta = document.getElementById(box.dataset.target), log = box.querySelector('.pchat-log');
    const instruction = input.value.trim();
    if (!instruction || !ta || btn.disabled) return;
    const before = ta.value;
    const item = document.createElement('div'); item.className = 'pchat-item';
    item.innerHTML = '<div class="pchat-q"></div><div class="pchat-a"><span class="dots"><i></i><i></i><i></i></span> A rever…</div>';
    item.querySelector('.pchat-q').textContent = instruction;
    log.appendChild(item); log.scrollTop = log.scrollHeight;
    input.value = ''; btn.disabled = true;
    const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
    const fail = (msg) => { item.querySelector('.pchat-a').textContent = msg; item.classList.add('err'); btn.disabled = false; };
    fetch(box.dataset.pchat, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ prompt: ta.value, instruction }) })
      .then((r) => r.json()).then((j) => {
        if (!j.ok) return fail(j.error || 'Não foi possível pedir.');
        item.querySelector('.pchat-a').lastChild.textContent = ` A rever com ${j.model}…`;
        const poll = () => fetch(box.dataset.pchatPoll + j.id, { credentials: 'same-origin' }).then((r) => r.json()).then((s) => {
          if (s.status === 'running') return setTimeout(poll, 2000);
          if (s.status !== 'done') return fail(s.error || 'Falhou.');
          ta.value = s.prompt; ta._touched = true; ta.dispatchEvent(new Event('input', { bubbles: true }));
          const a = item.querySelector('.pchat-a'); a.textContent = (s.reply || 'Feito.') + ' ';
          const undo = document.createElement('button'); undo.type = 'button'; undo.className = 'btn ghost sm'; undo.textContent = 'Desfazer';
          undo.addEventListener('click', () => { ta.value = before; ta.dispatchEvent(new Event('input', { bubbles: true })); undo.remove(); });
          a.appendChild(undo); btn.disabled = false; log.scrollTop = log.scrollHeight;
        }).catch(() => setTimeout(poll, 3000));
        setTimeout(poll, 2000);
      }).catch(() => fail('Sem ligação ao servidor.'));
  }
  document.addEventListener('click', (e) => { const b = e.target.closest('[data-pchat-send]'); if (b) pchatSend(b.closest('[data-pchat]')); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.matches('.pchat-row input')) { e.preventDefault(); pchatSend(e.target.closest('[data-pchat]')); }
  });

  // Navegação sem saltos: as páginas não são trocadas, são "morphed" (idiomorph): só muda o que mudou no HTML e a zona
  // de scroll é sempre o mesmo elemento, por isso fica onde está. Numa ação na mesma página (gravar, gerar, aprovar)
  // ignora-se a âncora do endereço; ao mudar de página, vai para o topo (ou para a âncora, se houver).
  let navSamePage = true;
  document.addEventListener('htmx:beforeSwap', e => {
    const d = e.detail;
    if (!d.boosted && !d.estudioRefresh) return;
    try { sessionStorage.removeItem(SCROLL_KEY); } catch (err) { /* nada a repor */ }
    const to = new URL((d.pathInfo && (d.pathInfo.responsePath || d.pathInfo.requestPath)) || location.href, location.href);
    navSamePage = to.pathname === location.pathname;
    if (navSamePage && d.pathInfo) d.pathInfo.anchor = null;
  });
  document.addEventListener('htmx:afterSettle', e => {
    if (e.target !== document.body || navSamePage) return;
    const c = document.querySelector('.content');
    if (c) c.scrollTop = 0;
    scrollToHash();
    navSamePage = true;
  });
  // A maquete 3D montada não é tocada enquanto o cenário for o mesmo (senão perdia a câmara e recarregava o three.js).
  // Modais: quem os abre e fecha é o browser (showModal/close). O morph não lhes mexe no "open" (tirá-lo à força deixava
  // o fundo do modal por cima da página, a bloquear os cliques); um modal fecha-se quando submete o seu formulário.
  document.addEventListener('submit', e => {
    const d = e.submitter && e.submitter.closest('dialog');
    if (d && d.open && !d.hasAttribute('data-keep-open')) d.close();  // os de trabalho (ex.: uma zona) ficam abertos
  }, true);
  // O que o utilizador mexeu e ainda não gravou (um seletor de modelo, um prompt a meio) não é reposto pelo morph:
  // fica marcado ao mudar e deixa de estar marcado quando o formulário dele é submetido.
  const touch = (e) => { const t = e.target; if (t && t.matches && t.matches('input, textarea, select')) t._touched = true; };
  document.addEventListener('input', touch, true);
  document.addEventListener('change', touch, true);
  // Campos que outra pessoa (o Assistente, o conector MCP, outra página) pode ter mudado entretanto: só seguem se foram
  // mexidos aqui. Senão, gravar a página repunha o valor antigo que ela mostrava.
  document.addEventListener('submit', (e) => {
    const els = [...e.target.elements];
    const off = els.filter((el) => el.hasAttribute && el.hasAttribute('data-only-if-changed') && !el._touched && !el.disabled);
    // um grupo inteiro (ex.: o que segue no pedido de vídeo) só segue se algum dos seus campos foi mexido
    els.filter((el) => el.dataset && el.dataset.unlessTouched && !el.disabled).forEach((marker) => {
      if (!els.some((x) => x.dataset && x.dataset.group === marker.dataset.unlessTouched && x._touched)) off.push(marker);
    });
    off.forEach((el) => { el.disabled = true; });
    setTimeout(() => off.forEach((el) => { el.disabled = false; }), 0);
  }, true);
  document.addEventListener('submit', (e) => {
    const f = e.target;
    [...f.elements].forEach((el) => { el._touched = false; });
  }, true);
  const touched = (el) => el && (el._touched || (el.tagName === 'OPTION' && el.parentElement && el.parentElement._touched));
  if (window.Idiomorph) {
    Idiomorph.defaults.callbacks.beforeAttributeUpdated = (attr, el) =>
      !(attr === 'open' && (el.tagName === 'DIALOG' || (el.matches && el.matches('details.rm-dir, details[data-fold]'))))
      && !(touched(el) && ['value', 'selected', 'checked'].includes(attr));
    Idiomorph.defaults.callbacks.beforeNodeMorphed = (oldNode, newNode) => {
      if (oldNode.nodeType === 1 && touched(oldNode)) return false;  // mexido e por gravar: fica como está
      if (oldNode.nodeType === 1 && oldNode.closest && oldNode.closest('form.dirty')) return false;  // formulário com alterações por guardar
      if (oldNode.nodeType !== 1 || !oldNode.hasAttribute('data-scene3d') || !oldNode.dataset.mounted) return true;
      const same = (sel) => oldNode.querySelector(sel)?.textContent === newNode.querySelector?.(sel)?.textContent;
      return !(same('.m3d-data') && same('.m3d-lviews') && same('.m3d-moments') && same('.m3d-locpending')
        && oldNode.dataset.previewHash === newNode.dataset?.previewHash
        && (oldNode.dataset.momentsStale || '') === (newNode.dataset?.momentsStale || ''));
    };
  }
  // Atualizar a página (ex.: uma imagem ficou pronta) sem a recarregar: pede-a de novo e faz morph.
  window.estudioRefresh = () => htmx.ajax('GET', location.pathname + location.search,
    { target: document.body, swap: 'morph:innerHTML', headers: { 'HX-Boosted': 'true' } });
  document.body.addEventListener('estudio-refresh', () => window.estudioRefresh());

  // Assistente: @ abre a lista de assets do projeto (a ficha e as imagens deles seguem com a mensagem).
  const KIND_PT = { character: 'Personagem', location: 'Local', prop: 'Adereço', element: 'Elemento do mundo' };
  let mention = null;  // {ta, list, start, items, index}
  function closeMentions() { if (mention) mention.list.hidden = true; mention = null; }
  function updateMentions(ta) {
    const list = ta.parentElement.querySelector('.mention-list');
    if (!list) return;
    const before = ta.value.slice(0, ta.selectionStart);
    const m = before.match(/@([A-Za-z0-9_.]*)$/);
    if (!m) { closeMentions(); return; }
    let all = [];
    try { all = JSON.parse(ta.dataset.mentions || '[]'); } catch (e) {}
    const q = m[1].toUpperCase();
    const items = all.filter(a => a.code.toUpperCase().includes(q) || (a.label || '').toUpperCase().includes(q)).slice(0, 8);
    if (!items.length) { closeMentions(); return; }
    mention = { ta, list, start: before.length - m[0].length, items, index: 0 };
    list.innerHTML = '';
    items.forEach((a, i) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'mention-item' + (i === 0 ? ' on' : ''); b.dataset.i = i;
      b.innerHTML = '<span class="mono"></span><span class="dim"></span>';
      b.children[0].textContent = a.code; b.children[1].textContent = (KIND_PT[a.kind] ? KIND_PT[a.kind] + ' · ' : '') + (a.label || '');
      list.appendChild(b);
    });
    list.hidden = false;
  }
  function pickMention(i) {
    if (!mention) return;
    const { ta, start, items } = mention;
    const code = '@' + items[i].code + ' ';
    const end = ta.selectionStart;
    ta.value = ta.value.slice(0, start) + code + ta.value.slice(end);
    const pos = start + code.length;
    ta.focus(); ta.setSelectionRange(pos, pos);
    closeMentions(); autoGrow(ta);
  }
  document.addEventListener('input', e => { if (e.target.matches('textarea[data-mentions]')) updateMentions(e.target); });
  // Vídeo: mudar o modelo ou o modo grava logo (a página mostra as imagens e o prompt desse modo)
  document.addEventListener('change', e => {
    if (e.target.matches('[data-video-model], [data-video-mode], [data-autosave]') && e.target.form) e.target.form.requestSubmit();
  });
  document.addEventListener('click', e => {
    const item = e.target.closest('.mention-item');
    if (item) { e.preventDefault(); pickMention(+item.dataset.i); return; }
    if (mention && !e.target.closest('.mention-list')) closeMentions();
  });
  document.addEventListener('keydown', e => {
    if (!mention || e.target !== mention.ta) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      mention.index = (mention.index + (e.key === 'ArrowDown' ? 1 : -1) + mention.items.length) % mention.items.length;
      mention.list.querySelectorAll('.mention-item').forEach((b, i) => b.classList.toggle('on', i === mention.index));
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault(); e.stopImmediatePropagation(); pickMention(mention.index);
    } else if (e.key === 'Escape') { closeMentions(); }
  }, true);

  // Assistente: Enter envia (Shift+Enter muda de linha); mostra logo a mensagem enviada.
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && e.target.matches('textarea[data-enter-submit]')) {
      e.preventDefault();
      if (e.target.value.trim()) e.target.form.requestSubmit();
    }
  });
  document.addEventListener('htmx:beforeRequest', e => {
    const form = e.detail.elt;
    if (!form || form.id !== 'asst-form') return;
    const text = form.querySelector('textarea').value.trim();
    const box = document.getElementById('asst-msgs');
    const thinking = document.getElementById('asst-thinking');
    if (text && box && thinking) {
      const bubble = document.createElement('div');
      bubble.className = 'msg u';
      bubble.textContent = text;
      box.insertBefore(bubble, thinking);
      box.querySelectorAll('.asst-empty').forEach(n => n.remove());
      scrollChat(true);
    }
  });
  document.addEventListener('htmx:afterRequest', e => {
    const form = e.detail.elt;
    if (form && form.id === 'asst-form') form.querySelectorAll('input[name^="draft_"]').forEach(n => n.remove());
    if (form && form.id === 'asst-form' && e.detail.successful) { const ta = form.querySelector('textarea'); ta.value = ''; autoGrow(ta); }
  });
  document.addEventListener('htmx:afterSwap', e => { if (e.target.id === 'asst-msgs') scrollChat(); growAll(e.target); });
  document.addEventListener('submit', e => {
    const b = e.submitter;
    if (b && b.dataset.busyLabel) {
      setTimeout(() => { b.disabled = true; b.textContent = b.dataset.busyLabel; }, 0);
    }
  });

  document.addEventListener('input', e => {
    if (e.target.matches('textarea[data-count]')) updateCounter(e.target);
  });

  // Configuração do Assistente: o seletor de modelo mostra só os do fornecedor escolhido (reconstrói as opções:
  // o Safari/iPad ignora "hidden" em <option>).
  function syncProvider(sel, keepSelection) {
    if (!sel) return;
    const key = sel.dataset.providerSelect;
    const models = document.querySelector(key ? `[data-provider-models="${key}"]` : '[data-provider-models]');
    if (!models) return;
    if (!models._all) {  // lista completa, guardada uma vez no próprio elemento
      models._all = [...models.options].map(o => ({ value: o.value, text: o.textContent, provider: o.dataset.provider }));
    }
    const current = keepSelection ? models.value : null;
    models.innerHTML = '';
    models._all.filter(o => o.provider === sel.value).forEach(o => {
      const opt = document.createElement('option');
      opt.value = o.value; opt.textContent = o.text; opt.dataset.provider = o.provider;
      if (o.value === current) opt.selected = true;
      models.appendChild(opt);
    });
    models.closest('.f').hidden = sel.value === 'same';
    if (!key) document.querySelectorAll('[data-provider-hint]').forEach(h => { h.hidden = h.dataset.providerHint !== sel.value; });
  }
  document.addEventListener('change', e => { if (e.target.matches('[data-provider-select]')) syncProvider(e.target, false); });
  const initProvider = () => document.querySelectorAll('[data-provider-select]').forEach(sel => syncProvider(sel, true));
  document.addEventListener('DOMContentLoaded', initProvider);
  document.addEventListener('htmx:load', e => { if (e.target.querySelector && e.target.querySelector('[data-provider-select]')) initProvider(); });

  // Assets: pesquisa imediata (filtra cartões/linhas e esconde grupos vazios).
  document.addEventListener('input', e => {
    if (!e.target.matches('[data-asset-search]')) return;
    const q = e.target.value.trim().toLowerCase();
    let any = false;
    document.querySelectorAll('details[data-group]').forEach(g => {
      let n = 0;
      g.querySelectorAll('[data-search]').forEach(el => {
        const ok = !q || el.dataset.search.includes(q);
        el.hidden = !ok; if (ok) n++;
      });
      g.hidden = n === 0; if (n) any = true;
      if (q && n) g.open = true;
    });
    const none = document.querySelector('.asset-none');
    if (none) none.hidden = any;
  });

  // Upload: arrastar ficheiros para a zona ou escolher (submete logo).
  document.addEventListener('dragover', e => {
    const z = e.target.closest && e.target.closest('[data-dropzone]');
    if (!z) return;
    e.preventDefault();
    z.classList.add('over');
  });
  document.addEventListener('dragleave', e => {
    const z = e.target.closest && e.target.closest('[data-dropzone]');
    if (z && !z.contains(e.relatedTarget)) z.classList.remove('over');
  });
  document.addEventListener('drop', e => {
    const z = e.target.closest && e.target.closest('[data-dropzone]');
    if (!z) return;
    e.preventDefault();
    z.classList.remove('over');
    const input = z.querySelector('input[type=file]');
    if (!e.dataTransfer.files.length) return;
    input.files = e.dataTransfer.files;
    z.requestSubmit();
  });

  document.addEventListener('change', e => {
    const t = e.target;
    if (t.matches('input[type=file][data-autosubmit]') && t.files.length) t.form.requestSubmit();
    if (t.matches('input[type=radio][data-autosubmit]') && t.form) t.form.requestSubmit();
    if (t.matches('select[data-autosubmit]') && t.form) t.form.requestSubmit();
    if (t.matches('select[data-nav]')) { if (window.estudioLeaveOk()) window.location = t.value; return; }
    if (t.matches('select[data-ep-select]')) {
      if (!window.estudioLeaveOk()) return;
      // Mudar de episódio: fica na mesma etapa, agora do outro episódio.
      const u = new URL(location.href);
      if (/\/historia\/episodios\/\d+/.test(u.pathname)) { window.location = u.pathname.replace(/episodios\/\d+/, 'episodios/' + t.value); return; }
      ['ep', 'grupo', 'ambito', 'cena'].forEach(k => u.searchParams.delete(k));
      if (/\/etapa\/3$/.test(u.pathname)) u.searchParams.set('grupo', t.value);
      else if (/\/etapa\/[67]$/.test(u.pathname)) u.searchParams.set('ambito', 'ep-' + t.value);
      else u.searchParams.set('ep', t.value);
      if (/\/storyboard\/(cenas|planos)\//.test(u.pathname)) { window.location = '/projetos/' + u.pathname.split('/')[2] + '/etapa/5?ep=' + t.value; return; }
      if (/\/video\/planos\//.test(u.pathname)) { window.location = '/projetos/' + u.pathname.split('/')[2] + '/etapa/6?ambito=ep-' + t.value; return; }
      u.hash = ''; window.location = u.toString();
    }
    if (t.matches('select[data-line-kind]')) {
      const ln = t.closest('.ln');
      ln.classList.toggle('k-action', t.value === 'action');
      ln.classList.toggle('k-dialogue', t.value === 'dialogue');
    }
  });

  // Confirmação antes de ações destrutivas (botões com data-confirm).
  let pending = null;
  document.addEventListener('click', e => {
    const b = e.target.closest('button[data-confirm]');
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    const d = document.getElementById('confirm-dlg');
    d.querySelector('[data-confirm-title]').textContent = b.dataset.confirm;
    d.querySelector('[data-confirm-text]').textContent = b.dataset.confirmText || 'Esta ação não pode ser desfeita.';
    d.querySelector('[data-confirm-ok]').textContent = b.dataset.confirmOk || 'Apagar';
    pending = b;
    d.showModal();
  }, true);

  document.addEventListener('click', e => {
    const t = e.target;
    const ok = t.closest('[data-confirm-ok]');
    if (ok && pending) {
      ok.closest('dialog').close();
      const form = pending.form;
      if (pending.name) {
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = pending.name;
        hidden.value = pending.value;
        form.appendChild(hidden);
      }
      if (pending.formAction && pending.getAttribute('formaction')) form.action = pending.formAction;
      pending = null;
      form.requestSubmit();
      return;
    }
    if (t.closest('[data-close-dialog]')) {
      const dlg = t.closest('dialog');
      if (dlg.matches('[data-picker]')) resetPicker(dlg);  // Cancelar: repõe a escolha que estava
      dlg.close(); pending = null; return;
    }

    // Visualizador de imagens: links para /media/ de imagens abrem num modal.
    const imgLink = t.closest('a[href^="/media/"]');
    if (imgLink && /\.(png|jpe?g|webp|mp4|mov)$/i.test(imgLink.getAttribute('href')) && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
      e.preventDefault();
      openLightbox(imgLink);
      return;
    }
    if (t.closest('[data-play-all]')) { playAll(); return; }
    const fcap = t.closest('[data-frame-capture]');
    if (fcap) { captureFrom(fcap); return; }
    const lbf = t.closest('[data-lb-frame]');
    if (lbf) {
      const a = lbLinks[lbIndex], v = document.querySelector('#lightbox [data-lb-video]');
      if (a && a.dataset.framePost) { v.pause(); saveFrame(v, a.dataset.framePost, a.dataset.take, lbf); }
      return;
    }
    if (t.closest('[data-lb-close]')) { document.getElementById('lightbox').close(); return; }
    if (t.closest('[data-lb-prev]')) { stepLightbox(-1); return; }
    if (t.closest('[data-lb-next]')) { stepLightbox(1); return; }
    if (t.id === 'lightbox') { t.close(); return; }  // clique fora da imagem

    const editBtn = t.closest('[data-edit-image]');
    if (editBtn) {
      const d = document.getElementById('edit-dlg');
      const form = d.querySelector('form');
      const pid = (location.pathname.match(/^\/projetos\/(\d+)/) || [])[1];
      form.action = `/projetos/${pid}/imagens/editar/${editBtn.dataset.editImage}`;
      form.querySelector('[name=role]').value = editBtn.dataset.editRole || '';
      form.querySelector('[name=back]').value = location.pathname + location.search;
      d.querySelector('.edit-preview').src = editBtn.dataset.editSrc;
      d.querySelector('[data-edit-title]').textContent = 'Editar imagem · ' + (editBtn.dataset.editName || '');
      d.showModal();
      growAll(d);
      return;
    }

    const det = t.closest('[data-open-details]');
    if (det) { const d = document.getElementById(det.dataset.openDetails); if (d) d.open = true; }

    const open = t.closest('[data-open-dialog]');
    if (open) { const dlg = document.getElementById(open.dataset.openDialog); dlg.showModal(); growAll(dlg); if (dlg.matches('[data-picker]')) pickerCount(dlg); return; }

    const theme = t.closest('[data-theme-set]');
    if (theme) { store.set('estudio-theme', theme.dataset.themeSet); applyTheme(); return; }

    // Sala de argumentistas: leitura em papel (fundo branco) ou escura; fica no <html> (o morph não lhe mexe)
    if (t.closest('[data-rd-toggle]')) {
      const paper = document.documentElement.dataset.rd !== 'paper';
      document.documentElement.dataset.rd = paper ? 'paper' : 'dark';
      store.set('estudio-rd', paper ? 'paper' : 'dark');
      return;
    }

    if (t.closest('[data-toggle-asst]')) {
      const a = app();
      if (window.innerWidth < 1024) {
        a.classList.toggle('asst-open');
      } else {
        a.classList.toggle('asst-collapsed');
        store.set('estudio-asst', a.classList.contains('asst-collapsed') ? 'collapsed' : 'open');
      }
      scrollChat();
      growAll(document.getElementById('asst'));
      return;
    }

    const ask = t.closest('[data-asst-ask]');
    if (ask && ask.dataset.askFrom && !ask.dataset.saved) {
      // Grava primeiro o campo de onde parte o pedido (o Assistente lê-o da página gravada)
      const src = document.getElementById(ask.dataset.askFrom);
      if (src && src.form) {
        fetch(src.form.action, { method: 'POST', body: new FormData(src.form), credentials: 'same-origin' })
          .finally(() => { ask.dataset.saved = '1'; ask.click(); delete ask.dataset.saved; src.defaultValue = src.value; });
        return;
      }
    }
    if (ask) {
      const form = document.getElementById('asst-form');
      if (!form) return;
      const a = app();
      if (window.innerWidth < 1024) a.classList.add('asst-open');
      else { a.classList.remove('asst-collapsed'); store.set('estudio-asst', 'open'); }
      // Prompt editado e ainda não gravado: o Assistente recebe o texto da caixa em vez do guardado.
      form.querySelectorAll('input[name^="draft_"]').forEach(n => n.remove());
      const src = ask.dataset.improve && document.getElementById(ask.dataset.improve);
      if (src && src.value !== src.defaultValue) {
        [['draft_field', ask.dataset.improve], ['draft_value', src.value]].forEach(([n, v]) => {
          const h = document.createElement('input'); h.type = 'hidden'; h.name = n; h.value = v; form.appendChild(h);
        });
      }
      form.querySelector('textarea').value = ask.dataset.asstAsk;
      form.requestSubmit();
      return;
    }

    if (t.closest('[data-toggle-side]')) {
      const a = app();
      if (window.innerWidth < 1024) {
        a.classList.toggle('side-open');
      } else {
        a.classList.toggle('side-collapsed');
        store.set('estudio-side', a.classList.contains('side-collapsed') ? 'collapsed' : 'open');
      }
      return;
    }

    const copy = t.closest('[data-copy]');
    if (copy) {
      const src = document.getElementById(copy.dataset.copy);
      navigator.clipboard.writeText(src.value !== undefined ? src.value : src.textContent).then(() => {
        const label = copy.querySelector('.lbl');
        if (!label) return;
        const old = label.textContent;
        label.textContent = 'Copiado';
        setTimeout(() => { label.textContent = old; }, 1200);
      });
      return;
    }

    // Fecha o menu de projetos ao clicar fora.
    document.querySelectorAll('details.psw-menu[open]').forEach(d => { if (!d.contains(t)) d.open = false; });
  });
})();

// Voz de referência: mostra os campos do modelo escolhido (voz do Artlist, voz de catálogo da fal ou descrição) e a
// amostra da voz do Artlist escolhida.
(function () {
  function sync(form) {
    const sel = form.querySelector('[data-voice-model]');
    if (!sel) return;
    const opt = sel.selectedOptions[0];
    const caps = (opt?.dataset.caps || '').split(' ');
    const artlist = sel.value === 'artlist';
    const show = { artlist, 'catalog-fal': !artlist && caps.includes('catalog'), design: caps.includes('design') };
    form.querySelectorAll('[data-voice-when]').forEach((box) => {
      const on = !!show[box.dataset.voiceWhen];
      box.hidden = !on;
      box.querySelectorAll('input, select, textarea').forEach((el) => { el.disabled = !on; });
    });
    const hint = form.querySelector('[data-voice-hint]');
    if (hint) hint.textContent = opt?.dataset.hint ? 'Escreve o ' + opt.dataset.hint + '.' : '';
  }
  function how(form) { // "Descrever a voz" / "Escolher do catálogo": só os modelos que fazem isso
    const choice = form.querySelector('[data-voice-how]:checked')?.value;
    const sel = form.querySelector('[data-voice-model]');
    if (!choice || !sel) return;
    let first = null;
    [...sel.options].forEach((o) => {
      const ok = (o.dataset.caps || '').split(' ').includes(choice);
      o.hidden = !ok; o.disabled = !ok;
      if (ok && !first) first = o;
    });
    if (sel.selectedOptions[0]?.disabled && first) sel.value = first.value;
  }
  document.addEventListener('change', (e) => {
    const form = e.target.closest('[data-voice-form]');
    if (form && e.target.matches('[data-voice-how]')) { how(form); sync(form); }
    if (form && e.target.matches('[data-voice-model]')) sync(form);
    if (e.target.matches('[data-voice-preview]')) {
      const player = e.target.closest('[data-voice-form]')?.querySelector('[data-voice-player]');
      const url = e.target.selectedOptions[0]?.dataset.preview;
      if (player && url) { player.src = url; player.play().catch(() => {}); }
    }
  });
  const all = (root) => (root || document).querySelectorAll('[data-voice-form]').forEach((f) => { how(f); sync(f); });
  document.addEventListener('DOMContentLoaded', () => all());
  document.addEventListener('htmx:load', (e) => all(e.target));
})();


// Cortar uma voz de referência: as barras e os números de início/fim andam juntos, "Ouvir o trecho" toca só esse
// pedaço e a duração que fica é comparada com os segundos livres de cada plano.
(() => {
  const num = (x) => Math.round(parseFloat(String(x).replace(',', '.')) * 10) / 10;
  const fmt = (x) => x.toFixed(1).replace('.', ',');
  function setMax(form, dur) {
    if (!(dur > 0)) return;
    form.dataset.duration = dur;
    form.querySelectorAll('[data-trim-start],[data-trim-end]').forEach((r) => { r.max = dur; });
    const end = form.querySelector('[data-trim-end-n]');
    if (num(end.value) > dur || !num(form.dataset.touched || 0)) {
      end.value = dur; form.querySelector('[data-trim-end]').value = dur;
    }
  }
  function update(form, from) {
    const dur = num(form.dataset.duration) || 9999;
    const s = form.querySelector('[data-trim-start]'), e = form.querySelector('[data-trim-end]');
    const sn = form.querySelector('[data-trim-start-n]'), en = form.querySelector('[data-trim-end-n]');
    let a = num(from === 'sn' ? sn.value : s.value), b = num(from === 'en' ? en.value : e.value);
    if (isNaN(a)) a = 0; if (isNaN(b)) b = dur;
    a = Math.max(0, Math.min(a, dur)); b = Math.max(0, Math.min(b, dur));
    if (b - a < 0.5) { if (from === 's' || from === 'sn') a = Math.max(0, b - 0.5); else b = Math.min(dur, a + 0.5); }
    s.value = a; e.value = b;
    if (from !== 'sn') sn.value = a;
    if (from !== 'en') en.value = b;
    const len = Math.round((b - a) * 10) / 10;
    const out = form.querySelector('[data-trim-len]');
    if (out) out.textContent = fmt(len);
    form.querySelectorAll('[data-trim-free]').forEach((x) => {
      x.closest('span').classList.toggle('warn-t', len > parseFloat(x.dataset.trimFree) + 0.05);
    });
  }
  document.addEventListener('input', (ev) => {
    const form = ev.target.closest('[data-trim]');
    if (!form) return;
    form.dataset.touched = 1;
    const t = ev.target;
    const from = t.matches('[data-trim-start]') ? 's' : t.matches('[data-trim-end]') ? 'e'
      : t.matches('[data-trim-start-n]') ? 'sn' : t.matches('[data-trim-end-n]') ? 'en' : null;
    if (from) update(form, from);
  });
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-trim-play]');
    if (!btn) return;
    const form = btn.closest('[data-trim]');
    const audio = form.querySelector('[data-trim-audio]');
    const a = num(form.querySelector('[data-trim-start-n]').value) || 0;
    const b = num(form.querySelector('[data-trim-end-n]').value);
    audio.currentTime = a;
    const stop = () => { if (audio.currentTime >= b) { audio.pause(); audio.removeEventListener('timeupdate', stop); } };
    audio.removeEventListener('timeupdate', audio._trimStop || stop);
    audio._trimStop = stop;
    audio.addEventListener('timeupdate', stop);
    audio.play().catch(() => {});
  });
  // Sem duração guardada (vozes antigas), lê-se do próprio áudio.
  document.addEventListener('loadedmetadata', (ev) => {
    const form = ev.target.closest && ev.target.closest('[data-trim]');
    if (form && ev.target.matches('[data-trim-audio]') && !(num(form.dataset.duration) > 0)) {
      setMax(form, Math.floor(ev.target.duration * 10) / 10); update(form, 'e');
    }
  }, true);
  const all = (root) => (root || document).querySelectorAll('[data-trim]').forEach((f) => update(f, 'e'));
  document.addEventListener('DOMContentLoaded', () => all());
  document.addEventListener('htmx:load', (e) => all(e.target));
})();

// Chat do Assistente dentro de um modal (ex.: a maquete 3D de um local): com o modal aberto, o painel do Assistente
// passa para o lugar marcado no modal (.asst-dock) — a conversa é a mesma — e volta ao sítio quando o modal fecha.
(() => {
  let home = null;
  const panel = () => document.getElementById('asst');
  function dock(dlg) {
    const a = panel(), slot = dlg.querySelector('.asst-dock');
    if (!a || !slot || slot.contains(a)) return;
    home = { parent: a.parentNode, next: a.nextSibling };
    slot.appendChild(a);
    a.classList.add('docked');
    const msgs = a.querySelector('.msgs');
    if (msgs) msgs.scrollTop = msgs.scrollHeight;
  }
  function undock() {
    const a = panel();
    if (!a || !a.classList.contains('docked')) return;
    a.classList.remove('docked');
    if (home && home.parent && home.parent.isConnected) home.parent.insertBefore(a, home.next && home.next.parentNode === home.parent ? home.next : null);
    home = null;
  }
  const sync = () => {
    const open = [...document.querySelectorAll('dialog[data-asst-dock][open]')].pop();
    if (open) dock(open); else undock();
  };
  new MutationObserver(sync).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['open'] });
  document.addEventListener('close', (e) => { if (e.target.matches && e.target.matches('dialog[data-asst-dock]')) undock(); }, true);
  document.addEventListener('htmx:afterSettle', sync); // a página atualizou-se (morph): volta a pôr o chat no modal aberto
  document.addEventListener('DOMContentLoaded', sync);
})();

// Botões que lançam trabalho demorado (ex.: «Gerar imagem»): ao carregar, mostram logo que o pedido seguiu, enquanto a
// página não volta. Desativa-se depois de o formulário partir, para o nome e o valor do botão irem no pedido.
document.addEventListener('submit', (e) => {
  const b = e.submitter;
  if (!b || !b.dataset.busyText) return;
  setTimeout(() => { b.disabled = true; b.classList.add('busy'); b.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>' + b.dataset.busyText; }, 0);
}, true);
