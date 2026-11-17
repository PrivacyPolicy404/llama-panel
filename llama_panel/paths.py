"""Rutas configurables desde el panel (pestana Configuracion): carpetas de modelos, ejecutables y nombre de la GPU.

Se guardan en ~/.config/llama-panel/paths.env, que config.py lee al arrancar. Una variable de entorno con el mismo
nombre tiene prioridad: esos campos se muestran bloqueados. Al guardar, el panel se reinicia solo para tomarlas
(y al arrancar reescribe los units de systemd si cambio algun ejecutable, ver install.sync_units)."""
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from . import config as C
from . import hardware
from .img_models import IMG_EXT
from .text_models import AUX_PREFIXES
from .i18n import _

# campo de la interfaz -> variable
FIELDS = {"img_dir": "LLAMA_IMG_DIR", "llama_server": "LLAMA_SERVER_BIN", "kobold": "KOBOLD_BIN",
          "gpu_name": "LLAMA_GPU_NAME"}
TEXT_VARS = ("LLAMA_MODELS_DIR", "LLAMA_EXTRA_MODELS_DIRS")
WALK_LIMIT = 20000  # entradas maximas a revisar al contar modelos (por si alguien escribe "/")


def current():
    """Valores vigentes, los campos fijados por variables de entorno y el estado de cada uno."""
    values = {"text_dirs": [str(d) for d in C.MODELS_DIRS], "img_dir": str(C.IMG_DIR),
              "llama_server": C.LLAMA_SERVER, "kobold": C.KOBOLD_BIN,
              "gpu_name": C._LOCAL.get("LLAMA_GPU_NAME", "") or os.environ.get("LLAMA_GPU_NAME", "")}
    locked = [f for f, var in FIELDS.items() if os.environ.get(var)]
    if any(os.environ.get(v) for v in TEXT_VARS):
        locked.append("text_dirs")
    return {"values": values, "locked": locked, "file": str(C.PATHS_ENV), "checks": check(values),
            "detected_gpu": hardware.gpu_name()}


def _expand(p):
    return Path(os.path.expanduser(str(p).strip())) if str(p).strip() else None


def count_files(root, want):
    """(cantidad, se_corto): archivos que cumplen want(nombre) bajo root, sin recorrer mas de WALK_LIMIT entradas."""
    n = seen = 0
    for _dir, subdirs, files in os.walk(root, followlinks=True):
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]
        seen += len(files) + len(subdirs)
        n += sum(1 for f in files if want(f))
        if seen > WALK_LIMIT:
            return n, True
    return n, False


def is_text_model(name):
    low = name.lower()
    if not low.endswith(".gguf") or low.startswith(AUX_PREFIXES):
        return False
    split = C.SPLIT_RE.search(name)
    return not (split and int(split[1]) > 1)  # solo el primer shard cuenta


def _dir_check(path, want, what):
    p = _expand(path)
    if p is None:
        return {"ok": False, "msg": _("vacío")}
    if not p.exists():
        return {"ok": False, "msg": _("no existe")}
    if not p.is_dir():
        return {"ok": False, "msg": _("no es una carpeta")}
    n, cut = count_files(p, want)
    what = _(what)
    msg = _("{n} o más {what} (carpeta muy grande, no se revisó toda)", n=n, what=what) if cut \
        else _("{n} {what}", n=n, what=what) if n else _("sin {what} todavía", what=what)
    return {"ok": n > 0, "msg": msg}


_versions = {}


def server_version(path):
    """Versión de llama-server (de --version), guardada por ruta y fecha del archivo."""
    try:
        key = (str(path), os.path.getmtime(path))
    except OSError:
        return None
    if key not in _versions:
        try:
            r = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=15)
            out = r.stdout + r.stderr
            # "version: 7123 (abc1234)" o "version: 1.0.0-dev (build 22, commit 5dc6c7f)"
            m = re.search(r"version:\s*(\S+)(?:\s*\((?:build \d+, )?(?:commit )?([0-9a-f]{6,})\))?", out)
            _versions[key] = (m[1] + (f" ({m[2]})" if m[2] else "")) if m else None
        except (OSError, subprocess.SubprocessError):
            _versions[key] = None
    return _versions[key]


def _bin_check(path, optional=""):
    p = _expand(path)
    if p is None:
        return {"ok": None if optional else False, "msg": _(optional or "vacío")}
    if not p.exists():
        return {"ok": None if optional else False,
                "msg": _("no existe (opcional: solo para imágenes)") if optional else _("no existe")}
    if not p.is_file() or not os.access(p, os.X_OK):
        return {"ok": False, "msg": _("no es un ejecutable")}
    return {"ok": True, "msg": _("encontrado")}


