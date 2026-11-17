// Pestana de modelos de texto: lista, estado en el router, carga/descarga y editor de ajustes.
import { $, $$, el, esc, fix, gb, median, modelStatus, post, report, setStatus, withBusy } from './util.js';
import { S, hooks, setDirty } from './state.js';
import { t, locale } from './i18n.js';

function textBadges(m, cfg) {
  const vramBytes = (S.config.vram_total || 0) * 2 ** 30;
  const rs = Object.fromEntries(S.config.reasoning_opts);
  const b = [`<span class="badge">${gb(m.size)}</span>`];
  if (cfg.mtp) b.push(`<span class="badge accent">MTP ×${esc(cfg.nmax || 2)}</span>`);
  if (cfg.reasoning) b.push(`<span class="badge">${esc(t('razonamiento: {value}', { value: rs[cfg.reasoning] }))}</span>`);
  b.push(`<span class="badge">${cfg.ctx ? 'ctx ' + esc(cfg.ctx) : t('ctx global')}</span>`);
  const nExtra = cfg.extra.split('\n').filter(l => l.trim() && !l.trim().startsWith('#')).length;
  if (nExtra) b.push(`<span class="badge">${nExtra > 1 ? t('+{n} opciones', { n: nExtra }) : t('+1 opción')}</span>`);
  const nAi = Object.keys(cfg.ai || {}).length + (cfg.extra_ai || '').split('\n').filter(l => l.trim() && !l.trim().startsWith('#')).length;
  if (nAi) b.push(`<span class="badge accent" title="${esc(t('Se aplican solo con el modo IA activo'))}">${t('+{n} en modo IA', { n: nAi })}</span>`);
  if (vramBytes && m.size > vramBytes * 0.95) b.push(`<span class="badge warn" title="${esc(t('No entra completo en la VRAM: parte va a la RAM y será más lento'))}">${t('más grande que la VRAM')}</span>`);
  return b.join('');
}

// "clave = valor" por linea; # comenta. Devuelve [{key, val}] sin comentarios ni lineas vacias.
function parseOpts(text) {
  const out = [];
  for (const raw of (text || '').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const i = line.indexOf('=');
    const key = (i < 0 ? line : line.slice(0, i)).trim().replace(/^--/, '');
    out.push({ key, val: i < 0 ? '' : line.slice(i + 1).trim() });
  }
  return out;
}

// Si una opcion extra cubre un campo (por ejemplo cpu-moe), el campo no vale: se desactiva y se dice por que
function markOverrides(row, extraEl = $('[data-f=extra]', row), extraAiEl = $('[data-f=extra_ai]', row), fields = S.config.fields) {
  const extra = parseOpts(extraEl.value), extraAi = parseOpts(extraAiEl ? extraAiEl.value : '');
  const find = (opts, keys) => [...opts].reverse().find(o => keys.includes(o.key));
  for (const f of fields) {
    const n = $(`[data-f="${f.id}"]`, row), a = $(`[data-ai="${f.id}"]`, row), hint = $(`[data-hint="${f.id}"]`, row);
    if (!n || !hint) continue;
    const o = find(extra, f.keys || []), oa = find(extraAi, f.keys || []);
    n.disabled = !!o;
    if (a) a.disabled = !!(oa || (o && !a.value));
    const shown = o ? t('Definido en opciones extra: {opt}', { opt: `${o.key} = ${o.val}` })
      : oa ? t('En modo IA lo define una opción extra: {opt}', { opt: `${oa.key} = ${oa.val}` }) : t(f.hint || '');
    hint.textContent = shown;
    hint.classList.toggle('over', !!(o || oa));
  }
}

// chips debajo de cada textarea: que entendio el panel de cada linea
function renderOpts(row) {
  markOverrides(row);
  const known = new Set(S.config.server_opts || []);
  const base = parseOpts($('[data-f=extra]', row).value);
  const baseKeys = new Set(base.map(o => o.key));
  for (const which of ['extra', 'extra_ai']) {
    const opts = which === 'extra' ? base : parseOpts($('[data-f=extra_ai]', row).value);
    const seen = new Set();
    $(`[data-opts=${which}]`, row).innerHTML = opts.map(o => {
      const bad = known.size && !known.has(o.key);
      const dup = seen.has(o.key); seen.add(o.key);
      const over = which === 'extra_ai' && baseKeys.has(o.key);
      const cls = bad ? 'bad' : dup ? 'dup' : over ? 'over' : '';
      const tip = bad ? t('llama-server no conoce esta opción (¿mal escrita?)') : dup ? t('repetida: vale la última')
        : over ? t('en modo IA reemplaza a la de arriba') : t('opción válida');
      const v = o.val.length > 48 ? o.val.slice(0, 45) + '…' : o.val;
      return `<span class="opt ${cls}" title="${esc(tip)}: ${esc(o.key)} = ${esc(o.val)}"><b>${esc(o.key)}</b>${o.val ? ' = ' + esc(v) : ''}${bad ? ' ✗' : over ? ' · ' + t('pisa') : ''}</span>`;
    }).join('');
  }
}

