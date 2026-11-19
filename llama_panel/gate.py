"""Encendido a demanda: el panel atiende los puertos de texto (8080) e imágenes (5001) y prende lo que pide cada cliente.

Con "swap" activo, llama-server y KoboldCpp escuchan solo en la máquina (config.INNER_PORTS) y este proxy les pasa los
pedidos. Un pedido de texto con el generador de imágenes prendido lo apaga y enciende el servidor de texto (y al revés),
pero nunca mientras el otro lado tiene pedidos en curso: espera a que terminen. Dentro del texto, el router de
llama-server ya elige, carga y desaloja modelos según el campo "model" del pedido.

Aparte, con "idle_min" > 0, un hilo descarga el modelo de texto o apaga el generador de imágenes después de esos
minutos sin uso (funciona con o sin el proxy).
"""
import collections
import http.client
import json
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import config as C
from .i18n import _
from .netserver import DualStackServer

SIDES = ("text", "img")
OTHER = {"text": "img", "img": "text"}
WAIT_OTHER = 900        # cuánto espera un pedido a que el otro lado termine antes de responder 503
UPSTREAM_TIMEOUT = 3600  # una carga de Flash-Next y una respuesta larga pueden tardar minutos
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "proxy-connection", "content-length", "host"}
RESP_SKIP = HOP | {"server", "date"}  # send_response ya pone los suyos

# Pedidos que no prenden nada: consultas que una interfaz abierta repite cada pocos segundos (si cambiaran de
# servicio, una pestaña olvidada pelearía con el cliente que sí está trabajando) y cortes de una generación
# y suscripciones que no terminan nunca (/models/sse: la interfaz web de llama-server abierta), que bloquearían las
# imágenes para siempre si contaran como pedidos en curso. Solo /v1/realtime (una sesión de voz) y las páginas de la
# interfaz web prenden el texto con un GET.
TEXT_PASSIVE_GET = {"/health", "/v1/health", "/models", "/v1/models", "/models/sse", "/props", "/v1/props", "/slots",
                    "/metrics", "/v1/stream", "/lora-adapters", "/tools", "/cors-proxy"}
TEXT_PASSIVE_POST = {"/models/unload", "/v1/chat/completions/control", "/v1/streams/lookup"}
IMG_PASSIVE_GET_PREFIXES = ("/sdapi/", "/api/", "/v1/")
IMG_PASSIVE_POST = {"/sdapi/v1/interrupt", "/sdapi/v1/skip", "/api/extra/abort", "/api/extra/generate/check",
                    "/api/extra/tokencount"}
# Pedidos de texto con campo "model" que se puede corregir (clientes que mandan un nombre fijo, como "gpt-4o")
MODEL_PATHS = {"/v1/chat/completions", "/chat/completions", "/v1/completions", "/completions", "/completion",
               "/v1/embeddings", "/embeddings", "/v1/messages", "/v1/responses", "/infill", "/v1/rerank", "/rerank"}


def needs_service(side, method, path):
    """True si el pedido tiene que prender su servicio (y apagar el otro); False si solo pasa si ya está prendido."""
    p = path.rstrip("/") or "/"
    if method in ("OPTIONS", "HEAD"):
        return False
    if side == "text":
        if method == "GET":
            return p not in TEXT_PASSIVE_GET
        return p not in TEXT_PASSIVE_POST
    if method == "GET":
        return not p.startswith(IMG_PASSIVE_GET_PREFIXES)
    return p not in IMG_PASSIVE_POST


# ---------- actividad (para la descarga por inactividad) ----------
_last = {s: time.time() for s in SIDES}
EVENTS = collections.deque(maxlen=8)


def touch(side):
    _last[side] = time.time()


def event(msg, **kw):
    """Lo que hizo el encendido a demanda, para la interfaz (se traduce al consultarlo) y el registro del panel."""
    EVENTS.append({"t": time.time(), "msg": msg, "kw": kw})
    print("a demanda: " + msg.format(**kw), flush=True)


# ---------- árbitro: texto e imágenes no se pisan ----------
class Busy(Exception):
    pass