def check(values):
    """Estado de cada campo: {"ok": True/False/None (opcional y ausente), "msg": texto}."""
    dirs = [d for d in values.get("text_dirs") or [] if str(d).strip()]
    text = [{"path": d, **_dir_check(d, is_text_model, "modelos .gguf")} for d in dirs]
    server = _bin_check(values.get("llama_server"))
    if server["ok"]:
        v = server_version(_expand(values["llama_server"]))
        server["msg"] = _("llama.cpp {version}", version=v) if v else _("encontrado (no respondió a --version)")
    return {
        "text_dirs": text,
        "img_dir": _dir_check(values.get("img_dir"), lambda f: Path(f).suffix.lower() in IMG_EXT,
                              "archivos de modelo de imagen") if str(values.get("img_dir") or "").strip()
        else {"ok": None, "msg": _("sin carpeta (opcional)")},
        "llama_server": server,
        "kobold": _bin_check(values.get("kobold"), "opcional: solo para imágenes"),
    }


def validate(values):
    """Lista de errores; vacia si se puede guardar."""
    errors = []
    dirs = [str(d).strip() for d in values.get("text_dirs") or [] if str(d).strip()]
    if not dirs:
        errors.append(_("Hace falta al menos una carpeta de modelos de texto."))
    for label, v in [("carpeta", d) for d in dirs] + [("carpeta", values.get("img_dir") or "")] + \
            [("ejecutable", values.get(k) or "") for k in ("llama_server", "kobold")] + \
            [("nombre", values.get("gpu_name") or "")]:
        v = str(v)
        if any(c in v for c in "\n\r\0"):
            errors.append(_("La ruta «{path}» tiene saltos de línea.", path=v.strip()))
        elif label != "nombre" and v.strip() and not v.strip().startswith(("/", "~")):
            errors.append(_("«{path}» tiene que ser una ruta absoluta (empezar con / o ~).", path=v.strip()))
    for k in ("llama_server", "kobold"):
        if re.search(r"\s", str(values.get(k) or "").strip()):
            errors.append(_("La ruta de {prog} no puede tener espacios (va en un servicio de systemd).",
                            prog="llama-server" if k == "llama_server" else "KoboldCpp"))
    if any(":" in d for d in dirs):
        errors.append(_("Las carpetas de modelos no pueden tener «:»."))
    if not str(values.get("llama_server") or "").strip():
        errors.append(_("Falta la ruta de llama-server."))
    return errors


def env_lines(values, old_text=""):
    """Contenido nuevo de paths.env: las claves del panel con los valores nuevos; el resto, como estaba."""
    dirs = [str(d).strip() for d in values["text_dirs"] if str(d).strip()]
    new = {"LLAMA_MODELS_DIR": dirs[0], "LLAMA_EXTRA_MODELS_DIRS": ":".join(dirs[1:])}
    for field, var in FIELDS.items():
        new[var] = str(values.get(field) or "").strip()
    out, done = [], set()
    for line in old_text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in new:
            if key not in done:
                out.append(f"{key}={new[key]}")
                done.add(key)
        else:
            out.append(line)
    if not old_text.strip():
        out.append("# Rutas de esta máquina (las edita la pestaña Configuración del panel;"
                   " una variable de entorno con el mismo nombre tiene prioridad)")
    out += [f"{k}={v}" for k, v in new.items() if k not in done]
    return "\n".join(out) + "\n"


def restart_panel(delay=0.8):
    """Reinicia el proceso del panel (mismo PID: systemd no se entera) para que relea paths.env."""
    def go():
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(delay, go).start()


def save(body):
    values = body.get("values") or {}
    if not isinstance(values, dict) or not isinstance(values.get("text_dirs", []), list):
        return {"ok": False, "msg": _("Datos inválidos.")}
    errors = validate(values)
    if errors:
        return {"ok": False, "msg": " ".join(errors)}
    before = current()["values"]
    try:
        old = C.PATHS_ENV.read_text()
    except OSError:
        old = ""
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = C.PATHS_ENV.with_suffix(".tmp")
    tmp.write_text(env_lines(values, old))
    tmp.replace(C.PATHS_ENV)
    norm = lambda v: [str(_expand(d)) for d in v if str(d).strip()] if isinstance(v, list) else str(_expand(v) or "")
    changed = [k for k in before if norm(before[k]) != norm(values.get(k, before[k]))]
    restart_panel()
    return {"ok": True, "changed": changed,
            "rescan": bool({"text_dirs", "img_dir"} & set(changed)),
            "msg": _("Guardado. El panel se reinicia para usar las rutas nuevas.")
                   + (_(" El servidor de texto usa el llama-server nuevo la próxima vez que se encienda o reinicie.")
                      if "llama_server" in changed else "")}


def suggest_dirs(prefix, limit=40):
    """Subcarpetas que empiezan con lo que se lleva escrito (para autocompletar)."""
    prefix = str(prefix or "").strip()
    if not prefix.startswith(("/", "~")):
        return []
    full = os.path.expanduser(prefix)
    base, start = (full, "") if full.endswith("/") else os.path.split(full)
    try:
        names = sorted(e.name for e in os.scandir(base or "/")
                       if e.is_dir() and e.name.startswith(start) and (start.startswith(".") or not e.name.startswith(".")))
    except OSError:
        return []
    head = prefix if prefix.endswith("/") else prefix[:len(prefix) - len(start)]
    return [head + n + "/" for n in names[:limit]]
