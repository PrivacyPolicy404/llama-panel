"""Lo que hacen los botones: encender/apagar, cargar modelos, guardar y aplicar ajustes.

Cada funcion devuelve {"ok": bool, "msg": str} para mostrar en la interfaz.
"""
import time

from . import config as C
from . import img_models as IM
from . import text_models as TM
from .services import free_vram_for, router, router_error, router_models, svc, svc_state, wait_router


def ok(msg, **kw):
    return {"ok": True, "msg": msg, **kw}


def fail(msg, **kw):
    return {"ok": False, "msg": msg, **kw}


# ---------- servicios ----------
def do_action(action):
    if action == "stop":
        svc("stop")
        return ok("Servidor de texto apagado.")
    if action in ("start", "restart"):
        note = free_vram_for("text")
        n = len(TM.write_preset())
        out = svc("restart" if action == "restart" else "start")
        verb = "reiniciado" if action == "restart" else "encendido"
        return ok(f"Servidor de texto {verb} ({n} modelos).{note} {out}".strip())
    if action in ("img_start", "img_restart"):
        m = IM.write_img_env()
        if not m:
            return fail(f"No hay modelos de imagen en {C.IMG_DIR}.")
        note = free_vram_for("img")
        out = svc("restart" if action == "img_restart" else "start", C.IMG_SERVICE)
        verb = "reiniciado" if action == "img_restart" else "encendido"
        return ok(f"Generador de imagenes {verb} con {m['id']}.{note} {out}".strip())
    if action == "img_stop":
        svc("stop", C.IMG_SERVICE)
        return ok("Generador de imagenes apagado.")
    if action == "stop_all":
        svc("stop")
        svc("stop", C.IMG_SERVICE)
        return ok("Todo apagado: la VRAM queda libre.")
    return fail("Accion desconocida.")


# ---------- texto ----------
def ensure_router():
    """Prende el router si hace falta (apagando imagenes). Devuelve (ok, nota)."""
    note = ""
    if svc_state() != "active":
        note = free_vram_for("text")
        TM.write_preset()
        svc("start")
        note = " Servidor de texto encendido." + note
    if not wait_router():
        return False, "El servidor de texto no responde. Mira la pestana Registros." + note
    return True, note


def text_load(body):
    mid = str(body.get("id", ""))
    if mid not in {m["id"] for m in TM.scan_models()}:
        return fail(f"No existe el modelo '{mid}'.")
    good, note = ensure_router()
    if not good:
        return fail(note)
    if mid not in router_models():  # modelo nuevo que el router todavia no conoce
        TM.write_preset()
        try:
            router("GET", "/models?reload=1", timeout=10)
        except Exception:  # noqa: BLE001
            pass
    others = _unload_others(mid)
    err = _load_with_retry(mid)
    if err:
        return fail(f"No se pudo cargar {mid}: {err}")
    if others:
        note = f" Se descargo {', '.join(others)}." + note
    return ok(f"Cargando {mid}…{note}")


def _unload_others(keep):
    """El router admite un solo modelo (--models-max 1): descarga los demas antes de cargar otro."""
    others = [k for k, v in router_models().items()
              if k != keep and v["status"] in ("loaded", "loading", "sleeping")]
    for k in others:
        try:
            router("POST", "/models/unload", {"model": k}, timeout=15)
        except Exception:  # noqa: BLE001
            pass
    return others


def _load_with_retry(mid, timeout=90):
    """La descarga es asincrona: mientras el modelo anterior termina de salir, el router responde
    "model limit reached". Reintenta solo en ese caso. Devuelve None si salio bien o el mensaje de error."""
    end = time.time() + timeout
    while True:
        try:
            router("POST", "/models/load", {"model": mid}, timeout=15)
            return None
        except Exception as e:  # noqa: BLE001
            msg = router_error(e)
            if "limit reached" not in msg or time.time() > end:
                return msg
            time.sleep(1)


def text_unload(body):
    mid = str(body.get("id", ""))
    try:
        router("POST", "/models/unload", {"model": mid}, timeout=15)
    except Exception as e:  # noqa: BLE001
        return fail(f"No se pudo descargar {mid}: {router_error(e)}")
    return ok(f"{mid} descargado: la VRAM queda libre.")


