"""Modelos de imagen: escaneo, deteccion de acompanantes (VAE, codificadores) y el kobold.env de KoboldCpp."""
import os
import re
import shlex
from pathlib import Path

from . import config as C

IMG_EXT = (".gguf", ".safetensors", ".ckpt")
ARG_RE = re.compile(r"^[A-Za-z0-9_./:=+,-]+$")
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


def build_kobold_args(m, extra=""):
    args = ["--sdmodel", m["path"]]
    for flag, path in m["comps"]:
        args += [f"--{flag}", path]
    args += ["--usevulkan", "0", "--host", "0.0.0.0", "--port", str(C.IMG_PORT)]
    try:
        args += shlex.split(extra or "")
    except ValueError:
        pass
    return args


def write_img_env():
    settings = C.load_settings()
    m = pick_img(settings, scan_img_models())
    if not m:
        return None
    cfg = settings["image"]["models"].get(m["key"]) or {}
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    C.IMG_ENV.write_text("KOBOLD_ARGS=" + " ".join(build_kobold_args(m, cfg.get("extra", ""))) + "\n")
    return m


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
            errors.append(f"{key}: opciones extra mal escritas ({ex})")
            continue
        for t in toks:
            if not ARG_RE.match(t):
                errors.append(f"{key}: '{t}' tiene caracteres no permitidos (solo letras, numeros y . _ / : = + , -)")
        cfgs[key] = {"extra": extra}
    return errors, cfgs
