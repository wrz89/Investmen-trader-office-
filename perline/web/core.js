// Logica pura (senza DOM): griglia, generatore motivi, conteggi, lettura per riga.
(function (root) {
  const DEFAULT_PALETTE = [
    { cod: 'DB 201', nome: 'Bianco', hex: '#f4f4f2' },
    { cod: 'DB 051', nome: 'Grigio chiaro', hex: '#b9b9b9' },
    { cod: 'DB 1812', nome: 'Grigio scuro', hex: '#6a6a6c' },
    { cod: '', nome: 'Colore 4', hex: '#2b2b2b' },
  ];

  const lettera = (i) => String.fromCharCode(65 + i); // A, B, C...

  function nuovaGriglia(w, h, fill) {
    return Array.from({ length: h }, () => Array(w).fill(fill || 0));
  }

  function ridimensiona(celle, w, h, fill) {
    return Array.from({ length: h }, (_, r) =>
      Array.from({ length: w }, (_, c) => (celle[r] && celle[r][c] !== undefined ? celle[r][c] : fill || 0)));
  }

  // rings: indici palette dal centro verso lo sfondo
  function genera(tipo, w, h, p, rings) {
    const n = rings.length;
    const g = nuovaGriglia(w, h, rings[n - 1]);
    const cx = (w - 1) / 2;
    for (let r = 0; r < h; r++) {
      const ry = r % p;
      for (let c = 0; c < w; c++) {
        const dc = Math.abs(c - cx);
        let k;
        if (tipo === 'rombi') {
          const dr = Math.abs(ry - (p - 1) / 2);
          const t = dc / (w / 2) + dr / (p / 2);
          k = Math.min(n - 1, Math.floor(t / 0.28));
        } else if (tipo === 'chevron') {
          const slope = Math.max(1, p / Math.max(1, w / 2));
          const pos = (((ry + Math.round(dc * slope)) % p) + p) % p;
          k = Math.min(n - 1, Math.floor((pos / p) * n));
        } else { // righe
          k = Math.min(n - 1, Math.floor((ry / p) * n));
        }
        g[r][c] = rings[k];
      }
    }
    return g;
  }

  // ripete le prime p righe fino a h righe
  function ripetiModulo(celle, p, h) {
    const w = celle[0].length;
    return Array.from({ length: h }, (_, r) => celle[r % p].slice(0, w));
  }

  function conteggi(celle, nColori) {
    const out = Array(nColori).fill(0);
    celle.forEach((riga) => riga.forEach((v) => { if (v < nColori) out[v]++; }));
    return out;
  }

  // "rp." come negli schemi di riferimento: perline / perline-per-rp (es. 220 -> 1,1)
  function rp(perline, perRp) { return perRp > 0 ? perline / perRp : 0; }

  // Telaio: ordito = w+1 fili lunghi (lavoro + sprechi), trama = 1 filo che attraversa ogni riga andata e ritorno.
  // Peyote: un solo filo, stima = lunghezza x fattore. Tutte stime, parametri in opz.
  function stimaMisure(w, h, opz, tecnica) {
    const largMm = w * opz.larghezzaMm, lungMm = h * opz.altezzaMm;
    const out = { largMm, lungMm };
    if (tecnica === 'telaio') {
      out.fiiOrdito = w + 1;
      out.ordCm = Math.round(lungMm / 10 + opz.spreccoOrditoCm);
      out.tramaCm = Math.round((h * (largMm * 2 + opz.extraTramaMm)) / 10 + opz.margineCm);
      out.filoCm = out.tramaCm;
    } else {
      out.filoCm = Math.round((lungMm * opz.fattoreFilo) / 10 + opz.margineCm);
    }
    return out;
  }

  // "A3 B A2" per ogni riga (run-length)
  function lettura(riga) {
    const parti = [];
    let i = 0;
    while (i < riga.length) {
      let j = i;
      while (j < riga.length && riga[j] === riga[i]) j++;
      parti.push(lettera(riga[i]) + (j - i > 1 ? j - i : ''));
      i = j;
    }
    return parti.join(' ');
  }

  function riempi(celle, r, c, nuovo) {
    const vecchio = celle[r][c];
    if (vecchio === nuovo) return;
    const h = celle.length, w = celle[0].length, pila = [[r, c]];
    while (pila.length) {
      const [y, x] = pila.pop();
      if (y < 0 || y >= h || x < 0 || x >= w || celle[y][x] !== vecchio) continue;
      celle[y][x] = nuovo;
      pila.push([y + 1, x], [y - 1, x], [y, x + 1], [y, x - 1]);
    }
  }

  const api = { DEFAULT_PALETTE, lettera, nuovaGriglia, ridimensiona, genera, ripetiModulo, conteggi, rp, stimaMisure, lettura, riempi };
  if (typeof module !== 'undefined') module.exports = api; else root.Core = api;
})(typeof window !== 'undefined' ? window : globalThis);
