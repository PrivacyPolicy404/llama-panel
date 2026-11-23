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
from pathlib import Path

from . import config as C
from .i18n import LANGS, _, set_lang


def kiosk_lang():
    """Idioma del dashboard: LLAMA_PANEL_LANG (entorno o paths.env); si no, el último que usó la interfaz web
    (server.py lo guarda en ui-lang); si no, el del sistema."""
    v = os.environ.get("LLAMA_PANEL_LANG") or C._LOCAL.get("LLAMA_PANEL_LANG", "")
    if v in LANGS:
        return v
    try:
        v = (C.CFG_DIR / "ui-lang").read_text().strip()
        if v in LANGS:
            return v
    except OSError:
        pass
    for f in ("/etc/default/locale", "/etc/locale.conf"):
        try:
            for line in Path(f).read_text().splitlines():
                if line.startswith("LANG="):
                    return "es" if line[5:].strip('"').startswith("es") else "en"
        except OSError:
            pass
    return "es" if os.environ.get("LANG", "").startswith("es") else "en"


def L(word, width):
    """Rótulo traducido y rellenado al ancho de su columna."""
    return _(word).ljust(width)

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
        self.disk = DiskSampler()

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
        return {"cores": usage, "total_hist": list(self.total_hist), "procs": plist, "disks": self.disk.sample()}


DISK_SKIP_MOUNTS = ("/boot", "/snap", "/var/snap", "/run", "/sys", "/proc", "/dev")


def disk_kind(dev):
    """NVMe, SSD o HDD según el disco físico de la partición (o ? si no se sabe)."""
    try:
        real = os.path.realpath(f"/sys/class/block/{dev}")
        parent = dev if os.path.exists(f"{real}/queue") else os.path.basename(os.path.dirname(real))
        if parent.startswith("nvme"):
            return "NVMe"
        rota = open(f"/sys/block/{parent}/queue/rotational").read().strip()
        return "HDD" if rota == "1" else "SSD"
    except OSError:
        return "?"


def list_disks(mounts_text, model_dirs=()):
    """[(dispositivo, punto de montaje, tiene_modelos)] de los discos reales montados, sin repetir dispositivo."""
    devs = {}
    for d in model_dirs:
        try:
            devs[os.stat(d).st_dev] = True
        except OSError:
            pass
    seen, out = set(), []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].startswith("/dev/") or parts[0].startswith("/dev/loop"):
            continue
        src, mnt = parts[0], parts[1].replace("\\040", " ")
        if any(mnt == m or mnt.startswith(m + "/") for m in DISK_SKIP_MOUNTS):
            continue
        dev = os.path.basename(os.path.realpath(src))
        if dev in seen:
            continue
        seen.add(dev)
        try:
            has_models = os.stat(mnt).st_dev in devs
        except OSError:
            has_models = False
        out.append((dev, mnt, has_models))
    return sorted(out, key=lambda x: (x[1] != "/", x[1]))


def read_sectors(diskstats_text):
    """{dispositivo: sectores leídos} de /proc/diskstats (512 bytes por sector)."""
    out = {}
    for line in diskstats_text.splitlines():
        f = line.split()
        if len(f) > 5:
            out[f[2]] = int(f[5])
    return out


class DiskSampler:
    """Uso y lectura del momento de cada disco montado. La lista de discos se relee cada minuto."""

    def __init__(self):
        self.prev, self.prev_t, self.disks, self.listed_t = {}, None, [], 0

    def sample(self):
        now = time.time()
        if now - self.listed_t > 60:
            try:
                dirs = [*C.MODELS_DIRS, C.IMG_DIR]
                self.disks = [(dev, mnt, models, disk_kind(dev))
                              for dev, mnt, models in list_disks(open("/proc/mounts").read(), dirs)]
            except OSError:
                self.disks = []
            self.listed_t = now
        try:
            sectors = read_sectors(open("/proc/diskstats").read())
        except OSError:
            sectors = {}
        dt = now - self.prev_t if self.prev_t else 0
        out = []
        for dev, mnt, models, kind in self.disks:
            try:
                st = os.statvfs(mnt)
            except OSError:
                continue
            total = st.f_blocks * st.f_frsize
            used = total - st.f_bfree * st.f_frsize
            rate = None
            if dt > 0 and dev in sectors and dev in self.prev:
                rate = max(0, sectors[dev] - self.prev[dev]) * 512 / dt / 1e6
            out.append({"dev": dev, "mount": mnt, "kind": kind, "models": models, "used": used, "total": total,
                        "read_mbs": rate})
        self.prev, self.prev_t = sectors, now
        return out


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
    return _("hace {t}", t=f"{s} s" if s < 60 else f"{s // 60} min" if s < 3600 else f"{s // 3600} h")


