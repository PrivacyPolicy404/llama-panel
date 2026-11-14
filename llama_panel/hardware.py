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


def sample():
    s = {"t": round(time.time(), 1)}
    if GPU_DIR:
        used, total = _read(f"{GPU_DIR}/mem_info_vram_used"), _read(f"{GPU_DIR}/mem_info_vram_total")
        if used is not None and total:
            s["vram_used"], s["vram_total"] = round(used / GIB, 2), round(total / GIB, 2)
        gtt = _read(f"{GPU_DIR}/mem_info_gtt_used")
        if gtt is not None:
            s["gtt_used"] = round(gtt / GIB, 2)
        s["gpu"] = _read(f"{GPU_DIR}/gpu_busy_percent")
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
    except Exception:  # noqa: BLE001
        pass
    s["load"] = round(os.getloadavg()[0], 2)
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
    threading.Thread(target=_loop, name="hw-sampler", daemon=True).start()
