// Barra de "cambios sin guardar": guarda ajustes de texto y/o imagen.
import { $, $$, post, report, withBusy } from './util.js';
import { S, clearDirty, hooks } from './state.js';
import { readTextCfg } from './text-models.js';
import { readImgCfg } from './img-models.js';


export async function save(apply, btn) {
  await withBusy(btn, async () => {
    let ok = true;
    if (S.dirtyText) {
      const models = Object.fromEntries($$('#text-models .model').map(r => [r.dataset.key, readTextCfg(r)]));
      const res = await post('/api/text/settings', { global: $('#global').value, models, apply });
      report(res); ok = ok && res.ok;
    }
    if (S.dirtyImg) {
      const res = await post('/api/img/settings', { selected: S.config.img_selected, models: readImgCfg(), apply });
      report(res); ok = ok && res.ok;
    }
    if (ok) { clearDirty(); await hooks.loadConfig(); }
  });
  hooks.pollState();
}
