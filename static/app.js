'use strict';

/* ---------- utilidades ---------- */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const el = html => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const now = () => Date.now() / 1000;
const gb = b => (b / 1e9).toFixed(b >= 10e9 ? 1 : 2) + ' GB';
const fix = (x, d = 1) => (x == null || isNaN(x) ? '–' : Number(x).toFixed(d));
const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
function ago(t) {
  const s = Math.max(0, Math.round(now() - t));
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} d`;
}
const clock = t => {
  const d = new Date(t * 1000), hms = d.toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? hms : `${d.toLocaleDateString('es', { day: '2-digit', month: '2-digit' })} ${hms}`;
};
const dur = s => s < 60 ? `${fix(s, 1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;

const S = { state: null, config: null, stats: null, hist: [], dirtyText: false, dirtyImg: false, tab: 'text', logSvc: 'text' };
const HIST_WINDOW = 600; // maximo de segundos que muestran los sparklines
// La ventana se adapta a la historia disponible (minimo 1 minuto) para que el grafico ocupe todo el ancho
const sparkWindow = () => S.hist.length ? Math.min(HIST_WINDOW, Math.max(60, now() - S.hist[0].t)) : HIST_WINDOW;

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function post(url, body = {}) {
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return await r.json();
  } catch {
    return { ok: false, msg: 'No se pudo contactar al panel.' };
  }
}
async function withBusy(btn, fn) {
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  try { return await fn(); } finally { if (btn) { btn.classList.remove('busy'); btn.disabled = false; } }
}
function toast(msg, kind = 'info', ms = 6000) {
  if (!msg) return;
  const t = el(`<div class="toast ${kind}"></div>`);
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}
function report(res) { toast(res.msg, res.ok ? 'ok' : 'error', res.ok ? 5000 : 10000); }

/* ---------- tooltip ---------- */
const tip = {
  show(html, x, y) {
    const t = $('#tooltip');
    t.innerHTML = html; t.hidden = false;
    const r = t.getBoundingClientRect();
    let left = x + 14, top = y - r.height - 10;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top < 8) top = y + 16;
    t.style.left = left + 'px'; t.style.top = top + 'px';
  },
  hide() { $('#tooltip').hidden = true; },
};

/* ---------- estados ---------- */
const SVC = {
  active: ['good', 'Encendido'], activating: ['warning', 'Arrancando'], reloading: ['warning', 'Recargando'],
  deactivating: ['warning', 'Apagando'], failed: ['critical', 'Error'], inactive: ['', 'Apagado'],
};
function setStatus(node, [cls, label]) {
  node.className = node.className.replace(/\b(good|warning|critical)\b/g, '').trim() + (cls ? ' ' + cls : '');
  $('b', node).textContent = label;
}
function modelStatus(ms, textOn) {
  if (!textOn || !ms) return ['', 'Sin cargar'];
  switch (ms.status) {
    case 'loaded': return ['good', 'Cargado'];
    case 'loading': return ['warning', 'Cargando'];
    case 'sleeping': return ['', 'En reposo'];
    case 'downloading': return ['warning', 'Descargando'];
    default: return ms.failed ? ['critical', 'Falló'] : ['', 'Sin cargar'];
  }
}

/* ---------- tiles + sparklines ---------- */
const TILES = {
  vram: {
    get: h => h.vram_used, max: h => h.vram_total, unit: 'GiB', d: 1,
    render(h) {
      const pct = h.vram_used / h.vram_total * 100;
      return { value: `${fix(h.vram_used)} <small>/ ${fix(h.vram_total, 0)} GiB</small>`, sub: h.gtt_used != null ? `GTT ${fix(h.gtt_used)} GiB` : '', pct, level: pct >= 95 ? 'critical' : pct >= 85 ? 'warning' : '' };
    },
  },
  gpu: {
    get: h => h.gpu, max: () => 100, unit: '%', d: 0,
    render(h) { return { value: `${h.gpu ?? '–'}<small>%</small>`, sub: h.sclk ? `${h.sclk} MHz` : '', pct: h.gpu ?? 0, level: '' }; },
  },
  temp: {
    get: h => h.temp, auto: true, unit: '°C', d: 0,
    render(h) {
      const sub = [h.power != null ? `${fix(h.power, 0)} W` : '', h.fan != null ? `${h.fan} rpm` : ''].filter(Boolean).join(' · ');
      const crit = h.temp_crit || 100;
      return { value: `${fix(h.temp, 0)}<small> °C</small>`, sub, pct: (h.temp ?? 0) / crit * 100, level: h.temp >= 90 ? 'critical' : h.temp >= 80 ? 'warning' : '' };
    },
  },
  ram: {
    get: h => h.ram_used, max: h => h.ram_total, unit: 'GiB', d: 1,
    render(h) {
      const pct = h.ram_used / h.ram_total * 100;
      return { value: `${fix(h.ram_used)} <small>/ ${fix(h.ram_total, 0)} GiB</small>`, sub: `carga ${fix(h.load, 2)}`, pct, level: pct >= 95 ? 'critical' : pct >= 85 ? 'warning' : '' };
    },
  },
};

