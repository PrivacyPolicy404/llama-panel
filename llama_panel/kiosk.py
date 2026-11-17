"""Dashboard de texto para el "modo IA", estilo btop: corre en la consola (tty1) sin escritorio.

Lee la API del panel (servicios, GPU, historia) y /proc (CPU por hilo, procesos) y dibuja cajas con graficos.
  python3 -m llama_panel.kiosk                      # pantalla completa (curses), «en vivo» cada 1 s, el resto cada 3 s
  python3 -m llama_panel.kiosk --once [--size 140x44]  # imprime un cuadro como texto (para probar por SSH)
Lo arranca el servicio llama-kiosk.service (ver scripts/install-modo-ia.sh).
Solo usa caracteres que entran en las fuentes de la consola de Linux (bloques y bordes; no braille).
"""
import collections
import json
import os
import sys
import time
import urllib.request

PANEL = "http://127.0.0.1:8090"
EIGHTHS = " ▁▂▃▄▅▆▇█"
SVC = {"active": ("●", "Encendido"), "activating": ("◌", "Arrancando"), "deactivating": ("◌", "Apagando"),
       "failed": ("✖", "Error"), "inactive": ("○", "Apagado")}
WATCH_PROCS = ("llama-server", "llama-perplexit", "llama-bench", "koboldcpp", "python3")  # comm: 15 caracteres
PANEL_UNITS = ("llama-router.service", "koboldcpp.service")


def model_from_cmd(argv):
    """Nombre corto del modelo a partir de los argumentos (-m RUTA): sin carpeta, extension ni sufijo de shard."""
    for flag in ("-m", "--model"):
        if flag in argv and argv.index(flag) + 1 < len(argv):
            name = os.path.basename(argv[argv.index(flag) + 1])
            for suf in (".gguf",):
                name = name[:-len(suf)] if name.endswith(suf) else name
            if "-0000" in name and "-of-" in name:
                name = name[:name.index("-0000")]
            return name
    return None

# Colores dentro del texto: caracteres de control que no se imprimen. curses los traduce a colores (la consola de
# Linux tiene 16) y las funciones de abajo cuentan solo los caracteres visibles. Sin costo de VRAM.
RESET, GREEN, YELLOW, RED, CYAN, DIM, BLUE, MAGENTA = (chr(c) for c in range(0x11, 0x19))
MARKS = (RESET, GREEN, YELLOW, RED, CYAN, DIM, BLUE, MAGENTA)
ANSI = {RESET: "\x1b[0m", GREEN: "\x1b[32m", YELLOW: "\x1b[33m", RED: "\x1b[31m", CYAN: "\x1b[36;1m",
        DIM: "\x1b[2m", BLUE: "\x1b[34;1m", MAGENTA: "\x1b[35m"}


def vlen(text):
    return len(text) - sum(text.count(m) for m in MARKS)


def vcut(text, width):
    """Corta a `width` caracteres visibles (las marcas no cuentan)."""
    out, n = [], 0
    for ch in text:
        if ch in MARKS:
            out.append(ch)
        elif n < width:
            out.append(ch)
            n += 1
    return "".join(out)


def vpad(text, width):
    t = vcut(text, width)
    return t + " " * (width - vlen(t)) + RESET


def strip_marks(text):
    return "".join(ch for ch in text if ch not in MARKS)


def level_color(frac, warn=0.6, bad=0.85):
    return GREEN if frac < warn else YELLOW if frac < bad else RED


# ---------- datos ----------
def fetch(path, timeout=3):
    with urllib.request.urlopen(PANEL + path, timeout=timeout) as r:
        return json.loads(r.read())


