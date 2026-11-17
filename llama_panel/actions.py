"""Lo que hacen los botones: encender/apagar, cargar modelos, guardar y aplicar ajustes.

Cada funcion devuelve {"ok": bool, "msg": str} para mostrar en la interfaz.
"""
import time

from . import config as C
from . import img_models as IM
from . import prompt_cache as PC
from . import services
from . import text_models as TM
from .services import free_vram_for, router, router_error, router_models, svc, svc_state, wait_router
from .i18n import _


def ok(msg, **kw):
    return {"ok": True, "msg": msg, **kw}


def fail(msg, **kw):
    return {"ok": False, "msg": msg, **kw}


# ---------- servicios ----------
def do_action(action):
    if action == "stop":
        svc("stop")
        return ok(_("Servidor de texto apagado."))
    if action in ("start", "restart"):
        note = free_vram_for("text")
        n = len(TM.write_preset())
        out = svc("restart" if action == "restart" else "start")
        msg = _("Servidor de texto reiniciado ({n} modelos).", n=n) if action == "restart" \
            else _("Servidor de texto encendido ({n} modelos).", n=n)
        return ok(f"{msg}{note} {out}".strip())
    if action in ("img_start", "img_restart"):
        m = IM.write_img_env()
        if not m:
            return fail(_("No hay modelos de imagen en {dir}.", dir=C.IMG_DIR))
        note = free_vram_for("img")
        out = svc("restart" if action == "img_restart" else "start", C.IMG_SERVICE)
        msg = _("Generador de imágenes reiniciado con {model}.", model=m["id"]) if action == "img_restart" \
            else _("Generador de imágenes encendido con {model}.", model=m["id"])
        return ok(f"{msg}{note} {out}".strip())
    if action == "img_stop":
        svc("stop", C.IMG_SERVICE)
        return ok(_("Generador de imágenes apagado."))
    if action in ("ai_mode_on", "ai_mode_off"):
        on = action == "ai_mode_on"
        good, out = services.ai_mode(on)
        if not good:
            return fail(f"{_('No se pudo activar el modo IA.') if on else _('No se pudo desactivar el modo IA.')} {out}".strip())
        # las "opciones extra en modo IA" de cada modelo entran o salen del preset
        with C.SETTINGS_LOCK:
            TM.write_preset(ai_mode=on)
        note = f" {apply_text()}" if svc_state() == "active" else ""
        return ok((_("Modo IA activado: escritorio apagado, dashboard en la pantalla.") if on
                   else _("Modo IA desactivado: el escritorio vuelve en unos segundos.")) + note)
    if action == "stop_all":
        svc("stop")
        svc("stop", C.IMG_SERVICE)
        return ok(_("Todo apagado: la VRAM queda libre."))
    return fail(_("Acción desconocida."))


# ---------- texto ----------
def ensure_router():
    """Prende el router si hace falta (apagando imagenes). Devuelve (ok, nota)."""
    note = ""
    if svc_state() != "active":
        note = free_vram_for("text")
        TM.write_preset()
        svc("start")
        note = _(" Servidor de texto encendido.") + note
    if not wait_router():
        return False, _("El servidor de texto no responde. Mirá la pestaña Registros.") + note
    return True, note


def text_load(body):
    mid = str(body.get("id", ""))
    if mid not in {m["id"] for m in TM.scan_models()}:
        return fail(_("No existe el modelo '{model}'.", model=mid))
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
        return fail(_("No se pudo cargar {model}: {err}", model=mid, err=err))
    if others:
        note = _(" Se descargó {models}.", models=", ".join(others)) + note
    return ok(_("Cargando {model}…", model=mid) + note)


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
        return fail(_("No se pudo descargar {model}: {err}", model=mid, err=router_error(e)))
    return ok(_("{model} descargado: la VRAM queda libre.", model=mid))


def _loaded_model(body):
    mid = str(body.get("id", ""))
    return mid if router_models().get(mid, {}).get("status") == "loaded" else None


def text_prompt_save(body):
    mid = _loaded_model(body)
    if not mid:
        return fail(_("El modelo tiene que estar cargado."))
    profile = PC.slug(body.get("name"))
    tokens, err = PC.save(mid, profile)
    if err:
        return fail(_("No se pudo guardar el prompt: {err}", err=err))
    return ok(_("Prompt «{name}» guardado: {tokens} tokens. Se precarga solo cada vez que {model} se cargue.",
                name=profile, tokens=tokens, model=mid))


def text_prompt_restore(body):
    mid = _loaded_model(body)
    if not mid:
        return fail(_("El modelo tiene que estar cargado."))
    tokens, err = PC.restore(mid, profile=PC.slug(body.get("name")))
    if err:
        return fail(_("No se pudo restaurar el prompt: {err}", err=err))
    return ok(_("Prompt restaurado: {tokens} tokens.", tokens=tokens) if tokens
              else _("No hay un prompt guardado para la configuración actual."))


