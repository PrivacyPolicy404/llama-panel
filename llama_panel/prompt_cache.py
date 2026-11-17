"""Prompt inicial guardado: el estado del slot (prompt ya procesado) de un modelo, en disco.

Un cliente tipo agente manda miles de tokens fijos (instrucciones y herramientas) antes del primer mensaje; a
~115 tok/s es un minuto o mas. Con el modelo cargado y ese prompt ya procesado, "guardar" escribe el estado del slot
(en Flash-Next ~0.5 GB, casi fijo: 517 MB con 2.5K tokens, 572 MB con 12K) y el panel lo restaura solo cada vez que
el modelo vuelve a cargarse: el cliente manda lo mismo de siempre y el server reusa todo el principio que coincide.

El archivo depende del modelo y de sus opciones (contexto, tipo de cache KV...): el nombre lleva un hash del preset
vigente, asi que al cambiar un ajuste o el modo IA el guardado viejo deja de usarse en vez de fallar al restaurar.
"""
import hashlib
import json
import re
import threading
import time
import urllib.parse

from . import config as C
from .services import router, router_error, router_models
from .i18n import _

NAME_RE = re.compile(r"\.[0-9a-f]{10}\.bin$")
_lock = threading.Lock()


def preset_block(text, mid):
    """Las lineas del preset que valen para un modelo: las de [*] y las de su seccion, sin comentarios."""
    out, section = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif line and not line.startswith("#") and section in ("*", mid):
            out.append(f"{section}:{line}")
    return out


def file_name(mid, preset_text=None):
    if preset_text is None:
        try:
            preset_text = C.PRESET_FILE.read_text()
        except OSError:
            preset_text = ""
    # Las opciones efectivas (las de [*] pisadas por las del modelo), ordenadas: reordenar el preset o mover un
    # valor igual entre global y modelo no invalida el guardado; cambiar una opción, sí.
    opts = {}
    for entry in preset_block(preset_text, mid):
        line = entry.split(":", 1)[1]
        key, _sep, value = line.partition("=")
        opts[key.strip()] = value.strip()
    canon = "\n".join(f"{k}={opts[k]}" for k in sorted(opts))
    digest = hashlib.sha256(canon.encode()).hexdigest()[:10]
    return f"{mid}.{digest}.bin"


def _files_of(mid):
    """Todos los guardados de un modelo, de cualquier configuracion (sin confundir ids que empiezan igual)."""
    if not C.SLOTS_DIR.is_dir():
        return []
    return [p for p in C.SLOTS_DIR.iterdir()
            if p.name.startswith(mid + ".") and NAME_RE.fullmatch(p.name[len(mid):])]


def info(mid):
    """Datos del guardado que corresponde a la configuracion vigente, o None."""
    path = C.SLOTS_DIR / file_name(mid)
    try:
        stat = path.stat()
    except OSError:
        return None
    try:
        tokens = json.loads(path.with_suffix(".json").read_text()).get("tokens")
    except (OSError, ValueError):
        tokens = None
    return {"tokens": tokens, "bytes": stat.st_size, "saved_at": stat.st_mtime}


def _slot(mid):
    return router("GET", "/slots?model=" + urllib.parse.quote(mid), timeout=3)[0]


def _slot_action(mid, action, name, timeout=180):
    return router("POST", f"/slots/0?action={action}&model=" + urllib.parse.quote(mid),
                  {"model": mid, "filename": name}, timeout=timeout)


def save(mid):
    """Guarda lo que el slot tiene procesado ahora. Devuelve (tokens, None) o (None, error)."""
    with _lock:
        try:
            slot = _slot(mid)
            if slot.get("is_processing"):
                return None, _("el modelo está respondiendo; esperar a que termine")
            name = file_name(mid)
            C.SLOTS_DIR.mkdir(parents=True, exist_ok=True)
            res = _slot_action(mid, "save", name)
        except Exception as e:  # noqa: BLE001
            return None, router_error(e)
        tokens = res.get("n_saved")
        if not tokens:
            (C.SLOTS_DIR / name).unlink(missing_ok=True)
            return None, _("el slot está vacío: mandar primero un mensaje desde el cliente")
        (C.SLOTS_DIR / name).with_suffix(".json").write_text(json.dumps({"tokens": tokens}))
        for old in _files_of(mid):  # guardados de configuraciones anteriores
            if old.name != name:
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
        return tokens, None


def restore(mid, only_if_fresh=False):
    """Restaura el guardado vigente. Con only_if_fresh no pisa un slot que ya proceso algun pedido.
    Devuelve (tokens, None), (0, None) si no habia nada que hacer, o (None, error)."""
    with _lock:
        name = file_name(mid)
        if not (C.SLOTS_DIR / name).is_file():
            return 0, None
        try:
            slot = _slot(mid)
            if slot.get("is_processing") or (only_if_fresh and slot.get("id_task", -1) != -1):
                return 0, None
            res = _slot_action(mid, "restore", name)
        except Exception as e:  # noqa: BLE001
            return None, router_error(e)
        return res.get("n_restored", 0), None


def delete(mid):
    n = 0
    with _lock:
        for path in _files_of(mid):
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            n += 1
    return n


def watch(interval=3):
    """Hilo de fondo: cuando un modelo pasa a "cargado" (por el panel, por un pedido de un cliente o al cambiar
    ajustes), restaura su prompt guardado si el slot todavia no atendio nada."""
    loaded = set()
    while True:
        try:
            now = {k for k, v in router_models().items() if v["status"] == "loaded"}
            for mid in now - loaded:
                tokens, err = restore(mid, only_if_fresh=True)
                if err:
                    print(f"prompt guardado de {mid}: no se pudo restaurar: {err}", flush=True)
                elif tokens:
                    print(f"prompt guardado de {mid}: {tokens} tokens restaurados", flush=True)
            loaded = now
        except Exception as e:  # noqa: BLE001
            print(f"prompt guardado: {e}", flush=True)
        time.sleep(interval)


def start_watcher():
    threading.Thread(target=watch, daemon=True).start()
