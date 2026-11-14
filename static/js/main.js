// Arranque: ciclos de consulta, pestanas y botones globales.
import { $, $$, getJSON, now, post, report, withBusy } from './util.js';
import { S, clearDirty, hooks, setDirty } from './state.js';
import { HIST_WINDOW, renderTiles } from './tiles.js';
import { renderEngines } from './engines.js';
import { renderTextModels, updateTextStates } from './text-models.js';
import { renderImgModels, updateImgStates } from './img-models.js';
import { save } from './save.js';
import { renderPerf } from './perf.js';
import { loadLogs } from './logs.js';

async function pollState() {
  try {
    S.state = await getJSON('/api/state');
    $('#conn').hidden = true;
    const h = S.state.hw;
    if (h && (!S.hist.length || h.t > S.hist[S.hist.length - 1].t)) S.hist.push(h);
    const cut = now() - HIST_WINDOW - 5;
    while (S.hist.length && S.hist[0].t < cut) S.hist.shift();
    if (S.config && S.state.img.selected_key !== undefined && S.state.img.selected_key !== S.config.img_selected && !S.dirtyImg) {
      S.config.img_selected = S.state.img.selected_key;
    }
    renderTiles(); renderEngines(); updateTextStates(); updateImgStates();
  } catch {
    $('#conn').hidden = false;
  }
}

async function loadConfig() {
  S.config = await getJSON('/api/config');
  renderTextModels(); renderImgModels();
}

async function loadStats() {
  try { S.stats = await getJSON('/api/stats'); } catch { return; }
  if (S.tab === 'perf') renderPerf();
  renderEngines();
}

function showTab(tab) {
  if (!$(`[data-panel="${tab}"]`)) tab = 'text';
  S.tab = tab;
  for (const b of $$('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  for (const p of $$('[data-panel]')) p.hidden = p.dataset.panel !== tab;
  history.replaceState(null, '', '#' + tab);
  if (tab === 'perf') renderPerf();
  if (tab === 'logs') loadLogs();
}

function bind() {
  const host = location.hostname;
  $('#host').textContent = host;
  for (const b of $$('.tabs button')) b.onclick = () => showTab(b.dataset.tab);

  for (const b of $$('[data-action]')) {
    b.onclick = async () => {
      if (b.dataset.action === 'stop_all' && !b.dataset.armed) {
        b.dataset.armed = '1'; const txt = b.textContent; b.textContent = '¿Seguro? Tocá de nuevo';
        setTimeout(() => { delete b.dataset.armed; b.textContent = txt; }, 3000);
        return;
      }
      delete b.dataset.armed;
      const res = await withBusy(b, () => post('/api/action', { do: b.dataset.action }));
      if (b.dataset.action === 'stop_all') b.textContent = 'Apagar todo';
      report(res);
      pollState();
    };
  }

  $('#rescan').onclick = async e => {
    const res = await withBusy(e.currentTarget, () => post('/api/text/rescan'));
    report(res);
    if (!S.dirtyText) await loadConfig();
    pollState();
  };
  $('#global').addEventListener('input', () => setDirty('text'));
  $('#save').onclick = e => save(false, e.currentTarget);
  $('#save-apply').onclick = e => save(true, e.currentTarget);
  $('#discard').onclick = () => { clearDirty(); renderTextModels(); renderImgModels(); };
  addEventListener('beforeunload', e => { if (S.dirtyText || S.dirtyImg) e.preventDefault(); });

  for (const b of $$('#log-svc button')) {
    b.onclick = () => {
      S.logSvc = b.dataset.svc;
      for (const x of $$('#log-svc button')) x.setAttribute('aria-pressed', String(x === b));
      $('#logs').scrollTop = 1e9;
      loadLogs();
    };
  }
  $('#log-filter').addEventListener('input', loadLogs);
  addEventListener('resize', () => { if (S.tab === 'perf') renderPerf(); });
}

async function start() {
  bind();
  const host = location.hostname;
  try {
    const [cfg, hist] = await Promise.all([getJSON('/api/config'), getJSON('/api/history')]);
    S.config = cfg; S.hist = hist.samples || [];
    $('#lnk-chat').href = `http://${host}:${cfg.ports.chat}`;
    $('#lnk-sdui').href = `http://${host}:${cfg.ports.img}/sdui`;
    renderTextModels(); renderImgModels();
  } catch { $('#conn').hidden = false; }
  showTab(location.hash.slice(1) || 'text');
  await pollState();
  loadStats();

  setInterval(pollState, 2000);
  setInterval(loadStats, 8000);
  setInterval(() => { if (S.tab === 'logs' && $('#log-follow').checked) loadLogs(); }, 3000);
  // Si aparecen o desaparecen modelos en disco, refresca las listas (sin tocar un formulario a medio editar)
  setInterval(async () => {
    if (S.dirtyText || S.dirtyImg || document.visibilityState !== 'visible') return;
    try {
      const cfg = await getJSON('/api/config');
      const sig = c => JSON.stringify([c.text_models.map(m => m.key), c.img_models.map(m => m.key)]);
      if (sig(cfg) !== sig(S.config)) { S.config = cfg; renderTextModels(); renderImgModels(); }
    } catch { /* nada */ }
  }, 60000);
}

hooks.pollState = pollState;
hooks.loadConfig = loadConfig;
start();