class Arbiter:
    """Cuenta los pedidos en curso de cada lado. Un pedido espera mientras el otro lado tenga pedidos en curso; una vez
    adentro, el otro lado no puede apagarle el servicio hasta que termine. `ensure(side)` prende el servicio (apagando
    el otro) y se llama de a uno."""

    def __init__(self, ensure):
        self.ensure = ensure
        self.cond = threading.Condition()
        self.inflight = {s: 0 for s in SIDES}
        self.waiting = {s: 0 for s in SIDES}
        self._swap = threading.Lock()

    def enter(self, side, timeout=WAIT_OTHER):
        other = OTHER[side]
        with self.cond:
            self.waiting[side] += 1
            try:
                if not self.cond.wait_for(lambda: self.inflight[other] == 0, timeout):
                    raise Busy(other)
            finally:
                self.waiting[side] -= 1
            self.inflight[side] += 1
        touch(side)
        try:
            with self._swap:
                self.ensure(side)
        except BaseException:
            self.leave(side)
            raise

    def leave(self, side):
        touch(side)
        with self.cond:
            self.inflight[side] -= 1
            self.cond.notify_all()

    def snapshot(self):
        with self.cond:
            return {"inflight": dict(self.inflight), "waiting": dict(self.waiting)}


class ServiceError(Exception):
    pass


def ensure_service(side):
    """Deja prendido el servicio de `side`; si hace falta, apaga el otro. Lanza ServiceError si no arranca."""
    from . import actions, img_models as IM, services
    if side == "text":
        was_on = services.svc_state(C.SERVICE) == "active"
        t0 = time.time()
        good, note = actions.ensure_router()
        if not good:
            raise ServiceError(note.strip())
        if not was_on:
            event("Pedido de texto: servidor de texto encendido en {s} s.", s=round(time.time() - t0))
        return
    if services.svc_state(C.IMG_SERVICE) == "active":
        if services.wait_kobold(timeout=300):
            return
        raise ServiceError(_("El generador de imágenes no responde. Mirá la pestaña Registros."))
    t0 = time.time()
    m = IM.write_img_env()
    if not m:
        raise ServiceError(_("No hay modelos de imagen en {dir}.", dir=C.IMG_DIR))
    services.free_vram_for("img")
    services.svc("start", C.IMG_SERVICE)
    if not services.wait_kobold(timeout=300):
        raise ServiceError(_("El generador de imágenes no arrancó. Mirá la pestaña Registros."))
    event("Pedido de imagen: generador encendido con {model} en {s} s.", model=m["id"], s=round(time.time() - t0))


ARBITER = Arbiter(ensure_service)


_last_model = {"id": None}


def fix_model(body, models, last=None):
    """Si el pedido de texto no nombra un modelo que exista, usa el que está cargado (o cargándose) o, si no hay
    ninguno (por ejemplo, después de generar imágenes), `last`, el último que se usó. `models` es la lista "data" de
    /models del router. Devuelve el cuerpo nuevo, o None si no hay que tocarlo."""
    try:
        req = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(req, dict):
        return None
    names = set()
    for m in models:
        names.add(m.get("id"))
        names.update(m.get("aliases") or [])
    if req.get("model") in names:
        return None
    active = [m["id"] for m in models if (m.get("status") or {}).get("value") in ("loaded", "loading", "sleeping")]
    if len(active) == 1:
        req["model"] = active[0]
    elif not active and last in names:
        req["model"] = last
    else:
        return None
    return json.dumps(req).encode()


def offline_models():
    """/v1/models con el servidor de texto apagado (por ejemplo, mientras se generan imágenes): los modelos en disco,
    para que los clientes puedan listarlos sin prender nada."""
    from . import text_models as TM
    return {"object": "list", "data": [{"id": m["id"], "object": "model", "owned_by": "llamacpp",
                                        "status": {"value": "unloaded"}} for m in TM.scan_models()]}


