#!/usr/bin/env python3
"""Panel web para controlar llama-server (texto) y KoboldCpp (imagenes). Solo biblioteca estandar.

Uso:
  python3 panel.py --install   # instala los servicios de systemd (usuario) y arranca el panel
  python3 panel.py             # corre el panel en primer plano (puerto 8090)

La interfaz vive en static/ y habla con este archivo por una API JSON en /api/*.
"""
import collections
import glob
import json
import mimetypes
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CFG_DIR = Path(os.environ.get("LLAMA_PANEL_DIR", HOME / ".config" / "llama-panel"))
SETTINGS_FILE = CFG_DIR / "settings.json"
PRESET_FILE = CFG_DIR / "presets.ini"
IMG_ENV = CFG_DIR / "kobold.env"
MODELS_DIR = Path(os.environ.get("LLAMA_MODELS_DIR", "/mnt/hdd1/gen/models"))
IMG_DIR = Path(os.environ.get("LLAMA_IMG_DIR", "/mnt/hdd1/gen/imgmodels"))
LLAMA_SERVER = os.environ.get("LLAMA_SERVER_BIN", str(HOME / "llama.cpp/build/bin/llama-server"))
KOBOLD_BIN = os.environ.get("KOBOLD_BIN", "/mnt/hdd1/gen/koboldcpp/koboldcpp")
SERVICE = "llama-router"
IMG_SERVICE = "kobold-img"
PANEL_SERVICE = "llama-panel"
PANEL_PORT = int(os.environ.get("LLAMA_PANEL_PORT", "8090"))
CHAT_PORT = 8080
IMG_PORT = 5001

DEFAULT_GLOBAL = "n-gpu-layers = 99\nctx-size = 8192\nreasoning-format = deepseek"
LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\s*=\s*\S.*$")
MTP_RE = re.compile(r"(^|[-_.])MTP([-_.]|$)")
REASONING_OPTS = [("", "por defecto"), ("auto", "auto"), ("on", "activado"), ("off", "apagado")]
IMG_EXT = (".gguf", ".safetensors", ".ckpt")
ARG_RE = re.compile(r"^[A-Za-z0-9_./:=+,-]+$")
SETTINGS_LOCK = threading.Lock()


# ---------- ajustes ----------
def clean_lines(text):
    """Devuelve (lineas_validas, lineas_con_error). Formato: clave = valor."""
    ok, bad = [], []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if LINE_RE.match(line):
            k, v = [x.strip() for x in line.split("=", 1)]
            ok.append(f"{k} = {v}")
        else:
            bad.append(line)
    return ok, bad


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except Exception:
        data = {}
    data.setdefault("global", DEFAULT_GLOBAL)
    data.setdefault("models", {})
    img = data.setdefault("image", {})
    img.setdefault("selected", "")
    img.setdefault("models", {})
    return data


def save_settings(data):
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(SETTINGS_FILE)


def _assign_ids(items):
    used = {}
    for m in items:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(m["key"]).stem)
        n = used.get(base, 0) + 1
        used[base] = n
        m["id"] = base if n == 1 else f"{base}_{n}"
    return items


# ---------- modelos de texto ----------
def scan_models():
    found = []
    if not MODELS_DIR.is_dir():
        return found
    seen = set()
    for p in sorted(MODELS_DIR.rglob("*.gguf")):
        if p.name.lower().startswith("mmproj"):
            continue
        try:
            real = os.path.realpath(p)
            size = os.path.getsize(real)
        except OSError:
            continue
        if real in seen or size < 50 * 1024 * 1024:
            continue
        seen.add(real)
        found.append({"key": str(p.relative_to(MODELS_DIR)), "path": str(p), "size": size})
    return _assign_ids(found)


def model_cfg(settings, m):
    default = {"mtp": bool(MTP_RE.search(Path(m["key"]).stem)), "nmax": "2", "reasoning": "", "ctx": "", "extra": ""}
    saved = settings["models"].get(m["key"]) or {}
    return {**default, **saved}