def gpu_box(hw, samples, width, energy=None):
    w = width - 2
    gw = w - 15  # " LABEL " (6) + grafico + " " + valor (8)
    bw = max(4, w - 42)
    vt = hw.get("vram_total") or 8
    # con Flash-Next la VRAM y la memoria fija (GTT) van casi llenas a proposito: rojo solo al borde
    used = hw.get("vram_used") or 0
    lines = [f" VRAM {bar(used, vt, bw, 0.92, 0.98)} {used:.2f}/{vt:.0f}G · {_('libre')} {max(0, vt - used):.2f}G"]
    if hw.get("gtt_used") is not None:  # memoria fija de amdgpu (GTT); NVIDIA no la informa
        gt = hw.get("gtt_total") or 31
        lines.append(f" GTT  {bar(hw['gtt_used'], gt, bw, 0.97, 0.995)} {hw['gtt_used']:5.2f}/{gt:.0f}G")
    gpu = [s.get("gpu") for s in samples]
    rows = graph(gpu, gw, 4, 0, 100, color="level")
    peak = hw.get("gpu_max")
    lines += [f" {'GPU' if i == 0 else (_('pico') if i == 1 and peak is not None else ''):<5}{r} "
              + (f"{hw.get('gpu') or 0:d} %".rjust(7) if i == 0 else (f"{peak} %".rjust(7) if i == 1 and peak is not None else ""))
              for i, r in enumerate(rows)]
    power = [s.get("power") for s in samples]
    cap = hw.get("power_cap") or max([p for p in power if p] or [100])
    rows = graph(power, gw, 2, 0, cap, color=MAGENTA)
    lines += [f" {'WATT' if i == 0 else '':<5}{r} " + (f"{hw.get('power') or 0:.0f} W".rjust(7) if i == 0 else f"/{cap} W".rjust(7))
              for i, r in enumerate(rows)]
    temps = [s.get("temp") for s in samples]
    lines.append(f" TEMP {''.join(graph(temps, gw, 1, 30, 90, color='level'))} " + f"{hw.get('temp') or 0:.0f} °C".rjust(7))
    lines.append(" " + _("núcleo {mhz} MHz · {mem} · ventilador {fan}", mhz=hw.get('sclk') or 0, mem=mclk_text(hw), fan=fan_text(hw)))
    if energy:
        lines.append(energy_line(energy, w))
    return box(" · ".join(x for x in ("gpu", hw.get("gpu_name")) if x), lines, width)


def money(v):
    return f"$ {v:.2f}" if v < 100 else f"$ {v:,.0f}".replace(",", ".")


def energy_line(e, w):
    """kWh de la GPU hoy y en el mes, con un gráfico de un renglón por día del mes (y el costo si hay tarifa)."""
    text = _("hoy {d} kWh · mes {m} kWh", d=f"{e.get('day_kwh', 0):.2f}", m=f"{e.get('month_kwh', 0):.1f}")
    if e.get("price") is not None:
        text += f" · {money(e['day_cost'])} / {money(e['month_cost'])}"
    days = e.get("month_days") or []
    gw = max(0, min(len(days), w - 8 - len(text)))
    spark = graph(days, gw, 1, 0, color=MAGENTA)[0] + " " if gw else ""
    return f" {'kWh':<5}{spark}{text}"


def fan_text(hw):
    if hw.get("fan") is not None:
        return f"{hw['fan']} rpm"
    return f"{hw['fan_pct']} %" if hw.get("fan_pct") is not None else "?"


