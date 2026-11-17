// Pestana Configuracion: carpetas de modelos, ejecutables y nombre de la GPU (se guardan en paths.env).
import { $, $$, esc, getJSON, post, report, toast, withBusy } from './util.js';
import { hooks } from './state.js';

let P = null;        // respuesta de /api/paths
let checkTimer = null;

const MARK = { true: ['good', '✓'], false: ['bad', '✗'], null: ['muted', '·'] };
const status = c => { const [cls, icon] = MARK[c?.ok ?? null]; return `<span class="path-check ${cls}">${icon} ${esc(c?.msg || '')}</span>`; };

function values() {
  return {
    text_dirs: $$('#setup [data-dir]').map(i => i.value.trim()).filter(Boolean),
    img_dir: $('#setup [name=img_dir]').value.trim(),
    llama_server: $('#setup [name=llama_server]').value.trim(),
    kobold: $('#setup [name=kobold]').value.trim(),
    gpu_name: $('#setup [name=gpu_name]').value.trim(),
  };
}

const dirRow = (path, check, locked) => `
  <div class="path-row">
    <input type="text" class="mono" data-dir list="dir-suggest" value="${esc(path)}" placeholder="/ruta/a/modelos" ${locked ? 'disabled' : ''}>
    <button class="btn ghost small" data-remove title="Quitar esta carpeta" ${locked ? 'disabled' : ''}>Quitar</button>
    <div class="path-status">${check ? status(check) : ''}</div>
  </div>`;

const field = (name, label, hint, value, check, locked, list = '') => `
  <label class="field">
    <span>${label}</span>
    <input type="text" class="mono" name="${name}" value="${esc(value)}" ${list ? `list="${list}"` : ''} ${locked ? 'disabled' : ''}>
    <div class="path-status">${check ? status(check) : ''}</div>
    ${hint ? `<p class="hint">${hint}</p>` : ''}
  </label>`;

export function renderSetup() {
  if (!P) return;
  const { values: v, checks: c, locked } = P;
  const lock = k => locked.includes(k);
  const env = locked.length ? `<p class="hint">Los campos grises están fijados por una variable de entorno del servicio y no se pueden cambiar desde acá.</p>` : '';
  $('#setup').innerHTML = `
    <div class="card setup-card">
      <h4>Modelos de texto</h4>
      <p class="hint">Carpetas donde buscar archivos <code>.gguf</code> (también en subcarpetas). Conviene que los modelos grandes estén en un disco rápido.</p>
      <div id="text-dirs">${v.text_dirs.map((d, i) => dirRow(d, c.text_dirs[i], lock('text_dirs'))).join('')}</div>
      <button class="btn ghost small" id="add-dir" ${lock('text_dirs') ? 'disabled' : ''}>Agregar carpeta</button>
    </div>
    <div class="card setup-card">
      <h4>Programas</h4>
      ${field('llama_server', 'llama-server', 'De llama.cpp, compilado con el backend de tu GPU (Vulkan, CUDA, ROCm…). Ej.: <code>~/llama.cpp/build/bin/llama-server</code>', v.llama_server, c.llama_server, lock('llama_server'))}
      ${field('kobold', 'KoboldCpp', 'Opcional, para generar imágenes.', v.kobold, c.kobold, lock('kobold'))}
    </div>
    <div class="card setup-card">
      <h4>Imágenes y GPU</h4>
      ${field('img_dir', 'Carpeta de modelos de imagen', 'Opcional. <code>.safetensors</code> o <code>.gguf</code> de Stable Diffusion / Flux.', v.img_dir, c.img_dir, lock('img_dir'), 'dir-suggest')}
      ${field('gpu_name', 'Nombre de la GPU', `Opcional, solo para mostrar. Detectado: <b>${esc(P.detected_gpu || 'ninguno')}</b>.`, v.gpu_name, null, lock('gpu_name'))}
    </div>
    ${env}
    <div class="setup-actions">
      <span class="muted mono">${esc(P.file)}</span>
      <button class="btn" id="paths-save">Guardar</button>
    </div>
    <datalist id="dir-suggest"></datalist>`;
  bindSetup();
}

function bindSetup() {
  const root = $('#setup');
  root.oninput = e => {
    if (e.target.matches('input')) { scheduleCheck(); if (e.target.list) suggest(e.target.value); }
  };
  root.onclick = e => {
    if (e.target.matches('[data-remove]')) { e.target.closest('.path-row').remove(); scheduleCheck(); }
  };
  $('#add-dir').onclick = () => {
    $('#text-dirs').insertAdjacentHTML('beforeend', dirRow('', null, false));
    $('#text-dirs .path-row:last-child input').focus();
  };
  $('#paths-save').onclick = e => saveSetup(e.currentTarget);
}

let lastPrefix = '';
async function suggest(prefix) {
  if (prefix === lastPrefix || !/^[~/]/.test(prefix)) return;
  lastPrefix = prefix;
  try {
    const { dirs } = await getJSON('/api/paths/dirs?prefix=' + encodeURIComponent(prefix));
    $('#dir-suggest').innerHTML = dirs.map(d => `<option value="${esc(d)}">`).join('');
  } catch { /* sin sugerencias */ }
}

function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(async () => {
    const res = await post('/api/paths/check', { values: values() });
    if (!res.text_dirs) return;
    const rows = $$('#text-dirs .path-row').filter(r => $('input', r).value.trim());
    rows.forEach((r, i) => { $('.path-status', r).innerHTML = status(res.text_dirs[i]); });
    for (const k of ['llama_server', 'kobold', 'img_dir']) {
      const box = $(`#setup [name=${k}]`)?.closest('.field')?.querySelector('.path-status');
      if (box) box.innerHTML = status(res[k]);
    }
  }, 400);
}

async function waitForPanel(timeout = 20000) {
  const end = Date.now() + timeout;
  await new Promise(r => setTimeout(r, 1500));
  while (Date.now() < end) {
    try { return await getJSON('/api/paths'); } catch { await new Promise(r => setTimeout(r, 700)); }
  }
  return null;
}

async function saveSetup(btn) {
  const res = await withBusy(btn, async () => {
    const r = await post('/api/paths', { values: values() });
    if (!r.ok) return r;
    toast(r.msg, 'info');
    P = await waitForPanel() || P;
    if (r.rescan) report(await post('/api/text/rescan'));
    await hooks.loadConfig?.();
    return { ok: true, msg: 'Rutas aplicadas.' };
  });
  report(res);
  renderSetup();
  $('#setup-needed').hidden = !needsSetup(P);
}

export async function loadSetup() {
  try { P = await getJSON('/api/paths'); } catch { return null; }
  renderSetup();
  return P;
}

// Hace falta configurar si no hay llama-server o ninguna carpeta tiene modelos
export const needsSetup = p => !p?.checks?.llama_server?.ok || !p.checks.text_dirs.some(c => c.ok);
