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

// chips debajo de cada textarea: que entendio el panel de cada linea
function renderOpts(row) {
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
  return {
    mtp: $('[data-f=mtp]', row).checked,
    nmax: $('[data-f=nmax]', row).value.trim() || '2',
    reasoning: $('[data-f=reasoning]', row).value,
    ctx: $('[data-f=ctx]', row).value.trim(),
    extra: $('[data-f=extra]', row).value,
    extra_ai: $('[data-f=extra_ai]', row).value,
    ai: readAiCfg(row),
  };
}

// columna "Modo IA": solo lo que difiere de la columna Normal ("igual" no se guarda)
function readAiCfg(row) {
  const ai = {};
  const mtp = $('[data-ai=mtp]', row)?.value;
  if (mtp === 'on' || mtp === 'off') ai.mtp = mtp === 'on';
  for (const k of ['nmax', 'ctx']) {
    const v = $(`[data-ai=${k}]`, row)?.value.trim();
    if (v) ai[k] = v;
  }
  const rs = $('[data-ai=reasoning]', row)?.value;
  if (rs !== undefined && rs !== SAME) ai.reasoning = rs;
  return ai;
}

const SAME = '=';

// Ajustes principales en dos columnas: Normal y, si el modo IA esta instalado, Modo IA (vacio = igual que Normal)
function modeGrid(cfg, opts, aiOn) {
  const ai = cfg.ai || {};
  const same = t('igual que normal');
  const aiCol = (html) => aiOn ? `<div class="mode-ai">${html}</div>` : '';
  return `
    <div class="mode-grid wide ${aiOn ? 'two' : ''}">
      <span></span><span class="mode-h">${aiOn ? t('Normal') : ''}</span>${aiOn ? `<span class="mode-h">${t('Modo IA')}</span>` : ''}
      <span class="mode-l">MTP</span>
      <div><input type="checkbox" data-f="mtp" ${cfg.mtp ? 'checked' : ''} class="check"></div>
      ${aiCol(`<select data-ai="mtp"><option value="">${same}</option>
        <option value="on" ${ai.mtp === true ? 'selected' : ''}>${t('activado')}</option>
        <option value="off" ${ai.mtp === false ? 'selected' : ''}>${t('apagado')}</option></select>`)}
      <span class="mode-l">${t('Borradores por paso')}</span>
      <div><input type="number" min="1" max="16" data-f="nmax" value="${esc(cfg.nmax || 2)}"></div>
      ${aiCol(`<input type="number" min="1" max="16" data-ai="nmax" value="${esc(ai.nmax || '')}" placeholder="${esc(same)}">`)}
      <span class="mode-l">${t('Razonamiento')}</span>
      <div><select data-f="reasoning">${opts(cfg.reasoning)}</select></div>
      ${aiCol(`<select data-ai="reasoning"><option value="${SAME}">${same}</option>${'reasoning' in ai ? opts(ai.reasoning) : opts(null)}</select>`)}
      <span class="mode-l">${t('Contexto')}</span>
      <div><input type="number" min="256" step="256" data-f="ctx" value="${esc(cfg.ctx)}" placeholder="global"></div>
      ${aiCol(`<input type="number" min="256" step="256" data-ai="ctx" value="${esc(ai.ctx || '')}" placeholder="${esc(same)}">`)}
    </div>
    ${aiOn ? `<p class="hint wide">${t('En modo IA el escritorio está apagado y quedan ~0.4 GB más de VRAM: sirve, por ejemplo, para más contexto. Lo que quede en «igual que normal» usa la columna Normal.')}</p>` : ''}`;
}

export function renderTextModels() {
  const c = S.config;
  $('#text-dir').textContent = c.models_dir;
  $('#global').value = c.global;
  const box = $('#text-models');
  box.innerHTML = '';
  if (!c.text_models.length) {
    box.append(el(`<div class="card empty">${esc(t('No hay archivos .gguf en {dir}.', { dir: c.models_dir }))}</div>`));
    return;
  }
  const opts = sel => c.reasoning_opts.map(([v, label]) => `<option value="${esc(v)}" ${v === sel ? 'selected' : ''}>${esc(label)}</option>`).join('');
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
            <button class="btn ghost small" data-psave hidden title="${esc(t('Guarda a disco el prompt que el modelo ya procesó (por ejemplo, las instrucciones y herramientas de un agente). Se restaura solo cada vez que el modelo se carga.'))}">${t('Guardar prompt')}</button>
            <button class="btn ghost small" data-pdel hidden>${t('Borrar prompt')}</button>
            <button class="btn small" data-load>${t('Cargar')}</button>
            <button class="btn ghost small" data-toggle aria-expanded="false">${t('Ajustes')}</button>
          </div>
        </div>
        <div class="model-edit" hidden>
          ${modeGrid(cfg, opts, aiOn)}
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
    $('[data-psave]', row).onclick = e => promptAction('save', m.id, e.currentTarget);
    $('[data-pdel]', row).onclick = e => promptAction('delete', m.id, e.currentTarget);
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
    const saved = loaded ? ms.prompt : null;
    const psave = $('[data-psave]', row), pdel = $('[data-pdel]', row);
    psave.hidden = !loaded;
    pdel.hidden = !saved;
    if (saved) {
      const k = saved.tokens ? fix(saved.tokens / 1000) + 'K tokens' : gb(saved.bytes);
      psave.textContent = t('Prompt guardado · {size}', { size: k });
      pdel.title = t('Guardado el {date} ({size}). Clic en "Prompt guardado" lo reemplaza por lo que el modelo tiene procesado ahora.',
        { date: new Date(saved.saved_at * 1000).toLocaleString(locale()), size: gb(saved.bytes) });
    } else {
      psave.textContent = t('Guardar prompt');
    }
    const btn = $('[data-load]', row);
    if (btn.classList.contains('busy')) continue;
    const st = textOn && ms ? ms.status : 'unloaded';
    btn.textContent = st === 'loaded' || st === 'sleeping' ? t('Descargar') : st === 'loading' ? t('Cargando…') : t('Cargar');
    btn.classList.toggle('ghost', st === 'loaded' || st === 'sleeping');
    btn.disabled = st === 'loading';
    btn.dataset.mode = st === 'loaded' || st === 'sleeping' ? 'unload' : 'load';
  }
}

async function promptAction(what, id, btn) {
  report(await withBusy(btn, () => post('/api/text/prompt/' + what, { id })));
  await hooks.pollState();
}

async function loadModel(id, btn) {
  const unload = btn.dataset.mode === 'unload';
  const res = await withBusy(btn, () => post(unload ? '/api/text/unload' : '/api/text/load', { id }));
  report(res);
  await hooks.pollState();
}