def mclk_text(hw):
    if not hw.get("mclk"):
        return _("memoria ?")
    tags = [_("fija") if hw["mclk_mode"] == "manual" else "auto"] if hw.get("mclk_mode") else []
    if hw.get("mclk_oc"):  # scripts/install-gpu-mclk.sh --oc
        tags.append(f"OC {hw['mclk_oc']}")
    return _("memoria {mhz} MHz", mhz=hw['mclk']) + (f" ({', '.join(tags)})" if tags else "")


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
    lines.append(" " + _("TEMP {t} °C · núcleo más caliente {p} °C", t=f"{temp:.0f}", p=f"{peak:.0f}") if temp is not None and peak is not None
                 else f" TEMP {temp:.0f} °C" if temp is not None else " " + _("TEMP sensor no disponible"))
    lines.append(" " + _("carga {load} · {n} hilos", load=f"{hw.get('load', 0):.2f}", n=len(cores)))
    return box(" · ".join(x for x in ("cpu", hw.get("cpu_name")) if x), lines, width)


def mem_box(hw, width, disks=None):
    w = width - 2
    bw = w - 24
    lines = [
        f" RAM   {bar(hw.get('ram_used'), hw.get('ram_total'), bw)} {hw.get('ram_used', 0):5.1f}/{hw.get('ram_total', 0):.0f}G",
        f" {L('caché', 5)} {bar(hw.get('ram_cache'), hw.get('ram_total'), bw)} {hw.get('ram_cache', 0):5.1f}G",
    ]
    if hw.get("swap_total"):
        lines.append(f" swap  {bar(hw.get('swap_used'), hw.get('swap_total'), bw)} {hw.get('swap_used', 0):5.1f}/{hw.get('swap_total', 0):.0f}G")
    if disks is None:
        return box(_("memoria · RAM"), lines, width)
    lines += disk_lines(disks, w)
    return box(_("memoria · discos"), lines, width)


def gb(n):
    return f"{n / 1e12:.1f}T" if n >= 1e12 else f"{n / 1e9:.0f}G"


def disk_lines(disks, w):
    """Una fila por disco: tipo, punto de montaje, uso y lo que lee en este momento (resaltado si un modelo
    está leyendo del disco: con los expertos fuera de la RAM, el HDD pone el techo de tok/s)."""
    bw = max(6, w - 52)
    out = []
    for d in disks:
        rate = d.get("read_mbs")
        rtxt = "    -- MB/s" if rate is None else f"{rate:6.1f} MB/s"
        mark = CYAN if rate and rate >= 20 else DIM if not rate or rate < 1 else ""
        tag = f" {DIM}{_('modelos')}{RESET}" if d.get("models") else ""
        mnt = d["mount"] if len(d["mount"]) <= 10 else "…" + d["mount"][-9:]
        used = f"{gb(d['used'])}/{gb(d['total'])}"
        out.append(f" {d['kind']:<5}{mnt:<11}{bar(d['used'], d['total'], bw, 0.85, 0.95)} {used:>11} "
                   f"{mark}▼{rtxt}{RESET}{tag}")
    return out


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
    return f"{p['cpu']:.0f}% · {p['cpu'] / 100:.1f} " + _("núcl.")


def external_lines(cpu, w):
    out = []
    for p in external_runs(cpu):
        out.append(f" {YELLOW}◆ {_('prueba fuera del panel')}{RESET} · {p['name']} · cpu {cpu_txt(p)}")
        out.append(f"   {(p.get('model') or '?')[:w - 4]}")
    return out


def models_box(state, stats, width, now, cpu=None, graph_rows=3, gap=True):
    w = width - 2
    svc = state["svc"]
    loaded = [k for k, v in (state["text"].get("models") or {}).items() if v.get("status") in ("loaded", "loading")]
    icon, label = SVC.get(svc["text"], ("?", svc["text"]))
    lines = [f" {L('texto', 9)} {icon} {_(label)}", f"   {(loaded[0] if loaded else (_('ninguno cargado') if svc['text'] == 'active' else '-'))[:w - 4]}"]
    img = state.get("img") or {}
    icon, label = SVC.get(svc["img"], ("?", svc["img"]))
    pr = img.get("progress") or {}
    gen = " · " + _("paso {a}/{b}", a=pr['step'], b=pr['steps']) if svc["img"] == "active" and pr.get("steps") else ""
    lines.append(f" {L('imágenes', 9)} {icon} {_(label)}{gen}")
    lines.append(f"   {(img.get('selected') or '-')[:w - 4]}")
    lines += external_lines(cpu, w)
    resp = (stats or {}).get("text") or []
    if resp:
        last = resp[-1]
        if gap:
            lines.append("")
        lines.append(" " + _("última: {g} tok/s · prompt {p} · {ago}", g=f"{last['g_tps']:.1f}", p=f"{last['p_tps']:.0f}", ago=ago(last['t'], now)))
        tps = [r["g_tps"] for r in resp]
        top = max(tps)
        rows = graph(tps, w - 10, graph_rows, 0, top, color=BLUE)
        lines += [f" {(f'{top:.0f}' if i == 0 else ''):>4} {r}" for i, r in enumerate(rows)]
        lines.append(f" {'':>4} " + _("tok/s de las últimas {n} respuestas", n=min(len(tps), w - 10)))
    return box(_("modelos"), lines, width)