function renderTiles() {
  const h = S.state?.hw;
  if (!h) return;
  for (const [key, def] of Object.entries(TILES)) {
    const tile = $(`#tile-${key}`);
    const r = def.render(h);
    $('[data-value]', tile).innerHTML = r.value;
    $('[data-sub]', tile).textContent = r.sub;
    const m = $('[data-meter]', tile);
    m.style.width = Math.max(0, Math.min(100, r.pct || 0)) + '%';
    m.className = r.level;
    drawSpark(tile, def);
  }
}

function sparkRange(def) {
  const vals = S.hist.map(def.get).filter(v => v != null);
  if (def.auto) {
    const lo = Math.min(...vals), hi = Math.max(...vals);
    return [Math.floor(lo - 3), Math.ceil(hi + 3)];
  }
  const last = S.hist[S.hist.length - 1];
  return [0, def.max(last) || Math.max(...vals) || 1];
}

function drawSpark(tile, def) {
  const box = $('[data-spark]', tile);
  const pts = S.hist.filter(h => def.get(h) != null);
  if (pts.length < 2) { box.innerHTML = ''; return; }
  const [lo, hi] = sparkRange(def);
  const t1 = now(), win = sparkWindow();
  const X = t => 100 - (t1 - t) / win * 100;
  const Y = v => 39 - (v - lo) / (hi - lo || 1) * 37;
  const line = pts.map((h, i) => `${i ? 'L' : 'M'}${X(h.t).toFixed(2)},${Y(def.get(h)).toFixed(2)}`).join('');
  const area = `${line}L${X(pts[pts.length - 1].t).toFixed(2)},40L${X(pts[0].t).toFixed(2)},40Z`;
  let svg = $('svg', box);
  if (!svg) {
    box.innerHTML = `<svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><path class="area"/><path class="line"/><line class="cross" y1="0" y2="40" visibility="hidden"/></svg><span class="dot" hidden></span>`;
    svg = $('svg', box);
    box.addEventListener('pointermove', e => sparkHover(box, def, e));
    box.addEventListener('pointerleave', () => { tip.hide(); $('.cross', box).setAttribute('visibility', 'hidden'); $('.dot', box).hidden = true; });
  }
  $('.line', svg).setAttribute('d', line);
  $('.area', svg).setAttribute('d', area);
}

function sparkHover(box, def, e) {
  const rect = box.getBoundingClientRect();
  const fx = (e.clientX - rect.left) / rect.width;
  const win = sparkWindow();
  const t = now() - (1 - fx) * win;
  const pts = S.hist.filter(h => def.get(h) != null);
  if (!pts.length) return;
  let best = pts[0];
  for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  const [lo, hi] = sparkRange(def);
  const x = 100 - (now() - best.t) / win * 100;
  const y = 39 - (def.get(best) - lo) / (hi - lo || 1) * 37;
  const cross = $('.cross', box);
  cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
  const dot = $('.dot', box);
  dot.hidden = false; dot.style.left = (x / 100 * rect.width) + 'px'; dot.style.top = (y / 40 * rect.height) + 'px';
  tip.show(`<b>${fix(def.get(best), def.d)} ${def.unit}</b> <span class="m">${ago(best.t)}</span>`, e.clientX, e.clientY);
}

