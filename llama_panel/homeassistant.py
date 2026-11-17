"""Cliente mínimo de la API REST de Home Assistant, para las herramientas del compañero.

El token va en BUDDY_DIR/homeassistant.token (permisos 600), nunca en settings.json. Conviene que sea de un usuario
de Home Assistant que no sea administrador: así, aunque alguien le dicte algo raro al compañero, no puede tocar la
configuración de la casa. La dirección se cambia con HA_URL en paths.env.
"""
import json
import os
import urllib.request

from . import config as C

HA_URL = os.environ.get("HA_URL") or C._LOCAL.get("HA_URL") or "http://127.0.0.1:8123"


def token_file():
    return C.BUDDY_DIR / "homeassistant.token"


def token():
    try:
        return token_file().read_text().strip()
    except OSError:
        return ""


def configured():
    return bool(token())


def save_token(value):
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    f = token_file()
    f.touch(mode=0o600)
    os.chmod(f, 0o600)
    f.write_text(value.strip() + "\n")


def _req(method, path, body=None, timeout=10):
    req = urllib.request.Request(HA_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def states():
    return _req("GET", "/api/states")


def call(domain, service, data):
    return _req("POST", f"/api/services/{domain}/{service}", data)


def check():
    """(ok, mensaje) para la interfaz."""
    if not configured():
        return False, "sin token"
    try:
        return True, _req("GET", "/api/", timeout=5).get("message", "ok")
    except Exception as e:  # noqa: BLE001
        return False, str(getattr(e, "reason", e))
