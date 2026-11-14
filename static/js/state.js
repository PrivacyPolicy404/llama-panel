// Estado compartido entre modulos y ganchos que main.js registra al arrancar.
import { $ } from './util.js';

export const S = { state: null, config: null, stats: null, hist: [], dirtyText: false, dirtyImg: false, tab: 'text', logSvc: 'text' };

// main.js completa esto (pollState, loadConfig) para que otros modulos lo llamen sin importarlo en ciclo
export const hooks = {};

export function setDirty(which) {
  if (which === 'text') S.dirtyText = true; else S.dirtyImg = true;
  $('#savebar').hidden = false;
}
export function clearDirty() { S.dirtyText = S.dirtyImg = false; $('#savebar').hidden = true; }