LIVE_ICON = {"gen": ("●", "generando"), "prompt": ("◐", "procesando el prompt"), "idle": ("○", "esperando")}


def live_status(live):
    live = live or {}
    icon, label = LIVE_ICON.get(live.get("state"), LIVE_ICON["idle"])
    return f"{icon} {_(label)}"


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
        lines.append(f" {L('prompt', 8)} {_('media')} {fmt(live['prompt_tps_prev'])} tok/s {DIM}{_('(pedido anterior)')}{RESET}")
    else:
        lines.append(f" {L('prompt', 8)} {_('media')} {fmt(live.get('prompt_tps'))} tok/s")
    lines += ["      " + row for row in graph(list(prompt_hist or []), w - 8, graph_height, 0, color=CYAN)]
    lines.append(f" {L('salida', 8)} {_('media')} {fmt(live.get('gen_avg_tps'))} · 3 s {fmt(live.get('tps'))} tok/s")
    lines += ["      " + row for row in graph(list(tps_hist or []), w - 8, graph_height, 0, color=GREEN)]
    progress = live.get("prompt_progress")
    pct = f"{progress * 100:5.0f} %" if progress is not None else "   -- %"
    lines.append(f" {L('prompt', 8)} {bar(progress, 1, max(1, w - 28))} {pct}")
    size, used = live.get("ctx_size"), live.get("ctx_used")
    ctx = f"{used if used is not None else 0:>6}/{size}" if size else "    --/--"
    lines.append(f" {L('contexto', 8)} {bar(used, size, max(1, w - 28))} {ctx}")
    processed = live.get("prompt_tokens")
    generated = live.get("n_gen")
    lines.append(f" {L('tokens', 8)} prompt {processed if processed is not None else '--'} · {_('salida')} {generated if generated is not None else '--'}")
    approximate = "~" if live.get("state") == "prompt" else ""
    lines.append(f" {L('reuso', 8)} {live.get('prompt_cached', 0)} tokens · {_('prompt total')} {approximate}{live.get('prompt_total') or '--'}")
    lines.append(f" {L('tiempo', 8)} prompt {duration(live.get('prompt_elapsed'))} · {_('salida')} {duration(live.get('gen_elapsed'))}")
    eta = None
    if live.get("state") == "prompt" and live.get("prompt_total") and live.get("prompt_tps"):
        remaining = max(0, live["prompt_total"] - live.get("prompt_cached", 0) - (processed or 0))
        eta = remaining / live["prompt_tps"]
    avg = live.get("gen_avg_tps")
    latency = f"{1000 / avg:.1f} ms/tok" if avg else "-- ms/tok"
    lines.append(" " + _("estimado prompt restante ~{t}", t=duration(eta)) if eta is not None else f" {L('salida', 8)} {latency}")
    rate = live.get("draft_rate")
    mtp = f"{rate * 100:.1f} % · {fmt(live.get('draft_len'))} " + _("tok/paso") if rate is not None else _("-- (se informa al finalizar)")
    lines.append(f" {L('MTP últ.', 8)} {mtp}")
    resp = (stats or {}).get("text") or []
    toks = sum(r.get("g_tok") or 0 for r in resp)
    seconds = sum((r.get("g_tok") or 0) / r["g_tps"] for r in resp if r.get("g_tps"))
    if graph_height > 1:
        lines.append(" " + _("historial {n} resp. · {t} tok · {r} tok/s", n=len(resp), t=toks, r=fmt(toks / seconds if seconds else None)))
    age = live.get("age")
    freshness = _("último dato hace {t}", t=duration(age)) if age is not None else _("sin datos del pedido")
    lines.append(" " + (YELLOW if live.get("stale") else DIM) + freshness + RESET)
    return box(_("en vivo"), lines, width)