def apply_text():
    """Aplica el preset nuevo sin reiniciar el router: recarga la lista y vuelve a cargar el modelo activo."""
    if svc_state() != "active":
        return "El servidor estaba apagado: se aplica cuando lo enciendas."
    before = [k for k, v in router_models().items() if v["status"] in ("loaded", "loading", "sleeping")]
    try:
        router("GET", "/models?reload=1", timeout=15)
    except Exception:  # noqa: BLE001
        svc("restart")
        return "Servidor reiniciado con la nueva configuracion."
    time.sleep(0.5)
    after = router_models()
    reloaded = []
    for k in before:
        if k in after and after[k]["status"] == "unloaded":
            try:
                router("POST", "/models/load", {"model": k}, timeout=15)
                reloaded.append(k)
            except Exception:  # noqa: BLE001
                pass
    if reloaded:
        return f"Aplicado: {', '.join(reloaded)} se esta recargando con los ajustes nuevos."
    return "Aplicado: el servidor ya usa la nueva configuracion."


def text_rescan(_body):
    with C.SETTINGS_LOCK:
        n = len(TM.write_preset())
    if svc_state() == "active":
        try:
            router("GET", "/models?reload=1", timeout=15)
        except Exception as e:  # noqa: BLE001
            return fail(f"{n} modelos encontrados, pero el servidor no pudo recargar la lista: {router_error(e)}")
        return ok(f"{n} modelos encontrados. El servidor ya los conoce.")
    return ok(f"{n} modelos encontrados.")


def text_settings(body):
    gl = str(body.get("global", ""))
    models_in = body.get("models") or {}
    if not isinstance(models_in, dict):
        return fail("Formato invalido.")
    errors, cfgs = TM.validate_settings(gl, models_in)
    if errors:
        return fail("No se guardo. Revisa estos campos (las opciones van como clave = valor):\n" + "\n".join(errors))
    with C.SETTINGS_LOCK:
        settings = C.load_settings()
        settings["global"] = gl
        settings["models"].update(cfgs)  # conserva ajustes de modelos que hoy no estan en disco
        C.save_settings(settings)
        n = len(TM.write_preset())
    if body.get("apply"):
        return ok(f"Guardado ({n} modelos). {apply_text()}")
    return ok(f"Guardado ({n} modelos). Se aplica en la proxima carga.")


# ---------- imagenes ----------
def img_settings(body):
    models = IM.scan_img_models()
    selected = str(body.get("selected", ""))
    errors, cfgs = IM.validate_settings(models, body.get("models"))
    if selected and selected not in {m["key"] for m in models}:
        errors.append(f"modelo '{selected}' no existe en {C.IMG_DIR}")
    if errors:
        return fail("No se guardo.\n" + "\n".join(errors))
    with C.SETTINGS_LOCK:
        settings = C.load_settings()
        settings["image"]["selected"] = selected or (models[0]["key"] if models else "")
        settings["image"]["models"].update(cfgs)
        C.save_settings(settings)
        m = IM.write_img_env()
    name = m["id"] if m else "ninguno"
    if body.get("apply"):
        if svc_state(C.IMG_SERVICE) == "active":
            svc("restart", C.IMG_SERVICE)
            return ok(f"Guardado (modelo de imagen: {name}) y generador reiniciado.")
        return ok(f"Guardado (modelo de imagen: {name}). El generador esta apagado: se aplica al encenderlo.")
    return ok(f"Guardado (modelo de imagen: {name}). Se aplica en el proximo arranque del generador.")


def img_use(body):
    key = str(body.get("key", ""))
    models = IM.scan_img_models()
    if key not in {m["key"] for m in models}:
        return fail(f"No existe el modelo '{key}'.")
    errors, cfgs = IM.validate_settings(models, body.get("models"))
    if errors:
        return fail("No se guardo.\n" + "\n".join(errors))
    with C.SETTINGS_LOCK:
        settings = C.load_settings()
        settings["image"]["selected"] = key
        settings["image"]["models"].update(cfgs)
        C.save_settings(settings)
        m = IM.write_img_env()
    note = free_vram_for("img")
    was_on = svc_state(C.IMG_SERVICE) in ("active", "activating")
    out = svc("restart" if was_on else "start", C.IMG_SERVICE)
    return ok(f"Generador de imagenes {'reiniciado' if was_on else 'encendido'} con {m['id']}.{note} {out}".strip())