class CpuSampler:
    """Uso por hilo de CPU y de los procesos que interesan, a partir de /proc (deltas entre lecturas)."""

    def __init__(self, history=600):
        self.prev = None
        self.prev_procs = {}
        self.prev_t = None
        self.total_hist = collections.deque(maxlen=history)

    @staticmethod
    def _read_stat():
        cores = []
        for line in open("/proc/stat"):
            if line.startswith("cpu") and line[3].isdigit():
                v = [int(x) for x in line.split()[1:]]
                idle = v[3] + v[4]
                cores.append((sum(v), idle))
        return cores

    @staticmethod
    def _read_procs():
        out = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/stat") as f:
                    raw = f.read()
                comm = raw[raw.index("(") + 1:raw.rindex(")")]
                if comm not in WATCH_PROCS:
                    continue
                extra = {}
                if comm == "python3":
                    cmd = open(f"/proc/{pid}/cmdline").read().replace("\0", " ")
                    if "panel.py" not in cmd:
                        continue
                    comm = "panel"
                elif comm.startswith("llama-"):
                    # los del panel corren en sus servicios; cualquier otro es una prueba lanzada a mano
                    argv = open(f"/proc/{pid}/cmdline").read().split("\0")
                    cgroup = open(f"/proc/{pid}/cgroup").read()
                    extra = {"model": model_from_cmd(argv), "external": not any(u in cgroup for u in PANEL_UNITS),
                             "router": "--models-preset" in argv}
                fields = raw[raw.rindex(")") + 2:].split()
                ticks = int(fields[11]) + int(fields[12])
                threads = int(fields[17])
                rss = int(open(f"/proc/{pid}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                out[int(pid)] = (comm, ticks, threads, rss, extra)
            except (OSError, ValueError, IndexError):
                continue
        return out

    def sample(self):
        now = time.time()
        cores, procs = self._read_stat(), self._read_procs()
        usage, plist = [0.0] * len(cores), []
        if self.prev and len(self.prev) == len(cores):
            for i, ((tot, idle), (ptot, pidle)) in enumerate(zip(cores, self.prev)):
                dt = tot - ptot
                usage[i] = 0.0 if dt <= 0 else max(0.0, min(100.0, (1 - (idle - pidle) / dt) * 100))
            self.total_hist.append(sum(usage) / len(usage))
            hz, dt = os.sysconf("SC_CLK_TCK"), now - self.prev_t
            for pid, (comm, ticks, threads, rss, extra) in procs.items():
                prev = self.prev_procs.get(pid)
                cpu = (ticks - prev[1]) / hz / dt * 100 if prev and dt > 0 else 0.0
                plist.append({"pid": pid, "name": comm, "cpu": cpu, "threads": threads, "rss": rss, **extra})
        self.prev, self.prev_procs, self.prev_t = cores, procs, now
        plist.sort(key=lambda p: -p["cpu"])
        return {"cores": usage, "total_hist": list(self.total_hist), "procs": plist}


# ---------- dibujo ----------
def bar(value, total, width, warn=0.6, bad=0.85, color=True):
    frac = 0 if not total else max(0.0, min(1.0, (value or 0) / total))
    full = round(frac * width)
    if not color:
        return "█" * full + "░" * (width - full)
    return level_color(frac, warn, bad) + "█" * full + DIM + "░" * (width - full) + RESET


def graph(values, width, height, lo=None, hi=None, color=None):
    """Grafico de varias filas con bloques de octavos; devuelve las filas de arriba hacia abajo.
    color: None = sin color; "level" = cada columna verde/amarilla/roja segun su valor; o una marca fija (CYAN...)."""
    vals = [v for v in values if v is not None][-width:]
    vals = [None] * (width - len(vals)) + vals  # alineado a la derecha: lo mas nuevo al final
    real = [v for v in vals if v is not None]
    if not real:
        return [" " * width] * height
    lo = min(real) if lo is None else lo
    hi = max(real) if hi is None else hi
    span = (hi - lo) or 1
    levels = [None if v is None else (min(max(v, lo), hi) - lo) / span * height * 8 for v in vals]
    rows = []
    for r in range(height - 1, -1, -1):  # r = fila desde abajo
        line, cur = [], None
        for lv in levels:
            if lv is None:
                ch, col = " ", None
            else:
                fill = max(0, min(8, round(lv - r * 8)))
                ch = EIGHTHS[fill] if (fill or r == 0) else " "
                if r == 0 and fill == 0:
                    ch = "▁"
                col = None
                if color == "level":
                    col = level_color(lv / (height * 8))
                elif color:
                    col = color
                if r == 0 and fill == 0:
                    col = DIM  # piso: linea tenue
            if color and col != cur and ch != " ":
                line.append(col or RESET)
                cur = col
            line.append(ch)
        rows.append("".join(line) + (RESET if color else ""))
    return rows


def box(title, lines, width, right=""):
    """Caja con borde redondeado y titulo (en color), como btop."""
    inner = width - 2
    head = f"─ {CYAN}{title}{RESET} "
    if right:
        head = head + "─" * max(1, inner - vlen(head) - vlen(right) - 2) + f" {right} "
    top = "╭" + vcut(head + "─" * inner, inner) + RESET + "╮"
    body = ["│" + vpad(l, inner) + "│" for l in lines]
    return [top] + body + ["╰" + "─" * inner + "╯"]


def side_by_side(a, b, gap=1):
    """Pone dos bloques de lineas uno al lado del otro."""
    wa = max((vlen(x) for x in a), default=0)
    wb = max((vlen(x) for x in b), default=0)
    h = max(len(a), len(b))
    a = a + [" " * wa] * (h - len(a))
    b = b + [" " * wb] * (h - len(b))
    return [x + " " * (wa - vlen(x)) + " " * gap + y for x, y in zip(a, b)]


def ago(t, now):
    s = max(0, int(now - t))
    return f"hace {s} s" if s < 60 else f"hace {s // 60} min" if s < 3600 else f"hace {s // 3600} h"


def gpu_box(hw, samples, width):
    w = width - 2
    gw = w - 15  # " LABEL " (6) + grafico + " " + valor (8)
    bw = max(4, w - 42)
    vt = hw.get("vram_total") or 8
    # con Flash-Next la VRAM y la memoria fija (GTT) van casi llenas a proposito: rojo solo al borde
    used = hw.get("vram_used") or 0
    lines = [f" VRAM {bar(used, vt, bw, 0.92, 0.98)} {used:.2f}/{vt:.0f}G · libre {max(0, vt - used):.2f}G",
             f" GTT  {bar(hw.get('gtt_used'), 31, bw, 0.97, 0.995)} {hw.get('gtt_used') or 0:5.2f}/31G"]
    gpu = [s.get("gpu") for s in samples]
    rows = graph(gpu, gw, 4, 0, 100, color="level")
    peak = hw.get("gpu_max")
    lines += [f" {'GPU' if i == 0 else ('pico' if i == 1 and peak is not None else ''):<5}{r} "
              + (f"{hw.get('gpu') or 0:d} %".rjust(7) if i == 0 else (f"{peak} %".rjust(7) if i == 1 and peak is not None else ""))
              for i, r in enumerate(rows)]
    power = [s.get("power") for s in samples]
    cap = hw.get("power_cap") or 155
    rows = graph(power, gw, 2, 0, cap, color=MAGENTA)
    lines += [f" {'WATT' if i == 0 else '':<5}{r} " + (f"{hw.get('power') or 0:.0f} W".rjust(7) if i == 0 else f"/{cap} W".rjust(7))
              for i, r in enumerate(rows)]
    temps = [s.get("temp") for s in samples]
    lines.append(f" TEMP {''.join(graph(temps, gw, 1, 30, 90, color='level'))} " + f"{hw.get('temp') or 0:.0f} °C".rjust(7))
    lines.append(f" núcleo {hw.get('sclk') or 0} MHz · {mclk_text(hw)} · ventilador {hw.get('fan') or 0} rpm")
    return box("gpu · RX 580", lines, width)


STOCK_MCLK = 2000  # RX 580 de fabrica


def mclk_text(hw):
    if not hw.get("mclk"):
        return "memoria ?"
    tags = ["fija" if hw.get("mclk_mode") == "manual" else "auto"]
    if (hw.get("mclk_max") or 0) > STOCK_MCLK:
        tags.append(f"OC {hw['mclk_max']}")
    return f"memoria {hw['mclk']} MHz ({', '.join(tags)})"


def cpu_box(cpu, hw, width):
    w = width - 2
    lines = []
    cores = cpu.get("cores") or []
    hist = cpu.get("total_hist") or []
    rows = graph(hist, w - 14, 3, 0, 100, color="level")
    total = hist[-1] if hist else 0
    lines += [f" {'total' if i == 0 else '':<5}{r} " + (f"{total:5.0f} %" if i == 0 else "") for i, r in enumerate(rows)]
    half = (len(cores) + 1) // 2
    cw = (w - 3) // 2 - 12
    for i in range(half):
        cells = []
        for j in (i, i + half):
            if j < len(cores):
                cells.append(f" {j:>2} {bar(cores[j], 100, cw)} {cores[j]:3.0f}%")
        lines.append(" ".join(cells))
    temp = hw.get("cpu_temp")
    peak = hw.get("cpu_core_temp_max")
    lines.append(f" TEMP {temp:.0f} °C · núcleo más caliente {peak:.0f} °C" if temp is not None and peak is not None
                 else f" TEMP {temp:.0f} °C" if temp is not None else " TEMP sensor no disponible")
    lines.append(f" carga {hw.get('load', 0):.2f} · {len(cores)} hilos")
    return box("cpu · i7-10700", lines, width)


def mem_box(hw, width):
    w = width - 2
    bw = w - 24
    lines = [
        f" RAM   {bar(hw.get('ram_used'), hw.get('ram_total'), bw)} {hw.get('ram_used', 0):5.1f}/{hw.get('ram_total', 0):.0f}G",
        f" caché {bar(hw.get('ram_cache'), hw.get('ram_total'), bw)} {hw.get('ram_cache', 0):5.1f}G",
    ]
    if hw.get("swap_total"):
        lines.append(f" swap  {bar(hw.get('swap_used'), hw.get('swap_total'), bw)} {hw.get('swap_used', 0):5.1f}/{hw.get('swap_total', 0):.0f}G")
    return box("memoria · RAM", lines, width)


def external_runs(cpu):
    """Procesos de llama.cpp que no son del panel (pruebas lanzadas a mano), sin repetir el mismo modelo."""
    seen, out = set(), []
    for p in (cpu or {}).get("procs") or []:
        if p.get("external") and not p.get("router"):
            key = (p["name"], p.get("model"))
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def cpu_txt(p):
    """Uso de CPU de un proceso: porcentaje estilo top (100 % = un hilo) y su equivalente en núcleos."""
    return f"{p['cpu']:.0f}% · {p['cpu'] / 100:.1f} núcl."


def external_lines(cpu, w):
    out = []
    for p in external_runs(cpu):
        out.append(f" {YELLOW}◆ prueba fuera del panel{RESET} · {p['name']} · cpu {cpu_txt(p)}")
        out.append(f"   {(p.get('model') or '?')[:w - 4]}")
    return out


def models_box(state, stats, width, now, cpu=None):
    w = width - 2
    svc = state["svc"]
    loaded = [k for k, v in (state["text"].get("models") or {}).items() if v.get("status") in ("loaded", "loading")]
    icon, label = SVC.get(svc["text"], ("?", svc["text"]))
    lines = [f" texto     {icon} {label}", f"   {(loaded[0] if loaded else ('ninguno cargado' if svc['text'] == 'active' else '-'))[:w - 4]}"]
    img = state.get("img") or {}
    icon, label = SVC.get(svc["img"], ("?", svc["img"]))
    pr = img.get("progress") or {}
    gen = f" · paso {pr['step']}/{pr['steps']}" if svc["img"] == "active" and pr.get("steps") else ""
    lines.append(f" imágenes  {icon} {label}{gen}")
    lines.append(f"   {(img.get('selected') or '-')[:w - 4]}")
    lines += external_lines(cpu, w)
    resp = (stats or {}).get("text") or []
    if resp:
        last = resp[-1]
        lines.append("")
        lines.append(f" última: {last['g_tps']:.1f} tok/s · prompt {last['p_tps']:.0f} · {ago(last['t'], now)}")
        tps = [r["g_tps"] for r in resp]
        top = max(tps)
        rows = graph(tps, w - 10, 3, 0, top, color=BLUE)
        lines += [f" {(f'{top:.0f}' if i == 0 else ''):>4} {r}" for i, r in enumerate(rows)]
        lines.append(f" {'':>4} tok/s de las últimas {min(len(tps), w - 10)} respuestas")
    return box("modelos", lines, width)


LIVE_ICON = {"gen": ("●", "generando"), "prompt": ("◐", "procesando el prompt"), "idle": ("○", "esperando")}


def live_status(live):
    live = live or {}
    icon, label = LIVE_ICON.get(live.get("state"), LIVE_ICON["idle"])
    return f"{icon} {label}"


def duration(seconds):
    if seconds is None:
        return "--"
    seconds = max(0, round(seconds))
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def live_box(live, stats, tps_hist, width, prompt_hist=None, graph_height=2):
    """Filas fijas: no desplazar las barras al cambiar de fase o al esperar un log."""
    w = width - 2
    live = live or {}
    fmt = lambda value: "--" if value is None else f"{value:.1f}"
    lines = [" " + live_status(live)]
    if live.get("prompt_tps") is None and live.get("prompt_tps_prev") is not None:
        lines.append(f" prompt   media {fmt(live['prompt_tps_prev'])} tok/s {DIM}(pedido anterior){RESET}")
    else:
        lines.append(f" prompt   media {fmt(live.get('prompt_tps'))} tok/s")
    lines += ["      " + row for row in graph(list(prompt_hist or []), w - 8, graph_height, 0, color=CYAN)]
    lines.append(f" salida   media {fmt(live.get('gen_avg_tps'))} · 3 s {fmt(live.get('tps'))} tok/s")
    lines += ["      " + row for row in graph(list(tps_hist or []), w - 8, graph_height, 0, color=GREEN)]
    progress = live.get("prompt_progress")
    pct = f"{progress * 100:5.0f} %" if progress is not None else "   -- %"
    lines.append(f" prompt   {bar(progress, 1, max(1, w - 28))} {pct}")
    size, used = live.get("ctx_size"), live.get("ctx_used")
    ctx = f"{used if used is not None else 0:>6}/{size}" if size else "    --/--"
    lines.append(f" contexto {bar(used, size, max(1, w - 28))} {ctx}")
    processed = live.get("prompt_tokens")
    generated = live.get("n_gen")
    lines.append(f" tokens   prompt {processed if processed is not None else '--'} · salida {generated if generated is not None else '--'}")
    approximate = "~" if live.get("state") == "prompt" else ""
    lines.append(f" reuso    {live.get('prompt_cached', 0)} tokens · prompt total {approximate}{live.get('prompt_total') or '--'}")
    lines.append(f" tiempo   prompt {duration(live.get('prompt_elapsed'))} · salida {duration(live.get('gen_elapsed'))}")
    eta = None
    if live.get("state") == "prompt" and live.get("prompt_total") and live.get("prompt_tps"):
        remaining = max(0, live["prompt_total"] - live.get("prompt_cached", 0) - (processed or 0))
        eta = remaining / live["prompt_tps"]
    avg = live.get("gen_avg_tps")
    latency = f"{1000 / avg:.1f} ms/tok" if avg else "-- ms/tok"
    lines.append(f" estimado prompt restante ~{duration(eta)}" if eta is not None else f" salida   {latency}")
    rate = live.get("draft_rate")
    mtp = f"{rate * 100:.1f} % · {fmt(live.get('draft_len'))} tok/paso" if rate is not None else "-- (se informa al finalizar)"
    lines.append(f" MTP últ. {mtp}")
    resp = (stats or {}).get("text") or []
    toks = sum(r.get("g_tok") or 0 for r in resp)
    seconds = sum((r.get("g_tok") or 0) / r["g_tps"] for r in resp if r.get("g_tps"))
    if graph_height > 1:
        lines.append(f" historial {len(resp)} resp. · {toks} tok · {fmt(toks / seconds if seconds else None)} tok/s")
    age = live.get("age")
    freshness = f"último dato hace {duration(age)}" if age is not None else "sin datos del pedido"
    lines.append(" " + (YELLOW if live.get("stale") else DIM) + freshness + RESET)
    return box("en vivo", lines, width)


def procs_box(cpu, width, max_rows=6):
    w = width - 2
    lines = [f" {'proceso':<14}{'pid':>8}{'cpu':>18}{'hilos':>7}{'ram':>9}"]
    for p in (cpu.get("procs") or [])[:max_rows]:
        lines.append(f" {p['name']:<14}{p['pid']:>8}{cpu_txt(p):>18}{p['threads']:>7}{p['rss'] / 2**30:>8.1f}G")
    if len(lines) == 1:
        lines.append(" (sin modelos corriendo)")
    return box("procesos", lines, width)


def compact_boxes(state, stats, hw, samples, cpu, width, gh, now, live=None):
    """Layout de una columna para pantallas chicas (por ejemplo 80x30): mismas cajas, graficos mas bajos."""
    w = width - 2
    bw = max(8, w - 24)
    vu, vt = hw.get("vram_used") or 0, hw.get("vram_total") or 8
    g = hw.get("gpu") or 0
    gpu = [f" VRAM {bar(vu, vt, bw)} {vu:4.1f}/{vt:.0f}G",
           f" GPU  {bar(g, 100, bw)} {g:4d} %"]
    gpu += [" " + r for r in graph([s.get("gpu") for s in samples], w - 2, gh, 0, 100)]
    gpu.append(f" {hw.get('temp') or 0:.0f} °C · {hw.get('power') or 0:.0f} W · {hw.get('sclk') or 0} MHz · {mclk_text(hw)}")

    cores = cpu.get("cores") or []
    hist = cpu.get("total_hist") or []
    total = hist[-1] if hist else 0
    cpu_l = [f" CPU  {bar(total, 100, bw)} {total:4.0f} %"]
    if cores:  # una columna por hilo, separadas por espacios
        rows = graph(cores, len(cores), max(1, gh - 1), 0, 100)
        cpu_l += [(" hilos " if i == 0 else "       ") + " ".join(r) for i, r in enumerate(rows)]
    if hw.get("cpu_temp") is not None:
        cpu_l.append(f" CPU {hw['cpu_temp']:.0f} °C")
    cpu_l.append(f" {len(cores)} hilos · carga {hw.get('load', 0):.1f} · RAM {hw.get('ram_used', 0):.1f}/{hw.get('ram_total', 0):.0f}G · GTT {hw.get('gtt_used', 0):.1f}/31G")

    svc = state["svc"]
    loaded = [k for k, v in (state["text"].get("models") or {}).items() if v.get("status") in ("loaded", "loading")]
    icon, label = SVC.get(svc["text"], ("?", svc["text"]))
    mod = [f" texto {icon} {label}  {(loaded[0] if loaded else '')[:w - 22]}"]
    if svc["text"] == "active":
        st = live_status(live)
        if (live or {}).get("draft_rate") is not None:
            st += f" · MTP {live['draft_rate'] * 100:.0f} %"
        mod.append("   " + st[:w - 4])
    img = state.get("img") or {}
    icon, label = SVC.get(svc["img"], ("?", svc["img"]))
    pr = img.get("progress") or {}
    gen = f" paso {pr['step']}/{pr['steps']}" if svc["img"] == "active" and pr.get("steps") else ""
    mod.append(f" imágenes {icon} {label}{gen}")
    mod += external_lines(cpu, w)
    resp = (stats or {}).get("text") or []
    if resp:
        r = resp[-1]
        mod.append(f" última {r['g_tps']:.1f} tok/s · prompt {r['p_tps']:.0f} · {ago(r['t'], now)}")
        mod += [" " + x for x in graph([x["g_tps"] for x in resp], w - 2, max(1, gh - 1), 0)]

    procs = [f" {p['name'][:12]:<12}{cpu_txt(p):>17}{p['threads']:>4} hilos{p['rss'] / 2**30:>6.1f}G"
             for p in (cpu.get("procs") or [])[:3]] or [" (sin modelos corriendo)"]
    return [box("gpu", gpu, width), box("cpu · memoria", cpu_l, width), box("modelos", mod, width), box("procesos", procs, width)]


def render(state, stats, hist, cpu, width=120, height=40, now=None, live=None, tps_hist=None, prompt_hist=None):
    """Devuelve las lineas del dashboard como [(texto, estilo)]; estilo en {"", "title", "dim", "good", "warn", "bad"}.
    Con 120x36 o mas usa dos columnas; si no, una columna compacta que se achica hasta entrar en la altura."""
    now = now or time.time()
    width = max(40, width)
    out = []
    clock = time.strftime("%H:%M:%S", time.localtime(now))
    out.append((f" PANEL IA · modo IA".ljust(width - len(clock) - 1)[:width - len(clock) - 1] + clock + " ", "title"))
    if state is None:
        out.append(("", ""))
        out.append(("  Sin conexión con el panel. Reintentando…"[:width], "bad"))
        return out
    hw = state.get("hw") or {}
    samples = (hist or {}).get("samples") or []
    cpu = cpu or {}
    if width < 120 or height < 36:
        for gh in (3, 2, 1):
            boxes = compact_boxes(state, stats, hw, samples, cpu, width, gh, now, live)
            while boxes and 1 + sum(len(b) for b in boxes) > height:
                if gh > 1:
                    break
                boxes.pop()  # sin lugar: se descartan las cajas de abajo (procesos, modelos)
            if 1 + sum(len(b) for b in boxes) <= height:
                break
        for b in boxes:
            out += [(l, "") for l in b]
        return out[:height]
    left_w = width // 2
    right_w = width - left_w - 1
    top = side_by_side(gpu_box(hw, samples, left_w), cpu_box(cpu, hw, right_w))
    live_panel = live_box(live, stats, tps_hist, right_w, prompt_hist, graph_height=2 if height >= 40 else 1)
    proc_rows = max(1, min(6, height - len(top) - len(live_panel) - 5))
    bottom = side_by_side(mem_box(hw, left_w) + models_box(state, stats, left_w, now, cpu),
                          live_panel + procs_box(cpu, right_w, proc_rows))
    for l in top + bottom:
        out.append((l, ""))
    out.append((" Escritorio apagado para dejarle la GPU a los modelos · para volver: «Salir del modo IA» en el panel", "dim"))
    return out[:height]


def snapshot():
    try:
        state = fetch("/api/state")
    except Exception:  # noqa: BLE001
        return None, None, None
    try:
        stats = fetch("/api/stats", timeout=10)
    except Exception:  # noqa: BLE001
        stats = None
    try:
        hist = fetch("/api/history")
    except Exception:  # noqa: BLE001
        hist = None
    return state, stats, hist


def fetch_live():
    try:
        return fetch("/api/live")
    except Exception:  # noqa: BLE001
        return None


LIVE_EVERY, REST_EVERY = 1, 3  # segundos: la caja «en vivo» y el resto (estado, CPU, historial)


def prompt_sample(live):
    """(pedido, tok/s) del último dato de prompt. Un pedido corto lo informa recién al terminar y el siguiente
    puede empezar antes de la próxima lectura: en ese caso se toma el que el panel guardó del pedido anterior."""
    live = live or {}
    if live.get("prompt_tps") is not None:
        return live.get("task_id"), live["prompt_tps"]
    if live.get("prompt_tps_prev") is not None:
        return live.get("prompt_prev_task"), live["prompt_tps_prev"]
    return None


def run_curses():
    import curses

    def main(scr):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_BLUE, -1)
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)
        styles = {"": curses.A_NORMAL, "title": curses.color_pair(1) | curses.A_BOLD, "dim": curses.A_DIM,
                  "good": curses.color_pair(2), "warn": curses.color_pair(3), "bad": curses.color_pair(4) | curses.A_BOLD}
        marks = {GREEN: curses.color_pair(2), YELLOW: curses.color_pair(3) | curses.A_BOLD,
                 RED: curses.color_pair(4) | curses.A_BOLD, CYAN: curses.color_pair(1) | curses.A_BOLD,
                 DIM: curses.A_DIM, BLUE: curses.color_pair(5) | curses.A_BOLD, MAGENTA: curses.color_pair(6) | curses.A_BOLD}
        scr.timeout(LIVE_EVERY * 1000)
        sampler = CpuSampler()
        sampler.sample()
        cached, last_fetch, started = (None, None, None), 0, time.time()
        live, tps_hist = None, collections.deque(maxlen=240)
        prompt_hist = collections.deque(maxlen=240)
        last_live_sample = last_prompt_sample = None
        last_live_fetch, cpu = 0, None
        while True:
            if source_changed(started):  # codigo nuevo: reiniciar el proceso con la version actual
                curses.endwin()
                os.execv(sys.executable, [sys.executable, "-m", "llama_panel.kiosk"])
            if time.time() - last_fetch > REST_EVERY - 0.2:
                state, stats, hist = snapshot()
                cached, last_fetch = (state, stats if stats is not None else cached[1], hist), time.time()
                cpu = sampler.sample()
            if time.time() - last_live_fetch > LIVE_EVERY - 0.2:
                last_live_fetch = time.time()
                update = fetch_live()
                if update is not None:
                    live = update
                elif live:
                    live = {**live, "stale": True, "age": time.time() - live["t"] if live.get("t") else None}
                if live and live.get("t") != last_live_sample:
                    last_live_sample = live.get("t")
                    if live.get("state") == "gen":
                        tps_hist.append(live.get("tps"))
                sample = prompt_sample(live)
                if sample and sample != last_prompt_sample:
                    last_prompt_sample = sample
                    prompt_hist.append(sample[1])
            h, w = scr.getmaxyx()
            scr.erase()
            for y, (text, style) in enumerate(render(*cached, cpu, width=w - 1, height=h - 1, live=live, tps_hist=tps_hist, prompt_hist=prompt_hist)):
                base = styles.get(style, curses.A_NORMAL)
                attr, x, seg = base, 0, []
                try:
                    for ch in text + RESET:
                        if ch in MARKS:
                            if seg:
                                scr.addstr(y, x, "".join(seg), attr)
                                x += len(seg)
                                seg = []
                            attr = base if ch == RESET else marks.get(ch, base)
                        else:
                            seg.append(ch)
                except curses.error:
                    pass
            scr.refresh()
            scr.getch()

    curses.wrapper(main)


