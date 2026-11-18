// Pestana Compañero: charla con el agente (llama_panel/buddy.py) por texto o voz.
// Voz: se graba mientras se mantiene apretado el boton (o el atajo elegido) y el panel lo pasa a texto con
// Whisper; la respuesta llega en eventos y cada oracion trae su audio de Piper, que se reproduce en orden.
// El atajo es local a este navegador; para cualquier programa se usa clients/dani.py.
import { $, $$, esc, getJSON, post, report, toast, withBusy } from './util.js';
import { S } from './state.js';
import { lang, t } from './i18n.js';

let B = null;            // respuesta de /api/buddy
let drawn = false;
let rec = null;          // grabacion en curso
let abort = null;        // charla en curso (para cortarla)
const audioQueue = [];
let playing = null;
let hotkeyDown = false;
let capturingHotkey = false;
const HOTKEY_KEY = 'buddy-push-to-talk';
const OLD_HOTKEYS = {
  buddy_space: { code: 'Space', scope: 'buddy' },
  all_ctrl_shift_space: { code: 'Space', ctrl: true, shift: true, scope: 'all' },
  all_f9: { code: 'F9', scope: 'all' },
};

// Id de esta pestana del navegador: el panel le habla los avisos al cliente que se uso ultimo (llama_panel/proactive.py)
const CLIENT = (() => {
  const make = () => 'web-' + Math.random().toString(36).slice(2, 10);
  try {
    let id = sessionStorage.getItem('dani-client');
    if (!id) { id = make(); sessionStorage.setItem('dani-client', id); }
    return id;
  } catch { return make(); }
})();
const KIND_ICON = { timer: '⏰', alerta: '⚠️', resumen: '☀️', aviso: '💬' };
const seen = new Set();
let alertsSource = null;
let mirroredChat = null;

const LEVELS = { lectura: 'solo mira', memoria: 'escribe su memoria', accion: 'cambia cosas', peligrosa: 'peligrosa' };

export async function loadBuddy() {
  try { B = await getJSON('/api/buddy'); } catch { return; }
  if (!drawn) draw();
  else renderConversations();
  renderHistory();
}

// Al arrancar el panel: la pestana se arma oculta (la charla funciona desde cualquier lado) y aparece el boton flotante
export async function initBuddy() {
  await loadBuddy();
  if (!B) return;
  drawFab();
  listen();
  addEventListener('keydown', e => {
    if (capturingHotkey) { captureHotkey(e); return; }
    if (e.repeat || !hotkeyMatches(e) || isInteractive(e.target)) return;
    e.preventDefault();
    hotkeyDown = true;
    startRec(true);
  });
  addEventListener('keyup', e => {
    const hotkey = hotkeyValue();
    if (!hotkeyDown || !hotkey?.code || e.code !== hotkey.code) return;
    e.preventDefault();
    hotkeyDown = false;
    stopRec();
  });
  addEventListener('pointerdown', e => {
    if (capturingHotkey) {
      if (e.button >= 3) captureMouseButton(e);
      return;
    }
    if (!mouseHotkeyMatches(e) || isInteractive(e.target)) return;
    e.preventDefault();
    hotkeyDown = true;
    startRec(true);
  }, true);
  addEventListener('pointerup', e => {
    const hotkey = hotkeyValue();
    if (!hotkeyDown || hotkey.mouseButton === undefined || e.button !== hotkey.mouseButton) return;
    e.preventDefault();
    hotkeyDown = false;
    stopRec();
  }, true);
  addEventListener('auxclick', e => { if (mouseHotkeyMatches(e)) e.preventDefault(); }, true);
}

function hotkeyValue() {
  try {
    const raw = localStorage.getItem(HOTKEY_KEY);
    if (!raw || raw === 'off') return { scope: 'all' };
    if (OLD_HOTKEYS[raw]) return OLD_HOTKEYS[raw];  // migración de la primera versión experimental
    const value = JSON.parse(raw);
    return typeof value.code === 'string' || Number.isInteger(value.mouseButton) && value.mouseButton >= 3 ?
      value : { scope: 'all' };
  } catch { return { scope: 'all' }; }
}

function hotkeyMatches(e) {
  const h = hotkeyValue();
  return h.code && (h.scope === 'all' || S.tab === 'buddy') && e.code === h.code &&
    !!e.ctrlKey === !!h.ctrl && !!e.shiftKey === !!h.shift && !!e.altKey === !!h.alt && !!e.metaKey === !!h.meta;
}

function mouseHotkeyMatches(e) {
  const h = hotkeyValue();
  return h.mouseButton !== undefined && (h.scope === 'all' || S.tab === 'buddy') && e.button === h.mouseButton;
}

function isInteractive(target) {
  return target instanceof Element &&
    !!target.closest('input, textarea, select, button, a, [contenteditable], [role="button"]');
}

function hotkeyLabel() {
  const h = hotkeyValue();
  if (h.mouseButton !== undefined) return t('Botón lateral {n}', { n: h.mouseButton - 2 });
  if (!h.code) return t('Desactivado');
  const keys = [];
  if (h.ctrl) keys.push('Ctrl');
  if (h.alt) keys.push('Alt');
  if (h.shift) keys.push('Shift');
  if (h.meta) keys.push(navigator.platform.includes('Mac') ? '⌘' : 'Meta');
  keys.push(h.code === 'Space' ? t('Espacio') : h.code.replace(/^Key/, '').replace(/^Digit/, ''));
  return keys.join(' + ');
}

