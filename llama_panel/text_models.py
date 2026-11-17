"""Modelos de texto: escaneo de .gguf y generacion del presets.ini que lee el router de llama-server."""
import os
import re
from pathlib import Path

from . import config as C
from .i18n import _

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


# Ajustes de cada modelo que la interfaz muestra como controles (en dos columnas: Normal y Modo IA).
# kind: "check" (si/no), "number" (vacio = por defecto), "select" (options; "" = por defecto),
#       "layers" (vacio, "all" o un numero). Los textos estan en castellano y la interfaz los traduce.
# Lo que no esta aca va en "opciones extra" (clave = valor), que manda sobre estos campos;
# keys: las opciones de llama-server que cubre cada campo (la interfaz avisa si una opcion extra lo pisa).
FIELDS = [
    {"id": "ctx", "keys": ["ctx-size"], "label": "Contexto", "kind": "number", "min": 256, "step": 256, "placeholder": "global",
     "hint": "Cuántos tokens recuerda la conversación. Más contexto usa más VRAM."},
    {"id": "ngl", "keys": ["n-gpu-layers", "gpu-layers"], "label": "Capas en GPU", "kind": "layers", "placeholder": "auto",
     "hint": "auto: llama.cpp calcula cuántas entran en la VRAM. all o un número para forzarlo."},
    {"id": "cpu_moe", "keys": ["cpu-moe", "n-cpu-moe"], "label": "Expertos MoE en RAM", "kind": "layers", "placeholder": "ninguno",
     "hint": "Para modelos MoE más grandes que la VRAM: all (todos) o en cuántas capas dejar los expertos en la RAM."},
    {"id": "parallel", "keys": ["parallel"], "label": "Pedidos en paralelo", "kind": "number", "min": 1, "max": 16, "placeholder": "auto",
     "hint": "1 si lo usa una sola persona: todo el contexto para un pedido y más rápido en MoE grandes."},
    {"id": "kv", "keys": ["cache-type-k", "cache-type-v"], "label": "Caché KV", "kind": "select",
     "options": [["", "por defecto (f16)"], ["q8_0", "q8_0 (la mitad de memoria)"], ["q4_0", "q4_0 (un cuarto, pierde algo de calidad)"]],
     "hint": "Memoria del contexto. q8_0 casi no cambia la calidad y deja usar el doble de contexto."},
    {"id": "fa", "keys": ["flash-attn"], "label": "Flash attention", "kind": "select",
     "options": [["", "auto"], ["on", "activado"], ["off", "apagado"]],
     "hint": "Hace falta para comprimir la caché KV. auto suele andar bien."},
    {"id": "ubatch", "keys": ["ubatch-size"], "label": "Tanda del prompt", "kind": "select",
     "options": [["", "por defecto (512)"], ["1024", "1024"], ["2048", "2048"]],
     "hint": "Más grande: el prompt se procesa más rápido, a cambio de VRAM."},
    {"id": "threads", "keys": ["threads"], "label": "Hilos de CPU", "kind": "number", "min": 1, "max": 256, "placeholder": "auto",
     "hint": "Normalmente auto. Importa cuando parte del modelo corre en el CPU."},
    {"id": "mtp", "keys": ["spec-type"], "label": "MTP", "kind": "check",
     "hint": "Predicción de varios tokens por paso, si el modelo la trae: suele dar +20–30 % de velocidad."},
    {"id": "nmax", "keys": ["spec-draft-n-max"], "label": "Borradores por paso", "kind": "number", "min": 1, "max": 16, "placeholder": "2",
     "hint": "Tokens que adivina el MTP por paso. 2 suele ser el mejor."},
    {"id": "reasoning", "keys": ["reasoning"], "label": "Razonamiento", "kind": "select", "options": [list(o) for o in C.REASONING_OPTS],
     "hint": "Si el modelo piensa antes de responder (en los que lo permiten)."},
]
FIELD_IDS = [f["id"] for f in FIELDS]
GLOBAL_FIELD_IDS = [f for f in FIELD_IDS if f not in ("mtp", "nmax")]  # el MTP depende de cada modelo
AI_FIELDS = tuple(FIELD_IDS)  # todos se pueden cambiar solo para el modo IA; cfg["ai"] guarda lo que difiere


