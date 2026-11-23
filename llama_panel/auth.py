"""Contraseña del panel (opcional). Sin contraseña, el panel queda abierto como siempre.

Con contraseña:
- Desde la misma máquina (127.0.0.1 / ::1) no se pide: el dashboard del modo IA, las herramientas locales y quien
  entra por un túnel SSH (que ya se autenticó con SSH).
- El navegador inicia sesión con la contraseña y recibe una cookie de sesión (30 días, HttpOnly, SameSite=Strict).
- Los programas (clients/dani.py, clientes de la API de OpenAI) usan un token con nombre: "Authorization: Bearer …".
  Los tokens se crean y revocan en la pestaña Configuración; solo se muestran una vez.

En AUTH_FILE (permisos 600) se guardan solo hashes: PBKDF2 para la contraseña, SHA-256 para sesiones y tokens.
Los puertos de texto e imágenes (8080 y 5001 por defecto: llama-server, KoboldCpp o el proxy del encendido a demanda)
no pasan por acá.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time

from . import config as C

AUTH_FILE = C.CFG_DIR / "auth.json"
ITERATIONS = 200_000
SESSION_DAYS = 30
COOKIE = "panel_session"
_lock = threading.Lock()
_fails = {}  # ip -> (fallos, último) para frenar a quien prueba contraseñas


def _load():
    try:
        data = json.loads(AUTH_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("password", None)
    data.setdefault("sessions", {})
    data.setdefault("tokens", [])
    return data


def _save(data):
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.touch(mode=0o600)
    os.chmod(tmp, 0o600)
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(AUTH_FILE)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _pbkdf2(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()


def enabled():
    return bool(_load()["password"])


def check_password(password):
    pw = _load()["password"]
    if not pw:
        return True
    return hmac.compare_digest(_pbkdf2(str(password), pw["salt"], pw["iterations"]), pw["hash"])


def set_password(new, current=""):
    """Pone o cambia la contraseña ("" la saca). Si ya hay una, hace falta la actual. Devuelve un error o None."""
    with _lock:
        data = _load()
        if data["password"] and not check_password(current):
            return "la contraseña actual no es correcta"
        if new and len(new) < 8:
            return "la contraseña nueva tiene que tener al menos 8 caracteres"
        if new:
            salt = secrets.token_hex(16)
            data["password"] = {"salt": salt, "iterations": ITERATIONS, "hash": _pbkdf2(new, salt)}
        else:
            data["password"] = None
        data["sessions"] = {}  # cambiar la contraseña cierra todas las sesiones
        _save(data)
    return None


def new_session():
    sid = secrets.token_urlsafe(32)
    with _lock:
        data = _load()
        now = time.time()
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v > now}
        data["sessions"][_sha(sid)] = now + SESSION_DAYS * 86400
        _save(data)
    return sid


def end_session(sid):
    with _lock:
        data = _load()
        data["sessions"].pop(_sha(sid or ""), None)
        _save(data)


def new_token(name):
    name = " ".join(str(name).split())[:40]
    if not name:
        return None, "el token necesita un nombre"
    token = "lp_" + secrets.token_urlsafe(32)
    with _lock:
        data = _load()
        data["tokens"] = [t for t in data["tokens"] if t["name"] != name]
        data["tokens"].append({"name": name, "hash": _sha(token), "created": int(time.time())})
        _save(data)
    return token, None


def revoke_token(name):
    with _lock:
        data = _load()
        before = len(data["tokens"])
        data["tokens"] = [t for t in data["tokens"] if t["name"] != name]
        _save(data)
    return before != len(data["tokens"])


def tokens():
    return [{"name": t["name"], "created": t["created"]} for t in _load()["tokens"]]


def is_local(ip):
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_loopback
    except ValueError:
        return False


def cookie_value(cookie_header):
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return ""


def allowed(ip, headers):
    """¿Puede pasar este pedido? Sin contraseña, siempre."""
    data = _load()
    if not data["password"] or is_local(ip):
        return True
    now = time.time()
    sid = cookie_value(headers.get("Cookie"))
    if sid and data["sessions"].get(_sha(sid), 0) > now:
        return True
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        h = _sha(auth[7:].strip())
        return any(hmac.compare_digest(h, t["hash"]) for t in data["tokens"])
    return False


def throttle(ip, ok):
    """Después de cada fallo, esperar más (hasta 10 s) antes de contestar el próximo intento de esa IP."""
    if ok:
        _fails.pop(ip, None)
        return 0
    n, _ = _fails.get(ip, (0, 0))
    _fails[ip] = (n + 1, time.time())
    return min(10, 2 ** n * 0.5)


def pending_delay(ip):
    n, last = _fails.get(ip, (0, 0))
    if not n:
        return 0
    return max(0, min(10, 2 ** (n - 1) * 0.5) - (time.time() - last))