function autosize(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight + 4, window.innerHeight * 0.7) + 'px';
}

// rendimiento real del modelo: mediana de sus ultimas respuestas (del log del router)
function perfBadge(id) {
  const resp = (S.stats?.text || []).filter(r => r.model === id).slice(-20);
  if (!resp.length) return '';
  const g = median(resp.map(r => r.g_tps));
  // la velocidad del prompt depende de su largo: solo vale con prompts largos
  const longP = resp.filter(r => r.p_tok >= 512);
  const p = longP.length ? median(longP.map(r => r.p_tps)) : null;
  const dr = resp.filter(r => r.draft);
  const acc = dr.length ? dr.reduce((a, r) => a + r.draft.acc, 0) / Math.max(1, dr.reduce((a, r) => a + r.draft.gen, 0)) : null;
  const title = t('Mediana de las últimas {n} respuestas: generación {g} tok/s', { n: resp.length, g: fix(g) }) +
    (p != null ? t(', prompt {p} tok/s (prompts de 512 tokens o más)', { p: fix(p, 0) }) : '') +
    (acc != null ? t(', MTP acierta {acc} %', { acc: fix(acc * 100, 0) }) : '');
  return `<span class="badge good" title="${esc(title)}">${fix(g)} tok/s${p != null ? ` · prompt ${fix(p, 0)}` : ''}${acc != null ? ` · MTP ${fix(acc * 100, 0)} %` : ''}</span>`;
}

export function updateTextPerf() {
  for (const row of $$('#text-models .model')) {
    const slot = $('[data-perf]', row);
    if (slot) slot.innerHTML = perfBadge(row.dataset.id);
  }
}

export function readTextCfg(row) {
  const cfg = { extra: $('[data-f=extra]', row).value, extra_ai: $('[data-f=extra_ai]', row).value, ai: {} };
  for (const f of S.config.fields) {
    const n = $(`[data-f="${f.id}"]`, row), a = $(`[data-ai="${f.id}"]`, row);
    cfg[f.id] = f.kind === 'check' ? n.checked : n.value.trim();
    // columna Modo IA: vacio = igual que Normal (no se guarda)
    const v = a ? a.value.trim() : '';
    if (v) cfg.ai[f.id] = f.kind === 'check' ? v === 'on' : v;
  }
  return cfg;
}

const optionsHtml = (opts, sel) => opts.map(([v, label]) => `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(t(label))}</option>`).join('');

function normalControl(f, v) {
  const attrs = `data-f="${f.id}"`;
  if (f.kind === 'check') return `<input type="checkbox" ${attrs} ${v ? 'checked' : ''} class="check">`;
  if (f.kind === 'select') return `<select ${attrs}>${optionsHtml(f.options, v ?? '')}</select>`;
  if (f.kind === 'layers') return `<input type="text" class="mono" ${attrs} value="${esc(v ?? '')}" placeholder="${esc(t(f.placeholder))}" spellcheck="false">`;
  return `<input type="number" ${attrs} value="${esc(v ?? '')}" min="${f.min ?? ''}" max="${f.max ?? ''}" step="${f.step ?? 1}" placeholder="${esc(t(f.placeholder || ''))}">`;
}

function aiControl(f, v) {
  const same = t('igual que normal'), attrs = `data-ai="${f.id}"`;
  if (f.kind === 'check') {
    return `<select ${attrs}><option value="">${same}</option>` +
      `<option value="on" ${v === true ? 'selected' : ''}>${t('activado')}</option>` +
      `<option value="off" ${v === false ? 'selected' : ''}>${t('apagado')}</option></select>`;
  }
  if (f.kind === 'select') {  // sin la opcion "por defecto": vacio ya significa "igual que normal"
    return `<select ${attrs}><option value="">${same}</option>${optionsHtml(f.options.filter(o => o[0] !== ''), v)}</select>`;
  }
  const type = f.kind === 'layers' ? 'type="text" class="mono" spellcheck="false"' : `type="number" min="${f.min ?? ''}" max="${f.max ?? ''}" step="${f.step ?? 1}"`;
  return `<input ${type} ${attrs} value="${esc(v ?? '')}" placeholder="${esc(same)}">`;
}

