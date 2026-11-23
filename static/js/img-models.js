// Pestana de modelos de imagen: tarjetas, opciones extra y "usar este".
import { $, $$, el, esc, gb, post, report, withBusy, WINDOWS_DISK_TIP } from './util.js';
import { S, hooks, setDirty } from './state.js';
import { t } from './i18n.js';

export function renderImgModels() {
  const c = S.config;
  $('#img-dir').textContent = c.img_dir;
  const box = $('#img-models');
  box.innerHTML = '';
  if (!c.img_models.length) {
    box.append(el(`<div class="card empty">${esc(t('No hay modelos (.gguf / .safetensors de más de 100 MB) en {dir}.', { dir: c.img_dir }))}</div>`));
    return;
  }
  for (const m of c.img_models) {
    const comps = m.comps.length
      ? m.comps.map(([f, n]) => `<span title="${esc(n)}"><b>${esc(f)}</b> ${esc(n)}</span>`).join('')
      : `<span class="muted">${t('solo el modelo')}</span>`;
    const card = el(`
      <div class="card img-card" data-key="${esc(m.key)}">
        <div class="model-name" title="${esc(m.key)}">${esc(m.id)}</div>
        <span class="badge accent sel-tag" hidden>${t('Seleccionado')}</span>
        <div class="model-meta"><span class="badge">${gb(m.size)}</span>${m.windows_disk ? `<span class="badge warn" title="${esc(t(WINDOWS_DISK_TIP))}">${t('en el disco de Windows')}</span>` : ''}</div>
        <div class="comps">${comps}</div>
        <label class="field"><span>${t('Opciones extra')}</span>
          <input type="text" class="mono" data-extra value="${esc(m.extra)}" placeholder="${esc(t('ej: --sdoffloadcpu --sdtiledvae 768'))}" spellcheck="false"></label>
        <div class="foot"><button class="btn small" data-use>${t('Usar este')}</button></div>
      </div>`);
    $('[data-extra]', card).addEventListener('input', () => setDirty('img'));
    $('[data-use]', card).onclick = e => useImg(m.key, e.currentTarget);
    box.append(card);
  }
  updateImgStates();
}

export function updateImgStates() {
  if (!S.config) return;
  const imgOn = S.state?.svc.img === 'active';
  for (const card of $$('#img-models .img-card')) {
    const sel = card.dataset.key === S.config.img_selected;
    card.classList.toggle('selected', sel);
    $('.sel-tag', card).hidden = !sel;
    const btn = $('[data-use]', card);
    if (btn.classList.contains('busy')) continue;
    btn.textContent = sel && imgOn ? t('Reiniciar con este') : imgOn ? t('Cambiar a este') : t('Usar y encender');
  }
}

export function readImgCfg() {
  return Object.fromEntries($$('#img-models .img-card').map(c => [c.dataset.key, { extra: $('[data-extra]', c).value.trim() }]));
}

async function useImg(key, btn) {
  const res = await withBusy(btn, () => post('/api/img/use', { key, models: readImgCfg() }));
  report(res);
  if (res.ok) { S.dirtyImg = false; await hooks.loadConfig(); }
  await hooks.pollState();
}
