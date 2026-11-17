// Arranque: ciclos de consulta, pestanas y botones globales.
import { $, $$, getJSON, now, post, report, withBusy } from './util.js';
import { S, clearDirty, hooks, setDirty } from './state.js';
import { HIST_WINDOW, renderTiles } from './tiles.js';
import { renderEngines } from './engines.js';
import { renderTextModels, updateTextStates, updateTextPerf } from './text-models.js';
import { renderImgModels, updateImgStates } from './img-models.js';
import { save } from './save.js';
import { renderPerf } from './perf.js';
import { loadLogs } from './logs.js';
import { loadSetup, needsSetup } from './setup.js';
import { lang, setLang, t, translateDom } from './i18n.js';

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
    renderTiles(); renderEngines(); updateTextStates(); updateImgStates(); renderAiMode();
  } catch {
    $('#conn').hidden = false;
  }
}

function renderAiMode() {
  const b = $('#ai-mode'), ai = S.state?.ai_mode;
  if (!b || !ai || b.dataset.armed || b.classList.contains('busy')) return;
  b.dataset.action = ai.active ? 'ai_mode_off' : 'ai_mode_on';
  b.textContent = ai.active ? t('Salir del modo IA') : t('Modo IA');
  b.dataset.confirm = ai.active ? '' : t('¿Apagar el escritorio? Tocá de nuevo');
  b.classList.toggle('on', ai.active);
  b.title = !ai.installed ? t('No instalado: correr una vez  sudo ~/llama-panel/scripts/install-modo-ia.sh')
    : !ai.active && ai.gpu_ok === false ? t('Falta acceso permanente a la GPU: correr de nuevo el instalador del modo IA y reiniciar')
    : ai.active ? t('Vuelve a prender el escritorio')
    : t('Apaga el escritorio para dejarle toda la VRAM a los modelos y muestra un dashboard en la pantalla');
}

async function loadConfig() {
  S.config = await getJSON('/api/config');
  renderTextModels(); renderImgModels();
}

async function loadStats() {
  try { S.stats = await getJSON('/api/stats'); } catch { return; }
  if (S.tab === 'perf') renderPerf();
  renderEngines();
  updateTextPerf();
}

function showTab(tab) {
  if (!$(`[data-panel="${tab}"]`)) tab = 'text';
  S.tab = tab;
  for (const b of $$('.tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  for (const p of $$('[data-panel]')) p.hidden = p.dataset.panel !== tab;
  history.replaceState(null, '', '#' + tab);
  if (tab === 'perf') renderPerf();
  if (tab === 'logs') loadLogs();
  if (tab === 'setup') loadSetup();
}

function bind() {
  const host = location.hostname;
  $('#host').textContent = host;
  for (const b of $$('.tabs button')) b.onclick = () => showTab(b.dataset.tab);
  for (const a of $$('[data-goto]')) a.onclick = e => { e.preventDefault(); showTab(a.dataset.goto); };

  for (const b of $$('[data-action]')) {
    b.onclick = async () => {
      // los botones con data-confirm piden un segundo toque antes de actuar
      if (b.dataset.confirm && !b.dataset.armed) {
        b.dataset.armed = '1'; b.dataset.label = b.textContent; b.textContent = b.dataset.confirm;
        setTimeout(() => { if (b.dataset.armed) { delete b.dataset.armed; b.textContent = b.dataset.label; } }, 3000);
        return;
      }
      const label = b.dataset.armed ? b.dataset.label : b.textContent;
      delete b.dataset.armed;
      const res = await withBusy(b, () => post('/api/action', { do: b.dataset.action }));
      b.textContent = label;
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
  translateDom(document.body);  // textos fijos de index.html
  const sel = $('#lang');
  sel.value = lang();
  sel.onchange = () => setLang(sel.value);  // guarda y recarga la pagina en el idioma nuevo
  bind();
  const host = location.hostname;
  try {
    const [cfg, hist] = await Promise.all([getJSON('/api/config'), getJSON('/api/history')]);
    S.config = cfg; S.hist = hist.samples || [];
    $('#lnk-chat').href = `http://${host}:${cfg.ports.chat}`;
    $('#lnk-sdui').href = `http://${host}:${cfg.ports.img}/sdui`;
    renderTextModels(); renderImgModels();
  } catch { $('#conn').hidden = false; }
  // primera vez (sin llama-server o sin modelos): mostrar la configuracion
  const setup = await loadSetup();
  $('#setup-needed').hidden = !needsSetup(setup);
  showTab(location.hash.slice(1) || (setup && needsSetup(setup) ? 'setup' : 'text'));
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