// Ajustes del modelo en columnas: Normal y, si el modo IA esta instalado, Modo IA (vacio = igual que Normal)
const globalFields = () => S.config.fields.filter(f => S.config.global_field_ids.includes(f.id));

// Controles de los ajustes globales: los mismos que los del modelo, sin MTP ni columna de modo IA
export function readGlobalFields() {
  const box = $('#global-fields'), out = {};
  for (const f of globalFields()) {
    const n = $(`[data-f="${f.id}"]`, box);
    if (n && n.value.trim()) out[f.id] = n.value.trim();
  }
  return out;
}

function renderGlobal() {
  const box = $('#global-fields');
  box.innerHTML = modeGrid(S.config.global_fields || {}, false, globalFields());
  const sync = () => markOverrides(box, $('#global'), null, globalFields());
  box.oninput = () => { sync(); setDirty('text'); };
  $('#global').oninput = () => { sync(); setDirty('text'); };
  sync();
}

function modeGrid(cfg, aiOn, fields = S.config.fields) {
  const ai = cfg.ai || {};
  const rows = fields.map(f => `
      <span class="mode-l">${esc(t(f.label))}</span>
      <div>${normalControl(f, cfg[f.id])}</div>
      ${aiOn ? `<div class="mode-ai">${aiControl(f, ai[f.id])}</div>` : ''}
      <span class="mode-hint" data-hint="${f.id}">${esc(t(f.hint || ''))}</span>`).join('');
  return `
    <div class="mode-grid wide ${aiOn ? 'two' : ''}">
      <span></span><span class="mode-h">${aiOn ? t('Normal') : ''}</span>${aiOn ? `<span class="mode-h">${t('Modo IA')}</span>` : ''}<span></span>
      ${rows}
    </div>
    ${aiOn ? `<p class="hint wide">${t('En modo IA el escritorio está apagado y quedan ~0.4 GB más de VRAM: sirve, por ejemplo, para más contexto. Lo que quede en «igual que normal» usa la columna Normal.')}</p>` : ''}`;
}

