"""Servidor HTTP: sirve static/ y despacha /api/* a las rutas de api.py."""
import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C
from . import hardware
from . import prompt_cache
from .api import GET_ROUTES, POST_ROUTES
from .i18n import set_lang
from .install import sync_units

MAX_BODY = 1_000_000


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencio
        pass

    def _send(self, code, data, ctype="application/json"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code, msg):
        self._send(code, {"ok": False, "msg": msg})

    def _static(self, rel):
        path = (C.STATIC_DIR / rel).resolve()
        if C.STATIC_DIR not in path.parents or not path.is_file():
            return self._error(404, "no encontrado")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype)

    def parse_request(self):
        ok = super().parse_request()
        if ok:
            set_lang(self.headers.get("X-Lang", ""))  # idioma de los mensajes de este pedido
        return ok

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._static("index.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        route = GET_ROUTES.get(u.path)
        if not route:
            return self._error(404, "no encontrado")
        try:
            self._send(200, route(urllib.parse.parse_qs(u.query)))
        except Exception as e:  # noqa: BLE001
            self._error(500, f"Error interno: {e}")

    def do_POST(self):
        route = POST_ROUTES.get(urllib.parse.urlparse(self.path).path)
        if not route:
            return self._error(404, "no encontrado")
        # Exigir JSON impide que otra pagina web dispare acciones con un formulario (CSRF)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._error(415, "Se espera JSON.")
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n > MAX_BODY:
            return self._error(413, "demasiado grande")
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("se espera un objeto")
        except ValueError as e:
            return self._error(400, f"JSON invalido: {e}")
        try:
            self._send(200, route(body))
        except Exception as e:  # noqa: BLE001
            self._error(500, f"Error interno: {e}")


def serve():
    try:
        changed = sync_units()
        if changed:
            print(f"units actualizados: {', '.join(changed)}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"no se pudieron revisar los units: {e}", flush=True)
    hardware.start_sampler()
    prompt_cache.start_watcher()
    ThreadingHTTPServer(("0.0.0.0", C.PANEL_PORT), Handler).serve_forever()
