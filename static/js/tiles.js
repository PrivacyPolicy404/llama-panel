// Tiles de hardware (VRAM, GPU, temperatura, RAM) con sparklines y tooltip.
import { $, ago, fix, now, tip } from './util.js';
import { S } from './state.js';

export const HIST_WINDOW = 600; // maximo de segundos que muestran los sparklines
// La ventana se adapta a la historia disponible (minimo 1 minuto) para que el grafico ocupe todo el ancho
const sparkWindow = () => S.hist.length ? Math.min(HIST_WINDOW, Math.max(60, now() - S.hist[0].t)) : HIST_WINDOW;

const TILES = {
  vram: {
    get: h => h.vram_used, max: h => h.vram_total, unit: 'GiB', d: 1,
    render(h) {
      const pct = h.vram_used / h.vram_total * 100;
      return { value: `${fix(h.vram_used)} <small>/ ${fix(h.vram_total, 0)} GiB</small>`, sub: h.gtt_used != null ? `GTT ${fix(h.gtt_used)} GiB` : '', pct, level: pct >= 95 ? 'critical' : pct >= 85 ? 'warning' : '' };
    },
  },
  gpu: {
    get: h => h.gpu, max: () => 100, unit: '%', d: 0,
    render(h) { return { value: `${h.gpu ?? '–'}<small>%</small>`, sub: h.sclk ? `${h.sclk} MHz` : '', pct: h.gpu ?? 0, level: '' }; },
  },
  temp: {
    get: h => h.temp, auto: true, unit: '°C', d: 0,
    render(h) {
      const sub = [h.power != null ? `${fix(h.power, 0)} W` : '', h.fan != null ? `${h.fan} rpm` : h.fan_pct != null ? `ventilador ${h.fan_pct} %` : ''].filter(Boolean).join(' · ');
      const crit = h.temp_crit || 100;
      return { value: `${fix(h.temp, 0)}<small> °C</small>`, sub, pct: (h.temp ?? 0) / crit * 100, level: h.temp >= 90 ? 'critical' : h.temp >= 80 ? 'warning' : '' };
    },
  },
  ram: {
    get: h => h.ram_used, max: h => h.ram_total, unit: 'GiB', d: 1,
    render(h) {
      const pct = h.ram_used / h.ram_total * 100;
      return { value: `${fix(h.ram_used)} <small>/ ${fix(h.ram_total, 0)} GiB</small>`, sub: `carga ${fix(h.load, 2)}`, pct, level: pct >= 95 ? 'critical' : pct >= 85 ? 'warning' : '' };
    },
  },
};

export function renderTiles() {
  const h = S.state?.hw;
  if (!h) return;
  for (const [key, def] of Object.entries(TILES)) {
    const tile = $(`#tile-${key}`);
    const r = def.render(h);
    $('[data-value]', tile).innerHTML = r.value;
    $('[data-sub]', tile).textContent = r.sub;
    const m = $('[data-meter]', tile);
    m.style.width = Math.max(0, Math.min(100, r.pct || 0)) + '%';
    m.className = r.level;
    drawSpark(tile, def);
  }
}

function sparkRange(def) {
  const vals = S.hist.map(def.get).filter(v => v != null);
  if (def.auto) {
    const lo = Math.min(...vals), hi = Math.max(...vals);
    return [Math.floor(lo - 3), Math.ceil(hi + 3)];
  }
  const last = S.hist[S.hist.length - 1];
  return [0, def.max(last) || Math.max(...vals) || 1];
}

function drawSpark(tile, def) {
  const box = $('[data-spark]', tile);
  const pts = S.hist.filter(h => def.get(h) != null);
  if (pts.length < 2) { box.innerHTML = ''; return; }
  const [lo, hi] = sparkRange(def);
  const t1 = now(), win = sparkWindow();
  const X = t => 100 - (t1 - t) / win * 100;
  const Y = v => 39 - (v - lo) / (hi - lo || 1) * 37;
  const line = pts.map((h, i) => `${i ? 'L' : 'M'}${X(h.t).toFixed(2)},${Y(def.get(h)).toFixed(2)}`).join('');
  const area = `${line}L${X(pts[pts.length - 1].t).toFixed(2)},40L${X(pts[0].t).toFixed(2)},40Z`;
  let svg = $('svg', box);
  if (!svg) {
    box.innerHTML = `<svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><path class="area"/><path class="line"/><line class="cross" y1="0" y2="40" visibility="hidden"/></svg><span class="dot" hidden></span>`;
    svg = $('svg', box);
    box.addEventListener('pointermove', e => sparkHover(box, def, e));
    box.addEventListener('pointerleave', () => { tip.hide(); $('.cross', box).setAttribute('visibility', 'hidden'); $('.dot', box).hidden = true; });
  }
  $('.line', svg).setAttribute('d', line);
  $('.area', svg).setAttribute('d', area);
}

function sparkHover(box, def, e) {
  const rect = box.getBoundingClientRect();
  const fx = (e.clientX - rect.left) / rect.width;
  const win = sparkWindow();
  const t = now() - (1 - fx) * win;
  const pts = S.hist.filter(h => def.get(h) != null);
  if (!pts.length) return;
  let best = pts[0];
  for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  const [lo, hi] = sparkRange(def);
  const x = 100 - (now() - best.t) / win * 100;
  const y = 39 - (def.get(best) - lo) / (hi - lo || 1) * 37;
  const cross = $('.cross', box);
  cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
  const dot = $('.dot', box);
  dot.hidden = false; dot.style.left = (x / 100 * rect.width) + 'px'; dot.style.top = (y / 40 * rect.height) + 'px';
  tip.show(`<b>${fix(def.get(best), def.d)} ${def.unit}</b> <span class="m">${ago(best.t)}</span>`, e.clientX, e.clientY);
}