export function renderTextModels() {
  const c = S.config;
  $('#text-dir').textContent = c.models_dir;
  $('#global').value = c.global;
  renderGlobal();
  const box = $('#text-models');
  box.innerHTML = '';
  if (!c.text_models.length) {
    box.append(el(`<div class="card empty">${esc(t('No hay archivos .gguf en {dir}.', { dir: c.models_dir }))}</div>`));
    return;
  }
  const aiOn = !!c.ai_mode_installed;
  for (const m of c.text_models) {
    const cfg = m.cfg;
    const row = el(`
      <div class="model" data-id="${esc(m.id)}" data-key="${esc(m.key)}">
        <div class="model-row">
          <div class="model-main">
            <div class="model-name" title="${esc(m.key)}">${esc(m.id)}</div>
            <div class="model-meta"><span data-perf></span><span class="model-meta" data-badges>${textBadges(m, cfg)}</span></div>
          </div>
          <span class="status model-state" data-mstate><i></i><b>${t('Sin cargar')}</b></span>
          <div class="model-actions">
            <span class="prompts" data-prompts></span>
            <button class="btn ghost small" data-psave hidden title="${esc(t('Guarda con un nombre el prompt que el modelo ya procesó (por ejemplo, las instrucciones y herramientas de Cline o de un asistente de voz). Al cargar el modelo se precargan todos: cada cliente arranca en ~1 s en vez de ~1 min.'))}">${t('Guardar prompt…')}</button>
            <button class="btn small" data-load>${t('Cargar')}</button>
            <button class="btn ghost small" data-toggle aria-expanded="false">${t('Ajustes')}</button>
          </div>
        </div>
        <div class="model-edit" hidden>
          ${modeGrid(cfg, aiOn)}
          <label class="field wide"><span>${t('Opciones extra de llama-server · una por línea: <code>clave = valor</code> · <code>#</code> para comentarios')}</span>
            <textarea rows="6" data-f="extra" data-autosize spellcheck="false">${esc(cfg.extra)}</textarea></label>
          <div class="opts wide" data-opts="extra"></div>
          <label class="field wide" ${aiOn ? '' : 'hidden'}><span>${t('Opciones extra en modo IA · se suman a las de arriba y las pisan, solo con el escritorio apagado (~0.4 GB más de VRAM)')}</span>
            <textarea rows="2" data-f="extra_ai" data-autosize spellcheck="false" placeholder="${esc(t('ej: ctx-size = 32768'))}">${esc(cfg.extra_ai || '')}</textarea></label>
          <div class="opts wide" data-opts="extra_ai"></div>
        </div>
      </div>`);
    $('[data-toggle]', row).onclick = e => {
      const ed = $('.model-edit', row);
      ed.hidden = !ed.hidden;
      e.currentTarget.setAttribute('aria-expanded', String(!ed.hidden));
      if (!ed.hidden) $$('[data-autosize]', row).forEach(autosize);  // oculto mide 0: ajustar al abrir
    };
    $('.model-edit', row).addEventListener('input', e => {
      if (e.target.matches('[data-autosize]')) autosize(e.target);
      $('[data-badges]', row).innerHTML = textBadges(m, readTextCfg(row));
      renderOpts(row);
      setDirty('text');
    });
    renderOpts(row);
    $('[data-load]', row).onclick = e => loadModel(m.id, e.currentTarget);
    $('[data-psave]', row).onclick = e => {
      const name = prompt(t('Nombre para este prompt (por ejemplo cline o voz). Si ya existe, se reemplaza:'), 'principal');
      if (name !== null) promptAction('save', m.id, e.currentTarget, name);
    };
    $('[data-prompts]', row).onclick = e => {
      const del = e.target.closest('[data-pdel]');
      if (del && confirm(t('¿Borrar el prompt guardado «{name}»?', { name: del.dataset.pdel }))) promptAction('delete', m.id, del, del.dataset.pdel);
    };
    box.append(row);
  }
  updateTextStates();
  updateTextPerf();
}

export function updateTextStates() {
  if (!S.state || !S.config) return;
  const textOn = S.state.svc.text === 'active';
  for (const row of $$('#text-models .model')) {
    const ms = S.state.text.models?.[row.dataset.id];
    setStatus($('[data-mstate]', row), modelStatus(ms, textOn));
    const loaded = textOn && ms?.status === 'loaded';
    row.classList.toggle('loaded', loaded);
    const saved = loaded ? (ms.prompts || []) : [];
    $('[data-psave]', row).hidden = !loaded;
    const chips = $('[data-prompts]', row);
    const html = saved.map(p => {
      const k = p.tokens ? fix(p.tokens / 1000) + 'K' : gb(p.bytes);
      const tip = t('Prompt guardado «{name}»: {size}, del {date}. Se precarga al cargar el modelo.',
        { name: p.name, size: gb(p.bytes), date: new Date(p.saved_at * 1000).toLocaleString(locale()) });
      return `<span class="badge accent prompt-chip" title="${esc(tip)}">${esc(p.name)} · ${k}<button class="chip-x" data-pdel="${esc(p.name)}" title="${esc(t('Borrar'))}">✕</button></span>`;
    }).join('');
    if (chips.dataset.html !== html) { chips.innerHTML = html; chips.dataset.html = html; }  // no redibujar en cada sondeo
    const btn = $('[data-load]', row);
    if (btn.classList.contains('busy')) continue;
    const st = textOn && ms ? ms.status : 'unloaded';
    btn.textContent = st === 'loaded' || st === 'sleeping' ? t('Descargar') : st === 'loading' ? t('Cargando…') : t('Cargar');
    btn.classList.toggle('ghost', st === 'loaded' || st === 'sleeping');
    btn.disabled = st === 'loading';
    btn.dataset.mode = st === 'loaded' || st === 'sleeping' ? 'unload' : 'load';
  }
}

async function promptAction(what, id, btn, name) {
  report(await withBusy(btn, () => post('/api/text/prompt/' + what, { id, name })));
  await hooks.pollState();
}

async function loadModel(id, btn) {
  const unload = btn.dataset.mode === 'unload';
  const res = await withBusy(btn, () => post(unload ? '/api/text/unload' : '/api/text/load', { id }));
  report(res);
  await hooks.pollState();
}
