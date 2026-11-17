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


def _env(name, default, allow_empty=False):
    """Variable de entorno; si no, la de paths.env; si no, el valor por defecto. Un valor vacio en paths.env vuelve al
    valor por defecto (una ruta vacia seria la carpeta actual), salvo con allow_empty ("sin carpetas extra")."""
    if os.environ.get(name):
        return os.environ[name]
    if name in _LOCAL and (_LOCAL[name] or allow_empty):
        return _LOCAL[name]
    return str(default)


SETTINGS_FILE = CFG_DIR / "settings.json"
PRESET_FILE = CFG_DIR / "presets.ini"
IMG_ENV = CFG_DIR / "kobold.env"
# Estados de slot guardados (prompt inicial ya procesado): ~0.5 GB por modelo, van a un disco rapido
SLOTS_DIR = Path(os.environ.get("LLAMA_PANEL_SLOTS_DIR", HOME / ".cache" / "llama-panel" / "slots"))

MODELS_DIR = Path(_env("LLAMA_MODELS_DIR", HOME / "models")).expanduser()
# Carpetas extra separadas por ":" (por ejemplo, un NVMe para modelos que necesitan lectura rapida)
EXTRA_MODELS_DIRS = [Path(p).expanduser() for p in
                     _env("LLAMA_EXTRA_MODELS_DIRS", "", allow_empty=True).split(":") if p]
MODELS_DIRS = [MODELS_DIR] + [d for d in EXTRA_MODELS_DIRS if d != MODELS_DIR]
IMG_DIR = Path(_env("LLAMA_IMG_DIR", HOME / "imgmodels")).expanduser()
LLAMA_SERVER = str(Path(_env("LLAMA_SERVER_BIN", HOME / "llama.cpp/build/bin/llama-server")).expanduser())
KOBOLD_BIN = str(Path(_env("KOBOLD_BIN", HOME / "koboldcpp/koboldcpp")).expanduser())

# Compañero (buddy.py): voz con whisper.cpp (entender, en el CPU) y Piper (hablar, en su propio venv)
# (scripts/install-voice.sh deja todo en VOICE_DIR; otras ubicaciones van en paths.env)
VOICE_DIR = HOME / ".local/share/llama-panel/voice"
WHISPER_CLI = str(Path(_env("WHISPER_CLI", HOME / "whisper.cpp/build/bin/whisper-cli")).expanduser())
WHISPER_MODEL = str(Path(_env("WHISPER_MODEL", VOICE_DIR / "ggml-small-q8_0.bin")).expanduser())
PIPER_PYTHON = str(Path(_env("PIPER_PYTHON", VOICE_DIR / "piper-venv/bin/python")).expanduser())
PIPER_VOICE = str(Path(_env("PIPER_VOICE", VOICE_DIR / "es_AR-daniela-high.onnx")).expanduser())
BUDDY_DIR = CFG_DIR / "buddy"  # personalidad, memoria y registro de herramientas

SERVICE = "llama-router"
IMG_SERVICE = "kobold-img"
PANEL_SERVICE = "llama-panel"
PANEL_PORT = int(os.environ.get("LLAMA_PANEL_PORT", "8090"))
CHAT_PORT = 8080  # puertos que usan los clientes
IMG_PORT = 5001
# Con el encendido a demanda (gate.py) el panel atiende CHAT_PORT e IMG_PORT y los servicios escuchan solo en la
# maquina, en estos puertos
INNER_PORTS = {"text": 18080, "img": 15001}
PUBLIC_PORTS = {"text": CHAT_PORT, "img": IMG_PORT}
DEFAULT_AUTO = {"swap": False, "idle_min": 0}

# Sin n-gpu-layers: con un valor fijo llama.cpp apaga --fit y un MoE que no entra en la VRAM va a ~2 tok/s
DEFAULT_GLOBAL = "reasoning-format = deepseek"  # opciones extra globales (texto)
DEFAULT_GLOBAL_FIELDS = {"ctx": "8192"}  # controles globales de una instalacion nueva
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
    # instalaciones anteriores a los controles globales: sin valores nuevos (lo suyo sigue en el texto)
    data.setdefault("global_fields", {} if "global" in data else dict(DEFAULT_GLOBAL_FIELDS))
    data.setdefault("global", DEFAULT_GLOBAL)
    data.setdefault("models", {})
    img = data.setdefault("image", {})
    img.setdefault("selected", "")
    img.setdefault("models", {})
    data["auto"] = {**DEFAULT_AUTO, **(data.get("auto") or {})}
    return data


_auto_cache = {"key": None, "val": dict(DEFAULT_AUTO)}


def auto_settings():
    """Ajustes del encendido a demanda ({"swap": bool, "idle_min": int}). Se consulta en cada pedido a los servicios:
    se relee settings.json solo si cambio."""
    try:
        st = SETTINGS_FILE.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key != _auto_cache["key"]:
        _auto_cache["val"] = load_settings()["auto"] if key else dict(DEFAULT_AUTO)
        _auto_cache["key"] = key
    return _auto_cache["val"]


def backend(which):
    """(host, puerto) donde escucha el servicio "text" o "img": el puerto de siempre en todas las interfaces o, con
    el encendido a demanda, uno interno solo en la maquina."""
    if auto_settings().get("swap"):
        return "127.0.0.1", INNER_PORTS[which]
    return "0.0.0.0", PUBLIC_PORTS[which]


def backend_port(which):
    return backend(which)[1]


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