def uptime(seconds):
    """Cuánto hace que anda, corto: 40 s, 12 min, 3 h 05, 2 d 4 h."""
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d}"
    return f"{s // 86400} d {s % 86400 // 3600} h"


def unit_cell(label, unit, width, lw=13):
    """● nombre  hace cuánto anda (o el estado si no anda), en `width` caracteres visibles."""
    if not unit:
        return vpad(f" {DIM}· {label:<{lw}}{_('no instalado')}{RESET}", width)
    icon, state = SVC.get(unit["state"], ("?", unit["state"]))
    color = GREEN if unit["state"] == "active" else RED if unit["state"] == "failed" else YELLOW if icon == "◌" else DIM
    tail = uptime(unit.get("up")) if unit["state"] == "active" else _(state)
    return vpad(f" {color}{icon}{RESET} {label:<{lw}}{tail}", width)


def cell(icon_color, icon, label, tail, width, lw=13):
    return vpad(f" {icon_color}{icon}{RESET} {label:<{lw}}{tail}", width)


def voice_names(audio):
    """Modelos de voz en corto: «large-v3-turbo · daniela» (sin ggml-, cuantización, idioma ni calidad)."""
    import re
    stt = re.sub(r"-q\d.*$", "", (audio.get("stt") or "").removeprefix("ggml-").removesuffix(".bin"))
    tts = (audio.get("tts") or "").split("-")
    tts = tts[1] if len(tts) == 3 else "-".join(tts)  # es_AR-daniela-high
    return [n for n, ok in ((stt, audio.get("stt_ok")), (tts, audio.get("tts_ok"))) if ok and n]


_net = {"t": 0, "ip": None, "host": None}


def local_net():
    """IP de la red local y nombre de la máquina (se recalcula cada minuto; no manda paquetes)."""
    import socket
    if time.time() - _net["t"] > 60:
        _net["t"] = time.time()
        _net["host"] = socket.gethostname()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect(("10.255.255.255", 1))
                _net["ip"] = sk.getsockname()[0]
        except OSError:
            _net["ip"] = None
    return _net["ip"], _net["host"]


def _hex_ip(h):
    import socket
    raw = bytes.fromhex(h)
    raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))  # palabras de 32 bits en orden del host
    if len(raw) == 4:
        return socket.inet_ntop(socket.AF_INET, raw)
    ip = socket.inet_ntop(socket.AF_INET6, raw)
    return ip[7:] if ip.startswith("::ffff:") else ip


def tcp_clients(texts, ports):
    """{ip: {puertos}} de las conexiones establecidas desde otras máquinas a `ports` (/proc/net/tcp y tcp6)."""
    out = {}
    for text in texts:
        for line in text.splitlines()[1:]:
            f = line.split()
            if len(f) < 4 or f[3] != "01":  # 01 = ESTABLISHED
                continue
            try:
                lip, lport = f[1].split(":")
                rip = _hex_ip(f[2].split(":")[0])
                port = int(lport, 16)
            except ValueError:
                continue
            if port in ports and rip not in ("127.0.0.1", "::1") and rip != _hex_ip(lip):
                out.setdefault(rip, set()).add(port)
    return out


def connected_clients():
    texts = []
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            texts.append(open(f).read())
        except OSError:
            pass
    return tcp_clients(texts, {C.CHAT_PORT, C.IMG_PORT, C.PANEL_PORT})


def who(ip):
    return _("esta máquina") if ip in ("127.0.0.1", "::1") else ip


SIDE_LABEL = {"text": "texto", "img": "imágenes"}