def text_prompt_delete(body):
    name = body.get("name")
    n = PC.delete(str(body.get("id", "")), PC.slug(name) if name else None)
    return ok(_("Prompt guardado borrado.") if n else _("No había un prompt guardado."))


def apply_text():
    """Aplica el preset nuevo sin reiniciar el router: recarga la lista y vuelve a cargar el modelo activo."""
    if svc_state() != "active":
        return _("El servidor estaba apagado: se aplica cuando lo enciendas.")
    before = [k for k, v in router_models().items() if v["status"] in ("loaded", "loading", "sleeping")]
    try:
        router("GET", "/models?reload=1", timeout=15)
    except Exception:  # noqa: BLE001
        svc("restart")
        return _("Servidor reiniciado con la nueva configuración.")
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
        return _("Aplicado: {models} se está recargando con los ajustes nuevos.", models=", ".join(reloaded))
    return _("Aplicado: el servidor ya usa la nueva configuración.")


def text_rescan(_body):
    with C.SETTINGS_LOCK:
        n = len(TM.write_preset())
    if svc_state() == "active":
        try:
            router("GET", "/models?reload=1", timeout=15)
        except Exception as e:  # noqa: BLE001
            return fail(_("{n} modelos encontrados, pero el servidor no pudo recargar la lista: {err}", n=n, err=router_error(e)))
        return ok(_("{n} modelos encontrados. El servidor ya los conoce.", n=n))
    return ok(_("{n} modelos encontrados.", n=n))


def text_settings(body):
    gl = str(body.get("global", ""))
    models_in = body.get("models") or {}
    if not isinstance(models_in, dict):
        return fail(_("Formato inválido."))
    errors, cfgs = TM.validate_settings(gl, models_in)
    gerrors, gfields = TM.validate_global_fields(body.get("global_fields"))
    errors += gerrors
    if errors:
        return fail(_("No se guardó. Revisá estos campos (las opciones van como clave = valor):") + "\n" + "\n".join(errors))
    with C.SETTINGS_LOCK:
        settings = C.load_settings()
        settings["global"] = gl
        if "global_fields" in body:
            settings["global_fields"] = gfields
        settings["models"].update(cfgs)  # conserva ajustes de modelos que hoy no estan en disco
        C.save_settings(settings)
        n = len(TM.write_preset())
    if body.get("apply"):
        return ok(_("Guardado ({n} modelos).", n=n) + " " + apply_text())
    return ok(_("Guardado ({n} modelos).", n=n) + " " + _("Se aplica en la próxima carga."))


# ---------- imagenes ----------
def img_settings(body):
    models = IM.scan_img_models()
    selected = str(body.get("selected", ""))
    errors, cfgs = IM.validate_settings(models, body.get("models"))
    if selected and selected not in {m["key"] for m in models}:
        errors.append(_("modelo '{model}' no existe en {dir}", model=selected, dir=C.IMG_DIR))
    if errors:
        return fail(_("No se guardó.") + "\n" + "\n".join(errors))
    with C.SETTINGS_LOCK:
        settings = C.load_settings()
        settings["image"]["selected"] = selected or (models[0]["key"] if models else "")
        settings["image"]["models"].update(cfgs)
        C.save_settings(settings)
        m = IM.write_img_env()
    name = m["id"] if m else _("ninguno")
    if body.get("apply"):
        if svc_state(C.IMG_SERVICE) == "active":
            svc("restart", C.IMG_SERVICE)
            return ok(_("Guardado (modelo de imagen: {model}) y generador reiniciado.", model=name))
        return ok(_("Guardado (modelo de imagen: {model}). El generador está apagado: se aplica al encenderlo.", model=name))
    return ok(_("Guardado (modelo de imagen: {model}). Se aplica en el próximo arranque del generador.", model=name))


def img_use(body):
    key = str(body.get("key", ""))
    models = IM.scan_img_models()
    if key not in {m["key"] for m in models}:
        return fail(_("No existe el modelo '{model}'.", model=key))
    errors, cfgs = IM.validate_settings(models, body.get("models"))
    if errors:
        return fail(_("No se guardó.") + "\n" + "\n".join(errors))
    with C.SETTINGS_LOCK:
        settings = C.load_settings()
        settings["image"]["selected"] = key
        settings["image"]["models"].update(cfgs)
        C.save_settings(settings)
        m = IM.write_img_env()
    note = free_vram_for("img")
    was_on = svc_state(C.IMG_SERVICE) in ("active", "activating")
    out = svc("restart" if was_on else "start", C.IMG_SERVICE)
    msg = _("Generador de imágenes reiniciado con {model}.", model=m["id"]) if was_on \
        else _("Generador de imágenes encendido con {model}.", model=m["id"])
    return ok(f"{msg}{note} {out}".strip())