function micTitle() {
  const h = hotkeyValue();
  return !h.code && h.mouseButton === undefined ? t('Mantené apretado para hablar') :
    t('Mantené apretado para hablar · Atajo: {key}', { key: hotkeyLabel() });
}

function saveHotkey(value) {
  localStorage.setItem(HOTKEY_KEY, value.code || value.mouseButton !== undefined ? JSON.stringify(value) : 'off');
  updateHotkeyUI();
}

function updateHotkeyUI() {
  const h = hotkeyValue();
  const label = $('#buddy-hotkey-label');
  if (label) label.textContent = hotkeyLabel();
  const scope = $('#buddy-hotkey-scope');
  if (scope) scope.value = h.scope || 'all';
  const title = micTitle();
  if ($('#buddy-mic')) $('#buddy-mic').title = title;
  if ($('#buddy-fab .buddy-fab-mic')) $('#buddy-fab .buddy-fab-mic').title = title;
}

function captureHotkey(e) {
  e.preventDefault();
  e.stopPropagation();
  if (e.key === 'Escape') { capturingHotkey = false; updateHotkeyUI(); return; }
  if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key) || e.repeat) return;
  const scope = $('#buddy-hotkey-scope').value;
  const modified = e.ctrlKey || e.altKey || e.shiftKey || e.metaKey;
  if (scope === 'all' && !modified && !/^F([1-9]|1[0-2])$/.test(e.code)) {
    toast(t('Para todo el panel usá una combinación con Ctrl, Alt o Shift, o una tecla F1–F12.'), 'error');
    return;
  }
  saveHotkey({ code: e.code, ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey, meta: e.metaKey, scope });
  capturingHotkey = false;
}

function captureMouseButton(e) {
  e.preventDefault();
  e.stopPropagation();
  saveHotkey({ mouseButton: e.button, scope: $('#buddy-hotkey-scope').value });
  capturingHotkey = false;
}

// Lo que Dani dice por su cuenta (timers, avisos, el resumen de la manana) llega por server-sent events
function listen() {
  const sync = () => {
    if (document.hidden) {
      alertsSource?.close();
      alertsSource = null;
    } else if (!alertsSource) {
      alertsSource = new EventSource('/api/buddy/events?client=' + encodeURIComponent(CLIENT));
      alertsSource.onmessage = onAlert;
    }
  };
  addEventListener('visibilitychange', sync);
  addEventListener('pagehide', () => { alertsSource?.close(); alertsSource = null; });
  sync();
}

function onAlert(e) {
  let ev;
  try { ev = JSON.parse(e.data); } catch { return; }
  if (ev.type === 'chat') { mirrorChatEvent(ev); return; }
  if (ev.type === 'conversation') { if (!mirroredChat) loadBuddy(); return; }
  if (ev.type !== 'say' || seen.has(ev.id)) return;
  seen.add(ev.id);
  const icon = KIND_ICON[ev.kind] || '💬';
  if (!abort) {  // en medio de una respuesta no se mete en el log: queda en el historial
    $('.buddy-empty')?.remove();
    $('#buddy-log')?.insertAdjacentHTML('beforeend', bubble('assistant', ev.text, [], icon));
    scroll();
  }
  fab('speaking', `${icon} ${ev.text}`);
  if (ev.speak && ev.audio?.length) {
    for (const w of ev.audio) audioQueue.push(wavUrl(w));
    playNext();
  } else if (!playing) fab('idle', `${icon} ${ev.text}`);
  loadBuddyTimers();
}

function mirrorChatEvent(message) {
  if (message.client === CLIENT || !message.event || abort) return;
  const ev = message.event;
  if (!mirroredChat || mirroredChat.id !== message.chat_id) {
    mirroredChat = { id: message.chat_id, out: null, text: '', textEl: null };
  }
  const m = mirroredChat;
  if ((ev.type === 'heard' || ev.type === 'start') && !m.out) {
    $('.buddy-empty')?.remove();
    const log = $('#buddy-log');
    log.insertAdjacentHTML('beforeend', bubble('user', ev.text || t('(no se entendió)'), [], '', 'pc'));
    log.insertAdjacentHTML('beforeend', `<div class="buddy-msg assistant"><span class="muted buddy-typing">…</span></div>`);
    m.out = log.lastElementChild;
    fab('thinking', ev.text ? t('Pensando…') : t('Escuchando lo que dijiste…'));
  } else if (ev.type === 'text' && m.out) {
    $('.buddy-typing', m.out)?.remove();
    if (!m.textEl) { m.out.insertAdjacentHTML('beforeend', '<div class="buddy-text"></div>'); m.textEl = m.out.lastElementChild; }
    m.text += ev.delta; m.textEl.textContent = m.text;
    fab('speaking', m.text);
  } else if (ev.type === 'tool' && m.out) {
    const chip = document.createElement('span');
    chip.className = 'buddy-tool' + (ev.ok ? '' : ' bad');
    chip.textContent = '🔧 ' + ev.name; chip.title = `${ev.args}\n→ ${ev.result}`;
    m.out.insertBefore(chip, m.textEl || m.out.querySelector('.buddy-typing'));
    m.textEl = null; m.text = '';
    fab('thinking', '🔧 ' + ev.name);
  } else if (ev.type === 'error') {
    (m.out || $('#buddy-log'))?.insertAdjacentHTML('beforeend', `<div class="buddy-text bad">${esc(ev.msg)}</div>`);
  } else if (ev.type === 'done') {
    mirroredChat = null;
    loadBuddy();
    return;
  }
  scroll();
}