def services_box(state, width, now, stats=None, clients=None):
    """Servicios en dos columnas (estado y hace cuánto andan) y la red: IP, clientes de la compuerta, último pedido."""
    w = width - 2
    k = state.get("kiosk") or {}
    units = k.get("units") or {}
    half = w // 2 + 2  # la columna de la derecha tiene rótulos más largos pero datos más cortos
    listening = k.get("listening") if "listening" in k else (state.get("auto") or {}).get("listening") or []
    ports = " ".join(f":{C.PUBLIC_PORTS[sd]}" for sd in listening if sd in C.PUBLIC_PORTS)
    gate = cell(GREEN, "●", _("compuerta"), ports, w - half, 15) if listening else \
        cell(DIM, "○", _("compuerta"), _("sin proxy"), w - half, 15)
    audio = state.get("audio") or {}
    ok = [audio.get("stt_ok"), audio.get("tts_ok")]
    voice_icon, voice_color = ("●", GREEN) if all(ok) else ("◐", YELLOW) if any(ok) else ("○", DIM)
    names = voice_names(audio)
    lines = [
        unit_cell("panel", units.get(C.PANEL_SERVICE), half) + gate,
        unit_cell("llama-server", units.get(C.SERVICE), half) + unit_cell("KoboldCpp", units.get(C.IMG_SERVICE), w - half, 15),
        cell(voice_color, voice_icon, _("voz"), " · ".join(names) or _("sin modelos"), half)
        + unit_cell("Home Assistant", units.get("homeassistant"), w - half, 15),
    ]
    ip, host = local_net()
    lines.append(f" {L('red', 9)} {ip or _('sin red')} · {host} · panel :{C.PANEL_PORT}")
    g = k.get("gate") or {}
    port_name = {C.CHAT_PORT: _("texto"), C.IMG_PORT: _("imágenes"), C.PANEL_PORT: "panel"}
    conns = clients if clients is not None else {}
    parts = [f"{GREEN}{ip}{RESET} {DIM}{'+'.join(port_name.get(pt, str(pt)) for pt in sorted(pts))}{RESET}"
             for ip, pts in sorted(conns.items())[:3]]
    recent = [c for c in g.get("clients") or [] if c["ip"] not in conns and not c["open"]][:max(0, 3 - len(parts))]
    parts += [f"{who(c['ip'])} {DIM}{ago(c['t'], now)}{RESET}" for c in recent]
    more = f" +{len(conns) - 3}" if len(conns) > 3 else ""
    lines.append(f" {L('clientes', 9)} " + (" · ".join(parts) + more if parts else f"{DIM}{_('nadie conectado')}{RESET}"))
    last = g.get("last")
    if last:
        what = _(SIDE_LABEL.get(last["side"], last["side"]))
        model = f" · {last['model']}" if last.get("model") else f" · {last['path']}"
        lines.append(f" {L('último', 9)} {who(last['ip'])} · {what}{model} · {ago(last['t'], now)}")
    else:  # sin la compuerta no se sabe quién pidió: la última respuesta de texto que midió el panel
        resp = (stats or {}).get("text") or []
        tail = f"{_('texto')} · {ago(resp[-1]['t'], now)}" if resp else "--"
        hint = f" {DIM}({_('con el proxy activo se ve quién')}){RESET}" if not listening else ""
        lines.append(f" {L('último', 9)} {tail}{hint}")
    return box(_("servicios · red"), lines, width)


EXPECTED_PROCS = ("llama-server", "koboldcpp")