def build_preset(settings, models):
    glob_lines, _ = clean_lines(settings["global"])
    out = ["# Generado por llama-panel. No editar a mano: se reescribe al guardar.", "", "[*]"]
    out += glob_lines + [""]
    for m in models:
        cfg = model_cfg(settings, m)
        extra, _ = clean_lines(cfg.get("extra", ""))
        keys = {l.split("=", 1)[0].strip() for l in extra}
        fields = []
        if cfg.get("mtp"):
            fields += ["spec-type = draft-mtp", f"spec-draft-n-max = {cfg.get('nmax') or 2}"]
        if cfg.get("reasoning"):
            fields.append(f"reasoning = {cfg['reasoning']}")
        if cfg.get("ctx"):
            fields.append(f"ctx-size = {cfg['ctx']}")
        fields = [f for f in fields if f.split("=", 1)[0].strip() not in keys]  # lo de "extra" manda
        out += [f"[{m['id']}]", f"model = {m['path']}"] + fields + extra + [""]
    return "\n".join(out)


def write_preset():
    settings = load_settings()
    models = scan_models()
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    PRESET_FILE.write_text(build_preset(settings, models))
    return models


# ---------- modelos de imagen ----------
def _img_role(name):
    """Detecta si un archivo es un acompanante (VAE / codificador de texto) y con que opcion se carga."""
    n = name.lower()
    if "vae" in n:
        return "sdvae"
    if re.search(r"clip[-_]?l(?![a-z])", n):
        return "sdclip1"
    if re.search(r"clip[-_]?g(?![a-z])|t5", n):
        return "sdclip2"
    if re.search(r"qwen|llm|gemma|mistral|encoder", n):
        return "sdllm"
    return None


def scan_img_models():
    if not IMG_DIR.is_dir():
        return []
    files = []
    for p in sorted(IMG_DIR.rglob("*")):
        if p.suffix.lower() not in IMG_EXT or not p.is_file():
            continue
        if re.search(r"\s", str(p)):  # rutas con espacios no se pueden pasar por systemd de forma simple
            continue
        try:
            size = os.path.getsize(os.path.realpath(p))
        except OSError:
            continue
        files.append((p, size, _img_role(p.name)))
    mains = []
    for p, size, role in files:
        if role or size < 100 * 1024 * 1024:
            continue
        comps = []
        if p.parent != IMG_DIR:  # los acompanantes se toman de la misma subcarpeta
            seen = {}
            for q, _qs, qr in files:
                if qr and q.parent == p.parent and qr not in seen:
                    seen[qr] = q
            comps = [(flag, str(q)) for flag, q in sorted(seen.items())]
        mains.append({"key": str(p.relative_to(IMG_DIR)), "path": str(p), "size": size, "comps": comps})
    return _assign_ids(mains)


def pick_img(settings, models):
    sel = settings["image"].get("selected", "")
    for m in models:
        if m["key"] == sel:
            return m
    return models[0] if models else None


def build_kobold_args(m, extra=""):
    args = ["--sdmodel", m["path"]]
    for flag, path in m["comps"]:
        args += [f"--{flag}", path]
    args += ["--usevulkan", "0", "--host", "0.0.0.0", "--port", str(IMG_PORT)]
    try:
        args += shlex.split(extra or "")
    except ValueError:
        pass
    return args


def write_img_env():
    settings = load_settings()
    m = pick_img(settings, scan_img_models())
    if not m:
        return None
    cfg = settings["image"]["models"].get(m["key"]) or {}
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    IMG_ENV.write_text("KOBOLD_ARGS=" + " ".join(build_kobold_args(m, cfg.get("extra", ""))) + "\n")
    return m


