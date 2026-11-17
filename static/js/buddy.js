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
const HOTKEY_KEY = 'buddy-push-to-talk';
const HOTKEYS = {
  off: { label: 'Desactivado' },
  buddy_space: { code: 'Space', scope: 'buddy', label: 'Espacio (sólo en Compañero)' },
  all_ctrl_shift_space: { code: 'Space', ctrl: true, shift: true, scope: 'all', label: 'Ctrl + Shift + Espacio (todo el panel)' },
  all_f9: { code: 'F9', scope: 'all', label: 'F9 (todo el panel)' },
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

const LEVELS = { lectura: 'solo mira', memoria: 'escribe su memoria', accion: 'cambia cosas', peligrosa: 'peligrosa' };

export async function loadBuddy() {
  try { B = await getJSON('/api/buddy'); } catch { return; }
  if (!drawn) draw();
  renderHistory();
}

// Al arrancar el panel: la pestana se arma oculta (la charla funciona desde cualquier lado) y aparece el boton flotante
export async function initBuddy() {
  await loadBuddy();
  if (!B) return;
  drawFab();
  listen();
  addEventListener('keydown', e => {
    if (e.repeat || !hotkeyMatches(e) || isInteractive(e.target)) return;
    e.preventDefault();
    hotkeyDown = true;
    startRec(true);
  });
  addEventListener('keyup', e => {
    const hotkey = HOTKEYS[hotkeyValue()];
    if (!hotkeyDown || !hotkey?.code || e.code !== hotkey.code) return;
    e.preventDefault();
    hotkeyDown = false;
    stopRec();
  });
}

function hotkeyValue() {
  try {
    const value = localStorage.getItem(HOTKEY_KEY);
    return HOTKEYS[value] ? value : 'off';
  } catch { return 'off'; }
}

function hotkeyMatches(e) {
  const h = HOTKEYS[hotkeyValue()];
  return h?.code && (h.scope === 'all' || S.tab === 'buddy') && e.code === h.code &&
    !!e.ctrlKey === !!h.ctrl && !!e.shiftKey === !!h.shift && !!e.altKey === !!h.alt && !!e.metaKey === !!h.meta;
}

function isInteractive(target) {
  return target instanceof Element &&
    !!target.closest('input, textarea, select, button, a, [contenteditable], [role="button"]');
}

function hotkeyLabel() {
  return t(HOTKEYS[hotkeyValue()].label);
}

function micTitle() {
  return hotkeyValue() === 'off' ? t('Mantené apretado para hablar') :
    t('Mantené apretado para hablar · Atajo: {key}', { key: hotkeyLabel() });
}

// Lo que Dani dice por su cuenta (timers, avisos, el resumen de la manana) llega por server-sent events
function listen() {
  const es = new EventSource('/api/buddy/events?client=' + encodeURIComponent(CLIENT));
  es.onmessage = e => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
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
  };
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
          <span class="spacer"></span>
          <label class="check"><input type="checkbox" id="buddy-speak"> ${t('Hablar en voz alta')}</label>
          <button class="btn ghost small" id="buddy-reset">${t('Charla nueva')}</button>
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
        <label class="field"><span>${t('Personalidad')}</span>
          <textarea id="buddy-cfg-persona" rows="9" spellcheck="false"></textarea>
          <p class="hint">${t('<code>{name}</code> se reemplaza por el nombre. Se lee en voz alta: conviene pedirle respuestas cortas.')}</p>
          <button class="btn ghost small" id="buddy-cfg-default">${t('Volver a la personalidad de fábrica')}</button>
        </label>
        <div class="field"><span>${t('Herramientas')}</span><div id="buddy-cfg-tools" class="buddy-tools"></div></div>
        <label class="field"><span>${t('Atajo para hablar en este navegador')}</span>
          <select id="buddy-hotkey">${Object.entries(HOTKEYS).map(([value, h]) =>
            `<option value="${value}">${esc(t(h.label))}</option>`).join('')}</select>
          <p class="hint">${t('Desactivado por defecto. Los atajos nunca se capturan mientras usás un campo, botón o enlace. Para hablar desde cualquier programa usá dani.py.')}</p>
        </label>
        <label class="check"><input type="checkbox" id="buddy-share-location"> ${t('Compartir ubicación aproximada con el compañero')}</label>
        <div class="field"><span>${t('Por su cuenta')}</span>
          <label class="check"><input type="checkbox" id="buddy-cfg-alerts"> ${t('Avisar si la GPU o la CPU calientan, se cae un servicio o falta RAM o disco')}</label>
          <label class="check">${t('Resumen de la mañana a las')} <input type="time" id="buddy-cfg-morning"></label>
          <p class="hint">${t('El resumen lo escribe el modelo si ya hay uno cargado; si no, uno corto con los datos. Vacío = sin resumen.')}</p>
        </div>
        <button class="btn" id="buddy-cfg-save">${t('Guardar')}</button>
        <div class="field"><span>${t('Avisos pendientes')}</span><div id="buddy-timers" class="buddy-tools"></div></div>
        <label class="field"><span>Home Assistant <span class="muted" id="buddy-ha-state"></span></span>
          <input type="password" id="buddy-ha-token" autocomplete="off" placeholder="${esc(t('token de acceso de larga duración'))}">
          <p class="hint">${t('En Home Assistant: tu perfil, Seguridad, «Tokens de acceso de larga duración». Mejor de un usuario que no sea administrador. Queda en un archivo aparte, solo legible por tu usuario.')}</p>
        </label>
        <button class="btn ghost" id="buddy-ha-save">${t('Guardar token')}</button>
        <div class="field"><span>${t('Desde tu PC, con una tecla')}</span>
          <p class="hint">${t('Para hablarle desde cualquier programa (sin el navegador): mantené apretado Ctrl derecho. En Windows, en PowerShell:')}</p>
          <pre class="buddy-cmd">py -m pip install sounddevice pynput
iwr ${esc(location.origin)}/clients/dani.py -OutFile dani.py
py dani.py --server ${esc(location.origin)}</pre>
          <p class="hint">${t('Si el panel tiene contraseña, agregá <code>--token</code> con uno creado en Configuración, Acceso.')}</p>
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
  $('#buddy-cfg-persona').value = c.persona;
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
  $('#buddy-hotkey').value = hotkeyValue();
  renderTimers();
  const ha = B.ha;
  $('#buddy-ha-state').textContent = !ha.configured ? `· ${t('sin conectar')} (${ha.url})` : ha.ok ? `· ✓ ${t('conectado')}` : `· ✗ ${ha.msg}`;
}