def model_cfg(settings, m):
    default = {f: "" for f in FIELD_IDS}
    default.update(mtp=bool(MTP_RE.search(Path(m["key"]).stem)), nmax="2", extra="", extra_ai="", ai={})
    saved = settings["models"].get(m["key"]) or {}
    return {**default, **saved}


def effective_cfg(cfg, ai_mode):
    """Los ajustes que valen: con el modo IA activo, los de cfg["ai"] pisan a los normales."""
    if not ai_mode:
        return cfg
    return {**cfg, **{k: v for k, v in (cfg.get("ai") or {}).items() if k in AI_FIELDS}}


def field_lines(cfg):
    """Las lineas "clave = valor" del preset que salen de los controles."""
    out = []
    if cfg.get("ctx"):
        out.append(f"ctx-size = {cfg['ctx']}")
    if cfg.get("ngl"):
        out.append(f"n-gpu-layers = {cfg['ngl']}")
    if cfg.get("cpu_moe") == "all":
        out.append("cpu-moe = 1")
    elif cfg.get("cpu_moe"):
        out.append(f"n-cpu-moe = {cfg['cpu_moe']}")
    if cfg.get("parallel"):
        out.append(f"parallel = {cfg['parallel']}")
    if cfg.get("kv"):
        out += [f"cache-type-k = {cfg['kv']}", f"cache-type-v = {cfg['kv']}"]
    if cfg.get("fa"):
        out.append(f"flash-attn = {cfg['fa']}")
    if cfg.get("ubatch"):
        out.append(f"ubatch-size = {cfg['ubatch']}")
    if cfg.get("threads"):
        out.append(f"threads = {cfg['threads']}")
    if cfg.get("mtp"):
        out += ["spec-type = draft-mtp", f"spec-draft-n-max = {cfg.get('nmax') or 2}"]
    if cfg.get("reasoning"):
        out.append(f"reasoning = {cfg['reasoning']}")
    return out


def check_field(f, value):
    """Valor normalizado del campo, o None si no es valido. "" = por defecto."""
    if f["kind"] == "check":
        return value if isinstance(value, bool) else None
    v = str(value if value is not None else "").strip()
    if not v:
        return ""
    if f["kind"] == "select":
        return v if v in {o[0] for o in f["options"]} else None
    if f["kind"] == "layers":
        return v if v == "all" or v.isdigit() else None
    if not v.isdigit():
        return None
    n = int(v)
    return v if f.get("min", 0) <= n <= f.get("max", 10 ** 9) else None


def ai_mode_active():
    """Modo IA (escritorio apagado): el dashboard de la consola esta corriendo."""
    from .services import run
    return run("systemctl", "is-active", "llama-kiosk.service") == "active"


def build_preset(settings, models, ai_mode=False):
    """Arma el presets.ini. Con ai_mode, las "opciones extra en modo IA" de cada modelo se suman y pisan al resto
    (la VRAM que libera el escritorio permite, por ejemplo, mas contexto)."""
    glob_lines, _ = C.clean_lines(settings["global"])
    glob_keys = {l.split("=", 1)[0].strip() for l in glob_lines}
    glob_lines = [l for l in field_lines(settings.get("global_fields") or {})
                  if l.split("=", 1)[0].strip() not in glob_keys] + glob_lines  # el texto manda sobre los controles
    out = ["# Generado por llama-panel. No editar a mano: se reescribe al guardar.",
           f"# Modo IA: {'activo (incluye las opciones extra de modo IA)' if ai_mode else 'inactivo'}", "", "[*]"]
    out += glob_lines
    if not any(l.split("=", 1)[0].strip() == "slot-save-path" for l in glob_lines):
        out.append(f"slot-save-path = {C.SLOTS_DIR}/")  # para guardar y restaurar el prompt inicial (prompt_cache.py)
    out.append("")
    for m in models:
        cfg = effective_cfg(model_cfg(settings, m), ai_mode)
        extra, _ = C.clean_lines(cfg.get("extra", ""))
        if ai_mode:
            extra_ai, _ = C.clean_lines(cfg.get("extra_ai", ""))
            ai_keys = {l.split("=", 1)[0].strip() for l in extra_ai}
            extra = [l for l in extra if l.split("=", 1)[0].strip() not in ai_keys] + extra_ai  # lo de modo IA manda
        keys = {l.split("=", 1)[0].strip() for l in extra}
        fields = [f for f in field_lines(cfg) if f.split("=", 1)[0].strip() not in keys]  # lo de "extra" manda
        out += [f"[{m['id']}]", f"model = {m['path']}"] + fields + extra + [""]
    return "\n".join(out)