/* ---------- motores ---------- */
function renderEngines() {
  const st = S.state;
  if (!st) return;
  const textOn = st.svc.text === 'active';
  const imgOn = st.svc.img === 'active';

  const et = $('#eng-text');
  et.classList.toggle('on', textOn);
  setStatus($('[data-status]', et), SVC[st.svc.text] || ['', st.svc.text]);
  const loaded = Object.entries(st.text.models || {}).find(([, m]) => m.status === 'loaded' || m.status === 'loading');
  const ld = $('[data-loaded]', et);
  ld.textContent = !textOn ? '–' : loaded ? loaded[0] + (loaded[1].status === 'loading' ? ' (cargando…)' : '') : 'ninguno (se carga al pedirlo)';
  ld.title = loaded ? loaded[0] : '';
  const last = S.stats?.text?.[S.stats.text.length - 1];
  $('[data-speed]', et).textContent = last ? `${fix(last.g_tps)} tok/s · ${ago(last.t)}` : '–';
  $('[data-action="start"]', et).disabled = textOn;
  $('[data-action="stop"]', et).disabled = st.svc.text === 'inactive';
  $('#lnk-chat').setAttribute('aria-disabled', String(!textOn));

  const ei = $('#eng-img');
  ei.classList.toggle('on', imgOn);
  setStatus($('[data-status]', ei), SVC[st.svc.img] || ['', st.svc.img]);
  const md = $('[data-model]', ei);
  md.textContent = st.img.selected || 'ninguno'; md.title = st.img.selected || '';
  const pr = st.img.progress, perf = st.img.perf;
  const bar = $('[data-progress]', ei);
  let act = '–';
  if (imgOn && pr && pr.steps > 0) {
    act = `Generando · paso ${pr.step}/${pr.steps}` + (pr.eta > 0 ? ` · faltan ${dur(pr.eta)}` : '');
    bar.hidden = false; $('span', bar).style.width = (pr.step / pr.steps * 100) + '%';
  } else {
    bar.hidden = true;
    if (imgOn && perf) act = perf.queue > 0 ? `En cola: ${perf.queue}` : `Libre · ${perf.total_img_gens} imágenes generadas`;
    else if (imgOn) act = 'Cargando el modelo…';
  }
  $('[data-activity]', ei).textContent = act;
  $('[data-action="img_start"]', ei).disabled = imgOn;
  $('[data-action="img_stop"]', ei).disabled = st.svc.img === 'inactive';
  $('#lnk-sdui').setAttribute('aria-disabled', String(!imgOn));
}

/* ---------- modelos de texto ---------- */
function textBadges(m, cfg) {
  const vramBytes = (S.config.vram_total || 0) * 2 ** 30;
  const rs = Object.fromEntries(S.config.reasoning_opts);
  const b = [`<span class="badge">${gb(m.size)}</span>`];
  if (cfg.mtp) b.push(`<span class="badge accent">MTP ×${esc(cfg.nmax || 2)}</span>`);
  if (cfg.reasoning) b.push(`<span class="badge">razonamiento: ${esc(rs[cfg.reasoning])}</span>`);
  b.push(`<span class="badge">${cfg.ctx ? 'ctx ' + esc(cfg.ctx) : 'ctx global'}</span>`);
  const nExtra = cfg.extra.split('\n').filter(l => l.trim() && !l.trim().startsWith('#')).length;
  if (nExtra) b.push(`<span class="badge">+${nExtra} opción${nExtra > 1 ? 'es' : ''}</span>`);
  if (vramBytes && m.size > vramBytes * 0.95) b.push(`<span class="badge warn" title="No entra completo en la VRAM: parte va a la RAM y será más lento">más grande que la VRAM</span>`);
  return b.join('');
}

function readTextCfg(row) {
  return {
    mtp: $('[data-f=mtp]', row).checked,
    nmax: $('[data-f=nmax]', row).value.trim() || '2',
    reasoning: $('[data-f=reasoning]', row).value,
    ctx: $('[data-f=ctx]', row).value.trim(),
    extra: $('[data-f=extra]', row).value,
  };
}