# ---------- sistema ----------
def run(*cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def svc(action, name=SERVICE):
    return run("systemctl", "--user", action, name)


def svc_state(name=SERVICE):
    return run("systemctl", "--user", "is-active", name) or "unknown"


def svc_states():
    out = run("systemctl", "--user", "is-active", SERVICE, IMG_SERVICE).splitlines()
    out += ["unknown"] * (2 - len(out))
    return {"text": out[0], "img": out[1]}


def http_json(port, method, path, body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def router(method, path, body=None, timeout=3):
    return http_json(CHAT_PORT, method, path, body, timeout)


def router_error(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            j = json.loads(e.read())
            return (j.get("error") or {}).get("message") or str(j)
        except Exception:  # noqa: BLE001
            return f"HTTP {e.code}"
    return str(getattr(e, "reason", e))


def router_models():
    """{id: {status, failed}} segun el router; {} si no responde."""
    try:
        data = router("GET", "/models", timeout=2).get("data", [])
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for m in data:
        st = m.get("status") or {}
        out[m["id"]] = {"status": st.get("value", "unloaded"), "failed": bool(st.get("failed")),
                        "ctx_max": m.get("context_length")}
    return out


def wait_router(timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            router("GET", "/health", timeout=1)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


# ---------- hardware ----------
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
GIB = 2 ** 30


def hw_sample():
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


HIST = collections.deque(maxlen=330)  # ~11 minutos a 2 s


def sampler():
    while True:
        try:
            HIST.append(hw_sample())
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)


# ---------- estadisticas y registros ----------
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TIMING_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| +(prompt eval|eval) time = +([\d.]+) ms / +(\d+) tokens"
                       r".*?([\d.]+) tokens per second")
DRAFT_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| draft acceptance = ([\d.]+) \( *(\d+) accepted / *(\d+) generated")
LOADM_RE = re.compile(r"^\[(\d+)\].*load_model: loading model '(.+)'")
KIMG_START = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] Generating Image \((\d+) steps\)")
KIMG_END = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] Generating Media Complete")
PROGRESS_RE = re.compile(r"^([A-Za-z ]+): \d+/(\d+) steps")
_stats_cache = {"t": 0, "data": None}


def journal(name, n, fmt="cat"):
    return run("journalctl", "--user", "-u", name, "-n", str(n), "--no-pager", "-o", fmt, timeout=10)


def clean_log_line(line):
    if "\x1b[K" in line:  # barras de progreso que se reescriben: queda el ultimo estado
        parts = [p for p in line.split("\x1b[K") if p.strip()]
        line = parts[-1] if parts else ""
    return ANSI_RE.sub("", line).replace("\r", "")


def _journal_lines(name, n):
    """[(timestamp, mensaje)] usando el formato short-unix de journalctl."""
    out = []
    for line in journal(name, n, "short-unix").splitlines():
        ts, _, rest = line.partition(" ")
        _, _, msg = rest.partition(": ")
        try:
            out.append((float(ts), msg))
        except ValueError:
            continue
    return out


def text_stats():
    paths = {}
    for m in scan_models():
        paths[m["path"]] = m["id"]
        paths[os.path.realpath(m["path"])] = m["id"]
    pid_model, reqs = {}, {}
    for ts, msg in _journal_lines(SERVICE, 6000):
        if (mm := LOADM_RE.match(msg)):
            pid_model[mm[1]] = paths.get(mm[2]) or Path(mm[2]).stem
        elif (mm := TIMING_RE.match(msg)):
            key = (mm[1], mm[2])
            r = reqs.setdefault(key, {"t": ts, "model": pid_model.get(mm[1], "?"), "p_tok": None, "p_tps": None,
                                      "g_tok": None, "g_tps": None, "draft": None})
            if mm[3] == "prompt eval":
                r["p_tok"], r["p_tps"] = int(mm[5]), float(mm[6])
            else:
                r["g_tok"], r["g_tps"], r["t"] = int(mm[5]), float(mm[6]), ts
        elif (mm := DRAFT_RE.match(msg)):
            key = (mm[1], mm[2])
            if key in reqs:
                reqs[key]["draft"] = {"rate": float(mm[3]), "acc": int(mm[4]), "gen": int(mm[5])}
    return [r for r in reqs.values() if r["g_tps"] is not None][-60:]


def img_stats():
    gens, start = [], None
    for ts, msg in _journal_lines(IMG_SERVICE, 4000):
        msg = clean_log_line(msg)
        if (mm := KIMG_START.match(msg)):
            start = (int(mm[1]) * 3600 + int(mm[2]) * 60 + int(mm[3]), int(mm[4]))
        elif (mm := KIMG_END.match(msg)) and start:
            end = int(mm[1]) * 3600 + int(mm[2]) * 60 + int(mm[3])
            secs = (end - start[0]) % 86400
            gens.append({"t": ts, "steps": start[1], "secs": secs})
            start = None
    return gens[-40:]


def stats():
    if time.time() - _stats_cache["t"] > 5:
        _stats_cache["data"] = {"text": text_stats(), "img": img_stats()}
        _stats_cache["t"] = time.time()
    return _stats_cache["data"]


def logs(which, n=300):
    name = {"text": SERVICE, "img": IMG_SERVICE, "panel": PANEL_SERVICE}.get(which, SERVICE)
    out, prev = [], None
    for raw in journal(name, n).splitlines():
        line = clean_log_line(raw)
        if not line.strip():
            continue
        # "Decoding: 3/27 steps" seguido de "Decoding: 4/27 steps": queda solo la ultima linea
        mm = PROGRESS_RE.match(line)
        key = (mm[1], mm[2]) if mm else None
        if key and key == prev:
            out[-1] = line
        else:
            out.append(line)
        prev = key
    return out


# ---------- estado ----------
def state():
    st = svc_states()
    settings = load_settings()
    sel = pick_img(settings, scan_img_models())
    img = {"selected": sel["id"] if sel else None, "selected_key": sel["key"] if sel else "", "progress": None, "perf": None}
    if st["img"] == "active":
        try:
            p = http_json(IMG_PORT, "GET", "/sdapi/v1/progress", timeout=1.5)
            s = p.get("state") or {}
            img["progress"] = {"step": s.get("sampling_step", 0), "steps": s.get("sampling_steps", 0),
                               "eta": p.get("eta_relative", 0)}
            perf = http_json(IMG_PORT, "GET", "/api/extra/perf", timeout=1.5)
            img["perf"] = {k: perf.get(k) for k in ("total_img_gens", "queue", "idle", "uptime")}
        except Exception:  # noqa: BLE001
            pass
    return {
        "svc": st,
        "hw": HIST[-1] if HIST else hw_sample(),
        "text": {"models": router_models() if st["text"] == "active" else {}},
        "img": img,
    }


def config():
    settings = load_settings()
    imgs = scan_img_models()
    sel = pick_img(settings, imgs)
    h = HIST[-1] if HIST else hw_sample()
    return {
        "global": settings["global"],
        "models_dir": str(MODELS_DIR),
        "img_dir": str(IMG_DIR),
        "vram_total": h.get("vram_total"),
        "reasoning_opts": REASONING_OPTS,
        "ports": {"chat": CHAT_PORT, "img": IMG_PORT},
        "text_models": [{"key": m["key"], "id": m["id"], "size": m["size"], "cfg": model_cfg(settings, m)}
                        for m in scan_models()],
        "img_models": [{"key": m["key"], "id": m["id"], "size": m["size"],
                        "comps": [(f, Path(p).name) for f, p in m["comps"]],
                        "extra": (settings["image"]["models"].get(m["key"]) or {}).get("extra", "")}
                       for m in imgs],
        "img_selected": sel["key"] if sel else "",
    }


# ---------- acciones ----------
def ok(msg, **kw):
    return {"ok": True, "msg": msg, **kw}


def fail(msg, **kw):
    return {"ok": False, "msg": msg, **kw}


def free_vram_for(which):
    """Apaga el otro servicio si esta prendido. Devuelve una nota para el usuario."""
    other, label = (IMG_SERVICE, "Generador de imagenes") if which == "text" else (SERVICE, "Servidor de texto")
    if svc_state(other) in ("active", "activating"):
        svc("stop", other)
        return f" {label} apagado para liberar VRAM."
    return ""


def do_action(action):
    if action == "stop":
        svc("stop")
        return ok("Servidor de texto apagado.")
    if action in ("start", "restart"):
        note = free_vram_for("text")
        n = len(write_preset())
        out = svc("restart" if action == "restart" else "start")
        verb = "Reiniciado" if action == "restart" else "Encendido"
        return ok(f"Servidor de texto {verb.lower()} ({n} modelos).{note} {out}".strip())
    if action in ("img_start", "img_restart"):
        m = write_img_env()
        if not m:
            return fail(f"No hay modelos de imagen en {IMG_DIR}.")
        note = free_vram_for("img")
        out = svc("restart" if action == "img_restart" else "start", IMG_SERVICE)
        verb = "reiniciado" if action == "img_restart" else "encendido"
        return ok(f"Generador de imagenes {verb} con {m['id']}.{note} {out}".strip())
    if action == "img_stop":
        svc("stop", IMG_SERVICE)
        return ok("Generador de imagenes apagado.")
    if action == "stop_all":
        svc("stop")
        svc("stop", IMG_SERVICE)
        return ok("Todo apagado: la VRAM queda libre.")
    return fail("Accion desconocida.")


def ensure_router():
    """Prende el router si hace falta (apagando imagenes). Devuelve (ok, nota)."""
    note = ""
    if svc_state() != "active":
        note = free_vram_for("text")
        write_preset()
        svc("start")
        note = " Servidor de texto encendido." + note
    if not wait_router():
        return False, "El servidor de texto no responde. Mira la pestana Registros." + note
    return True, note


def text_load(body):
    mid = str(body.get("id", ""))
    if mid not in {m["id"] for m in scan_models()}:
        return fail(f"No existe el modelo '{mid}'.")
    good, note = ensure_router()
    if not good:
        return fail(note)
    if mid not in router_models():  # modelo nuevo que el router todavia no conoce
        write_preset()
        try:
            router("GET", "/models?reload=1", timeout=10)
        except Exception:  # noqa: BLE001
            pass
    try:
        router("POST", "/models/load", {"model": mid}, timeout=15)
    except Exception as e:  # noqa: BLE001
        return fail(f"No se pudo cargar {mid}: {router_error(e)}")
    return ok(f"Cargando {mid}… (si habia otro modelo cargado, se descarga).{note}")


def text_unload(body):
    mid = str(body.get("id", ""))
    try:
        router("POST", "/models/unload", {"model": mid}, timeout=15)
    except Exception as e:  # noqa: BLE001
        return fail(f"No se pudo descargar {mid}: {router_error(e)}")
    return ok(f"{mid} descargado: la VRAM queda libre.")


def apply_text():
    """Aplica el preset nuevo sin reiniciar el router: recarga la lista y vuelve a cargar el modelo activo."""
    if svc_state() != "active":
        return "El servidor estaba apagado: se aplica cuando lo enciendas."
    before = [k for k, v in router_models().items() if v["status"] in ("loaded", "loading", "sleeping")]
    try:
        router("GET", "/models?reload=1", timeout=15)
    except Exception:  # noqa: BLE001
        svc("restart")
        return "Servidor reiniciado con la nueva configuracion."
    time.sleep(0.5)
    after = router_models()
    reloaded = []
    for k in before:
        if k in after and after[k]["status"] == "unloaded":
            try:
                router("POST", "/models/load", {"model": k}, timeout=15)
                reloaded.append(k)
            except Exception:  # noqa: BLE001
                pass
    if reloaded:
        return f"Aplicado: {', '.join(reloaded)} se esta recargando con los ajustes nuevos."
    return "Aplicado: el servidor ya usa la nueva configuracion."


def text_rescan(_body):
    with SETTINGS_LOCK:
        n = len(write_preset())
    if svc_state() == "active":
        try:
            router("GET", "/models?reload=1", timeout=15)
        except Exception as e:  # noqa: BLE001
            return fail(f"{n} modelos encontrados, pero el servidor no pudo recargar la lista: {router_error(e)}")
        return ok(f"{n} modelos encontrados. El servidor ya los conoce.")
    return ok(f"{n} modelos encontrados.")


def text_settings(body):
    gl = str(body.get("global", ""))
    models_in = body.get("models") or {}
    if not isinstance(models_in, dict):
        return fail("Formato invalido.")
    errors = [f"global: {b}" for b in clean_lines(gl)[1]]
    valid_rs = {v for v, _ in REASONING_OPTS}
    models_cfg = {}
    for key, c in models_in.items():
        c = c if isinstance(c, dict) else {}
        extra = str(c.get("extra", ""))
        nmax = str(c.get("nmax", "2")).strip() or "2"
        rs = str(c.get("reasoning", ""))
        ctx = str(c.get("ctx", "")).strip()
        errors += [f"{key}: {b}" for b in clean_lines(extra)[1]]
        if not (nmax.isdigit() and 1 <= int(nmax) <= 16):
            errors.append(f"{key}: borradores MTP debe ser un numero entre 1 y 16 (puso '{nmax}')")
        if ctx and not (ctx.isdigit() and int(ctx) >= 256):
            errors.append(f"{key}: contexto debe ser un numero de 256 o mas (puso '{ctx}')")
        if rs not in valid_rs:
            errors.append(f"{key}: razonamiento invalido '{rs}'")
        models_cfg[key] = {"mtp": bool(c.get("mtp")), "nmax": nmax, "reasoning": rs, "ctx": ctx, "extra": extra}
    if errors:
        return fail("No se guardo. Revisa estos campos (las opciones van como clave = valor):\n" + "\n".join(errors))
    with SETTINGS_LOCK:
        settings = load_settings()
        settings["global"] = gl
        settings["models"].update(models_cfg)  # conserva ajustes de modelos que hoy no estan en disco
        save_settings(settings)
        n = len(write_preset())
    if body.get("apply"):
        return ok(f"Guardado ({n} modelos). {apply_text()}")
    return ok(f"Guardado ({n} modelos). Se aplica en la proxima carga.")


def _validate_img(models, cfg_in):
    valid = {m["key"] for m in models}
    errors, cfgs = [], {}
    for key, c in (cfg_in or {}).items():
        if key not in valid:
            continue
        extra = str((c or {}).get("extra", "")).strip()
        try:
            toks = shlex.split(extra)
        except ValueError as ex:
            errors.append(f"{key}: opciones extra mal escritas ({ex})")
            continue
        for t in toks:
            if not ARG_RE.match(t):
                errors.append(f"{key}: '{t}' tiene caracteres no permitidos (solo letras, numeros y . _ / : = + , -)")
        cfgs[key] = {"extra": extra}
    return errors, cfgs


def img_settings(body):
    models = scan_img_models()
    selected = str(body.get("selected", ""))
    errors, cfgs = _validate_img(models, body.get("models"))
    if selected and selected not in {m["key"] for m in models}:
        errors.append(f"modelo '{selected}' no existe en {IMG_DIR}")
    if errors:
        return fail("No se guardo.\n" + "\n".join(errors))
    with SETTINGS_LOCK:
        settings = load_settings()
        settings["image"]["selected"] = selected or (models[0]["key"] if models else "")
        settings["image"]["models"].update(cfgs)
        save_settings(settings)
        m = write_img_env()
    name = m["id"] if m else "ninguno"
    if body.get("apply"):
        if svc_state(IMG_SERVICE) == "active":
            svc("restart", IMG_SERVICE)
            return ok(f"Guardado (modelo de imagen: {name}) y generador reiniciado.")
        return ok(f"Guardado (modelo de imagen: {name}). El generador esta apagado: se aplica al encenderlo.")
    return ok(f"Guardado (modelo de imagen: {name}). Se aplica en el proximo arranque del generador.")


def img_use(body):
    key = str(body.get("key", ""))
    models = scan_img_models()
    if key not in {m["key"] for m in models}:
        return fail(f"No existe el modelo '{key}'.")
    errors, cfgs = _validate_img(models, body.get("models"))
    if errors:
        return fail("No se guardo.\n" + "\n".join(errors))
    with SETTINGS_LOCK:
        settings = load_settings()
        settings["image"]["selected"] = key
        settings["image"]["models"].update(cfgs)
        save_settings(settings)
        m = write_img_env()
    note = free_vram_for("img")
    was_on = svc_state(IMG_SERVICE) in ("active", "activating")
    out = svc("restart" if was_on else "start", IMG_SERVICE)
    return ok(f"Generador de imagenes {'reiniciado' if was_on else 'encendido'} con {m['id']}.{note} {out}".strip())


# ---------- servidor HTTP ----------
GET_ROUTES = {
    "/api/state": lambda q: state(),
    "/api/config": lambda q: config(),
    "/api/history": lambda q: {"samples": list(HIST)},
    "/api/stats": lambda q: stats(),
    "/api/logs": lambda q: {"lines": logs(q.get("svc", ["text"])[0], min(2000, int(q.get("n", ["300"])[0] or 300)))},
}
POST_ROUTES = {
    "/api/action": lambda b: do_action(str(b.get("do", ""))),
    "/api/text/load": text_load,
    "/api/text/unload": text_unload,
    "/api/text/rescan": text_rescan,
    "/api/text/settings": text_settings,
    "/api/img/settings": img_settings,
    "/api/img/use": img_use,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencio
        pass

    def _send(self, code, data, ctype="application/json", headers=None):
        if not isinstance(data, bytes):
            data = (json.dumps(data, ensure_ascii=False) if ctype == "application/json" else str(data)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _static(self, rel):
        path = (STATIC_DIR / rel).resolve()
        if STATIC_DIR not in path.parents or not path.is_file():
            return self._send(404, {"ok": False, "msg": "no encontrado"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        route = GET_ROUTES.get(u.path)
        if not route:
            return self._send(404, {"ok": False, "msg": "no encontrado"})
        try:
            self._send(200, route(urllib.parse.parse_qs(u.query)))
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "msg": f"Error interno: {e}"})

    def do_POST(self):
        route = POST_ROUTES.get(urllib.parse.urlparse(self.path).path)
        if not route:
            return self._send(404, {"ok": False, "msg": "no encontrado"})
        # Exigir JSON impide que otra pagina web dispare acciones con un formulario (CSRF)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"ok": False, "msg": "Se espera JSON."})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n > 1_000_000:
            return self._send(413, {"ok": False, "msg": "demasiado grande"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("se espera un objeto")
        except ValueError as e:
            return self._send(400, {"ok": False, "msg": f"JSON invalido: {e}"})
        try:
            self._send(200, route(body))
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "msg": f"Error interno: {e}"})


# ---------- instalacion ----------
def install():
    dest = APP_DIR / "panel.py"
    unit_dir = HOME / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    router_unit = unit_dir / f"{SERVICE}.service"
    if router_unit.exists() and not (unit_dir / f"{SERVICE}.service.bak").exists():
        router_unit.rename(unit_dir / f"{SERVICE}.service.bak")
    router_unit.write_text(
        "[Unit]\nDescription=llama.cpp router server (Vulkan)\n"
        f"Conflicts={IMG_SERVICE}.service\n\n"
        "[Service]\nEnvironment=GGML_VK_VISIBLE_DEVICES=0\n"
        f"ExecStart={LLAMA_SERVER} --models-preset {PRESET_FILE} --models-max 1 --host 0.0.0.0 --port {CHAT_PORT}\n"
        "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
    )
    (unit_dir / f"{IMG_SERVICE}.service").write_text(
        "[Unit]\nDescription=KoboldCpp generador de imagenes (Vulkan)\n"
        f"Conflicts={SERVICE}.service\n\n"
        "[Service]\nEnvironment=GGML_VK_VISIBLE_DEVICES=0\n"
        f"EnvironmentFile={IMG_ENV}\n"
        f"ExecStart={KOBOLD_BIN} $KOBOLD_ARGS\n"
        "Restart=on-failure\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    (unit_dir / f"{PANEL_SERVICE}.service").write_text(
        "[Unit]\nDescription=Panel de control llama-server\n\n"
        f"[Service]\nWorkingDirectory={APP_DIR}\nExecStart=/usr/bin/python3 {dest}\nRestart=on-failure\nRestartSec=3\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    # Reinicia el panel solo cada vez que se guarda panel.py (los archivos de static/ se leen en cada pedido)
    (unit_dir / f"{PANEL_SERVICE}-reload.path").write_text(
        "[Unit]\nDescription=Vigila panel.py para reiniciar el panel\n\n"
        f"[Path]\nPathChanged={dest}\nUnit={PANEL_SERVICE}-reload.service\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    (unit_dir / f"{PANEL_SERVICE}-reload.service").write_text(
        "[Unit]\nDescription=Reinicia el panel tras un cambio en panel.py\nStartLimitIntervalSec=0\n\n"
        f"[Service]\nType=oneshot\nExecStart=/usr/bin/systemctl --user restart {PANEL_SERVICE}.service\n"
    )
    n = len(write_preset())
    print(f"Configuracion de texto generada con {n} modelos: {PRESET_FILE}")
    m = write_img_env()
    if m:
        print(f"Generador de imagenes configurado con el modelo: {m['id']}")
    else:
        print(f"(No hay modelos de imagen todavia en {IMG_DIR}.)")
    if not Path(KOBOLD_BIN).exists():
        print(f"AVISO: no existe {KOBOLD_BIN}")
    print(run("systemctl", "--user", "daemon-reload") or "daemon-reload ok")
    print(run("systemctl", "--user", "enable", PANEL_SERVICE) or "panel habilitado")
    print(run("systemctl", "--user", "enable", "--now", f"{PANEL_SERVICE}-reload.path") or "auto-reinicio habilitado")
    print(run("systemctl", "--user", "restart", PANEL_SERVICE) or "panel (re)iniciado")
    if svc_state() == "active":
        svc("restart")
        print("Servidor de modelos de texto reiniciado con la nueva configuracion.")
    print(f"\nListo. Abri http://{os.uname().nodename}.local:{PANEL_PORT} desde la laptop.")


def main():
    if "--install" in sys.argv:
        return install()
    threading.Thread(target=sampler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PANEL_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
