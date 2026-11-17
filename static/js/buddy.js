// Pestana Compañero: charla con el agente (llama_panel/buddy.py) por texto o voz.
// Voz: se graba mientras se mantiene apretado el boton (o la barra espaciadora) y el panel lo pasa a texto con
// Whisper; la respuesta llega en eventos y cada oracion trae su audio de Piper, que se reproduce en orden.
// Funciona desde cualquier pestana: la barra espaciadora vale en todo el panel y hay un boton flotante con el estado.
import { $, $$, esc, getJSON, post, report, toast, withBusy } from './util.js';
import { S } from './state.js';
import { lang, t } from './i18n.js';

let B = null;            // respuesta de /api/buddy
let drawn = false;
let rec = null;          // grabacion en curso
let abort = null;        // charla en curso (para cortarla)
const audioQueue = [];
let playing = null;

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
  addEventListener('keydown', e => {
    // barra espaciadora para hablar en todo el panel, salvo escribiendo en un campo
    if (e.code !== 'Space' || e.repeat || e.target.matches('input, textarea, select, [contenteditable]')) return;
    e.preventDefault(); startRec();
  });
  addEventListener('keyup', e => { if (e.code === 'Space' && rec) { e.preventDefault(); stopRec(); } });
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
      <button class="buddy-fab-mic" title="${esc(t('Mantené apretado para hablar (o la barra espaciadora)'))}">🎤</button>
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
            title="${esc(t('Mantené apretado para hablar (o la barra espaciadora)'))}">🎤</button>
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
        <button class="btn" id="buddy-cfg-save">${t('Guardar')}</button>
        <label class="field"><span>Home Assistant <span class="muted" id="buddy-ha-state"></span></span>
          <input type="password" id="buddy-ha-token" autocomplete="off" placeholder="${esc(t('token de acceso de larga duración'))}">
          <p class="hint">${t('En Home Assistant: tu perfil, Seguridad, «Tokens de acceso de larga duración». Mejor de un usuario que no sea administrador. Queda en un archivo aparte, solo legible por tu usuario.')}</p>
        </label>
        <button class="btn ghost" id="buddy-ha-save">${t('Guardar token')}</button>
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
  const ha = B.ha;
  $('#buddy-ha-state').textContent = !ha.configured ? `· ${t('sin conectar')} (${ha.url})` : ha.ok ? `· ✓ ${t('conectado')}` : `· ✗ ${ha.msg}`;
}

function renderHistory() {
  if (abort) return;  // no pisar la respuesta que se esta escribiendo
  $('#buddy-log').innerHTML = B.history.map(m => bubble(m.role, m.content, m.tools)).join('') ||
    `<p class="muted buddy-empty">${esc(t('Decile hola a {name}.', { name: B.config.name }))}</p>`;
  scroll();
}

function bubble(role, text, tools = []) {
  const chips = tools.map(n => `<span class="buddy-tool">🔧 ${esc(n)}</span>`).join('');
  if (!text && !chips) return '';
  return `<div class="buddy-msg ${role}">${chips}${text ? `<div class="buddy-text">${esc(text)}</div>` : ''}</div>`;
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
    };
    report(await withBusy(e.currentTarget, () => post('/api/buddy/config', body)));
    B = await getJSON('/api/buddy'); fillSettings();
  };
  $('#buddy-speak').onchange = () => post('/api/buddy/config', { speak: $('#buddy-speak').checked });
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
async function startRec() {
  if (rec || !window.isSecureContext) return;
  stopAudio(); abort?.abort();  // hablarle encima lo calla
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch { toast(t('No hay permiso para usar el micrófono.'), 'error'); return; }
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
    const r = await fetch('/api/buddy/chat', {
      method: 'POST', signal: abort.signal,
      headers: { 'Content-Type': 'application/json', 'X-Lang': lang() },
      body: JSON.stringify({ ...body, speak: $('#buddy-speak').checked }),
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