async function loadBuddyTimers() {
  try { B = await getJSON('/api/buddy'); } catch { return; }
  renderTimers();
}

function renderTimers() {
  const box = $('#buddy-timers');
  if (!box) return;
  const hm = s => new Date(s * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
  box.innerHTML = B.timers.map(x => `
    <div class="path-row"><span>⏰ <b>${esc(hm(x.at))}</b> ${esc(x.text)}</span>
      <button class="btn ghost small" data-cancel="${esc(x.id)}">${t('Cancelar')}</button></div>`).join('') ||
    `<p class="muted">${t('Ningún aviso pendiente. Pedile «avisame en 20 minutos…».')}</p>`;
  for (const b of $$('#buddy-timers [data-cancel]')) {
    b.onclick = async () => { report(await post('/api/buddy/timer/cancel', { id: b.dataset.cancel })); loadBuddyTimers(); };
  }
}

export function buddyTab(tab) {
  const fab = $('#buddy-fab');
  if (fab) fab.hidden = tab === 'buddy';  // en la pestana ya esta todo
}

// ---------- boton flotante ----------
let fabTimer = null;

function drawFab() {
  document.body.insertAdjacentHTML('beforeend', `
    <div class="buddy-fab" id="buddy-fab">
      <div class="buddy-fab-bubble" hidden><b></b><span></span></div>
      <button class="buddy-fab-mic" title="${esc(micTitle())}">🎤</button>
    </div>`);
  const mic = $('#buddy-fab .buddy-fab-mic');
  if (!window.isSecureContext) {
    mic.onclick = () => { location.hash = 'buddy'; $('[data-tab="buddy"]').click(); };
    return;
  }
  mic.onpointerdown = e => { e.preventDefault(); startRec(); };
  mic.onpointerup = mic.onpointerleave = () => stopRec();
  buddyTab(S.tab);
}

function fab(state, text) {
  const bubble = $('#buddy-fab .buddy-fab-bubble');
  if (!bubble) return;
  clearTimeout(fabTimer);
  $('#buddy-fab').dataset.state = state;
  $('b', bubble).textContent = state === 'user' ? t('Vos') : B.config.name;
  $('span', bubble).textContent = text;
  bubble.hidden = !text;
  if (state === 'idle') fabTimer = setTimeout(() => { if (!playing && !abort && !rec) bubble.hidden = true; }, 12000);
}

function draw() {
  const secure = window.isSecureContext;
  $('#buddy').innerHTML = `
    <div class="buddy-grid">
      <div class="card buddy-chat">
        <div class="buddy-head">
          <b id="buddy-name"></b>
          <span class="muted" id="buddy-voice"></span>
          <select id="buddy-conversation" title="${esc(t('Conversación activa'))}"></select>
          <span class="spacer"></span>
          <label class="check"><input type="checkbox" id="buddy-speak"> ${t('Hablar en voz alta')}</label>
          <button class="btn ghost small" id="buddy-reset">${t('Charla nueva')}</button>
          <button class="btn ghost small" id="buddy-conversation-delete" title="${esc(t('Eliminar conversación activa'))}">×</button>
        </div>
        <div class="buddy-log" id="buddy-log" aria-live="polite"></div>
        <div class="buddy-input">
          <button class="btn buddy-mic" id="buddy-mic" ${secure ? '' : 'disabled'}
            title="${esc(micTitle())}">🎤</button>
          <input type="text" id="buddy-text" placeholder="${esc(t('Escribile algo…'))}" autocomplete="off">
          <button class="btn" id="buddy-send">${t('Enviar')}</button>
          <button class="btn ghost" id="buddy-stop" hidden>${t('Callar')}</button>
        </div>
        <p class="hint" id="buddy-stats"></p>
        ${secure ? '' : `<p class="hint">${t('El navegador solo deja usar el micrófono en una página segura. Abrí el panel por un túnel SSH (<code>ssh -L 8090:localhost:8090 …</code>) y entrá a <code>http://localhost:8090</code>.')}</p>`}
      </div>
      <details class="card buddy-settings">
        <summary><b>${t('Ajustes del compañero')}</b></summary>
        <label class="field"><span>${t('Nombre')}</span><input type="text" id="buddy-cfg-name" maxlength="40"></label>
        <label class="field"><span>${t('Modelo')}</span><select id="buddy-cfg-model"></select></label>
        <label class="field"><span>${t('Idioma de la charla')}</span>
          <select id="buddy-cfg-language"><option value="es">Castellano</option><option value="en">English</option></select>
        </label>
        <div class="field"><span>${t('Instrucciones')}</span>
          <div id="buddy-cfg-sections" class="buddy-sections"></div>
          <p class="hint">${t('Cada parte por separado: si no la tocás, usa la de fábrica (y recibe sus mejoras). <code>{name}</code> se reemplaza por el nombre. Las fijas dependen del panel y no se editan.')}</p>
          <p class="hint">${t('Al guardar un cambio, la próxima respuesta tarda unos 20 s más: el modelo vuelve a leer todas las instrucciones.')}</p>
        </div>
        <div class="field"><span>${t('Herramientas')}</span><div id="buddy-cfg-tools" class="buddy-tools"></div></div>
        <label class="field"><span>${t('Atajo para hablar en este navegador')}</span>
          <span class="path-row"><b id="buddy-hotkey-label"></b>
            <button type="button" class="btn ghost small" id="buddy-hotkey-record">${t('Definir atajo')}</button>
            <button type="button" class="btn ghost small" id="buddy-hotkey-clear">${t('Borrar')}</button></span>
          <select id="buddy-hotkey-scope">
            <option value="buddy">${t('Sólo en la sección Compañero')}</option>
            <option value="all">${t('En cualquier sección del panel')}</option>
          </select>
          <p class="hint">${t('Apretá «Definir atajo» y después una combinación de teclado o un botón lateral del mouse. No se captura mientras usás un campo, botón o enlace.')}</p>
          <p class="hint">${t('Algunos navegadores reservan los botones Atrás/Adelante del mouse; si no llegan a la página, usá una tecla o el cliente dani.py.')}</p>
        </label>
        <label class="check"><input type="checkbox" id="buddy-share-location"> ${t('Compartir ubicación aproximada con el compañero')}</label>
        <div class="field"><span>${t('Por su cuenta')}</span>
          <label class="check"><input type="checkbox" id="buddy-cfg-alerts"> ${t('Avisar si la GPU o la CPU calientan, se cae un servicio o falta RAM o disco')}</label>
          <label class="check">${t('Resumen de la mañana a las')} <input type="time" id="buddy-cfg-morning"></label>
          <p class="hint">${t('El resumen lo escribe el modelo si ya hay uno cargado; si no, uno corto con los datos. Vacío = sin resumen.')}</p>
        </div>
        <span class="path-row"><button class="btn" id="buddy-cfg-save">${t('Guardar')}</button>
          <button class="btn ghost" id="buddy-cfg-test" title="${esc(t('Guarda y le hace 10 preguntas de prueba al modelo cargado (~1 min): si consulta cuando tiene que consultar y no inventa.'))}">${t('Guardar y probar')}</button></span>
        <div id="buddy-test-result" class="buddy-tools"></div>
        <div class="field"><span>${t('Avisos pendientes')}</span><div id="buddy-timers" class="buddy-tools"></div></div>
        <label class="field"><span>Home Assistant <span class="muted" id="buddy-ha-state"></span></span>
          <input type="password" id="buddy-ha-token" autocomplete="off" placeholder="${esc(t('token de acceso de larga duración'))}">
          <p class="hint">${t('En Home Assistant: tu perfil, Seguridad, «Tokens de acceso de larga duración». Mejor de un usuario que no sea administrador. Queda en un archivo aparte, solo legible por tu usuario.')}</p>
        </label>
        <button class="btn ghost" id="buddy-ha-save">${t('Guardar token')}</button>
        <label class="field"><span>Brave Search <span class="muted" id="buddy-brave-state"></span></span>
          <input type="password" id="buddy-brave-token" autocomplete="new-password" placeholder="Brave Search API key">
          <p class="hint">${t('La clave se envía al backend y queda en un archivo privado; nunca se vuelve a mostrar en el navegador. Si dejás el campo vacío y guardás, se borra y se usa DuckDuckGo experimental.')}</p>
        </label>
        <button class="btn ghost" id="buddy-brave-save">${t('Guardar clave')}</button>
        <div class="field"><span>${t('Hablar desde cualquier programa de tu PC')}</span>
          <p class="hint">${t('El atajo de arriba funciona sólo cuando llama-panel tiene el foco. Para usar una tecla sobre cualquier programa, descargá este cliente en la PC que tiene el micrófono y dejalo corriendo. En Windows, abrí PowerShell:')}</p>
          <pre class="buddy-cmd">py -m pip install sounddevice pynput
iwr ${esc(location.origin)}/clients/dani.py -OutFile dani.py
py dani.py --server ${esc(location.origin)} --key f9</pre>
          <p class="hint">${t('Mientras mantenés F9 apretado graba; al soltarlo manda el audio. Podés cambiar <code>f9</code> por <code>ctrl_r</code>, otra tecla F o una letra.')}</p>
          <p class="hint">${t('Si el panel tiene contraseña, agregá <code>--token</code> con uno creado en Configuración, Acceso.')}</p>
        </div>
        <div class="field"><span>${t('Oído')}</span>
          <p class="hint">${t('Vocabulario que recibe Whisper para escribir bien los nombres. Se arma solo: el nombre del compañero, tu zona, los lugares que consultaste, Home Assistant, la memoria y lo aprendido.')}</p>
          <p class="mono buddy-vocab" id="buddy-hearing-vocab"></p>
          <div id="buddy-hearing-corr" class="buddy-tools"></div>
          <label class="check"><input type="checkbox" id="buddy-cfg-keep-audio"> ${t('Guardar mis últimos 50 audios (solo en esta máquina) para comparar con y sin vocabulario')}</label>
          <span class="path-row"><button type="button" class="btn ghost small" id="buddy-hearing-compare">${t('Comparar ahora')}</button>
            <button type="button" class="btn ghost small" id="buddy-hearing-delete">${t('Borrar audios')}</button></span>
          <div id="buddy-hearing-result" class="buddy-tools"></div>
        </div>
        <label class="field"><span>${t('Memoria')}</span>
          <textarea id="buddy-mem" rows="6" spellcheck="false"></textarea>
          <p class="hint">${t('Lo que anota con «recordar». Se lee al empezar cada charla.')}</p>
        </label>
        <button class="btn ghost" id="buddy-mem-save">${t('Guardar memoria')}</button>
      </details>
    </div>`;
  drawn = true;
  fillSettings();
  bind();
}

function fillSettings() {
  const c = B.config;
  $('#buddy-name').textContent = c.name;
  $('#buddy-speak').checked = c.speak && B.voice.tts;
  $('#buddy-speak').disabled = !B.voice.tts;
  $('#buddy-voice').textContent = [
    B.voice.stt ? t('oído: Whisper') : t('sin oído (falta whisper.cpp)'),
    B.voice.tts ? t('voz: Piper') : t('sin voz (falta Piper)'),
  ].join(' · ');
  $('#buddy-cfg-name').value = c.name;
  $('#buddy-cfg-language').value = c.language || 'es';
  renderSections();
  renderTest();
  renderHearing();
  renderConversations();
  const models = (S.config?.text_models || []).map(m => m.id);
  $('#buddy-cfg-model').innerHTML = `<option value="">${esc(t('El que esté cargado'))}</option>` +
    models.map(m => `<option ${m === c.model ? 'selected' : ''}>${esc(m)}</option>`).join('');
  $('#buddy-cfg-tools').innerHTML = B.tools.map(tl => `
    <label class="check" title="${esc(tl.description)}"><input type="checkbox" value="${esc(tl.name)}" ${c.tools.includes(tl.name) ? 'checked' : ''}>
      <span class="mono">${esc(tl.name)}</span> <span class="muted">${esc(t(LEVELS[tl.level] || tl.level))}${tl.available ? '' : ' · ' + esc(t('no disponible'))}</span></label>`).join('');
  $('#buddy-mem').value = B.memory;
  $('#buddy-cfg-alerts').checked = c.alerts;
  $('#buddy-cfg-morning').value = c.morning || '';
  $('#buddy-share-location').checked = localStorage.getItem('buddy-share-location') === '1';
  updateHotkeyUI();
  renderTimers();
  const ha = B.ha;
  $('#buddy-ha-state').textContent = !ha.configured ? `· ${t('sin conectar')} (${ha.url})` : ha.ok ? `· ✓ ${t('conectado')}` : `· ✗ ${ha.msg}`;
  $('#buddy-brave-state').textContent = B.web_search.configured ? `· ✓ ${t('configurada')}` : `· ${t('sin configurar')} · DuckDuckGo`;
}

// Secciones del prompt (llama_panel/buddy_prompt.py): las editables muestran lo propio o, si no hay, la de fábrica
function renderSections() {
  const box = $('#buddy-cfg-sections');
  if (!box || !B.sections) return;
  box.innerHTML = B.sections.map(s => s.editable ? `
    <details class="buddy-section" data-name="${esc(s.name)}">
      <summary>${esc(s.title)} <span class="muted" data-state></span></summary>
      <textarea rows="6" spellcheck="false" data-section="${esc(s.name)}">${esc(s.value || s.default)}</textarea>
      <button type="button" class="btn ghost small" data-default>${t('Volver a la de fábrica')}</button>
    </details>` : `
    <details class="buddy-section fixed">
      <summary>${esc(s.title)} <span class="muted">· ${t('fija')}</span></summary>
      <p class="hint buddy-fixed">${esc(s.default)}</p>
    </details>`).join('');
  for (const d of $$('#buddy-cfg-sections details[data-name]')) {
    const s = B.sections.find(x => x.name === d.dataset.name);
    const area = $('textarea', d);
    const mark = () => { $('[data-state]', d).textContent = area.value.trim() === s.default.trim() ? '' : `· ${t('modificada')}`; };
    area.oninput = mark;
    $('[data-default]', d).onclick = () => { area.value = s.default; mark(); };
    mark();
  }
}

function renderTest() {
  const box = $('#buddy-test-result');
  const s = B?.test;
  if (!box || !s) return;
  if (s.running) { box.innerHTML = `<p class="muted">${esc(t('Probando… {done} de {total}', { done: s.done, total: s.total || 10 }))}</p>`; return; }
  if (s.error) { box.innerHTML = `<p class="bad">${esc(s.error)}</p>`; return; }
  if (!s.result) { box.innerHTML = ''; return; }
  const r = s.result;
  box.innerHTML = `<p><b>${esc(t('{ok} de {total} bien', { ok: r.ok, total: r.total }))}</b></p>` +
    r.fails.map(f => `<p class="hint">✗ «${esc(f.msg)}»: ${esc(t('esperaba'))} ${esc(f.expect)}; ${esc(t('hizo'))}: ${esc(f.got || '—')}</p>`).join('');
}

// Oído (llama_panel/hearing.py): vocabulario automático, correcciones aprendidas y comparación con la voz real
function renderHearing() {
  const h = B?.hearing;
  if (!h || !$('#buddy-hearing-vocab')) return;
  $('#buddy-hearing-vocab').textContent = (h.vocabulary || []).join(', ') || '—';
  const corr = (h.corrections || []).map(c => `
    <div class="path-row"><span>«${esc(c.from)}» → <b>${esc(c.to)}</b> <span class="muted">(${c.count}×)</span></span>
      <button type="button" class="btn ghost small" data-forget="${esc(c.from)}">${t('Borrar')}</button></div>`).join('');
  const pending = (h.pending || []).map(c => `<span class="muted">«${esc(c.from)}» → ${esc(c.to)}</span>`).join(' · ');
  $('#buddy-hearing-corr').innerHTML =
    `<p class="hint">${t('Correcciones aprendidas: cuando el modelo entiende un nombre que Whisper escribió mal, lo anota; a la segunda vez se aplica sola.')}</p>` +
    (corr || `<p class="muted">${t('Todavía ninguna.')}</p>`) +
    (pending ? `<p class="hint">${t('En espera (vistas una vez):')} ${pending}</p>` : '');
  for (const b of $$('#buddy-hearing-corr [data-forget]')) {
    b.onclick = async () => { report(await post('/api/buddy/hearing/forget', { from: b.dataset.forget })); B = await getJSON('/api/buddy'); renderHearing(); };
  }
  $('#buddy-cfg-keep-audio').checked = h.keep_audio;
  const c = h.compare;
  $('#buddy-hearing-result').innerHTML = h.comparing ? `<p class="muted">${t('Comparando…')}</p>` : !c ? `<p class="muted">${esc(t('{n} audios guardados.', { n: h.audios }))}</p>` :
    `<p class="hint">${esc(t('{n} audios guardados. Última comparación ({m} audios): cambió en {changed}; nombres del vocabulario {sin} sin y {con} con.', {
      n: h.audios, m: c.n, changed: c.changed, sin: c.terms_sin, con: c.terms_con }))}</p>` +
    (h.compare_rows || []).map(r => `<p class="hint">${t('sin')}: ${esc(r.sin)}<br>${t('con')}: <b>${esc(r.con)}</b></p>`).join('');
}

function sectionValues() {
  const out = {};
  for (const a of $$('#buddy-cfg-sections textarea[data-section]')) out[a.dataset.section] = a.value;
  return out;
}

function renderConversations() {
  const select = $('#buddy-conversation');
  if (!select || !B) return;
  select.innerHTML = B.conversations.map(x =>
    `<option value="${esc(x.id)}">${esc(t(x.title))}</option>`).join('');
  select.value = B.active_conversation;
}

function renderHistory() {
  if (abort) return;  // no pisar la respuesta que se esta escribiendo
  $('#buddy-log').innerHTML = B.history.map(m => bubble(m.role, m.content, m.tools, m.spont ? '💬' : '', m.source)).join('') ||
    `<p class="muted buddy-empty">${esc(t('Decile hola a {name}.', { name: B.config.name }))}</p>`;
  scroll();
}

function bubble(role, text, tools = [], icon = '', source = '') {
  const chips = tools.map(n => `<span class="buddy-tool">🔧 ${esc(n)}</span>`).join('');
  if (!text && !chips) return '';
  const tag = icon ? `<span class="buddy-tool" title="${esc(t('Lo dijo por su cuenta'))}">${icon}</span>` :
    source === 'pc' ? `<span class="buddy-tool" title="${esc(t('Enviado desde el cliente de PC'))}">PC</span>` : '';
  return `<div class="buddy-msg ${role}${icon ? ' spont' : ''}">${tag}${chips}${text ? `<div class="buddy-text">${esc(text)}</div>` : ''}</div>`;
}

const scroll = () => { const l = $('#buddy-log'); l.scrollTop = l.scrollHeight; };

function bind() {
  $('#buddy-send').onclick = () => send({ text: $('#buddy-text').value });
  $('#buddy-text').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send({ text: e.target.value }); } };
  $('#buddy-stop').onclick = () => { abort?.abort(); stopAudio(); };
  $('#buddy-reset').onclick = async e => { report(await withBusy(e.currentTarget, () => post('/api/buddy/reset'))); loadBuddy(); };
  $('#buddy-conversation').onchange = async e => {
    const id = e.target.value;
    report(await withBusy(e.currentTarget, () => post('/api/buddy/conversation/use', { id })));
    await loadBuddy();
  };
  $('#buddy-conversation-delete').onclick = async e => {
    if (!e.currentTarget.dataset.armed) {
      e.currentTarget.dataset.armed = '1';
      e.currentTarget.textContent = t('¿Eliminar?');
      setTimeout(() => { if (e.currentTarget.dataset.armed) {
        delete e.currentTarget.dataset.armed; e.currentTarget.textContent = '×';
      } }, 3000);
      return;
    }
    delete e.currentTarget.dataset.armed;
    report(await withBusy(e.currentTarget, () => post('/api/buddy/conversation/delete', { id: B.active_conversation })));
    await loadBuddy();
  };
  $('#buddy-cfg-language').onchange = async e => {
    // las de fábrica cambian con el idioma: se guarda y se vuelven a dibujar
    report(await post('/api/buddy/config', { language: e.target.value }));
    B = await getJSON('/api/buddy'); renderSections();
  };
  const cfgBody = () => ({
    name: $('#buddy-cfg-name').value, sections: sectionValues(), language: $('#buddy-cfg-language').value,
    model: $('#buddy-cfg-model').value,
    speak: $('#buddy-speak').checked, tools: $$('#buddy-cfg-tools input:checked').map(i => i.value),
    alerts: $('#buddy-cfg-alerts').checked, morning: $('#buddy-cfg-morning').value,
    keep_audio: $('#buddy-cfg-keep-audio').checked,
  });
  $('#buddy-cfg-save').onclick = async e => {
    report(await withBusy(e.currentTarget, () => post('/api/buddy/config', cfgBody())));
    B = await getJSON('/api/buddy'); fillSettings();
  };
  $('#buddy-cfg-keep-audio').onchange = e => post('/api/buddy/config', { keep_audio: e.target.checked });
  $('#buddy-hearing-compare').onclick = async e => {
    report(await withBusy(e.currentTarget, () => post('/api/buddy/hearing/compare')));
    for (;;) {
      B = await getJSON('/api/buddy'); renderHearing();
      if (!B.hearing.comparing) break;
      await new Promise(r => setTimeout(r, 2000));
    }
  };
  $('#buddy-hearing-delete').onclick = async e => {
    report(await withBusy(e.currentTarget, () => post('/api/buddy/hearing/audios/delete')));
    B = await getJSON('/api/buddy'); renderHearing();
  };
  // «Guardar y probar»: guarda y corre la versión corta de las pruebas (llama_panel/buddy_eval.py) con eso
  $('#buddy-cfg-test').onclick = async e => {
    const btn = e.currentTarget;
    const saved = await post('/api/buddy/config', cfgBody());
    if (!saved.ok) { report(saved); return; }
    const started = await post('/api/buddy/test');
    if (!started.ok) { report(started); return; }
    btn.disabled = true;
    try {
      for (;;) {
        await new Promise(r => setTimeout(r, 1500));
        B = await getJSON('/api/buddy');
        renderTest();
        if (!B.test.running) break;
      }
    } finally { btn.disabled = false; }
  };
  $('#buddy-speak').onchange = () => post('/api/buddy/config', { speak: $('#buddy-speak').checked });
  $('#buddy-share-location').onchange = e => localStorage.setItem('buddy-share-location', e.target.checked ? '1' : '0');
  $('#buddy-hotkey-record').onclick = () => {
    capturingHotkey = true;
    $('#buddy-hotkey-label').textContent = t('Presioná la combinación… (Escape cancela)');
  };
  $('#buddy-hotkey-clear').onclick = () => { capturingHotkey = false; saveHotkey({ scope: 'all' }); };
  $('#buddy-hotkey-scope').onchange = e => {
    const h = hotkeyValue();
    if (h.code) saveHotkey({ ...h, scope: e.target.value });
  };
  $('#buddy-ha-save').onclick = async e => {
    report(await withBusy(e.currentTarget, () => post('/api/buddy/ha', { token: $('#buddy-ha-token').value })));
    $('#buddy-ha-token').value = '';
    B = await getJSON('/api/buddy'); fillSettings();
  };
  $('#buddy-brave-save').onclick = async e => {
    report(await withBusy(e.currentTarget, () => post('/api/buddy/brave', { token: $('#buddy-brave-token').value })));
    $('#buddy-brave-token').value = '';
    B = await getJSON('/api/buddy'); fillSettings();
  };
  $('#buddy-mem-save').onclick = async e => report(await withBusy(e.currentTarget, () => post('/api/buddy/memory', { text: $('#buddy-mem').value })));

  const mic = $('#buddy-mic');
  mic.onpointerdown = e => { e.preventDefault(); startRec(); };
  mic.onpointerup = mic.onpointerleave = () => stopRec();
}

