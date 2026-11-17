// Pestana de rendimiento: resumen, grafico de tokens/s por respuesta y tablas.
import { $, $$, clock, dur, esc, fix, median, tip } from './util.js';
import { S } from './state.js';
import { t, locale } from './i18n.js';

export function renderPerf() {
  const st = S.stats;
  if (!st) return;
  const reqs = st.text.slice(-40);
  const recent = reqs.slice(-20);
  const drafts = recent.filter(r => r.draft);
  const acc = drafts.length ? drafts.reduce((a, r) => a + r.draft.acc, 0) / Math.max(1, drafts.reduce((a, r) => a + r.draft.gen, 0)) : null;
  const stat = (k, v, u) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}${u ? ` <small>${u}</small>` : ''}</div></div>`;
  $('#perf-summary').innerHTML =
    stat(t('Generación (mediana)'), fix(median(recent.map(r => r.g_tps))), 'tok/s') +
    stat(t('Prompt largo (mediana)'), (() => { const l = reqs.filter(r => r.p_tok >= 512); return l.length ? fix(median(l.map(r => r.p_tps)), 0) : '–'; })(), 'tok/s') +
    stat(t('Aceptación MTP'), acc == null ? '–' : fix(acc * 100, 0), acc == null ? '' : '%') +
    stat(t('Respuestas registradas'), st.text.length, '');
  drawBars(reqs);
  renderModelTable(st.text);

  const tb = $('#perf-table');
  if (!reqs.length) {
    tb.innerHTML = `<tr><td class="empty">${t('Todavía no hay respuestas registradas. Generá algo en el chat.')}</td></tr>`;
  } else {
    tb.innerHTML = `<tr><th>${t('Hora')}</th><th>${t('Modelo')}</th><th class="r">${t('Tokens prompt')}</th><th class="r">${t('Prompt tok/s')}</th><th class="r">${t('Tokens generados')}</th><th class="r">${t('Generación tok/s')}</th><th class="r">${t('Aceptación MTP')}</th></tr>` +
      [...reqs].reverse().map(r => `<tr><td>${clock(r.t)}</td><td class="name" title="${esc(r.model)}">${esc(r.model)}</td>
        <td class="r">${r.p_tok}</td><td class="r">${fix(r.p_tps, 0)}</td><td class="r">${r.g_tok ?? '–'}</td><td class="r">${fix(r.g_tps)}</td>
        <td class="r">${r.draft ? fix(r.draft.rate * 100, 0) + '%' : '–'}</td></tr>`).join('');
  }
  const ti = $('#perf-img');
  if (!st.img.length) {
    ti.innerHTML = `<tr><td class="empty">${t('Todavía no hay imágenes registradas.')}</td></tr>`;
  } else {
    ti.innerHTML = `<tr><th>${t('Hora')}</th><th class="r">${t('Pasos')}</th><th class="r">${t('Duración')}</th><th class="r">${t('Segundos por paso')}</th></tr>` +
      [...st.img].reverse().slice(0, 20).map(g => `<tr><td>${clock(g.t)}</td><td class="r">${g.steps}</td><td class="r">${dur(g.secs)}</td><td class="r">${fix(g.secs / g.steps, 2)}</td></tr>`).join('');
  }
}

// resumen por modelo de todas las respuestas registradas (el log del router guarda las ultimas)
function renderModelTable(all) {
  const tb = $('#perf-models');
  const by = new Map();
  for (const r of all) {
    if (r.g_tps == null) continue;
    if (!by.has(r.model)) by.set(r.model, []);
    by.get(r.model).push(r);
  }
  if (!by.size) { tb.innerHTML = `<tr><td class="empty">${t('Sin datos todavía.')}</td></tr>`; return; }
  const rows = [...by.entries()].map(([model, rs]) => {
    const longP = rs.filter(r => r.p_tok >= 512);
    const dr = rs.filter(r => r.draft);
    const acc = dr.length ? dr.reduce((a, r) => a + r.draft.acc, 0) / Math.max(1, dr.reduce((a, r) => a + r.draft.gen, 0)) : null;
    return { model, n: rs.length, g: median(rs.map(r => r.g_tps)), best: Math.max(...rs.map(r => r.g_tps)),
      p: longP.length ? median(longP.map(r => r.p_tps)) : null, acc,
      toks: rs.reduce((a, r) => a + (r.g_tok || 0), 0), last: Math.max(...rs.map(r => r.t)) };
  }).sort((a, b) => b.last - a.last);
  tb.innerHTML = `<tr><th>${t('Modelo')}</th><th class="r">${t('Respuestas')}</th><th class="r">${t('Generación (mediana)')}</th><th class="r">${t('Mejor')}</th>` +
    `<th class="r" title="${esc(t('Mediana con prompts de 512 tokens o más: con prompts cortos la velocidad no dice nada'))}">${t('Prompt largo')}</th>` +
    `<th class="r">${t('Aciertos MTP')}</th><th class="r">${t('Tokens generados')}</th><th>${t('Última')}</th></tr>` +
    rows.map(r => `<tr><td class="name" title="${esc(r.model)}">${esc(r.model)}</td><td class="r">${r.n}</td>
      <td class="r"><b>${fix(r.g)}</b> tok/s</td><td class="r">${fix(r.best)}</td>
      <td class="r">${r.p != null ? fix(r.p, 0) + ' tok/s' : '–'}</td>
      <td class="r">${r.acc != null ? fix(r.acc * 100, 0) + ' %' : '–'}</td>
      <td class="r">${r.toks.toLocaleString(locale())}</td><td>${clock(r.last)}</td></tr>`).join('');
}

function niceMax(v) {
  if (!v) return 10;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function drawBars(reqs) {
  const box = $('#perf-bars');
  const data = reqs.filter(r => r.g_tps != null);
  if (!data.length) { box.innerHTML = `<div class="empty">${t('Sin datos todavía.')}</div>`; return; }
  const W = Math.max(300, box.clientWidth), H = 160, padL = 34, padB = 6, padT = 6;
  const max = niceMax(Math.max(...data.map(r => r.g_tps)));
  const n = Math.max(data.length, 12);
  const slot = (W - padL) / n, gap = 2, bw = Math.max(2, Math.min(28, slot - gap));
  const y = v => H - padB - v / max * (H - padB - padT);
  const lastModel = data[data.length - 1].model;
  $('#perf-legend').textContent = data.some(r => r.model !== lastModel) ? t('· atenuadas: otro modelo que {model}', { model: lastModel }) : '';
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t('Tokens por segundo de generación en las últimas respuestas'))}">`;
  for (const g of [0, max / 2, max]) {
    s += `<line class="grid" x1="${padL}" x2="${W}" y1="${y(g)}" y2="${y(g)}"/><text class="axis" x="${padL - 6}" y="${y(g) + 4}" text-anchor="end">${+g.toFixed(1)}</text>`;
  }
  data.forEach((r, i) => {
    const x = padL + i * slot + (slot - bw) / 2, top = y(r.g_tps), base = y(0), rad = Math.min(4, bw / 2, base - top);
    const d = `M${x},${base}V${top + rad}Q${x},${top} ${x + rad},${top}H${x + bw - rad}Q${x + bw},${top} ${x + bw},${top + rad}V${base}Z`;
    s += `<path class="bar${r.model !== lastModel ? ' dim' : ''}" d="${d}"/>`;
    s += `<rect class="hit" data-i="${i}" x="${padL + i * slot}" y="0" width="${slot}" height="${H}"/>`;
  });
  box.innerHTML = s + '</svg>';
  $$('.hit', box).forEach(h => {
    h.addEventListener('pointermove', e => {
      const r = data[+h.dataset.i];
      tip.show(`<b>${fix(r.g_tps)} tok/s</b> ${t('generando')} · ${r.g_tok} tokens<br><span class="m">prompt</span> ${fix(r.p_tps, 0)} tok/s · ${r.p_tok} tokens<br><span class="m">${esc(r.model)} · ${clock(r.t)}</span>${r.draft ? `<br><span class="m">MTP</span> ${t('{pct}% aceptado', { pct: fix(r.draft.rate * 100, 0) })}` : ''}`, e.clientX, e.clientY);
    });
    h.addEventListener('pointerleave', tip.hide);
  });
}