function renderHistory() {
  if (abort) return;  // no pisar la respuesta que se esta escribiendo
  $('#buddy-log').innerHTML = B.history.map(m => bubble(m.role, m.content, m.tools, m.spont ? '💬' : '')).join('') ||
    `<p class="muted buddy-empty">${esc(t('Decile hola a {name}.', { name: B.config.name }))}</p>`;
  scroll();
}

function bubble(role, text, tools = [], icon = '') {
  const chips = tools.map(n => `<span class="buddy-tool">🔧 ${esc(n)}</span>`).join('');
  if (!text && !chips) return '';
  const tag = icon ? `<span class="buddy-tool" title="${esc(t('Lo dijo por su cuenta'))}">${icon}</span>` : '';
  return `<div class="buddy-msg ${role}${icon ? ' spont' : ''}">${tag}${chips}${text ? `<div class="buddy-text">${esc(text)}</div>` : ''}</div>`;
}

const scroll = () => { const l = $('#buddy-log'); l.scrollTop = l.scrollHeight; };

function bind() {
  $('#buddy-send').onclick = () => send({ text: $('#buddy-text').value });
  $('#buddy-text').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send({ text: e.target.value }); } };
  $('#buddy-stop').onclick = () => { abort?.abort(); stopAudio(); };
  $('#buddy-reset').onclick = async e => { report(await withBusy(e.currentTarget, () => post('/api/buddy/reset'))); loadBuddy(); };
  $('#buddy-cfg-default').onclick = () => { $('#buddy-cfg-persona').value = B.default_persona; };
  $('#buddy-cfg-save').onclick = async e => {
    const body = {
      name: $('#buddy-cfg-name').value, persona: $('#buddy-cfg-persona').value, model: $('#buddy-cfg-model').value,
      speak: $('#buddy-speak').checked, tools: $$('#buddy-cfg-tools input:checked').map(i => i.value),
      alerts: $('#buddy-cfg-alerts').checked, morning: $('#buddy-cfg-morning').value,
    };
    report(await withBusy(e.currentTarget, () => post('/api/buddy/config', body)));
    B = await getJSON('/api/buddy'); fillSettings();
  };
  $('#buddy-speak').onchange = () => post('/api/buddy/config', { speak: $('#buddy-speak').checked });
  $('#buddy-share-location').onchange = e => localStorage.setItem('buddy-share-location', e.target.checked ? '1' : '0');
  $('#buddy-hotkey').onchange = e => {
    localStorage.setItem(HOTKEY_KEY, e.target.value);
    const title = micTitle();
    $('#buddy-mic').title = title;
    $('#buddy-fab .buddy-fab-mic').title = title;
  };
  $('#buddy-ha-save').onclick = async e => {
    report(await withBusy(e.currentTarget, () => post('/api/buddy/ha', { token: $('#buddy-ha-token').value })));
    $('#buddy-ha-token').value = '';
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