def procs_box(cpu, width, max_rows=6, fill=False):
    """Procesos que interesan; con fill, los que se esperan y no andan completan las filas libres, atenuados."""
    lines = [f" {L('proceso', 14)}{'pid':>8}{'cpu':>18}{_('hilos'):>8}{'ram':>9}"]
    procs = (cpu.get("procs") or [])[:max_rows]
    for p in procs:
        lines.append(f" {p['name']:<14}{p['pid']:>8}{cpu_txt(p):>18}{p['threads']:>8}{p['rss'] / 2**30:>8.1f}G")
    if fill:
        running = {p["name"] for p in cpu.get("procs") or []}
        for name in EXPECTED_PROCS:
            if name not in running and len(lines) <= max_rows:
                lines.append(f" {DIM}{name:<14}{'--':>8}{_('apagado'):>18}{RESET}")
    if len(lines) == 1:
        lines.append(" " + _("(sin modelos corriendo)"))
    if fill:
        lines += [""] * (max_rows + 1 - len(lines))  # que la columna llegue hasta el pie
    return box(_("procesos"), lines, width)


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
        cpu_l += [(f" {L('hilos', 5)} " if i == 0 else "       ") + " ".join(r) for i, r in enumerate(rows)]
    if hw.get("cpu_temp") is not None:
        cpu_l.append(f" CPU {hw['cpu_temp']:.0f} °C")
    cpu_l.append(f" {len(cores)} {_('hilos')} · {_('carga')} {hw.get('load', 0):.1f} · RAM {hw.get('ram_used', 0):.1f}/{hw.get('ram_total', 0):.0f}G · GTT {hw.get('gtt_used', 0):.1f}/31G")

    svc = state["svc"]
    loaded = [k for k, v in (state["text"].get("models") or {}).items() if v.get("status") in ("loaded", "loading")]
    icon, label = SVC.get(svc["text"], ("?", svc["text"]))
    mod = [f" {_('texto')} {icon} {_(label)}  {(loaded[0] if loaded else '')[:w - 22]}"]
    if svc["text"] == "active":
        st = live_status(live)
        if (live or {}).get("draft_rate") is not None:
            st += f" · MTP {live['draft_rate'] * 100:.0f} %"
        mod.append("   " + st[:w - 4])
    img = state.get("img") or {}
    icon, label = SVC.get(svc["img"], ("?", svc["img"]))
    pr = img.get("progress") or {}
    gen = " " + _("paso {a}/{b}", a=pr['step'], b=pr['steps']) if svc["img"] == "active" and pr.get("steps") else ""
    mod.append(f" {_('imágenes')} {icon} {_(label)}{gen}")
    mod += external_lines(cpu, w)
    resp = (stats or {}).get("text") or []
    if resp:
        r = resp[-1]
        mod.append(" " + _("última: {g} tok/s · prompt {p} · {ago}", g=f"{r['g_tps']:.1f}", p=f"{r['p_tps']:.0f}", ago=ago(r['t'], now)))
        mod += [" " + x for x in graph([x["g_tps"] for x in resp], w - 2, max(1, gh - 1), 0)]

    procs = [f" {p['name'][:12]:<12}{cpu_txt(p):>17}{p['threads']:>4} {_('hilos')}{p['rss'] / 2**30:>6.1f}G"
             for p in (cpu.get("procs") or [])[:3]] or [" " + _("(sin modelos corriendo)")]
    return [box("gpu", gpu, width), box(_("cpu · memoria"), cpu_l, width), box(_("modelos"), mod, width), box(_("procesos"), procs, width)]


FOOTER = " Para volver al escritorio: S dos veces · «Salir del modo IA» en el panel · Ctrl+Alt+F2 y  sudo llama-modo-ia off"
FOOTER_SHORT = " Volver al escritorio: S dos veces · Ctrl+Alt+F2 y  sudo llama-modo-ia off"
AI_MODE_HELPER = "/usr/local/sbin/llama-modo-ia"
CONFIRM_SECS = 5


def footer(width, notice=None):
    """Ultima linea: como volver al escritorio, o el aviso de la tecla S (confirmar / volviendo / error)."""
    if notice:
        msg = _(notice, secs=CONFIRM_SECS) if "{secs}" in notice else _(notice)  # un error con llaves no se formatea
        return (" " + msg)[:width], "warn"
    full, short = _(FOOTER), _(FOOTER_SHORT)
    return (full if len(full) <= width else short)[:width], "dim"


