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
PATHS_ENV = CFG_DIR / "paths.env"


def _read_env_file(path):
    """Lee un archivo CLAVE=valor (con # para comentarios). Vacio si no existe."""
    out = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


_LOCAL = _read_env_file(PATHS_ENV)


def _env(name, default):
    """Variable de entorno; si no, la de paths.env (vacia vale: "sin carpetas extra"); si no, el valor por defecto."""
    if os.environ.get(name):
        return os.environ[name]
    return _LOCAL[name] if name in _LOCAL else str(default)


SETTINGS_FILE = CFG_DIR / "settings.json"
PRESET_FILE = CFG_DIR / "presets.ini"
IMG_ENV = CFG_DIR / "kobold.env"
# Estados de slot guardados (prompt inicial ya procesado): ~0.5 GB por modelo, van a un disco rapido
SLOTS_DIR = Path(os.environ.get("LLAMA_PANEL_SLOTS_DIR", HOME / ".cache" / "llama-panel" / "slots"))

MODELS_DIR = Path(_env("LLAMA_MODELS_DIR", HOME / "models")).expanduser()
# Carpetas extra separadas por ":" (por ejemplo, un NVMe para modelos que necesitan lectura rapida)
EXTRA_MODELS_DIRS = [Path(p).expanduser() for p in
                     _env("LLAMA_EXTRA_MODELS_DIRS", HOME / "models-nvme").split(":") if p]
MODELS_DIRS = [MODELS_DIR] + [d for d in EXTRA_MODELS_DIRS if d != MODELS_DIR]
IMG_DIR = Path(_env("LLAMA_IMG_DIR", HOME / "imgmodels")).expanduser()
LLAMA_SERVER = str(Path(_env("LLAMA_SERVER_BIN", HOME / "llama.cpp/build/bin/llama-server")).expanduser())
KOBOLD_BIN = str(Path(_env("KOBOLD_BIN", HOME / "koboldcpp/koboldcpp")).expanduser())

SERVICE = "llama-router"
IMG_SERVICE = "kobold-img"
PANEL_SERVICE = "llama-panel"
PANEL_PORT = int(os.environ.get("LLAMA_PANEL_PORT", "8090"))
CHAT_PORT = 8080
IMG_PORT = 5001

DEFAULT_GLOBAL = "n-gpu-layers = 99\nctx-size = 8192\nreasoning-format = deepseek"
REASONING_OPTS = [("", "por defecto"), ("auto", "auto"), ("on", "activado"), ("off", "apagado")]
LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\s*=\s*\S.*$")
SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")  # modelos partidos en varios archivos

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
        stem = re.sub(r"-\d{5}-of-\d{5}$", "", Path(m["key"]).stem)  # sin el sufijo de shard
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
        n = used.get(base, 0) + 1
        used[base] = n
        m["id"] = base if n == 1 else f"{base}_{n}"
    return items