def write_preset(ai_mode=None):
    """Escribe presets.ini; si no se dice, detecta si el modo IA esta activo."""
    settings = C.load_settings()
    models = scan_models()
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    C.SLOTS_DIR.mkdir(parents=True, exist_ok=True)  # llama-server no arranca si slot-save-path no existe
    C.PRESET_FILE.write_text(build_preset(settings, models, ai_mode_active() if ai_mode is None else ai_mode))
    return models


_server_opts = {"mtime": None, "opts": []}
OPT_RE = re.compile(r"(?:^|[\s,])--([a-z0-9][a-z0-9-]*)")


def server_options():
    """Opciones largas que entiende llama-server (de su --help; se recalcula si cambia el binario).
    Sirve para que la interfaz marque las claves mal escritas en las opciones extra."""
    try:
        mtime = os.path.getmtime(C.LLAMA_SERVER)
    except OSError:
        return []
    if _server_opts["mtime"] != mtime:
        import subprocess
        try:
            out = subprocess.run([C.LLAMA_SERVER, "--help"], capture_output=True, text=True, timeout=20).stdout
        except Exception:  # noqa: BLE001
            out = ""
        _server_opts.update(mtime=mtime, opts=sorted(set(OPT_RE.findall(out))))
    return _server_opts["opts"]


def validate_global_fields(fields_in):
    """Controles de los ajustes globales. Devuelve (errores, {campo: valor}) sin los vacios."""
    fields_in = fields_in if isinstance(fields_in, dict) else {}
    errors, out = [], {}
    for f in FIELDS:
        if f["id"] not in GLOBAL_FIELD_IDS or fields_in.get(f["id"]) in (None, ""):
            continue
        v = check_field(f, fields_in[f["id"]])
        if v is None:
            errors.append(_("{key}: valor inválido en «{field}»: '{value}'", key="global", field=_(f["label"]),
                            value=fields_in[f["id"]]))
        elif v != "":
            out[f["id"]] = v
    return errors, out


def validate_settings(global_text, models_in):
    """Valida lo que manda la interfaz. Devuelve (errores, {key: cfg_normalizada})."""
    errors = [f"global: {b}" for b in C.clean_lines(global_text)[1]]
    cfgs = {}
    for key, c in models_in.items():
        c = c if isinstance(c, dict) else {}
        extra = str(c.get("extra", ""))
        extra_ai = str(c.get("extra_ai", ""))
        errors += [f"{key}: {b}" for b in C.clean_lines(extra)[1]]
        errors += [f"{key} (modo IA): {b}" for b in C.clean_lines(extra_ai)[1]]
        ai_in = c.get("ai") if isinstance(c.get("ai"), dict) else {}
        cfg, ai = {}, {}
        for f in FIELDS:
            raw = bool(c.get(f["id"])) if f["kind"] == "check" else c.get(f["id"], "")
            v = check_field(f, raw)
            if v is None:
                errors.append(_("{key}: valor inválido en «{field}»: '{value}'", key=key, field=_(f["label"]), value=raw))
                v = ""
            cfg[f["id"]] = v
            if f["id"] in ai_in and ai_in[f["id"]] not in ("", None):  # vacio en modo IA = igual que normal
                v = check_field(f, ai_in[f["id"]])
                if v is None:
                    errors.append(_("{key}: valor inválido en «{field}»: '{value}'",
                                    key=f"{key} (modo IA)", field=_(f["label"]), value=ai_in[f["id"]]))
                else:
                    ai[f["id"]] = v
        cfg["nmax"] = cfg["nmax"] or "2"
        cfgs[key] = {**cfg, "extra": extra, "extra_ai": extra_ai, "ai": ai}
    return errors, cfgs
