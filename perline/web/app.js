(function () {
  const C = window.Core;
  const $ = (id) => document.getElementById(id);
  const cv = $('cv'), ctx = cv.getContext('2d');
  const OPZ = { larghezzaMm: 1.6, altezzaMm: 1.3, fattoreFilo: 4, margineCm: 30, perRp: 200, spreccoOrditoCm: 40, extraTramaMm: 10 };

  let S = nuovoProgetto();
  let colore = 0, strumento = 'penna', zoom = 22, disegnando = false;
  let undoSt = [], redoSt = [];

  function nuovoProgetto() {
    const palette = C.DEFAULT_PALETTE.map((c) => ({ ...c }));
    const rings = [2, 1, 0, 0];
    return { nome: '', tecnica: 'telaio', w: 10, h: 56, palette, celle: C.genera('rombi', 10, 56, 14, rings), opz: { ...OPZ } };
  }

  function msg(t) { $('msg').textContent = t; clearTimeout(msg.t); msg.t = setTimeout(() => ($('msg').textContent = ''), 3500); }

  // ---- disegno ----
  function geom(z) {
    const cw = z, ch = Math.round(z * 0.8), margL = 44, margT = 26;
    return { cw, ch, margL, margT };
  }
  function disegna(c2d, z, opt) {
    const { cw, ch, margL, margT } = geom(z);
    const sfal = S.tecnica === 'peyote' ? ch / 2 : 0;
    const W = margL + S.w * cw + 10, H = margT + S.h * ch + sfal + 6;
    c2d.canvas.width = W; c2d.canvas.height = H;
    c2d.fillStyle = '#fff'; c2d.fillRect(0, 0, W, H);
    c2d.font = Math.max(9, Math.round(z * 0.5)) + 'px sans-serif';
    c2d.textAlign = 'center'; c2d.textBaseline = 'middle';
    for (let r = 0; r < S.h; r++) for (let c = 0; c < S.w; c++) {
      const x = margL + c * cw, y = margT + r * ch + (c % 2 ? sfal : 0);
      const col = S.palette[S.celle[r][c]] || { hex: '#f0f' };
      c2d.fillStyle = col.hex; c2d.fillRect(x, y, cw, ch);
      c2d.strokeStyle = 'rgba(0,0,0,.35)'; c2d.lineWidth = 1; c2d.strokeRect(x + .5, y + .5, cw - 1, ch - 1);
      if (opt.lettere) {
        const lum = parseInt(col.hex.slice(1, 3), 16) * .3 + parseInt(col.hex.slice(3, 5), 16) * .59 + parseInt(col.hex.slice(5, 7), 16) * .11;
        c2d.fillStyle = lum > 140 ? '#222' : '#fff';
        c2d.fillText(C.lettera(S.celle[r][c]), x + cw / 2, y + ch / 2 + 1);
      }
    }
    c2d.fillStyle = '#222'; c2d.textAlign = 'right';
    for (let r = 0; r < S.h; r++) if ((r + 1) % 5 === 0 || r === 0) {
      c2d.fillText(String(r + 1), margL - 6, margT + r * ch + ch / 2);
      c2d.strokeStyle = '#999'; c2d.beginPath(); c2d.moveTo(margL - 4, margT + r * ch + ch / 2 + .5); c2d.lineTo(margL, margT + r * ch + ch / 2 + .5); c2d.stroke();
    }
    c2d.textAlign = 'center';
    for (let c = 0; c < S.w; c++) c2d.fillText(String(c + 1), margL + c * cw + cw / 2, 12);
    c2d.textAlign = 'left'; c2d.fillText('START ↓', 2, 12);
  }
  function ridisegna() { disegna(ctx, zoom, { lettere: $('lettere').checked }); materiali(); }

  function cella(ev) {
    const rect = cv.getBoundingClientRect();
    const { cw, ch, margL, margT } = geom(zoom);
    const x = (ev.clientX - rect.left) * (cv.width / rect.width), y = (ev.clientY - rect.top) * (cv.height / rect.height);
    const c = Math.floor((x - margL) / cw);
    if (c < 0 || c >= S.w) return null;
    const sf = S.tecnica === 'peyote' && c % 2 ? ch / 2 : 0;
    const r = Math.floor((y - margT - sf) / ch);
    return r < 0 || r >= S.h ? null : { r, c };
  }

  // ---- storia ----
  function snapshot() { undoSt.push(JSON.stringify(S.celle)); if (undoSt.length > 100) undoSt.shift(); redoSt = []; }
  function annulla() { if (!undoSt.length) return; redoSt.push(JSON.stringify(S.celle)); S.celle = JSON.parse(undoSt.pop()); ridisegna(); }
  function ripeti() { if (!redoSt.length) return; undoSt.push(JSON.stringify(S.celle)); S.celle = JSON.parse(redoSt.pop()); ridisegna(); }

  function applica(p) {
    const val = (r, c) => { S.celle[r][c] = colore; };
    if (strumento === 'riempi') { C.riempi(S.celle, p.r, p.c, colore); if ($('simm').checked) C.riempi(S.celle, p.r, S.w - 1 - p.c, colore); }
    else { val(p.r, p.c); if ($('simm').checked) val(p.r, S.w - 1 - p.c); }
  }
  cv.addEventListener('mousedown', (e) => {
    const p = cella(e); if (!p) return;
    snapshot(); disegnando = strumento === 'penna'; applica(p); ridisegna();
  });
  cv.addEventListener('mousemove', (e) => { if (!disegnando) return; const p = cella(e); if (p) { applica(p); ridisegna(); } });
  window.addEventListener('mouseup', () => { disegnando = false; });
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); annulla(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'y') { e.preventDefault(); ripeti(); }
  });

  // ---- palette ----
  function opzioniColore() {
    ['g-c0', 'g-c1', 'g-c2', 'g-c3'].forEach((id, i) => {
      const sel = $(id), v = sel.value;
      sel.innerHTML = S.palette.map((c, k) => `<option value="${k}">${C.lettera(k)} - ${c.cod || c.nome}</option>`).join('');
      sel.value = v !== '' && +v < S.palette.length ? v : String(Math.min(i === 0 ? 2 : i === 1 ? 1 : 0, S.palette.length - 1));
    });
  }
  function disegnaPalette() {
    const usati = C.conteggi(S.celle, S.palette.length);
    $('pal').innerHTML = '';
    S.palette.forEach((c, i) => {
      const d = document.createElement('div'); d.className = 'pal' + (i === colore ? ' sel' : '');
      d.innerHTML = `<b>${C.lettera(i)}</b><input type="color" value="${c.hex}"><input type="text" value="${c.cod}" placeholder="DB 000"><input type="text" value="${c.nome}" placeholder="nome"><button title="Rimuovi" ${usati[i] || S.palette.length < 2 ? 'disabled' : ''}>×</button>`;
      const [ , hx, cod, nome, rm] = d.children;
      d.addEventListener('click', () => { colore = i; disegnaPalette(); });
      hx.addEventListener('input', () => { c.hex = hx.value; ridisegna(); });
      cod.addEventListener('input', () => { c.cod = cod.value; materiali(); });
      nome.addEventListener('input', () => { c.nome = nome.value; materiali(); });
      rm.addEventListener('click', (e) => { e.stopPropagation(); rimuoviColore(i); });
      $('pal').appendChild(d);
    });
    opzioniColore();
  }
  function rimuoviColore(i) {
    S.palette.splice(i, 1);
    S.celle = S.celle.map((r) => r.map((v) => (v > i ? v - 1 : v)));
    colore = Math.min(colore, S.palette.length - 1); disegnaPalette(); ridisegna();
  }
  $('addcol').onclick = () => {
    if (S.palette.length >= 26) return;
    S.palette.push({ cod: '', nome: 'Colore ' + (S.palette.length + 1), hex: '#' + Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, '0') });
    colore = S.palette.length - 1; disegnaPalette();
  };

  // ---- materiali ----
  const num = (n, d) => n.toFixed(d).replace('.', ',');
  function materiali() {
    const cnt = C.conteggi(S.celle, S.palette.length), tot = cnt.reduce((a, b) => a + b, 0);
    const m = C.stimaMisure(S.w, S.h, S.opz, S.tecnica);
    $('mat').innerHTML = `<table><tr><th></th><th>Colore</th><th>Perline</th><th>rp.</th></tr>` +
      S.palette.map((c, i) => `<tr><td><span class="sw" style="background:${c.hex}"></span> ${C.lettera(i)}</td><td>${esc(c.cod)} ${esc(c.nome)}</td><td>${cnt[i]}</td><td>${num(C.rp(cnt[i], S.opz.perRp), 1)}</td></tr>`).join('') +
      `<tr><th></th><th>Totale</th><th>${tot}</th><th></th></tr></table>` +
      `<small>${testoMisure(m)}</small>`;
  }
  function testoMisure(m) {
    const mis = `Misure circa ${num(m.largMm / 10, 1)} × ${num(m.lungMm / 10, 1)} cm`;
    const filo = S.tecnica === 'telaio'
      ? `${m.fiiOrdito} fili di ordito da ≈ ${m.ordCm} cm · filo di trama ≈ ${num(m.tramaCm / 100, 1)} m`
      : `filo ≈ ${m.filoCm} cm`;
    return `${mis} · ${filo} (stime). 1 rp. = ${S.opz.perRp} perline.`;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ---- controlli ----
  function sync() {
    $('nome').value = S.nome; $('tecnica').value = S.tecnica; $('w').value = S.w; $('h').value = S.h;
    disegnaPalette(); ridisegna();
  }
  $('tecnica').onchange = () => { S.tecnica = $('tecnica').value; ridisegna(); };
  $('lettere').onchange = ridisegna;
  $('zoom').oninput = () => { zoom = +$('zoom').value; ridisegna(); };
  $('ridim').onclick = () => {
    const w = Math.max(1, Math.min(60, +$('w').value || 1)), h = Math.max(1, Math.min(800, +$('h').value || 1));
    const cnt = C.conteggi(S.celle, S.palette.length);
    snapshot(); S.celle = C.ridimensiona(S.celle, w, h, cnt.indexOf(Math.max(...cnt)));
    S.w = w; S.h = h; sync();
  };
  $('t-penna').onclick = () => { strumento = 'penna'; $('t-penna').className = 'on'; $('t-riempi').className = ''; };
  $('t-riempi').onclick = () => { strumento = 'riempi'; $('t-riempi').className = 'on'; $('t-penna').className = ''; };
  $('undo').onclick = annulla; $('redo').onclick = ripeti;
  $('genera').onclick = () => {
    const rings = ['g-c0', 'g-c1', 'g-c2', 'g-c3'].map((id) => +$(id).value);
    snapshot(); S.celle = C.genera($('g-tipo').value, S.w, S.h, Math.max(2, +$('g-p').value || 2), rings); ridisegna(); disegnaPalette();
  };
  $('ripeti').onclick = () => {
    const p = Math.max(1, Math.min(S.h, +$('g-p').value || 1));
    snapshot(); S.celle = C.ripetiModulo(S.celle, p, S.h); ridisegna(); disegnaPalette();
  };

  // ---- progetti ----
  async function api(metodo, url, corpo) {
    const r = await fetch(url, { method: metodo, body: corpo ? JSON.stringify(corpo) : undefined, headers: corpo ? { 'Content-Type': 'application/json' } : undefined });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).errore || r.status);
    return r.json();
  }
  async function elenco() {
    const nomi = await api('GET', '/api/progetti');
    $('elenco').innerHTML = '<option value="">Apri progetto…</option>' + nomi.map((n) => `<option>${esc(n)}</option>`).join('');
  }
  $('salva').onclick = async () => {
    S.nome = $('nome').value.trim();
    if (!S.nome) return msg('Dai un nome al progetto');
    try { await api('PUT', '/api/progetti/' + encodeURIComponent(S.nome), S); msg('Salvato'); elenco(); } catch (e) { msg('Errore: ' + e.message); }
  };
  $('elenco').onchange = async () => {
    const n = $('elenco').value; if (!n) return;
    try { S = await api('GET', '/api/progetti/' + encodeURIComponent(n)); S.opz = { ...OPZ, ...(S.opz || {}) }; undoSt = []; redoSt = []; colore = 0; sync(); msg('Aperto: ' + n); } catch (e) { msg('Errore: ' + e.message); }
  };
  $('nuovo').onclick = () => { S = nuovoProgetto(); undoSt = []; redoSt = []; colore = 0; sync(); };
  $('elimina').onclick = async () => {
    const n = $('nome').value.trim(); if (!n || !confirm('Eliminare "' + n + '"?')) return;
    try { await api('DELETE', '/api/progetti/' + encodeURIComponent(n)); msg('Eliminato'); elenco(); } catch (e) { msg('Errore: ' + e.message); }
  };
  $('esci').onclick = async () => { if (confirm('Chiudere Perline?')) { try { await api('POST', '/api/esci'); } catch (e) {} document.body.innerHTML = '<p style="padding:20px">Perline chiuso. Puoi chiudere questa scheda.</p>'; } };

  // ---- stampa ----
  $('stampa').onclick = () => {
    const off = document.createElement('canvas'), c2 = off.getContext('2d');
    disegna(c2, 26, { lettere: true });
    const cnt = C.conteggi(S.celle, S.palette.length), m = C.stimaMisure(S.w, S.h, S.opz, S.tecnica);
    const righe = S.celle.map((r, i) => `Riga ${i + 1}: ${C.lettura(r)}`).join('<br>');
    $('sheet').innerHTML = `<h1>${esc(S.nome || 'Progetto')} — ${S.tecnica === 'peyote' ? 'Peyote' : 'Telaio'} ${S.w} × ${S.h}</h1>
      <img src="${off.toDataURL('image/png')}"><table style="margin-top:8px"><tr><th>Lettera</th><th>Colore</th><th>Perline</th><th>rp.</th></tr>` +
      S.palette.map((c, i) => `<tr><td><span class="sw" style="background:${c.hex}"></span> ${C.lettera(i)}</td><td>${esc(c.cod)} ${esc(c.nome)}</td><td>${cnt[i]}</td><td>${num(C.rp(cnt[i], S.opz.perRp), 1)}</td></tr>`).join('') +
      `</table><p>${testoMisure(m)}</p><h1 style="page-break-before:always">Lettura per riga (sinistra → destra, dall'alto)</h1><div class="rig">${righe}</div>`;
    window.print();
  };

  fetch('/api/versione').then((r) => r.json()).then((v) => ($('ver').textContent = 'v' + v.versione)).catch(() => {});
  elenco().catch(() => {});
  sync();
})();
