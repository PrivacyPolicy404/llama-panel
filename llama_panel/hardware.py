"""Sensores de la GPU (amdgpu en sysfs) y de la RAM, con una historia corta muestreada en segundo plano."""
import collections
import glob
import os
import threading
import time
from pathlib import Path

GIB = 2 ** 30
INTERVAL = 2  # segundos entre muestras
HIST = collections.deque(maxlen=330)  # ~11 minutos


def _read(path, cast=int):
    try:
        return cast(Path(path).read_text().strip())
    except Exception:  # noqa: BLE001
        return None


def _gpu_dir():
    for d in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        if os.path.exists(os.path.join(d, "mem_info_vram_total")):
            return d
    return None


GPU_DIR = _gpu_dir()
HWMON = next(iter(sorted(glob.glob(f"{GPU_DIR}/hwmon/hwmon*"))), None) if GPU_DIR else None


def cpu_temperatures(root=Path("/sys/class/hwmon")):
    """coretemp/k10temp: temperatura del paquete y máximo de los núcleos disponibles."""
    package, cores = [], []
    for dev in Path(root).glob("hwmon*"):
        try:
            if (dev / "name").read_text().strip() not in ("coretemp", "k10temp"):
                continue
            for label_file in dev.glob("temp*_label"):
                label = label_file.read_text().strip()
                value = _read(label_file.with_name(label_file.name.replace("_label", "_input")))
                if value is None:
                    continue
                if label.startswith("Package") or label in ("Tctl", "Tdie"):
                    package.append(value / 1000)
                elif label.startswith("Core"):
                    cores.append(value / 1000)
        except OSError:
            continue
    return {"cpu_temp": max(package or cores) if package or cores else None,
            "cpu_core_temp_max": max(cores) if cores else None}

# gpu_busy_percent es instantaneo y la GPU trabaja a rafagas (los MoE la alternan con el CPU en cada capa):
# una lectura cada INTERVAL es casi ruido. Este hilo lo lee cada FAST_INTERVAL y sample() usa el promedio.
FAST_INTERVAL = 0.1
_fast = {"busy_sum": 0, "busy_max": 0, "n": 0, "power_sum": 0.0, "power_n": 0}
_fast_lock = threading.Lock()


def _fast_loop():
    while True:
        b = _read(f"{GPU_DIR}/gpu_busy_percent") if GPU_DIR else None
        p = (_read(f"{HWMON}/power1_input") or _read(f"{HWMON}/power1_average")) if HWMON else None
        with _fast_lock:
            if b is not None:
                _fast["busy_sum"] += b
                _fast["busy_max"] = max(_fast["busy_max"], b)
                _fast["n"] += 1
            if p is not None:
                _fast["power_sum"] += p / 1e6
                _fast["power_n"] += 1
        time.sleep(FAST_INTERVAL)


def _take_fast():
    """Promedios desde la ultima muestra (None si el hilo rapido no corrio)."""
    with _fast_lock:
        out = {}
        if _fast["n"]:
            out["gpu"] = round(_fast["busy_sum"] / _fast["n"])
            out["gpu_max"] = _fast["busy_max"]
        if _fast["power_n"]:
            out["power"] = round(_fast["power_sum"] / _fast["power_n"], 1)
        _fast.update(busy_sum=0, busy_max=0, n=0, power_sum=0.0, power_n=0)
    return out


def _mclk():
    """Reloj de la memoria de la GPU: actual, maximo y si esta fijo (manual) o en automatico."""
    try:
        levels = Path(f"{GPU_DIR}/pp_dpm_mclk").read_text().splitlines()
        mhz = [int(l.split(":")[1].strip().split("Mhz")[0]) for l in levels]
        cur = next(m for l, m in zip(levels, mhz) if "*" in l)
        mode = Path(f"{GPU_DIR}/power_dpm_force_performance_level").read_text().strip()
        return {"mclk": cur, "mclk_max": max(mhz), "mclk_mode": mode}
    except Exception:  # noqa: BLE001
        return {}


def sample():
    s = {"t": round(time.time(), 1)}
    s.update(cpu_temperatures())
    if GPU_DIR:
        used, total = _read(f"{GPU_DIR}/mem_info_vram_used"), _read(f"{GPU_DIR}/mem_info_vram_total")
        if used is not None and total:
            s["vram_used"], s["vram_total"] = round(used / GIB, 2), round(total / GIB, 2)
        gtt = _read(f"{GPU_DIR}/mem_info_gtt_used")
        if gtt is not None:
            s["gtt_used"] = round(gtt / GIB, 2)
        s["gpu"] = _read(f"{GPU_DIR}/gpu_busy_percent")
        s.update(_mclk())
    if HWMON:
        t = _read(f"{HWMON}/temp1_input")
        crit = _read(f"{HWMON}/temp1_crit")
        p = _read(f"{HWMON}/power1_input") or _read(f"{HWMON}/power1_average")
        cap = _read(f"{HWMON}/power1_cap")
        clk = _read(f"{HWMON}/freq1_input")
        s.update({
            "temp": t / 1000 if t is not None else None,
            "temp_crit": crit / 1000 if crit else None,
            "power": round(p / 1e6, 1) if p is not None else None,
            "power_cap": round(cap / 1e6) if cap else None,
            "fan": _read(f"{HWMON}/fan1_input"),
            "sclk": round(clk / 1e6) if clk else None,
        })
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
        s["ram_total"] = round(mem["MemTotal"] / GIB, 2)
        s["ram_used"] = round((mem["MemTotal"] - mem["MemAvailable"]) / GIB, 2)
        s["ram_cache"] = round((mem.get("Cached", 0) + mem.get("Buffers", 0)) / GIB, 2)
        s["swap_used"] = round((mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) / GIB, 2)
        s["swap_total"] = round(mem.get("SwapTotal", 0) / GIB, 2)
    except Exception:  # noqa: BLE001
        pass
    s["load"] = round(os.getloadavg()[0], 2)
    s.update(_take_fast())  # actividad y potencia promediadas (pisan la lectura instantanea)
    return s


def latest():
    return HIST[-1] if HIST else sample()


def _loop():
    while True:
        try:
            HIST.append(sample())
        except Exception:  # noqa: BLE001
            pass
        time.sleep(INTERVAL)


def start_sampler():
    threading.Thread(target=_fast_loop, name="hw-fast", daemon=True).start()
    threading.Thread(target=_loop, name="hw-sampler", daemon=True).start()
