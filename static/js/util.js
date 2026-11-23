// Utilidades sin estado: DOM, formato, red, toasts, tooltip y etiquetas de estado.
import { lang, locale, t as tr } from './i18n.js';

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const el = html => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
export const now = () => Date.now() / 1000;
export const gb = b => (b / 1e9).toFixed(b >= 10e9 ? 1 : 2) + ' GB';
export const fix = (x, d = 1) => (x == null || isNaN(x) ? '–' : Number(x).toFixed(d));
export const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
export function ago(t) {
  const s = Math.max(0, Math.round(now() - t));
  const v = s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
  return tr('hace {time}', { time: v });
}
export const clock = t => {
  const d = new Date(t * 1000), hms = d.toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return d.toDateString() === new Date().toDateString() ? hms : `${d.toLocaleDateString(locale(), { day: '2-digit', month: '2-digit' })} ${hms}`;
};
export const dur = s => s < 60 ? `${fix(s, 1)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;


export async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store', headers: { 'X-Lang': lang() } });
  if (r.status === 401) showLogin();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
export async function post(url, body = {}) {
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lang': lang() }, body: JSON.stringify(body) });
    if (r.status === 401) showLogin();
    return await r.json();
  } catch {
    return { ok: false, msg: tr('No se pudo contactar al panel.') };
  }
}

// Contraseña del panel (llama_panel/auth.py): si un pedido vuelve 401, se pide la contraseña y se recarga
export function showLogin() {
  if ($('#login')) return;
  document.body.insertAdjacentHTML('beforeend', `
    <div class="login" id="login">
      <form class="card login-card">
        <h2>${esc(tr('Panel con contraseña'))}</h2>
        <input type="password" name="password" autocomplete="current-password" placeholder="${esc(tr('Contraseña'))}" required>
        <button class="btn" type="submit">${esc(tr('Entrar'))}</button>
        <p class="hint" data-msg></p>
      </form>
    </div>`);
  const form = $('#login form');
  $('input', form).focus();
  form.onsubmit = async e => {
    e.preventDefault();
    const btn = $('button', form);
    const r = await withBusy(btn, () => fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lang': lang() },
      body: JSON.stringify({ password: form.password.value }),
    }).then(x => x.json()).catch(() => ({ ok: false, msg: tr('No se pudo contactar al panel.') })));
    if (r.ok) location.reload();
    else { $('[data-msg]', form).textContent = r.msg; form.password.select(); }
  };
}
export async function withBusy(btn, fn) {
  if (btn) { btn.classList.add('busy'); btn.disabled = true; }
  try { return await fn(); } finally { if (btn) { btn.classList.remove('busy'); btn.disabled = false; } }
}
export function toast(msg, kind = 'info', ms = 6000) {
  if (!msg) return;
  const t = el(`<div class="toast ${kind}"></div>`);
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}
export function report(res) { toast(res.msg, res.ok ? 'ok' : 'error', res.ok ? 5000 : 10000); }

export const tip = {
  show(html, x, y) {
    const t = $('#tooltip');
    t.innerHTML = html; t.hidden = false;
    const r = t.getBoundingClientRect();
    let left = x + 14, top = y - r.height - 10;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top < 8) top = y + 16;
    t.style.left = left + 'px'; t.style.top = top + 'px';
  },
  hide() { $('#tooltip').hidden = true; },
};

export const SVC = {
  active: ['good', 'Encendido'], activating: ['warning', 'Arrancando'], reloading: ['warning', 'Recargando'],
  deactivating: ['warning', 'Apagando'], failed: ['critical', 'Error'], inactive: ['', 'Apagado'],
};
export function setStatus(node, [cls, label]) {
  node.className = node.className.replace(/\b(good|warning|critical)\b/g, '').trim() + (cls ? ' ' + cls : '');
  $('b', node).textContent = tr(label);  // las etiquetas de SVC y modelStatus son claves en castellano
}
export function modelStatus(ms, textOn) {
  if (!textOn || !ms) return ['', 'Sin cargar'];
  switch (ms.status) {
    case 'loaded': return ['good', 'Cargado'];
    case 'loading': return ['warning', 'Cargando'];
    case 'sleeping': return ['', 'En reposo'];
    case 'downloading': return ['warning', 'Descargando'];
    default: return ms.failed ? ['critical', 'Falló'] : ['', 'Sin cargar'];
  }
}

// WSL2: un modelo bajo /mnt/ se lee del disco de Windows por 9P, mucho más lento que desde el disco de Linux
export const WINDOWS_DISK_TIP = 'Está en el disco de Windows (/mnt/…): en WSL carga unas 4 o 5 veces más lento que desde el disco de Linux. Copialo (no lo enlaces) a ~/models o ~/imgmodels';
