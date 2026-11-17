"""Prompt inicial guardado: el estado del slot (prompt ya procesado) de un modelo, en disco.

Un cliente tipo agente manda miles de tokens fijos (instrucciones y herramientas) antes del primer mensaje; a
~115 tok/s es un minuto o mas. Con el modelo cargado y ese prompt ya procesado, "guardar" escribe el estado del slot
(en Flash-Next ~0.5 GB, casi fijo: 517 MB con 2.5K tokens, 572 MB con 12K) y el panel lo restaura solo cada vez que
el modelo vuelve a cargarse: el cliente manda lo mismo de siempre y el server reusa todo el principio que coincide.

El archivo depende del modelo y de sus opciones (contexto, tipo de cache KV...): el nombre lleva un hash del preset
vigente, asi que al cambiar un ajuste o el modo IA el guardado viejo deja de usarse en vez de fallar al restaurar.

Perfiles: cada modelo puede tener varios prompts guardados con nombre (por ejemplo "cline" y "voz"). Al cargar el
modelo el panel los PRECARGA en el cache de prompts en RAM de llama-server (--cache-ram): restaura cada archivo y manda
un pedido minimo, que hace que el servidor guarde ese slot en RAM; el principal queda en el slot. Despues cada cliente
encuentra su prompt solo, en ~1 s en vez de ~1 min (bitacora 5.34). "principal" usa el nombre de archivo de siempre.
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

NAME_RE = re.compile(r"\.([0-9a-f]{10})(?:\.([a-z0-9_-]{1,32}))?\.bin$")
MAIN = "principal"
_lock = threading.Lock()


def slug(name):
    """Nombre de perfil para el archivo: minusculas, letras, numeros, - y _ (vacio = principal)."""
    out = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-")[:32]
    return out or MAIN


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


def config_hash(mid, preset_text=None):
    """Hash de las opciones efectivas del modelo (las de [*] pisadas por las suyas), ordenadas: reordenar el preset
    o mover un valor igual entre global y modelo no invalida los guardados; cambiar una opcion, si."""
    if preset_text is None:
        try:
            preset_text = C.PRESET_FILE.read_text()
        except OSError:
            preset_text = ""
    opts = {}
    for entry in preset_block(preset_text, mid):
        key, _sep, value = entry.split(":", 1)[1].partition("=")
        opts[key.strip()] = value.strip()
    canon = "\n".join(f"{k}={opts[k]}" for k in sorted(opts))
    return hashlib.sha256(canon.encode()).hexdigest()[:10]


def file_name(mid, preset_text=None, profile=MAIN):
    digest = config_hash(mid, preset_text)
    profile = slug(profile)
    return f"{mid}.{digest}.bin" if profile == MAIN else f"{mid}.{digest}.{profile}.bin"


def _parse(mid, name):
    """(hash, perfil) de un archivo de este modelo, o None (sin confundir ids que empiezan igual)."""
    if not name.startswith(mid + "."):
        return None
    m = NAME_RE.fullmatch(name[len(mid):])
    return (m[1], m[2] or MAIN) if m else None


def _files_of(mid):
    """Todos los guardados de un modelo, de cualquier configuracion y perfil."""
    if not C.SLOTS_DIR.is_dir():
        return []
    return [p for p in C.SLOTS_DIR.iterdir() if _parse(mid, p.name)]


def profiles(mid):
    """Perfiles guardados para la configuracion vigente: [{name, tokens, bytes, saved_at}], el principal primero."""
    digest = config_hash(mid)
    out = []
    for path in _files_of(mid):
        h, profile = _parse(mid, path.name)
        if h != digest:
            continue
        try:
            tokens = json.loads(path.with_suffix(".json").read_text()).get("tokens")
        except (OSError, ValueError):
            tokens = None
        st = path.stat()
        out.append({"name": profile, "tokens": tokens, "bytes": st.st_size, "saved_at": st.st_mtime})
    return sorted(out, key=lambda x: (x["name"] != MAIN, -x["saved_at"]))


def info(mid):
    """El perfil principal (o el ultimo guardado) de la configuracion vigente, o None."""
    found = profiles(mid)
    return found[0] if found else None


def _slot(mid):
    return router("GET", "/slots?model=" + urllib.parse.quote(mid), timeout=3)[0]


def _slot_action(mid, action, name, timeout=180):
    return router("POST", f"/slots/0?action={action}&model=" + urllib.parse.quote(mid),
                  {"model": mid, "filename": name}, timeout=timeout)


def save(mid, profile=MAIN):
    """Guarda lo que el slot tiene procesado ahora como ese perfil. Devuelve (tokens, None) o (None, error)."""
    with _lock:
        try:
            slot = _slot(mid)
            if slot.get("is_processing"):
                return None, _("el modelo está respondiendo; esperar a que termine")
            name = file_name(mid, profile=profile)
            C.SLOTS_DIR.mkdir(parents=True, exist_ok=True)
            res = _slot_action(mid, "save", name)
        except Exception as e:  # noqa: BLE001
            return None, router_error(e)
        tokens = res.get("n_saved")
        if not tokens:
            (C.SLOTS_DIR / name).unlink(missing_ok=True)
            return None, _("el slot está vacío: mandar primero un mensaje desde el cliente")
        (C.SLOTS_DIR / name).with_suffix(".json").write_text(json.dumps({"tokens": tokens}))
        return tokens, None


def restore(mid, only_if_fresh=False, profile=MAIN):
    """Restaura un perfil de la configuracion vigente. Con only_if_fresh no pisa un slot que ya proceso algun pedido.
    Devuelve (tokens, None), (0, None) si no habia nada que hacer, o (None, error)."""
    with _lock:
        name = file_name(mid, profile=profile)
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


def _nudge(mid):
    """Pedido minimo: al llegar, el servidor guarda el slot (recien restaurado) en su cache de prompts en RAM."""
    router("POST", "/v1/chat/completions", {"model": mid, "messages": [{"role": "user", "content": "ok"}],
                                            "max_tokens": 1, "chat_template_kwargs": {"enable_thinking": False}},
           timeout=120)


def prime(mid):
    """Precarga todos los perfiles del modelo en el cache en RAM del servidor y deja el principal en el slot.
    Solo con el slot sin usar (recien cargado). Devuelve [(perfil, tokens)] de lo que quedo listo."""
    found = profiles(mid)
    if not found:
        return []
    try:
        if _slot(mid).get("id_task", -1) != -1:
            return []
    except Exception:  # noqa: BLE001
        return []
    done = []
    for p in found[1:] + found[:1]:  # el principal al final: queda en el slot
        tokens, err = restore(mid, profile=p["name"])
        if err or not tokens:
            continue
        if p is not found[0]:
            try:
                _nudge(mid)
            except Exception:  # noqa: BLE001
                continue
        done.append((p["name"], tokens))
    return done


def delete(mid, profile=None):
    """Borra un perfil (de la configuracion vigente) o, sin perfil, todos los guardados del modelo."""
    n = 0
    digest = config_hash(mid) if profile is not None else None
    with _lock:
        for path in _files_of(mid):
            h, prof = _parse(mid, path.name)
            if profile is not None and (h != digest or prof != slug(profile)):
                continue
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            n += 1
    return n


def watch(interval=3):
    """Hilo de fondo: cuando un modelo pasa a "cargado" (por el panel, por un pedido de un cliente o al cambiar
    ajustes), precarga sus prompts guardados si el slot todavia no atendio nada."""
    loaded = set()
    while True:
        try:
            now = {k for k, v in router_models().items() if v["status"] == "loaded"}
            for mid in now - loaded:
                done = prime(mid)
                if done:
                    print(f"prompts guardados de {mid} precargados: "
                          + ", ".join(f"{p} ({t} tokens)" for p, t in done), flush=True)
            loaded = now
        except Exception as e:  # noqa: BLE001
            print(f"prompt guardado: {e}", flush=True)
        time.sleep(interval)


def start_watcher():
    threading.Thread(target=watch, daemon=True).start()
