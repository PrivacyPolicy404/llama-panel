"""Control de los servicios: systemctl y los clientes HTTP del router de llama-server y de KoboldCpp."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config as C
from .i18n import _


# ---------- systemd ----------
def run(*cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def svc(action, name=C.SERVICE):
    return run("systemctl", "--user", action, name)


def svc_state(name=C.SERVICE):
    return run("systemctl", "--user", "is-active", name) or "unknown"


def svc_states():
    out = run("systemctl", "--user", "is-active", C.SERVICE, C.IMG_SERVICE).splitlines()
    out += ["unknown"] * (2 - len(out))
    return {"text": out[0], "img": out[1]}


AI_MODE_HELPER = "/usr/local/sbin/llama-modo-ia"


def gpu_access_without_desktop():
    """True si este proceso puede usar la GPU sin sesion de escritorio: necesita el grupo render (el permiso que da
    logind a la sesion activa desaparece al apagar el escritorio y los modelos caerian a llvmpipe, en el CPU)."""
    import grp
    try:
        return grp.getgrnam("render").gr_gid in os.getgroups()
    except KeyError:  # sin grupo render en el sistema: no hay forma de saberlo, no bloquear
        return True


def ai_mode_state():
    """Modo IA: escritorio apagado y dashboard de texto en tty1 (ver scripts/install-modo-ia.sh). En WSL2 no hay
    escritorio de Linux ni tty1 que mostrar: queda como no instalado y la interfaz esconde el botón."""
    from .hardware import IS_WSL
    installed = not IS_WSL and os.path.exists(AI_MODE_HELPER)
    active = installed and run("systemctl", "is-active", "llama-kiosk.service") == "active"
    return {"installed": installed, "active": active, "gpu_ok": gpu_access_without_desktop()}


def ai_mode(on):
    """Prende o apaga el modo IA. Devuelve (ok, salida)."""
    if not ai_mode_state()["installed"]:
        return False, _("El modo IA no está instalado: correr una vez  sudo ~/llama-panel/scripts/install-modo-ia.sh")
    if on and not gpu_access_without_desktop():
        return False, _("Sin escritorio, los modelos perderían el acceso a la GPU. Correr de nuevo  "
                        "sudo ~/llama-panel/scripts/install-modo-ia.sh  (agrega los grupos render y video) y reiniciar.")
    if on:
        run("systemctl", "--user", "stop", "sunshine.service")
    out = run("sudo", "-n", AI_MODE_HELPER, "on" if on else "off", timeout=60)
    ok = ai_mode_state()["active"] == on
    if not on:
        run("systemctl", "--user", "start", "sunshine.service")
    return ok, out


def free_vram_for(which):
    """Apaga el otro servicio si esta prendido. Devuelve una nota para el usuario."""
    other, note = (C.IMG_SERVICE, " Generador de imágenes apagado para liberar VRAM.") if which == "text" \
        else (C.SERVICE, " Servidor de texto apagado para liberar VRAM.")
    if svc_state(other) in ("active", "activating"):
        svc("stop", other)
        return _(note)
    return ""


# ---------- HTTP ----------
def http_json(port, method, path, body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


# Modelos que el panel mandó descargar. Mientras se descarga, el router lo sigue mostrando como cargado y cualquier
# consulta que lo nombre (/slots?model=…, de la telemetría o del gate) queda en cola y lo vuelve a cargar: con el
# panel abierto en el navegador, «Descargar» no descargaba (2026-11-18). Hasta que el router lo muestre descargado,
# esas consultas no salen.
_UNLOADING = {}
UNLOAD_GUARD_S = 30


def _named_model(path):
    return (urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get("model") or [None])[0]


def router(method, path, body=None, timeout=3):
    if path == "/models/unload" and isinstance(body, dict) and body.get("model"):
        _UNLOADING[body["model"]] = time.time()
    else:
        mid = _named_model(path)
        if mid and time.time() - _UNLOADING.get(mid, 0) < UNLOAD_GUARD_S:
            raise RuntimeError(f"{mid} se está descargando")
    return http_json(C.backend_port("text"), method, path, body, timeout)


def router_error(e):
    """Mensaje legible de un error del router (que responde {"error": {"message": ...}})."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            j = json.loads(e.read())
            return (j.get("error") or {}).get("message") or str(j)
        except Exception:  # noqa: BLE001
            return f"HTTP {e.code}"
    return str(getattr(e, "reason", e))


def router_models():
    """{id: {status, failed, ctx_max}} segun el router; {} si no responde."""
    try:
        data = router("GET", "/models", timeout=2).get("data", [])
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for m in data:
        st = m.get("status") or {}
        if st.get("value", "unloaded") == "unloaded":
            _UNLOADING.pop(m["id"], None)  # ya está descargado: las consultas vuelven a ser inofensivas
        out[m["id"]] = {"status": st.get("value", "unloaded"), "failed": bool(st.get("failed")),
                        "ctx_max": m.get("context_length")}
    return out


def wait_router(timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            router("GET", "/health", timeout=1)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def wait_kobold(timeout=300):
    """Espera a que KoboldCpp termine de cargar el modelo de imagen. Corta antes si el servicio se cae."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            http_json(C.backend_port("img"), "GET", "/api/extra/version", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            if svc_state(C.IMG_SERVICE) in ("inactive", "failed"):
                return False
            time.sleep(1)
    return False


def kobold_status():
    """(progreso, perf) de KoboldCpp, o (None, None) si no responde."""
    try:
        p = http_json(C.backend_port("img"), "GET", "/sdapi/v1/progress", timeout=1.5)
        s = p.get("state") or {}
        progress = {"step": s.get("sampling_step", 0), "steps": s.get("sampling_steps", 0),
                    "eta": p.get("eta_relative", 0)}
        perf = http_json(C.backend_port("img"), "GET", "/api/extra/perf", timeout=1.5)
        return progress, {k: perf.get(k) for k in ("total_img_gens", "queue", "idle", "uptime")}
    except Exception:  # noqa: BLE001
        return None, None
