// Motor de audio (CPU): tarjeta en la cabecera y pestana "Modelos de audio" (Whisper para oir, Piper para hablar).
import { $, $$, ago, el, esc, gb, post, report, setStatus, toast, withBusy } from './util.js';
import { S, hooks } from './state.js';
import { t } from './i18n.js';

let preview = null;

export function renderAudioEngine() {
  const a = S.state?.audio, card = $('#eng-audio');
  if (!a || !card) return;
  const ready = a.stt_ok && a.tts_ok;
  card.classList.toggle('on', ready);
  setStatus($('[data-status]', card), ready ? ['good', 'Listo'] : a.stt_ok || a.tts_ok ? ['warning', 'Incompleto'] : ['', 'Sin instalar']);
  $('[data-stt]', card).textContent = a.stt_ok ? a.stt.replace(/^ggml-|\.bin$/g, '') : t('falta whisper.cpp');
  $('[data-tts]', card).textContent = a.tts_ok ? a.tts : t('falta Piper');
  const parts = [];
  if (a.last.stt) parts.push(t('oído {s} s', { s: a.last.stt.s }) + ' · ' + ago(a.last.stt.t));
  if (a.last.tts) parts.push(t('voz {s} s', { s: a.last.tts.s }));
  $('[data-last]', card).textContent = parts.join(' · ') || '–';
  $('[data-audio-preview]', card).disabled = !a.tts_ok;
}

export function renderAudioModels() {
  const c = S.config;
  if (!c?.audio_models) return;
  const box = $('#audio-models');
  const sel = c.audio_selected;
  const card = (kind, m) => {
    const used = sel[kind] === m.path;
    const meta = [gb(m.size), m.lang, m.quality].filter(Boolean).map(x => `<span class="badge">${esc(x)}</span>`).join('');
    return `
      <div class="card img-card ${used ? 'selected' : ''}" data-kind="${kind}" data-path="${esc(m.path)}">
        <div class="model-name" title="${esc(m.path)}">${esc(m.id)}</div>
        <span class="badge accent sel-tag" ${used ? '' : 'hidden'}>${t('En uso')}</span>
        <div class="model-meta">${meta}</div>
        <div class="foot">
          ${kind === 'tts' ? `<button class="btn ghost small" data-preview>${t('Escuchar')}</button>` : ''}
          <button class="btn small" data-use ${used ? 'disabled' : ''}>${t('Usar este')}</button>
        </div>
      </div>`;
  };
  const group = (kind, title, hint, empty) => `
    <h3>${title}</h3>
    <p class="hint">${hint}</p>
    <div class="img-grid">${c.audio_models[kind].map(m => card(kind, m)).join('') || `<div class="card empty">${empty}</div>`}</div>`;
  box.innerHTML =
    group('stt', t('Oído: Whisper'), t('Pasa tu voz a texto en el CPU. small es rápido (~0.7 s para 4 s de audio); large-v3-turbo entiende mejor pero tarda ~3 veces más.'), t('No hay modelos <code>ggml-*.bin</code> de whisper.cpp.')) +
    group('tts', t('Voz: Piper'), t('Lee las respuestas en voz alta, en el CPU (~0.2 s por oración).'), t('No hay voces de Piper (<code>.onnx</code> con su <code>.onnx.json</code>).')) +
    `<p class="hint">${t('Carpetas: {dirs}. Otras se agregan con <code>LLAMA_AUDIO_DIRS</code> en paths.env.', { dirs: c.audio_dirs.map(d => `<code>${esc(d)}</code>`).join(', ') })}</p>
     <p class="hint">${t('Otros programas pueden usar esta voz con la API de OpenAI: <code>base_url</code> <code>http://{host}:{port}/v1</code>, rutas <code>/audio/speech</code> y <code>/audio/transcriptions</code>.', { host: esc(location.hostname), port: esc(location.port || '80') })}</p>`;
  for (const cardEl of $$('#audio-models [data-path]')) {
    const { kind, path } = cardEl.dataset;
    $('[data-use]', cardEl).onclick = async e => {
      report(await withBusy(e.currentTarget, () => post('/api/audio/use', { kind, path })));
      await hooks.loadConfig(); hooks.pollState();
    };
    const pv = $('[data-preview]', cardEl);
    if (pv) pv.onclick = e => playPreview(path, e.currentTarget);
  }
}

export async function playPreview(path, btn) {
  const res = await withBusy(btn, () => post('/api/audio/preview', path ? { path } : {}));
  if (!res.ok) return report(res);
  if (preview) preview.pause();
  const bytes = Uint8Array.from(atob(res.wav), ch => ch.charCodeAt(0));
  preview = new Audio(URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' })));
  preview.play().catch(() => toast(t('El navegador no dejó reproducir el audio.'), 'error'));
}
