// Pestana de modelos de texto: lista, estado en el router, carga/descarga y editor de ajustes.
import { $, $$, el, esc, gb, modelStatus, post, report, setStatus, withBusy } from './util.js';
import { S, hooks, setDirty } from './state.js';

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

export function readTextCfg(row) {
  return {
    mtp: $('[data-f=mtp]', row).checked,
    nmax: $('[data-f=nmax]', row).value.trim() || '2',
    reasoning: $('[data-f=reasoning]', row).value,
    ctx: $('[data-f=ctx]', row).value.trim(),
    extra: $('[data-f=extra]', row).value,
  };
}

export function renderTextModels() {
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

export function updateTextStates() {
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
  await hooks.pollState();
}
