"""Modelos de texto: escaneo de .gguf y generacion del presets.ini que lee el router de llama-server."""
import os
import re
from pathlib import Path

from . import config as C

MTP_RE = re.compile(r"(^|[-_.])MTP([-_.]|$)")
MIN_SIZE = 50 * 1024 * 1024
AUX_PREFIXES = ("mmproj", "mtp-", "mtp_")  # proyectores de vision y borradores MTP sueltos: no son modelos


def _shard_size(p, total):
    """Suma el tamano de todos los shards de un modelo partido (...-00001-of-0000N.gguf)."""
    size = 0
    for i in range(1, total + 1):
        q = p.with_name(C.SPLIT_RE.sub(f"-{i:05d}-of-{total:05d}.gguf", p.name))
        size += os.path.getsize(os.path.realpath(q))
    return size


def scan_models(models_dirs=None):
    """Busca .gguf en todas las carpetas. La clave es la ruta relativa en la primera carpeta
    (compatible con ajustes viejos) y la ruta absoluta en las demas."""
    dirs = [Path(d) for d in (models_dirs or C.MODELS_DIRS)]
    found, seen = [], set()
    for idx, models_dir in enumerate(dirs):
        if not models_dir.is_dir():
            continue
        for p in sorted(models_dir.rglob("*.gguf")):
            if p.name.lower().startswith(AUX_PREFIXES):
                continue
            split = C.SPLIT_RE.search(p.name)
            if split and int(split[1]) > 1:  # solo el primer shard representa al modelo
                continue
            try:
                real = os.path.realpath(p)
                size = _shard_size(p, int(split[2])) if split else os.path.getsize(real)
            except OSError:  # falta algun shard (por ejemplo, todavia se esta bajando)
                continue
            if real in seen or size < MIN_SIZE:
                continue
            seen.add(real)
            key = str(p.relative_to(models_dir)) if idx == 0 else str(p)
            found.append({"key": key, "path": str(p), "size": size})
    return C.assign_ids(found)


def model_cfg(settings, m):
    default = {"mtp": bool(MTP_RE.search(Path(m["key"]).stem)), "nmax": "2", "reasoning": "", "ctx": "", "extra": ""}
    saved = settings["models"].get(m["key"]) or {}
    return {**default, **saved}


def build_preset(settings, models):
    glob_lines, _ = C.clean_lines(settings["global"])
    out = ["# Generado por llama-panel. No editar a mano: se reescribe al guardar.", "", "[*]"]
    out += glob_lines + [""]
    for m in models:
        cfg = model_cfg(settings, m)
        extra, _ = C.clean_lines(cfg.get("extra", ""))
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
    settings = C.load_settings()
    models = scan_models()
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    C.PRESET_FILE.write_text(build_preset(settings, models))
    return models


def validate_settings(global_text, models_in):
    """Valida lo que manda la interfaz. Devuelve (errores, {key: cfg_normalizada})."""
    errors = [f"global: {b}" for b in C.clean_lines(global_text)[1]]
    valid_rs = {v for v, _ in C.REASONING_OPTS}
    cfgs = {}
    for key, c in models_in.items():
        c = c if isinstance(c, dict) else {}
        extra = str(c.get("extra", ""))
        nmax = str(c.get("nmax", "2")).strip() or "2"
        rs = str(c.get("reasoning", ""))
        ctx = str(c.get("ctx", "")).strip()
        errors += [f"{key}: {b}" for b in C.clean_lines(extra)[1]]
        if not (nmax.isdigit() and 1 <= int(nmax) <= 16):
            errors.append(f"{key}: borradores MTP debe ser un numero entre 1 y 16 (puso '{nmax}')")
        if ctx and not (ctx.isdigit() and int(ctx) >= 256):
            errors.append(f"{key}: contexto debe ser un numero de 256 o mas (puso '{ctx}')")
        if rs not in valid_rs:
            errors.append(f"{key}: razonamiento invalido '{rs}'")
        cfgs[key] = {"mtp": bool(c.get("mtp")), "nmax": nmax, "reasoning": rs, "ctx": ctx, "extra": extra}
    return errors, cfgs