# ---------- proxy ----------
class GateHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    side = "text"
    arbiter = ARBITER
    timeout = 600  # conexión de un cliente sin pedidos

    def log_message(self, *a):
        pass

    def _upstream_port(self):
        return C.INNER_PORTS[self.side]

    def _read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            data = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(data)
                data += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n > 0 else None

    def _reply(self, code, data, retry=None):
        raw = json.dumps(data, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if retry:
                self.send_header("Retry-After", str(retry))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            self.close_connection = True

    def _unavailable(self, msg, retry=30):
        self._reply(503, {"error": {"code": 503, "message": msg, "type": "unavailable_error"}}, retry)

    def _handle(self):
        try:
            body = self._read_body()
        except (ValueError, OSError):
            self.close_connection = True
            return
        path = urllib.parse.urlsplit(self.path).path
        if not needs_service(self.side, self.command, path):
            return self._forward(body, passive=True)
        try:
            self.arbiter.enter(self.side)
        except Busy:
            return self._unavailable(_("El generador de imágenes está ocupado; probá de nuevo en un rato.")
                                     if self.side == "text" else
                                     _("El servidor de texto está respondiendo; probá de nuevo en un rato."))
        except ServiceError as e:
            return self._unavailable(str(e))
        try:
            if self.side == "text" and self.command == "POST" and body and path.rstrip("/") in MODEL_PATHS:
                body = self._fix_model(body)
            self._forward(body)
        finally:
            self.arbiter.leave(self.side)

    def _fix_model(self, body):
        from .services import router
        try:
            models = router("GET", "/models", timeout=3).get("data", [])
        except Exception:  # noqa: BLE001
            return body
        fixed = fix_model(body, models, _last_model["id"])
        body = fixed if fixed is not None else body
        try:
            _last_model["id"] = json.loads(body).get("model") or _last_model["id"]
        except (ValueError, AttributeError):
            pass
        return body

    def _forward(self, body, passive=False):
        if "upgrade" in self.headers.get("Connection", "").lower():
            return self._tunnel(body)
        port = self._upstream_port()
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = f"127.0.0.1:{port}"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        fwd = self.headers.get("X-Forwarded-For")
        headers["X-Forwarded-For"] = f"{fwd}, {self.client_address[0]}" if fwd else self.client_address[0]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=UPSTREAM_TIMEOUT)
        try:
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
            except (ConnectionRefusedError, ConnectionResetError, http.client.RemoteDisconnected, socket.timeout):
                if passive:
                    return self._passive_offline()
                return self._unavailable(_("El servicio no respondió."))
            self._relay(resp)
        finally:
            conn.close()

    def _passive_offline(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if self.side == "text" and path in ("/models", "/v1/models") and self.command == "GET":
            return self._reply(200, offline_models())
        self._unavailable(_("El servidor de texto está apagado: se prende con el primer pedido.") if self.side == "text"
                          else _("El generador de imágenes está apagado: se prende con el primer pedido."))

    def _relay(self, resp):
        try:
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in RESP_SKIP:
                    self.send_header(k, v)
            length = resp.getheader("Content-Length")
            no_body = self.command == "HEAD" or resp.status in (204, 304) or 100 <= resp.status < 200
            if no_body:
                self.send_header("Content-Length", length or "0")
                self.end_headers()
                return
            # sin largo (streaming): en trozos; un cliente HTTP/1.0 no los entiende, recibe todo y se cierra
            chunked = length is None and self.request_version != "HTTP/1.0"
            if length is not None:
                self.send_header("Content-Length", length)
            elif chunked:
                self.send_header("Transfer-Encoding", "chunked")
            if length is None and not chunked or resp.getheader("Connection", "").lower() == "close":
                self.close_connection = True
            self.end_headers()
            while True:
                data = resp.read1(65536)
                if not data:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data) if chunked else data)
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except OSError:
            # el cliente se fue (por ejemplo, cortó una respuesta en streaming): cerrar el upstream corta la generación
            self.close_connection = True

    def _tunnel(self, body):
        """WebSocket (u otro Upgrade): se pasan los bytes tal cual en los dos sentidos."""
        port = self._upstream_port()
        try:
            up = socket.create_connection(("127.0.0.1", port), timeout=10)
        except OSError:
            return self._unavailable(_("El servicio no respondió."))
        up.settimeout(None)
        head = [f"{self.command} {self.path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
        head += [f"{k}: {v}" for k, v in self.headers.items() if k.lower() != "host"]
        up.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + (body or b""))
        self.close_connection = True
        client = self.connection

        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=pipe, args=(up, client), daemon=True)
        t.start()
        pipe(client, up)
        t.join()
        up.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _handle


