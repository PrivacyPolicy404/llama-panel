"""Rutas, puertos, nombres de servicios y el archivo de ajustes del usuario (settings.json)."""
import json
import os
import re
import threading
from pathlib import Path

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent.parent  # raiz del repo
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
REASONING_OPTS = [("", "por defecto"), ("auto", "auto"), ("on", "activado"), ("off", "apagado")]
LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\s*=\s*\S.*$")

# Serializa lectura-modificacion-escritura de settings.json entre pedidos concurrentes
SETTINGS_LOCK = threading.Lock()


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
    except Exception:  # noqa: BLE001
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


def assign_ids(items):
    """Le da a cada modelo un id corto y unico a partir del nombre de archivo."""
    used = {}
    for m in items:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(m["key"]).stem)
        n = used.get(base, 0) + 1
        used[base] = n
        m["id"] = base if n == 1 else f"{base}_{n}"
    return items
