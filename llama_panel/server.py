"""Servidor HTTP: sirve static/ y despacha /api/* a las rutas de api.py."""
import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C
from . import gate
from . import hardware
from . import prompt_cache
from .api import GET_ROUTES, POST_ROUTES, POST_STREAM_ROUTES
from .audio_api import RAW_POST_ROUTES
from .i18n import remember_ui_lang, set_lang
from .install import sync_units

MAX_BODY = 8_000_000  # el audio del compañero llega en base64 dentro del JSON
MAX_RAW_BODY = 30_000_000  # /v1/audio/transcriptions: OpenAI acepta archivos de hasta 25 MB


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencio
        pass

    def _send(self, code, data, ctype="application/json"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/j")) else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # el cliente se fue antes de la respuesta (por ejemplo, el dashboard del modo IA, que se apaga con
            # la misma accion que pidio): la accion ya se hizo, no es un error
            self.close_connection = True

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
            lang = self.headers.get("X-Lang", "")
            set_lang(lang)  # idioma de los mensajes de este pedido
            remember_ui_lang(lang)  # y el que sigue el dashboard del modo IA
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

    def _stream(self, events):
        """Eventos (server-sent events) de una ruta que va contestando de a partes, como la charla del compañero.
        Si el cliente se va, se cierra el generador: la ruta corta lo que estaba haciendo."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for ev in events:
                self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            events.close()
            self.close_connection = True

    def _raw(self, route):
        """Rutas compatibles con OpenAI (/v1/audio/*): cuerpo y respuesta que no son JSON del panel. No cambian nada
        del panel (solo convierten audio), así que no necesitan la protección de exigir JSON."""
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n > MAX_RAW_BODY:
            return self._error(413, "demasiado grande")
        try:
            code, ctype, data = route(self.headers, self.rfile.read(n))
        except Exception as e:  # noqa: BLE001
            return self._error(500, f"Error interno: {e}")
        self._send(code, data, ctype)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in RAW_POST_ROUTES:
            return self._raw(RAW_POST_ROUTES[path])
        route = POST_ROUTES.get(path) or POST_STREAM_ROUTES.get(path)
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
        if path in POST_STREAM_ROUTES:
            return self._stream(route(body))
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
    gate.start_background()
    ThreadingHTTPServer(("0.0.0.0", C.PANEL_PORT), Handler).serve_forever()
