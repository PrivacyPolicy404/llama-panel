// Pestana de registros: journal de cada servicio, con filtro.
import { $, esc, getJSON } from './util.js';
import { S } from './state.js';
import { t } from './i18n.js';

export async function loadLogs() {
  const pre = $('#logs');
  try {
    const { lines } = await getJSON(`/api/logs?svc=${S.logSvc}&n=500`);
    const f = $('#log-filter').value.trim().toLowerCase();
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    pre.innerHTML = lines.filter(l => !f || l.toLowerCase().includes(f)).map(l => {
      const cls = /\b(error|failed|fatal)\b| E /i.test(l) ? 'e' : /\b(warn(ing)?)\b| W /i.test(l) ? 'w' : '';
      return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l);
    }).join('\n') || `<span class="muted">${t('(vacío)')}</span>`;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  } catch { /* reintenta en el próximo ciclo */ }
}
