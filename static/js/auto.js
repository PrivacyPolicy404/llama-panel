// Pestana Configuracion, tarjeta "Encendido a demanda": el panel atiende 8080/5001 y prende texto o imagenes segun el
// pedido (llama_panel/gate.py), y la descarga por inactividad. El estado llega con /api/state cada 2 s.
import { $, clock, esc, post, report, withBusy } from './util.js';
import { S, hooks } from './state.js';
import { t } from './i18n.js';

let drawn = false;

function form(a) {
  const ports = S.config?.ports || { chat: 8080, img: 5001 };
  return `
    <div class="card setup-card" id="auto-card">
      <h4>${t('Encendido a demanda')}</h4>
      <label class="check"><input type="checkbox" name="swap" ${a.swap ? 'checked' : ''}>
        ${t('Prender texto o imágenes según lo que pida cada cliente')}</label>
      <p class="hint">${t('El panel atiende los puertos {chat} y {img}: un pedido de imagen con un modelo de texto cargado lo baja y prende el generador, y al revés, siempre después de que terminen los pedidos en curso. Los clientes no cambian de dirección. Al activarlo o desactivarlo se reinician los servicios encendidos. Si el panel se reinicia, se corta lo que esté pasando por él.', ports)}</p>
      <label class="field"><span>${t('Descargar después de estos minutos sin uso')}</span>
        <div class="row"><input type="number" name="idle_min" min="0" max="1440" step="5" value="${a.idle_min || 0}">
        <span class="muted">${t('0 = nunca')}</span></div>
        <p class="hint">${t('Baja el modelo de texto o apaga el generador de imágenes para liberar la VRAM. Funciona también sin el encendido a demanda.')}</p>
      </label>
      <div class="auto-status" id="auto-status"></div>
      <button class="btn" id="auto-save">${t('Guardar')}</button>
    </div>`;
}

function status(a) {
  const parts = [];
  if (a.swap) {
    parts.push(a.listening?.length === 2
      ? `<span class="path-check good">✓ ${esc(t('Atendiendo los puertos {chat} y {img}.', S.config?.ports || { chat: 8080, img: 5001 }))}</span>`
      : `<span class="path-check bad">✗ ${esc(a.error || t('Todavía no atiende los puertos.'))}</span>`);
    const busy = [];
    if (a.inflight?.text) busy.push(t('{n} pedidos de texto en curso', { n: a.inflight.text }));
    if (a.inflight?.img) busy.push(t('{n} pedidos de imagen en curso', { n: a.inflight.img }));
    if (a.waiting?.text) busy.push(t('{n} de texto esperando', { n: a.waiting.text }));
    if (a.waiting?.img) busy.push(t('{n} de imagen esperando', { n: a.waiting.img }));
    if (busy.length) parts.push(`<span class="muted">${esc(busy.join(' · '))}</span>`);
  }
  const ev = (a.events || []).slice().reverse();
  if (ev.length) {
    parts.push(`<ul class="auto-events">${ev.map(e => `<li><span class="muted mono">${esc(clock(e.t))}</span> ${esc(e.msg)}</li>`).join('')}</ul>`);
  }
  return parts.join('');
}

export function renderAuto() {
  const a = S.state?.auto, box = $('#auto');
  if (!a || !box) return;
  if (!drawn) {
    box.innerHTML = form(a);
    $('#auto-save').onclick = e => saveAuto(e.currentTarget);
    drawn = true;
  }
  $('#auto-status').innerHTML = status(a);
}

async function saveAuto(btn) {
  const body = { swap: $('#auto-card [name=swap]').checked, idle_min: Number($('#auto-card [name=idle_min]').value || 0) };
  // cambiar el proxy reinicia los servicios: puede tardar
  report(await withBusy(btn, () => post('/api/auto', body)));
  await hooks.pollState();
  drawn = false;
  renderAuto();
}
