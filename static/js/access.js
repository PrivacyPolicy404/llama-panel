// Pestana Configuracion, tarjeta "Acceso": contraseña del panel y tokens para programas (llama_panel/auth.py).
import { $, $$, esc, getJSON, post, report, withBusy } from './util.js';
import { t } from './i18n.js';

export async function loadAccess() {
  let a, tk;
  try { [a, tk] = await Promise.all([getJSON('/api/auth'), getJSON('/api/auth/tokens')]); } catch { return; }
  const box = $('#access');
  const date = s => new Date(s * 1000).toLocaleDateString();
  box.innerHTML = `
    <div class="card setup-card" id="access-card">
      <h4>${t('Acceso')}</h4>
      <p class="hint">${a.enabled
        ? t('El panel pide contraseña en la red. Desde esta máquina (y por túnel SSH) no la pide.')
        : t('Sin contraseña: cualquiera en tu red puede usar el panel y hablarle al compañero (que puede manejar luces). Los puertos 8080 y 5001 no pasan por acá.')}</p>
      ${a.enabled ? `<label class="field"><span>${t('Contraseña actual')}</span><input type="password" name="current" autocomplete="current-password"></label>` : ''}
      <label class="field"><span>${a.enabled ? t('Contraseña nueva (vacía para sacarla)') : t('Contraseña nueva')}</span>
        <input type="password" name="new" autocomplete="new-password" minlength="8"></label>
      <label class="field"><span>${t('Repetila')}</span><input type="password" name="again" autocomplete="new-password"></label>
      <div class="row-actions">
        <button class="btn" id="access-save">${a.enabled ? t('Cambiar contraseña') : t('Poner contraseña')}</button>
        ${a.enabled && !a.local ? `<button class="btn ghost" id="access-logout">${t('Cerrar sesión')}</button>` : ''}
      </div>
      <h4>${t('Tokens para programas')}</h4>
      <p class="hint">${t('Para <code>buddy.py --token …</code> o un cliente de la API de OpenAI (<code>api_key</code>). Cada programa con el suyo, así se puede revocar uno solo.')}</p>
      <div class="access-tokens">${tk.tokens.map(x => `
        <div class="path-row"><span class="mono">${esc(x.name)} <span class="muted">${esc(date(x.created))}</span></span>
          <button class="btn ghost small" data-revoke="${esc(x.name)}">${t('Revocar')}</button></div>`).join('') || `<p class="muted">${t('Ninguno todavía.')}</p>`}</div>
      <div class="path-row"><input type="text" id="access-token-name" placeholder="${esc(t('nombre, ej.: laptop'))}">
        <button class="btn ghost small" id="access-token-new">${t('Crear token')}</button></div>
      <pre class="buddy-cmd" id="access-token-show" hidden></pre>
    </div>`;
  $('#access-save').onclick = async e => {
    const card = $('#access-card');
    const nw = $('[name=new]', card).value, again = $('[name=again]', card).value;
    if (nw !== again) return report({ ok: false, msg: t('Las dos contraseñas no coinciden.') });
    report(await withBusy(e.currentTarget, () => post('/api/auth/password', { current: $('[name=current]', card)?.value || '', new: nw })));
    loadAccess();
  };
  const logout = $('#access-logout');
  if (logout) logout.onclick = async () => { await post('/api/logout'); location.reload(); };
  $('#access-token-new').onclick = async e => {
    const r = await withBusy(e.currentTarget, () => post('/api/auth/token', { name: $('#access-token-name').value }));
    report(r);
    if (!r.ok) return;
    await loadAccess();
    const show = $('#access-token-show');
    show.textContent = r.token; show.hidden = false;
  };
  for (const b of $$('#access [data-revoke]')) {
    b.onclick = async () => { report(await post('/api/auth/revoke', { name: b.dataset.revoke })); loadAccess(); };
  }
}