function renderTextModels() {
  const c = S.config;
  $('#text-dir').textContent = c.models_dir;
  $('#global').value = c.global;
  const box = $('#text-models');
  box.innerHTML = '';
  if (!c.text_models.length) {
    box.append(el(`<div class="card empty">No hay archivos .gguf en ${esc(c.models_dir)}.</div>`));
    return;
  }
  const opts = cfg => c.reasoning_opts.map(([v, t]) => `<option value="${esc(v)}" ${v === cfg.reasoning ? 'selected' : ''}>${esc(t)}</option>`).join('');
  for (const m of c.text_models) {
    const cfg = m.cfg;
    const row = el(`
      <div class="model" data-id="${esc(m.id)}" data-key="${esc(m.key)}">
        <div class="model-row">
          <div class="model-main">
            <div class="model-name" title="${esc(m.key)}">${esc(m.id)}</div>
            <div class="model-meta" data-badges>${textBadges(m, cfg)}</div>
          </div>
          <span class="status model-state" data-mstate><i></i><b>Sin cargar</b></span>
          <div class="model-actions">
            <button class="btn small" data-load>Cargar</button>
            <button class="btn ghost small" data-toggle aria-expanded="false">Ajustes</button>
          </div>
        </div>
        <div class="model-edit" hidden>
          <label class="field"><span>MTP (borradores por paso)</span>
            <span class="row"><input type="checkbox" data-f="mtp" ${cfg.mtp ? 'checked' : ''} class="check">
            <input type="number" min="1" max="16" data-f="nmax" value="${esc(cfg.nmax || 2)}"></span></label>
          <label class="field"><span>Razonamiento</span><select data-f="reasoning">${opts(cfg)}</select></label>
          <label class="field"><span>Contexto</span><input type="number" min="256" step="256" data-f="ctx" value="${esc(cfg.ctx)}" placeholder="global"></label>
          <span></span>
          <label class="field wide"><span>Opciones extra (clave = valor, una por línea)</span>
            <textarea rows="2" data-f="extra" spellcheck="false">${esc(cfg.extra)}</textarea></label>
        </div>
      </div>`);
    $('[data-toggle]', row).onclick = e => {
      const ed = $('.model-edit', row);
      ed.hidden = !ed.hidden;
      e.currentTarget.setAttribute('aria-expanded', String(!ed.hidden));
    };
    $('.model-edit', row).addEventListener('input', () => {
      $('[data-badges]', row).innerHTML = textBadges(m, readTextCfg(row));
      setDirty('text');
    });
    $('[data-load]', row).onclick = e => loadModel(m.id, e.currentTarget);
    box.append(row);
  }
  updateTextStates();
}

function updateTextStates() {
  if (!S.state || !S.config) return;
  const textOn = S.state.svc.text === 'active';
  for (const row of $$('#text-models .model')) {
    const ms = S.state.text.models?.[row.dataset.id];
    const info = modelStatus(ms, textOn);
    setStatus($('[data-mstate]', row), info);
    row.classList.toggle('loaded', info[1] === 'Cargado');
    const btn = $('[data-load]', row);
    if (btn.classList.contains('busy')) continue;
    const st = textOn && ms ? ms.status : 'unloaded';
    btn.textContent = st === 'loaded' || st === 'sleeping' ? 'Descargar' : st === 'loading' ? 'Cargando…' : 'Cargar';
    btn.classList.toggle('ghost', st === 'loaded' || st === 'sleeping');
    btn.disabled = st === 'loading';
    btn.dataset.mode = st === 'loaded' || st === 'sleeping' ? 'unload' : 'load';
  }
}

async function loadModel(id, btn) {
  const unload = btn.dataset.mode === 'unload';
  const res = await withBusy(btn, () => post(unload ? '/api/text/unload' : '/api/text/load', { id }));
  report(res);
  await pollState();
}