FONTS = ["Uni2-TerminusBold24x12", "Uni2-TerminusBold22x11", "Uni2-TerminusBold20x10", "Uni2-TerminusBold16",
         "Uni2-TerminusBold14", "Uni2-Terminus12x6"]


def tty_size():
    import fcntl, struct, termios
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
    return cols, rows


def pick_font(min_cols=80, min_rows=28):
    """En la consola de Linux, prueba de la fuente mas grande a la mas chica y se queda con la primera que deja
    al menos min_cols x min_rows (la resolucion depende de las pantallas conectadas). Fuera de una consola no hace nada."""
    import subprocess
    if os.environ.get("TERM") != "linux" or not sys.stdout.isatty():
        return
    for font in FONTS:
        if subprocess.run(["setfont", font], capture_output=True).returncode != 0:
            continue
        cols, rows = tty_size()
        if cols >= min_cols and rows >= min_rows:
            return


def source_changed(started):
    """True si el codigo del dashboard cambio desde que arranco (para recargarse solo)."""
    try:
        return os.path.getmtime(__file__) > started
    except OSError:
        return False


if __name__ == "__main__":
    if "--once" in sys.argv:
        size = sys.argv[sys.argv.index("--size") + 1] if "--size" in sys.argv else "140x44"
        w, h = (int(x) for x in size.split("x"))
        sampler = CpuSampler()
        sampler.sample()
        time.sleep(1)
        live = fetch_live()
        tty = sys.stdout.isatty()
        for text, _ in render(*snapshot(), sampler.sample(), width=w, height=h, live=live,
                              tps_hist=[live["tps"]] if live and live.get("state") == "gen" else [],
                              prompt_hist=[live["prompt_tps"]] if live and live.get("prompt_tps") is not None else []):
            print("".join(ANSI.get(c, c) for c in text) + ANSI[RESET] if tty else strip_marks(text))
    else:
        import signal
        # al salir del modo IA systemd cuelga la terminal (SIGHUP) o pide terminar (SIGTERM): salir limpio, no como error
        for sig in (signal.SIGHUP, signal.SIGTERM):
            signal.signal(sig, lambda *_: os._exit(0))
        pick_font()
        while True:  # si algo falla de forma inesperada, no dejar la consola colgada
            try:
                run_curses()
            except KeyboardInterrupt:
                break
            except Exception as e:  # noqa: BLE001
                print(f"error en el dashboard: {e}; reintentando en 5 s", flush=True)
                time.sleep(5)
