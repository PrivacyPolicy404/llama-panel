"""Control de los servicios: systemctl y los clientes HTTP del router de llama-server y de KoboldCpp."""
import json
import subprocess
import time
import urllib.error
import urllib.request

from . import config as C


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


def free_vram_for(which):
    """Apaga el otro servicio si esta prendido. Devuelve una nota para el usuario."""
    other, label = (C.IMG_SERVICE, "Generador de imagenes") if which == "text" else (C.SERVICE, "Servidor de texto")
    if svc_state(other) in ("active", "activating"):
        svc("stop", other)
        return f" {label} apagado para liberar VRAM."
    return ""


# ---------- HTTP ----------
def http_json(port, method, path, body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def router(method, path, body=None, timeout=3):
    return http_json(C.CHAT_PORT, method, path, body, timeout)


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


def kobold_status():
    """(progreso, perf) de KoboldCpp, o (None, None) si no responde."""
    try:
        p = http_json(C.IMG_PORT, "GET", "/sdapi/v1/progress", timeout=1.5)
        s = p.get("state") or {}
        progress = {"step": s.get("sampling_step", 0), "steps": s.get("sampling_steps", 0),
                    "eta": p.get("eta_relative", 0)}
        perf = http_json(C.IMG_PORT, "GET", "/api/extra/perf", timeout=1.5)
        return progress, {k: perf.get(k) for k in ("total_img_gens", "queue", "idle", "uptime")}
    except Exception:  # noqa: BLE001
        return None, None