// ---------- microfono ----------
async function startRec(fromHotkey = false) {
  if (rec || !window.isSecureContext) return;
  stopAudio(); abort?.abort();  // hablarle encima lo calla
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch { toast(t('No hay permiso para usar el micrófono.'), 'error'); return; }
  if (fromHotkey && !hotkeyDown) { stream.getTracks().forEach(tr => tr.stop()); return; }
  const chunks = [];
  const mr = new MediaRecorder(stream);
  rec = { mr, stream, t0: Date.now() };
  mr.ondataavailable = e => chunks.push(e.data);
  mr.onstop = async () => {
    stream.getTracks().forEach(tr => tr.stop());
    if (Date.now() - rec.t0 < 300) { rec = null; return; }  // un toque, no una frase
    rec = null;
    const blob = new Blob(chunks, { type: mr.mimeType });
    const b64 = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result.split(',')[1]); r.readAsDataURL(blob); });
    send({ audio: b64 });
  };
  mr.start();
  $('#buddy-mic').classList.add('on');
  fab('listening', t('Te escucho…'));
}

function stopRec() {
  if (!rec || rec.mr.state !== 'recording') return;
  $('#buddy-mic').classList.remove('on');
  rec.mr.stop();
  if (Date.now() - rec.t0 < 300) fab('idle', '');
}