/* ---------- modelos de imagen ---------- */
function renderImgModels() {
  const c = S.config;
  $('#img-dir').textContent = c.img_dir;
  const box = $('#img-models');
  box.innerHTML = '';
  if (!c.img_models.length) {
    box.append(el(`<div class="card empty">No hay modelos (.gguf / .safetensors de más de 100 MB) en ${esc(c.img_dir)}.</div>`));
    return;
  }
  for (const m of c.img_models) {
    const comps = m.comps.length
      ? m.comps.map(([f, n]) => `<span title="${esc(n)}"><b>${esc(f)}</b> ${esc(n)}</span>`).join('')
      : '<span class="muted">solo el modelo</span>';
    const card = el(`
      <div class="card img-card" data-key="${esc(m.key)}">
        <div class="model-name" title="${esc(m.key)}">${esc(m.id)}</div>
        <span class="badge accent sel-tag" hidden>Seleccionado</span>
        <div class="model-meta"><span class="badge">${gb(m.size)}</span></div>
        <div class="comps">${comps}</div>
        <label class="field"><span>Opciones extra</span>
          <input type="text" class="mono" data-extra value="${esc(m.extra)}" placeholder="ej: --sdoffloadcpu --sdtiledvae 768" spellcheck="false"></label>
        <div class="foot"><button class="btn small" data-use>Usar este</button></div>
      </div>`);
    $('[data-extra]', card).addEventListener('input', () => setDirty('img'));
    $('[data-use]', card).onclick = e => useImg(m.key, e.currentTarget);
    box.append(card);
  }
  updateImgStates();
}

function updateImgStates() {
  if (!S.config) return;
  const imgOn = S.state?.svc.img === 'active';
  for (const card of $$('#img-models .img-card')) {
    const sel = card.dataset.key === S.config.img_selected;
    card.classList.toggle('selected', sel);
    $('.sel-tag', card).hidden = !sel;
    const btn = $('[data-use]', card);
    if (btn.classList.contains('busy')) continue;
    btn.textContent = sel && imgOn ? 'Reiniciar con este' : imgOn ? 'Cambiar a este' : 'Usar y encender';
  }
}

function readImgCfg() {
  return Object.fromEntries($$('#img-models .img-card').map(c => [c.dataset.key, { extra: $('[data-extra]', c).value.trim() }]));
}

async function useImg(key, btn) {
  const res = await withBusy(btn, () => post('/api/img/use', { key, models: readImgCfg() }));
  report(res);
  if (res.ok) { S.dirtyImg = false; await loadConfig(); }
  await pollState();
}

/* ---------- guardar ---------- */
function setDirty(which) {
  if (which === 'text') S.dirtyText = true; else S.dirtyImg = true;
  $('#savebar').hidden = false;
}
function clearDirty() { S.dirtyText = S.dirtyImg = false; $('#savebar').hidden = true; }

async function save(apply, btn) {
  await withBusy(btn, async () => {
    let ok = true;
    if (S.dirtyText) {
      const models = Object.fromEntries($$('#text-models .model').map(r => [r.dataset.key, readTextCfg(r)]));
      const res = await post('/api/text/settings', { global: $('#global').value, models, apply });
      report(res); ok = ok && res.ok;
    }
    if (S.dirtyImg) {
      const res = await post('/api/img/settings', { selected: S.config.img_selected, models: readImgCfg(), apply });
      report(res); ok = ok && res.ok;
    }
    if (ok) { clearDirty(); await loadConfig(); }
  });
  pollState();
}

