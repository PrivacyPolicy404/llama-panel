"""Dashboard de texto para el "modo IA", estilo btop: corre en la consola (tty1) sin escritorio.

Lee la API del panel (servicios, GPU, historia) y /proc (CPU por hilo, procesos) y dibuja cajas con graficos.
  python3 -m llama_panel.kiosk                      # pantalla completa (curses), se actualiza cada 2 s
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
WATCH_PROCS = ("llama-server", "koboldcpp", "python3")


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
                if comm == "python3":
                    cmd = open(f"/proc/{pid}/cmdline").read().replace("\0", " ")
                    if "panel.py" not in cmd:
                        continue
                    comm = "panel"
                fields = raw[raw.rindex(")") + 2:].split()
                ticks = int(fields[11]) + int(fields[12])
                threads = int(fields[17])
                rss = int(open(f"/proc/{pid}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
                out[int(pid)] = (comm, ticks, threads, rss)
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
            for pid, (comm, ticks, threads, rss) in procs.items():
                prev = self.prev_procs.get(pid)
                cpu = (ticks - prev[1]) / hz / dt * 100 if prev and dt > 0 else 0.0
                plist.append({"pid": pid, "name": comm, "cpu": cpu, "threads": threads, "rss": rss})
        self.prev, self.prev_procs, self.prev_t = cores, procs, now
        plist.sort(key=lambda p: -p["cpu"])
        return {"cores": usage, "total_hist": list(self.total_hist), "procs": plist}


# ---------- dibujo ----------
def bar(value, total, width):
    frac = 0 if not total else max(0.0, min(1.0, (value or 0) / total))
    full = round(frac * width)
    return "█" * full + "░" * (width - full)


def graph(values, width, height, lo=None, hi=None):
    """Grafico de varias filas con bloques de octavos; devuelve las filas de arriba hacia abajo."""
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
        line = []
        for lv in levels:
            if lv is None:
                line.append(" ")
            else:
                fill = max(0, min(8, round(lv - r * 8)))
                line.append(EIGHTHS[fill] if (fill or r == 0) else " ")
                if r == 0 and fill == 0:
                    line[-1] = "▁"
        rows.append("".join(line))
    return rows


def box(title, lines, width, right=""):
    """Caja con borde redondeado y titulo, como btop."""
    inner = width - 2
    head = f"─ {title} "
    if right:
        head = head + "─" * max(1, inner - len(head) - len(right) - 2) + f" {right} "
    top = "╭" + (head + "─" * inner)[:inner] + "╮"
    body = ["│" + (l + " " * inner)[:inner] + "│" for l in lines]
    return [top] + body + ["╰" + "─" * inner + "╯"]


def side_by_side(a, b, gap=1):
    """Pone dos bloques de lineas uno al lado del otro."""
    wa = max((len(x) for x in a), default=0)
    wb = max((len(x) for x in b), default=0)
    h = max(len(a), len(b))
    a = a + [" " * wa] * (h - len(a))
    b = b + [" " * wb] * (h - len(b))
    return [x.ljust(wa) + " " * gap + y for x, y in zip(a, b)]


def ago(t, now):
    s = max(0, int(now - t))
    return f"hace {s} s" if s < 60 else f"hace {s // 60} min" if s < 3600 else f"hace {s // 3600} h"


def gpu_box(hw, samples, width):
    w = width - 2
    gw = w - 15  # " LABEL " (6) + grafico + " " + valor (8)
    lines = []
    vt = hw.get("vram_total") or 8
    vram = [s.get("vram_used") for s in samples]
    gpu = [s.get("gpu") for s in samples]
    rows = graph(vram, gw, 4, 0, vt)
    lines += [f" {'VRAM' if i == 0 else '':<5}{r} " + (f"{hw.get('vram_used', 0):4.1f}/{vt:.0f}G".rjust(7) if i == 0 else "") for i, r in enumerate(rows)]
    rows = graph(gpu, gw, 4, 0, 100)
    lines += [f" {'GPU' if i == 0 else '':<5}{r} " + (f"{hw.get('gpu') or 0:d} %".rjust(7) if i == 0 else "") for i, r in enumerate(rows)]
    temps = [s.get("temp") for s in samples]
    lines.append(f" TEMP {''.join(graph(temps, gw, 1))} " + f"{hw.get('temp') or 0:.0f} °C".rjust(7))
    lines.append(f" {hw.get('power') or 0:.0f} W / {hw.get('power_cap') or 0} W · ventilador {hw.get('fan') or 0} rpm")
    lines.append(f" núcleo {hw.get('sclk') or 0} MHz · {mclk_text(hw)}")
    return box("gpu · RX 580", lines, width)


def mclk_text(hw):
    if not hw.get("mclk"):
        return "memoria ?"
    mode = "fija" if hw.get("mclk_mode") == "manual" else "auto"
    return f"memoria {hw['mclk']} MHz ({mode})"


def cpu_box(cpu, hw, width):
    w = width - 2
    lines = []
    cores = cpu.get("cores") or []
    hist = cpu.get("total_hist") or []
    rows = graph(hist, w - 14, 3, 0, 100)
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
    lines.append(f" carga {hw.get('load', 0):.2f} · {len(cores)} hilos")
    return box("cpu · i7-10700", lines, width)


def mem_box(hw, width):
    w = width - 2
    bw = w - 24
    lines = [
        f" RAM  {bar(hw.get('ram_used'), hw.get('ram_total'), bw)} {hw.get('ram_used', 0):5.1f}/{hw.get('ram_total', 0):.0f}G",
        f" GTT  {bar(hw.get('gtt_used'), 31, bw)} {hw.get('gtt_used', 0):5.1f}/31G",
        f" VRAM {bar(hw.get('vram_used'), hw.get('vram_total'), bw)} {hw.get('vram_used', 0):5.1f}/{hw.get('vram_total', 0):.0f}G",
    ]
    return box("memoria", lines, width)


def models_box(state, stats, width, now):
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
    resp = (stats or {}).get("text") or []
    if resp:
        last = resp[-1]
        lines.append("")
        lines.append(f" última: {last['g_tps']:.1f} tok/s · prompt {last['p_tps']:.0f} · {ago(last['t'], now)}")
        tps = [r["g_tps"] for r in resp]
        top = max(tps)
        rows = graph(tps, w - 10, 3, 0, top)
        lines += [f" {(f'{top:.0f}' if i == 0 else ''):>4} {r}" for i, r in enumerate(rows)]
        lines.append(f" {'':>4} tok/s de las últimas {min(len(tps), w - 10)} respuestas")
    return box("modelos", lines, width)


LIVE_ICON = {"gen": ("●", "generando"), "prompt": ("◐", "procesando el prompt"), "idle": ("○", "esperando")}


def live_status(live):
    icon, label = LIVE_ICON.get((live or {}).get("state"), LIVE_ICON["idle"])
    if live and live.get("state") == "gen":
        return f"{icon} {label} · {live['tps']:.1f} tok/s · {live['n_gen']} tokens"
    if live and live.get("state") == "prompt":
        return f"{icon} {label} {live['prompt_progress'] * 100:.0f} % · {live['prompt_tps']:.0f} tok/s"
    return f"{icon} {label}"


def live_box(live, stats, tps_hist, width):
    """Lo que esta haciendo el modelo de texto ahora: estado, tok/s en vivo, contexto, MTP y la sesion."""
    w = width - 2
    live = live or {}
    lines = [" " + live_status(live)]
    rows = graph(list(tps_hist or []), w - 10, 3, 0)
    top = max([v for v in (tps_hist or []) if v] or [0])
    lines += [f" {(f'{top:.0f}' if i == 0 and top else ''):>4} {r}" for i, r in enumerate(rows)]
    lines.append(f" {'':>4} tok/s en vivo")
    if live.get("ctx_size"):
        used = live.get("ctx_used") or 0
        if live.get("state") in ("prompt", "gen") and live.get("prompt_tokens"):
            used = live["prompt_tokens"] + (live.get("n_gen") or 0 if live["state"] == "gen" else 0)
        lines.append(f" contexto {bar(used, live['ctx_size'], w - 28)} {used:>6}/{live['ctx_size']}")
    if live.get("draft_rate") is not None:
        extra = f" · {live['draft_len']:.1f} tokens por paso" if live.get("draft_len") else ""
        lines.append(f" MTP      acierta {live['draft_rate'] * 100:.0f} %{extra}")
    resp = (stats or {}).get("text") or []
    if resp:
        toks = sum(r.get("g_tok") or 0 for r in resp)
        avg = sum(r["g_tps"] for r in resp) / len(resp)
        lines.append(f" últimas  {len(resp)} respuestas · {toks} tokens · {avg:.1f} tok/s promedio")
    return box("en vivo", lines, width)


def procs_box(cpu, width):
    w = width - 2
    lines = [f" {'proceso':<14}{'pid':>8}{'cpu':>8}{'hilos':>7}{'ram':>9}"]
    for p in (cpu.get("procs") or [])[:6]:
        lines.append(f" {p['name']:<14}{p['pid']:>8}{p['cpu']:>7.0f}%{p['threads']:>7}{p['rss'] / 2**30:>8.1f}G")
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
    resp = (stats or {}).get("text") or []
    if resp:
        r = resp[-1]
        mod.append(f" última {r['g_tps']:.1f} tok/s · prompt {r['p_tps']:.0f} · {ago(r['t'], now)}")
        mod += [" " + x for x in graph([x["g_tps"] for x in resp], w - 2, max(1, gh - 1), 0)]

    procs = [f" {p['name'][:12]:<12}{p['cpu']:>6.0f}%{p['threads']:>4} hilos{p['rss'] / 2**30:>6.1f}G"
             for p in (cpu.get("procs") or [])[:3]] or [" (sin modelos corriendo)"]
    return [box("gpu", gpu, width), box("cpu · memoria", cpu_l, width), box("modelos", mod, width), box("procesos", procs, width)]


def render(state, stats, hist, cpu, width=120, height=40, now=None, live=None, tps_hist=None):
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
    bottom = side_by_side(mem_box(hw, left_w) + models_box(state, stats, left_w, now),
                          live_box(live, stats, tps_hist, right_w) + procs_box(cpu, right_w))
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


def run_curses():
    import curses

    def main(scr):
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        styles = {"": curses.A_NORMAL, "title": curses.color_pair(1) | curses.A_BOLD, "dim": curses.A_DIM,
                  "good": curses.color_pair(2), "warn": curses.color_pair(3), "bad": curses.color_pair(4) | curses.A_BOLD}
        scr.timeout(2000)
        sampler = CpuSampler()
        sampler.sample()
        cached, last_fetch, started = (None, None, None), 0, time.time()
        live, tps_hist = None, collections.deque(maxlen=240)
        while True:
            if source_changed(started):  # codigo nuevo: reiniciar el proceso con la version actual
                curses.endwin()
                os.execv(sys.executable, [sys.executable, "-m", "llama_panel.kiosk"])
            if time.time() - last_fetch > 1.8:
                state, stats, hist = snapshot()
                cached, last_fetch = (state, stats if stats is not None else cached[1], hist), time.time()
                live = fetch_live()
                tps_hist.append(live["tps"] if live and live.get("state") == "gen" else 0)
            cpu = sampler.sample()
            h, w = scr.getmaxyx()
            scr.erase()
            for y, (text, style) in enumerate(render(*cached, cpu, width=w - 1, height=h - 1, live=live, tps_hist=tps_hist)):
                try:
                    scr.addstr(y, 0, text, styles.get(style, curses.A_NORMAL))
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
        for text, _ in render(*snapshot(), sampler.sample(), width=w, height=h, live=live,
                              tps_hist=[live["tps"]] if live and live.get("state") == "gen" else []):
            print(text)
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