// ---------- audio de la respuesta ----------
const updStop = () => { $('#buddy-stop').hidden = !(abort || playing || audioQueue.length); };

function playNext() {
  updStop();
  if (playing || !audioQueue.length) return;
  const url = audioQueue.shift();
  playing = new Audio(url);
  playing.onended = playing.onerror = () => {
    URL.revokeObjectURL(url); playing = null; playNext();
    if (!playing && !abort) fab('idle', $('#buddy-fab .buddy-fab-bubble span')?.textContent || '');
  };
  playing.play().catch(() => { playing = null; updStop(); });
}

function stopAudio() {
  audioQueue.splice(0).forEach(u => URL.revokeObjectURL(u));
  if (playing) { playing.pause(); playing = null; }
  updStop();
}

const wavUrl = b64 => URL.createObjectURL(new Blob([Uint8Array.from(atob(b64), c => c.charCodeAt(0))], { type: 'audio/wav' }));

// ---------- una charla ----------
async function send(body) {
  if (body.text !== undefined && !body.text.trim()) return;
  if (abort) abort.abort();
  $('#buddy-text').value = '';
  $('.buddy-empty')?.remove();
  const log = $('#buddy-log');
  let mine = null;
  if (body.text) { log.insertAdjacentHTML('beforeend', bubble('user', body.text)); }
  else { log.insertAdjacentHTML('beforeend', `<div class="buddy-msg user pending"><div class="buddy-text">${esc(t('escuchando…'))}</div></div>`); mine = log.lastElementChild; }
  log.insertAdjacentHTML('beforeend', `<div class="buddy-msg assistant"><span class="muted buddy-typing">…</span></div>`);
  const out = log.lastElementChild;
  let text = '', txtEl = null;
  const stats = [];
  let said = '';  // toda la respuesta, para la burbuja flotante
  scroll();
  fab('thinking', body.text ? t('Pensando…') : t('Escuchando lo que dijiste…'));
  abort = new AbortController();
  $('#buddy-stop').hidden = false;
  try {
    const userContext = { language: navigator.language || '', timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '' };
    if (localStorage.getItem('buddy-share-location') === '1' && navigator.geolocation) {
      try {
        const pos = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve, reject,
          { enableHighAccuracy: false, timeout: 3500, maximumAge: 300000 }));
        userContext.location = { latitude: Math.round(pos.coords.latitude * 100) / 100,
          longitude: Math.round(pos.coords.longitude * 100) / 100 };
      } catch { /* permiso denegado o ubicación no disponible: el backend recibe sólo idioma/zona horaria */ }
    }
    const r = await fetch('/api/buddy/chat', {
      method: 'POST', signal: abort.signal,
      headers: { 'Content-Type': 'application/json', 'X-Lang': lang() },
      body: JSON.stringify({ ...body, speak: $('#buddy-speak').checked, client: CLIENT, user_context: userContext }),
    });
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === 'heard' && mine) {
          mine.classList.remove('pending');
          $('.buddy-text', mine).textContent = ev.text || '…';
          if (ev.raw) mine.title = t('Whisper escribió: {raw}', { raw: ev.raw });
          stats.push(t('oído en {s} s', { s: ev.stt_s }));
          if (ev.text) fab('user', ev.text);
        } else if (ev.type === 'text') {
          $('.buddy-typing', out)?.remove();
          if (!txtEl) { out.insertAdjacentHTML('beforeend', '<div class="buddy-text"></div>'); txtEl = out.lastElementChild; }
          text += ev.delta; txtEl.textContent = text;
          said += ev.delta; fab('speaking', said);
        } else if (ev.type === 'tool') {
          const chip = document.createElement('span');
          chip.className = 'buddy-tool' + (ev.ok ? '' : ' bad');
          chip.textContent = '🔧 ' + ev.name; chip.title = `${ev.args}\n→ ${ev.result}`;
          if (!said) fab('thinking', '🔧 ' + ev.name); else said += '\n';
          out.insertBefore(chip, txtEl || out.querySelector('.buddy-typing'));
          txtEl = null; text = '';  // lo que diga despues va en un renglon nuevo
        } else if (ev.type === 'learned') {
          toast(t('Aprendí cómo se escribe un nombre que no entendí bien.'));
        } else if (ev.type === 'first_token') {
          stats.push(t('primera palabra a los {s} s', { s: ev.s }));
        } else if (ev.type === 'audio') {
          audioQueue.push(wavUrl(ev.wav)); playNext();
        } else if (ev.type === 'error') {
          out.insertAdjacentHTML('beforeend', `<div class="buddy-text bad">${esc(ev.msg)}</div>`);
          said = ev.msg; fab('error', ev.msg);
        }
        scroll();
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') toast(t('No se pudo contactar al panel.'), 'error');
  } finally {
    $('.buddy-typing', out)?.remove();
    abort = null;
    updStop();
    if (!playing) fab('idle', said);
    $('#buddy-stats').textContent = stats.join(' · ');
  }
}