class _Server(DualStackServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return  # un cliente que corta o deja la conexión colgada: normal, no ensuciar el registro
        super().handle_error(request, client_address)


_servers = {}
_status = {"error": None}
_srv_lock = threading.Lock()


def _bind(side, deadline):
    handler = type(f"Gate_{side}", (GateHandler,), {"side": side})
    while True:
        try:
            return _Server(("0.0.0.0", C.PUBLIC_PORTS[side]), handler)
        except OSError as e:
            if time.time() > deadline:
                raise e
            time.sleep(0.5)


def start(wait=15):
    """Empieza a atender los puertos públicos. Si siguen ocupados (un servicio que todavía escucha ahí), reintenta en
    segundo plano y lo informa en status()."""
    def run(side):
        try:
            srv = _bind(side, time.time() + wait)
        except OSError as e:
            _status["error"] = _("No se pudo usar el puerto {port} ({err}). Reiniciá el servicio que lo ocupa.",
                                 port=C.PUBLIC_PORTS[side], err=e.strerror or e)
            print(f"a demanda: {_status['error']}", flush=True)
            return
        with _srv_lock:
            if not C.auto_settings().get("swap") or side in _servers:  # lo apagaron mientras esperaba
                srv.server_close()
                return
            _servers[side] = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    _status["error"] = None
    threads = [threading.Thread(target=run, args=(s,), daemon=True) for s in SIDES]
    for t in threads:
        t.start()
    return threads


def stop():
    with _srv_lock:
        servers = list(_servers.values())
        _servers.clear()
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def status():
    cfg = C.auto_settings()
    events = [{"t": e["t"], "msg": _(e["msg"], **e["kw"])} for e in EVENTS]
    return {**cfg, "listening": sorted(_servers), "error": _status["error"], **ARBITER.snapshot(),
            "idle": {s: round(time.time() - _last[s]) for s in SIDES}, "events": events}


# ---------- cambiar el modo ----------
def apply_swap(on, reload_models=()):
    """Prende o apaga el proxy y mueve los servicios encendidos al puerto que corresponde. Se llama después de
    guardar el ajuste nuevo; `reload_models` son los modelos de texto que estaban cargados (se vuelven a cargar)."""
    from . import img_models as IM
    from . import services, text_models as TM
    from .install import sync_units
    text_on = services.svc_state(C.SERVICE) == "active"
    img_on = services.svc_state(C.IMG_SERVICE) == "active"
    if not on:
        stop()
    sync_units()
    IM.write_img_env()
    if text_on:
        TM.write_preset()
        services.svc("restart")
    if img_on:
        services.svc("restart", C.IMG_SERVICE)
    if on:
        for t in start():
            t.join(timeout=20)
    if text_on and reload_models and services.wait_router():
        for mid in reload_models:
            try:
                services.router("POST", "/models/load", {"model": mid}, timeout=15)
            except Exception:  # noqa: BLE001
                pass
    return text_on, img_on


# ---------- descarga por inactividad ----------
def idle_actions(now, last, idle_s, text_loaded, img_on, inflight):
    """Qué apagar: "text" (descargar los modelos) y/o "img" (apagar el generador)."""
    if idle_s <= 0:
        return []
    out = []
    if text_loaded and not inflight["text"] and now - last["text"] >= idle_s:
        out.append("text")
    if img_on and not inflight["img"] and now - last["img"] >= idle_s:
        out.append("img")
    return out


def _busy_now():
    """Marca como activo lo que está trabajando aunque no haya pasado por el proxy (pedidos directos a los puertos,
    precarga de prompts, una carga desde el panel). Devuelve (modelos de texto cargados, generador prendido)."""
    from urllib.parse import quote
    from . import services
    loaded = []
    st = services.svc_states()
    if st["text"] == "active":
        for mid, m in services.router_models().items():
            if m["status"] == "loading":
                touch("text")
            elif m["status"] in ("loaded", "sleeping"):
                loaded.append(mid)
                try:
                    slots = services.router("GET", "/slots?model=" + quote(mid, safe=""), timeout=2)
                    if any(s.get("is_processing") for s in slots):
                        touch("text")
                except Exception:  # noqa: BLE001
                    pass
    img_on = st["img"] in ("active", "activating")
    if img_on:
        progress, perf = services.kobold_status()
        if progress is None or progress.get("steps") or (perf or {}).get("queue"):
            touch("img")  # cargando (todavía no responde) o generando
    return loaded, img_on


def idle_watch(interval=20):
    from . import services
    while True:
        time.sleep(interval)
        try:
            idle_min = int(C.auto_settings().get("idle_min") or 0)
            if idle_min <= 0:
                continue
            loaded, img_on = _busy_now()
            todo = idle_actions(time.time(), _last, idle_min * 60, loaded, img_on, ARBITER.snapshot()["inflight"])
            if "text" in todo:
                for mid in loaded:
                    try:
                        services.router("POST", "/models/unload", {"model": mid}, timeout=15)
                    except Exception:  # noqa: BLE001
                        pass
                event("{min} min sin uso: {models} descargado.", min=idle_min, models=", ".join(loaded))
            if "img" in todo:
                services.svc("stop", C.IMG_SERVICE)
                event("{min} min sin uso: generador de imágenes apagado.", min=idle_min)
        except Exception as e:  # noqa: BLE001
            print(f"inactividad: {e}", flush=True)


def start_background():
    """Al arrancar el panel."""
    threading.Thread(target=idle_watch, daemon=True).start()
    if C.auto_settings().get("swap"):
        start()