/* ---------- rendimiento ---------- */
function renderPerf() {
  const st = S.stats;
  if (!st) return;
  const reqs = st.text.slice(-40);
  const recent = reqs.slice(-20);
  const drafts = recent.filter(r => r.draft);
  const acc = drafts.length ? drafts.reduce((a, r) => a + r.draft.acc, 0) / Math.max(1, drafts.reduce((a, r) => a + r.draft.gen, 0)) : null;
  const stat = (k, v, u) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}${u ? ` <small>${u}</small>` : ''}</div></div>`;
  $('#perf-summary').innerHTML =
    stat('Generación (mediana)', fix(median(recent.map(r => r.g_tps))), 'tok/s') +
    stat('Prompt (mediana)', fix(median(recent.map(r => r.p_tps)), 0), 'tok/s') +
    stat('Aceptación MTP', acc == null ? '–' : fix(acc * 100, 0), acc == null ? '' : '%') +
    stat('Respuestas registradas', st.text.length, '');
  drawBars(reqs);

  const t = $('#perf-table');
  if (!reqs.length) {
    t.innerHTML = '<tr><td class="empty">Todavía no hay respuestas registradas. Generá algo en el chat.</td></tr>';
  } else {
    t.innerHTML = '<tr><th>Hora</th><th>Modelo</th><th class="r">Tokens prompt</th><th class="r">Prompt tok/s</th><th class="r">Tokens generados</th><th class="r">Generación tok/s</th><th class="r">Aceptación MTP</th></tr>' +
      [...reqs].reverse().map(r => `<tr><td>${clock(r.t)}</td><td class="name" title="${esc(r.model)}">${esc(r.model)}</td>
        <td class="r">${r.p_tok}</td><td class="r">${fix(r.p_tps, 0)}</td><td class="r">${r.g_tok ?? '–'}</td><td class="r">${fix(r.g_tps)}</td>
        <td class="r">${r.draft ? fix(r.draft.rate * 100, 0) + '%' : '–'}</td></tr>`).join('');
  }
  const ti = $('#perf-img');
  if (!st.img.length) {
    ti.innerHTML = '<tr><td class="empty">Todavía no hay imágenes registradas.</td></tr>';
  } else {
    ti.innerHTML = '<tr><th>Hora</th><th class="r">Pasos</th><th class="r">Duración</th><th class="r">Segundos por paso</th></tr>' +
      [...st.img].reverse().slice(0, 20).map(g => `<tr><td>${clock(g.t)}</td><td class="r">${g.steps}</td><td class="r">${dur(g.secs)}</td><td class="r">${fix(g.secs / g.steps, 2)}</td></tr>`).join('');
  }
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
  if (!data.length) { box.innerHTML = '<div class="empty">Sin datos todavía.</div>'; return; }
  const W = Math.max(300, box.clientWidth), H = 160, padL = 34, padB = 6, padT = 6;
  const max = niceMax(Math.max(...data.map(r => r.g_tps)));
  const n = Math.max(data.length, 12);
  const slot = (W - padL) / n, gap = 2, bw = Math.max(2, Math.min(28, slot - gap));
  const y = v => H - padB - v / max * (H - padB - padT);
  const lastModel = data[data.length - 1].model;
  $('#perf-legend').textContent = data.some(r => r.model !== lastModel) ? `· atenuadas: otro modelo que ${lastModel}` : '';
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Tokens por segundo de generación en las últimas respuestas">`;
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
      tip.show(`<b>${fix(r.g_tps)} tok/s</b> generando · ${r.g_tok} tokens<br><span class="m">prompt</span> ${fix(r.p_tps, 0)} tok/s · ${r.p_tok} tokens<br><span class="m">${esc(r.model)} · ${clock(r.t)}</span>${r.draft ? `<br><span class="m">MTP</span> ${fix(r.draft.rate * 100, 0)}% aceptado` : ''}`, e.clientX, e.clientY);
    });
    h.addEventListener('pointerleave', tip.hide);
  });
}

/* ---------- registros ---------- */
async function loadLogs() {
  const pre = $('#logs');
  try {
    const { lines } = await getJSON(`/api/logs?svc=${S.logSvc}&n=500`);
    const f = $('#log-filter').value.trim().toLowerCase();
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    pre.innerHTML = lines.filter(l => !f || l.toLowerCase().includes(f)).map(l => {
      const cls = /\b(error|failed|fatal)\b| E /i.test(l) ? 'e' : /\b(warn(ing)?)\b| W /i.test(l) ? 'w' : '';
      return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l);
    }).join('\n') || '<span class="muted">(vacío)</span>';
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  } catch { /* reintenta en el próximo ciclo */ }
}

/* ---------- ciclos ---------- */
async function pollState() {
  try {
    S.state = await getJSON('/api/state');
    $('#conn').hidden = true;
    const h = S.state.hw;
    if (h && (!S.hist.length || h.t > S.hist[S.hist.length - 1].t)) S.hist.push(h);
    const cut = now() - HIST_WINDOW - 5;
    while (S.hist.length && S.hist[0].t < cut) S.hist.shift();
    if (S.config && S.state.img.selected_key !== undefined && S.state.img.selected_key !== S.config.img_selected && !S.dirtyImg) {
      S.config.img_selected = S.state.img.selected_key;
    }
    renderTiles(); renderEngines(); updateTextStates(); updateImgStates();
  } catch {
    $('#conn').hidden = false;
  }
}

