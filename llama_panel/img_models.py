"""Modelos de imagen: escaneo, deteccion de acompanantes (VAE, codificadores) y el kobold.env de KoboldCpp."""
import json
import os
import re
import shlex
from pathlib import Path

from . import config as C
from .i18n import _

IMG_EXT = (".gguf", ".safetensors", ".ckpt")
ARG_RE = re.compile(r"^[A-Za-z0-9_./:=+,-]+$")
# --gendefaults lleva un objeto JSON (pasos, CFG, muestreador por defecto de cada pedido de imagen): se escribe entre
# comillas simples en las opciones extra. Sin ' \ $ ` para que kobold.env lo pase entero a KoboldCpp.
JSON_ARG_FLAGS = {"--gendefaults"}
JSON_ARG_RE = re.compile(r'^[A-Za-z0-9_.:,+ "{}\[\]-]+$')
MIN_SIZE = 100 * 1024 * 1024


def img_role(name):
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


def scan_img_models(img_dir=None):
    img_dir = Path(img_dir or C.IMG_DIR)
    if not img_dir.is_dir():
        return []
    files = []
    for p in sorted(img_dir.rglob("*")):
        if p.suffix.lower() not in IMG_EXT or not p.is_file():
            continue
        if re.search(r"\s", str(p)):  # rutas con espacios no se pueden pasar por systemd de forma simple
            continue
        try:
            size = os.path.getsize(os.path.realpath(p))
        except OSError:
            continue
        files.append((p, size, img_role(p.name)))
    mains = []
    for p, size, role in files:
        if role or size < MIN_SIZE:
            continue
        comps = []
        if p.parent != img_dir:  # los acompanantes se toman de la misma subcarpeta
            seen = {}
            for q, _qs, qr in files:
                if qr and q.parent == p.parent and qr not in seen:
                    seen[qr] = q
            comps = [(flag, str(q)) for flag, q in sorted(seen.items())]
        mains.append({"key": str(p.relative_to(img_dir)), "path": str(p), "size": size, "comps": comps})
    return C.assign_ids(mains)


def pick_img(settings, models):
    sel = settings["image"].get("selected", "")
    for m in models:
        if m["key"] == sel:
            return m
    return models[0] if models else None


GPU_BACKEND_FLAGS = {"--usevulkan", "--usecuda", "--usecublas", "--usehipblas", "--useclblast", "--usecpu"}


def build_kobold_args(m, extra=""):
    args = ["--sdmodel", m["path"]]
    for flag, path in m["comps"]:
        args += [f"--{flag}", path]
    try:
        user = shlex.split(extra or "")
    except ValueError:
        user = []
    # Vulkan en la GPU 0 salvo que las opciones del modelo elijan otro backend (por ejemplo --usecuda)
    if not any(a in GPU_BACKEND_FLAGS for a in user):
        args += ["--usevulkan", "0"]
    host, port = C.backend("img")
    return args + ["--host", host, "--port", str(port)] + user


def write_img_env():
    settings = C.load_settings()
    m = pick_img(settings, scan_img_models())
    if not m:
        return None
    cfg = settings["image"]["models"].get(m["key"]) or {}
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    C.IMG_ENV.write_text(env_line(build_kobold_args(m, cfg.get("extra", ""))))
    return m


def env_line(args):
    """KOBOLD_ARGS para el EnvironmentFile. systemd saca comillas dos veces (al leer el archivo y al partir
    $KOBOLD_ARGS en palabras): el valor va entre comillas dobles y cada argumento con espacios o comillas, entre
    simples. Así un JSON como {"steps":4} le llega a KoboldCpp tal cual."""
    words = " ".join(shlex.quote(a) for a in args)
    return 'KOBOLD_ARGS="' + words.replace("\\", "\\\\").replace('"', '\\"') + '"\n'


def validate_settings(models, cfg_in):
    """Valida las opciones extra de cada modelo. Devuelve (errores, {key: {"extra": ...}})."""
    valid = {m["key"] for m in models}
    errors, cfgs = [], {}
    for key, c in (cfg_in or {}).items():
        if key not in valid:
            continue
        extra = str((c or {}).get("extra", "")).strip()
        try:
            toks = shlex.split(extra)
        except ValueError as ex:
            errors.append(_("{key}: opciones extra mal escritas ({err})", key=key, err=ex))
            continue
        for i, t in enumerate(toks):
            if i and toks[i - 1] in JSON_ARG_FLAGS:
                if not (JSON_ARG_RE.match(t) and _json_object(t)):
                    errors.append(_("{key}: {flag} necesita un objeto JSON entre comillas simples, por ejemplo "
                                    "{example}", key=key, flag=toks[i - 1], example="'{\"steps\":28}'"))
                continue
            if not ARG_RE.match(t):
                errors.append(_("{key}: '{token}' tiene caracteres no permitidos (solo letras, números y . _ / : = + , -)", key=key, token=t))
        cfgs[key] = {"extra": extra}
    return errors, cfgs


def _json_object(text):
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False