def leave_ai_mode():
    """Sale del modo IA por el panel (que tambien saca las opciones de modo IA de los modelos); si el panel no
    responde, con el ayudante directamente. Al salir, systemd apaga este dashboard. Devuelve un error o None."""
    try:
        req = urllib.request.Request(PANEL + "/api/action", data=json.dumps({"do": "ai_mode_off"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.loads(r.read())
        if res.get("ok"):
            return None
    except Exception:  # noqa: BLE001
        pass
    import subprocess
    try:
        r = subprocess.run(["sudo", "-n", AI_MODE_HELPER, "off"], capture_output=True, text=True, timeout=60)
        return None if r.returncode == 0 else (r.stderr or r.stdout).strip() or "el ayudante falló"
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)


def render(state, stats, hist, cpu, width=120, height=40, now=None, live=None, tps_hist=None, prompt_hist=None,
           notice=None, clients=None):
    """Devuelve las lineas del dashboard como [(texto, estilo)]; estilo en {"", "title", "dim", "good", "warn", "bad"}.
    Con 120x36 o mas usa dos columnas; si no, una columna compacta que se achica hasta entrar en la altura."""
    now = now or time.time()
    width = max(40, width)
    set_lang(kiosk_lang())
    out = []
    clock = time.strftime("%H:%M:%S", time.localtime(now))
    out.append(((" " + _("PANEL IA · modo IA")).ljust(width - len(clock) - 1)[:width - len(clock) - 1] + clock + " ", "title"))
    if state is None:
        out.append(("", ""))
        out.append(("  " + _("Sin conexión con el panel. Reintentando…")[:width], "bad"))
        out.append(("", ""))
        out.append(footer(width, notice))
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
        out = out[:height - 1]
        out.append(footer(width, notice))
        return out
    left_w = width // 2
    right_w = width - left_w - 1
    room = height - 2  # sin el título ni el pie
    energy = (state.get("kiosk") or {}).get("energy")
    left = gpu_box(hw, samples, left_w, energy) + mem_box(hw, left_w, cpu.get("disks"))
    extra = services_box(state, left_w, now, stats, clients) if state.get("kiosk") else []
    models = []
    for graph_rows, gap in ((3, True), (3, False), (2, False), (1, False)):  # el gráfico se achica si no entra
        models = models_box(state, stats, left_w, now, cpu, graph_rows, gap)
        if len(left) + len(models) + len(extra) <= room:
            break
    if len(left) + len(models) + len(extra) > room:
        extra = []  # pantalla baja: primero se pierde la caja de servicios
    left += models + extra
    right = cpu_box(cpu, hw, right_w)
    procs_min = 2 + 1 + min(3, max(1, len(cpu.get("procs") or [])))  # bordes, encabezado y hasta 3 procesos
    for gh in (3, 2, 1):  # gráficos de «en vivo» lo más altos que entren
        live_panel = live_box(live, stats, tps_hist, right_w, prompt_hist, graph_height=gh)
        if len(right) + len(live_panel) + procs_min <= room:
            break
    running = {p["name"] for p in cpu.get("procs") or []}
    procs_all = 3 + len((cpu.get("procs") or [])[:8]) + sum(n not in running for n in EXPECTED_PROCS)
    target = min(room, max(len(left), len(right) + len(live_panel) + procs_all))  # parejo con la izquierda
    proc_rows = max(1, min(8, target - len(right) - len(live_panel) - 3))
    right += live_panel + procs_box(cpu, right_w, proc_rows, fill=True)
    for l in side_by_side(left, right):
        out.append((l, ""))
    out = out[:height - 1]
    out.append(footer(width, notice))
    return out


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
    try:
        state["kiosk"] = fetch("/api/kiosk")
    except Exception:  # noqa: BLE001
        pass  # panel viejo: el dashboard anda sin servicios ni energía
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
        last_live_fetch, cpu, clients = 0, None, {}
        armed, notice = 0, None  # tecla S: primer toque arma, el segundo (dentro de CONFIRM_SECS) sale
        while True:
            if armed and time.time() - armed > CONFIRM_SECS and notice and notice.startswith("¿"):
                armed, notice = 0, None
            if source_changed(started):  # codigo nuevo: reiniciar el proceso con la version actual
                curses.endwin()
                os.execv(sys.executable, [sys.executable, "-m", "llama_panel.kiosk"])
            if time.time() - last_fetch > REST_EVERY - 0.2:
                state, stats, hist = snapshot()
                cached, last_fetch = (state, stats if stats is not None else cached[1], hist), time.time()
                cpu = sampler.sample()
                clients = connected_clients()
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
            for y, (text, style) in enumerate(render(*cached, cpu, width=w - 1, height=h - 1, live=live, tps_hist=tps_hist,
                                                     prompt_hist=prompt_hist, notice=notice, clients=clients)):
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
            if scr.getch() in (ord("s"), ord("S")):
                if armed and time.time() - armed <= CONFIRM_SECS:
                    armed, notice = 0, "Volviendo al escritorio… (puede tardar unos segundos)"  # footer() lo traduce
                    last_fetch = 0  # redibujar ya con el aviso
                    scr.erase()
                    scr.addstr(0, 0, footer(w - 1, notice)[0], curses.A_BOLD)
                    scr.refresh()
                    err = leave_ai_mode()
                    notice = f"{_('No se pudo salir del modo IA:')} {err}" if err else notice
                else:
                    armed, notice = time.time(), "¿Volver al escritorio? Apretá S de nuevo en {secs} s"

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
                              prompt_hist=[live["prompt_tps"]] if live and live.get("prompt_tps") is not None else [],
                              clients=connected_clients()):
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
