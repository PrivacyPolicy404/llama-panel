// Tarjetas de los motores: Texto (llama-server) e Imagenes (KoboldCpp).
import { $, SVC, ago, dur, fix, setStatus } from './util.js';
import { S } from './state.js';
import { t } from './i18n.js';

export function renderEngines() {
  const st = S.state;
  if (!st) return;
  const textOn = st.svc.text === 'active';
  const imgOn = st.svc.img === 'active';

  const et = $('#eng-text');
  et.classList.toggle('on', textOn);
  setStatus($('[data-status]', et), SVC[st.svc.text] || ['', st.svc.text]);
  const loaded = Object.entries(st.text.models || {}).find(([, m]) => m.status === 'loaded' || m.status === 'loading');
  const ld = $('[data-loaded]', et);
  ld.textContent = !textOn ? '–' : loaded ? loaded[0] + (loaded[1].status === 'loading' ? ' ' + t('(cargando…)') : '') : t('ninguno (se carga al pedirlo)');
  ld.title = loaded ? loaded[0] : '';
  const last = S.stats?.text?.[S.stats.text.length - 1];
  $('[data-speed]', et).textContent = last ? `${fix(last.g_tps)} tok/s · ${ago(last.t)}` : '–';
  $('[data-action="start"]', et).disabled = textOn;
  $('[data-action="stop"]', et).disabled = st.svc.text === 'inactive';
  $('#lnk-chat').setAttribute('aria-disabled', String(!textOn));

  const ei = $('#eng-img');
  ei.classList.toggle('on', imgOn);
  setStatus($('[data-status]', ei), SVC[st.svc.img] || ['', st.svc.img]);
  const md = $('[data-model]', ei);
  md.textContent = st.img.selected || t('ninguno'); md.title = st.img.selected || '';
  const pr = st.img.progress, perf = st.img.perf;
  const bar = $('[data-progress]', ei);
  let act = '–';
  if (imgOn && pr && pr.steps > 0) {
    act = t('Generando · paso {step}/{steps}', { step: pr.step, steps: pr.steps }) + (pr.eta > 0 ? ' · ' + t('faltan {time}', { time: dur(pr.eta) }) : '');
    bar.hidden = false; $('span', bar).style.width = (pr.step / pr.steps * 100) + '%';
  } else {
    bar.hidden = true;
    if (imgOn && perf) act = perf.queue > 0 ? t('En cola: {n}', { n: perf.queue }) : t('Libre · {n} imágenes generadas', { n: perf.total_img_gens });
    else if (imgOn) act = t('Cargando el modelo…');
  }
  $('[data-activity]', ei).textContent = act;
  $('[data-action="img_start"]', ei).disabled = imgOn;
  $('[data-action="img_stop"]', ei).disabled = st.svc.img === 'inactive';
  $('#lnk-sdui').setAttribute('aria-disabled', String(!imgOn));
}