async function loadConfig() {
  S.config = await getJSON('/api/config');
  renderTextModels(); renderImgModels();
}

async function loadStats() {
  try { S.stats = await getJSON('/api/stats'); } catch { return; }
  if (S.tab === 'perf') renderPerf();
  renderEngines();
}

function showTab(tab) {
  if (!$(`[data-panel="${tab}"]`)) tab = 'text';
  S.tab = tab;
  for (const b of $$('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  for (const p of $$('[data-panel]')) p.hidden = p.dataset.panel !== tab;
  history.replaceState(null, '', '#' + tab);
  if (tab === 'perf') renderPerf();
  if (tab === 'logs') loadLogs();
}

/* ---------- arranque ---------- */
function bind() {
  const host = location.hostname;
  $('#host').textContent = host;
  for (const b of $$('.tabs button')) b.onclick = () => showTab(b.dataset.tab);

  for (const b of $$('[data-action]')) {
    b.onclick = async () => {
      if (b.dataset.action === 'stop_all' && !b.dataset.armed) {
        b.dataset.armed = '1'; const txt = b.textContent; b.textContent = '¿Seguro? Tocá de nuevo';
        setTimeout(() => { delete b.dataset.armed; b.textContent = txt; }, 3000);
        return;
      }
      delete b.dataset.armed;
      const res = await withBusy(b, () => post('/api/action', { do: b.dataset.action }));
      if (b.dataset.action === 'stop_all') b.textContent = 'Apagar todo';
      report(res);
      pollState();
    };
  }

  $('#rescan').onclick = async e => {
    const res = await withBusy(e.currentTarget, () => post('/api/text/rescan'));
    report(res);
    if (!S.dirtyText) await loadConfig();
    pollState();
  };
  $('#global').addEventListener('input', () => setDirty('text'));
  $('#save').onclick = e => save(false, e.currentTarget);
  $('#save-apply').onclick = e => save(true, e.currentTarget);
  $('#discard').onclick = () => { clearDirty(); renderTextModels(); renderImgModels(); };
  addEventListener('beforeunload', e => { if (S.dirtyText || S.dirtyImg) e.preventDefault(); });

  for (const b of $$('#log-svc button')) {
    b.onclick = () => {
      S.logSvc = b.dataset.svc;
      for (const x of $$('#log-svc button')) x.setAttribute('aria-pressed', String(x === b));
      $('#logs').scrollTop = 1e9;
      loadLogs();
    };
  }
  $('#log-filter').addEventListener('input', loadLogs);
  addEventListener('resize', () => { if (S.tab === 'perf') renderPerf(); });
}

async function start() {
  bind();
  const host = location.hostname;
  try {
    const [cfg, hist] = await Promise.all([getJSON('/api/config'), getJSON('/api/history')]);
    S.config = cfg; S.hist = hist.samples || [];
    $('#lnk-chat').href = `http://${host}:${cfg.ports.chat}`;
    $('#lnk-sdui').href = `http://${host}:${cfg.ports.img}/sdui`;
    renderTextModels(); renderImgModels();
  } catch { $('#conn').hidden = false; }
  showTab(location.hash.slice(1) || 'text');
  await pollState();
  loadStats();

  setInterval(pollState, 2000);
  setInterval(loadStats, 8000);
  setInterval(() => { if (S.tab === 'logs' && $('#log-follow').checked) loadLogs(); }, 3000);
  // Si aparecen o desaparecen modelos en disco, refresca las listas (sin tocar un formulario a medio editar)
  setInterval(async () => {
    if (S.dirtyText || S.dirtyImg || document.visibilityState !== 'visible') return;
    try {
      const cfg = await getJSON('/api/config');
      const sig = c => JSON.stringify([c.text_models.map(m => m.key), c.img_models.map(m => m.key)]);
      if (sig(cfg) !== sig(S.config)) { S.config = cfg; renderTextModels(); renderImgModels(); }
    } catch { /* nada */ }
  }, 60000);
}

start();
