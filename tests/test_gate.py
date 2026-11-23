"""Encendido a demanda: qué pedidos prenden servicios, el árbitro entre texto e imágenes y el reenvío del proxy."""
import http.client
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from unittest import mock

from llama_panel import config as C
from llama_panel import gate
from llama_panel import img_models as IM
from llama_panel import install


class NeedsService(unittest.TestCase):
    def test_text(self):
        self.assertTrue(gate.needs_service("text", "POST", "/v1/chat/completions"))
        self.assertTrue(gate.needs_service("text", "GET", "/"))  # abrir la interfaz web de llama-server
        # /models/sse no termina nunca (la interfaz web de llama-server abierta): no puede contar como pedido en curso
        for path in ("/v1/models", "/models", "/health", "/props", "/slots/", "/models/sse", "/v1/stream"):
            self.assertFalse(gate.needs_service("text", "GET", path), path)
        self.assertFalse(gate.needs_service("text", "POST", "/models/unload"))
        self.assertFalse(gate.needs_service("text", "POST", "/v1/chat/completions/control"))
        self.assertTrue(gate.needs_service("text", "GET", "/v1/realtime"))
        self.assertFalse(gate.needs_service("text", "OPTIONS", "/v1/chat/completions"))

    def test_image(self):
        self.assertTrue(gate.needs_service("img", "POST", "/sdapi/v1/txt2img"))
        self.assertTrue(gate.needs_service("img", "POST", "/v1/images/generations"))
        self.assertTrue(gate.needs_service("img", "GET", "/sdui"))
        # una pestaña de StableUI abierta consulta el progreso: no tiene que prender nada
        self.assertFalse(gate.needs_service("img", "GET", "/sdapi/v1/progress"))
        self.assertFalse(gate.needs_service("img", "POST", "/sdapi/v1/interrupt"))


class ArbiterTest(unittest.TestCase):
    def test_other_side_waits_until_requests_end(self):
        calls = []
        a = gate.Arbiter(calls.append)
        a.enter("text")
        a.enter("text")  # dos pedidos de texto a la vez: no se esperan entre sí
        got_in = threading.Event()
        t = threading.Thread(target=lambda: (a.enter("img"), got_in.set()))
        t.start()
        time.sleep(0.1)
        self.assertFalse(got_in.is_set())
        self.assertEqual(a.snapshot()["waiting"]["img"], 1)
        a.leave("text")
        time.sleep(0.05)
        self.assertFalse(got_in.is_set())
        a.leave("text")
        self.assertTrue(got_in.wait(1))
        t.join()
        self.assertEqual(calls, ["text", "text", "img"])
        self.assertEqual(a.snapshot()["inflight"], {"text": 0, "img": 1})

    def test_timeout_and_failed_start_release(self):
        a = gate.Arbiter(lambda side: None)
        a.enter("img")
        with self.assertRaises(gate.Busy):
            a.enter("text", timeout=0.05)
        self.assertEqual(a.snapshot()["waiting"]["text"], 0)
        a.leave("img")

        def boom(side):
            raise gate.ServiceError("no arranca")
        b = gate.Arbiter(boom)
        with self.assertRaises(gate.ServiceError):
            b.enter("text")
        self.assertEqual(b.snapshot()["inflight"]["text"], 0)


class FixModel(unittest.TestCase):
    def test_nothing_loaded_uses_the_last_model(self):
        nothing_loaded = [dict(m, status={"value": "unloaded"}) for m in self.MODELS]
        self.assertEqual(json.loads(gate.fix_model(b'{"model": "x"}', nothing_loaded, last="otro"))["model"], "otro")

    MODELS = [{"id": "qwen", "aliases": ["q"], "status": {"value": "loaded"}},
              {"id": "otro", "aliases": [], "status": {"value": "unloaded"}}]

    def test_unknown_or_missing_model_uses_the_loaded_one(self):
        for body in ({"model": "gpt-4o", "messages": []}, {"messages": []}):
            fixed = gate.fix_model(json.dumps(body).encode(), self.MODELS)
            self.assertEqual(json.loads(fixed)["model"], "qwen")

    def test_known_names_and_ambiguous_cases_are_left_alone(self):
        for name in ("qwen", "q", "otro"):
            self.assertIsNone(gate.fix_model(json.dumps({"model": name}).encode(), self.MODELS))
        nothing_loaded = [dict(m, status={"value": "unloaded"}) for m in self.MODELS]
        self.assertIsNone(gate.fix_model(b'{"model": "x"}', nothing_loaded))
        self.assertIsNone(gate.fix_model(b'{"model": "x"}', nothing_loaded, last="borrado"))
        self.assertIsNone(gate.fix_model(b"no es json", self.MODELS))
        self.assertIsNone(gate.fix_model(b"[1]", self.MODELS))


class IdleActions(unittest.TestCase):
    def test_after_the_idle_time_only(self):
        last = {"text": 1000, "img": 1500}
        none = {"text": 0, "img": 0}
        self.assertEqual(gate.idle_actions(1000 + 600, last, 600, ["m"], True, none), ["text"])
        self.assertEqual(gate.idle_actions(2200, last, 600, ["m"], True, none), ["text", "img"])
        self.assertEqual(gate.idle_actions(2200, last, 600, [], False, none), [])  # nada prendido
        self.assertEqual(gate.idle_actions(2200, last, 0, ["m"], True, none), [])  # apagado
        self.assertEqual(gate.idle_actions(2200, last, 600, ["m"], True, {"text": 1, "img": 0}), ["img"])


class PortsFollowTheSetting(unittest.TestCase):
    def test_units_and_kobold_args(self):
        m = {"path": "/m/sd.safetensors", "comps": []}
        with mock.patch.object(C, "auto_settings", return_value={"swap": True, "idle_min": 0}):
            self.assertIn("--host 127.0.0.1 --port 18080", install.units()[f"{C.SERVICE}.service"])
            args = IM.build_kobold_args(m)
            self.assertEqual(args[args.index("--host") + 1:args.index("--port") + 2], ["127.0.0.1", "--port", "15001"])
        with mock.patch.object(C, "auto_settings", return_value={"swap": False, "idle_min": 0}):
            self.assertIn("--host 0.0.0.0 --port 8080", install.units()[f"{C.SERVICE}.service"])
            self.assertIn("5001", IM.build_kobold_args(m))


# ---------- proxy de punta a punta, con un backend falso ----------
class Backend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(3):
                data = f"data: {i}\n\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
                time.sleep(0.1)
            self.wfile.write(b"0\r\n\r\n")
            return
        if self.headers.get("Upgrade") == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.write(self.connection.recv(100).upper())  # eco en mayúsculas
            return
        self._json({"path": self.path, "host": self.headers["Host"], "xff": self.headers.get("X-Forwarded-For")})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self._json({"body": json.loads(body)})

    def _json(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(handler):
    srv = gate._Server(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.backend = serve(Backend)
        self.ensured = []
        port = self.backend.server_address[1]
        arbiter = gate.Arbiter(self.ensured.append)
        handler = type("H", (gate.GateHandler,), {"side": "img", "arbiter": arbiter,
                                                  "_upstream_port": lambda h: port})
        self.gate = serve(handler)
        self.port = self.gate.server_address[1]

    def tearDown(self):
        for s in (self.gate, self.backend):
            s.shutdown()
            s.server_close()

    def conn(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(c.close)
        return c

    def test_get_post_and_keep_alive(self):
        c = self.conn()
        c.request("GET", "/sdapi/v1/progress?x=1")  # pasivo: no prende nada
        r = c.getresponse()
        data = json.loads(r.read())
        self.assertEqual(data["path"], "/sdapi/v1/progress?x=1")
        self.assertTrue(data["host"].startswith("127.0.0.1:"))
        self.assertEqual(data["xff"], "127.0.0.1")
        self.assertEqual(self.ensured, [])
        c.request("POST", "/sdapi/v1/txt2img", body=json.dumps({"prompt": "gato"}),
                  headers={"Content-Type": "application/json"})  # misma conexión
        self.assertEqual(json.loads(c.getresponse().read()), {"body": {"prompt": "gato"}})
        self.assertEqual(self.ensured, ["img"])

    def test_streaming_arrives_in_pieces(self):
        c = self.conn()
        c.request("GET", "/stream")
        r = c.getresponse()
        self.assertEqual(r.getheader("Transfer-Encoding"), "chunked")
        t0 = time.time()
        first = r.read1(100)
        self.assertEqual(first, b"data: 0\n\n")
        self.assertLess(time.time() - t0, 0.15)  # no esperó a que terminara la respuesta entera
        self.assertEqual(first + r.read(), b"data: 0\n\ndata: 1\n\ndata: 2\n\n")

    def test_websocket_tunnel(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(1000)
        self.assertIn(b"101", head.split(b"\r\n")[0])
        s.sendall(b"hola")
        rest = head.split(b"\r\n\r\n", 1)[1]
        while len(rest) < 4:
            rest += s.recv(100)
        self.assertEqual(rest, b"HOLA")
        s.close()

    def test_backend_down(self):
        self.backend.shutdown()
        self.backend.server_close()
        c = self.conn()
        c.request("POST", "/sdapi/v1/txt2img", body="{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual(r.status, 503)
        self.assertIn("message", json.loads(r.read())["error"])


class OfflineModels(unittest.TestCase):
    def test_text_models_list_without_the_server(self):
        handler = type("H", (gate.GateHandler,), {"side": "text", "_upstream_port": lambda h: 9})  # puerto cerrado
        srv = serve(handler)
        try:
            with mock.patch("llama_panel.text_models.scan_models", return_value=[{"id": "qwen"}]):
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                self.addCleanup(c.close)
                c.request("GET", "/v1/models")
                r = c.getresponse()
                self.assertEqual(r.status, 200)
                self.assertEqual([m["id"] for m in json.loads(r.read())["data"]], ["qwen"])
                c.request("GET", "/health")
                self.assertEqual(c.getresponse().status, 503)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()


class UnloadGuard(unittest.TestCase):
    """Mientras el panel descarga un modelo, las consultas que lo nombran no salen: lo volvían a cargar."""

    def setUp(self):
        from llama_panel import services
        self.s = services
        self.sent = []
        p = mock.patch.object(services, "http_json", side_effect=lambda port, m, path, body, t: self.sent.append(path) or [])
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(services._UNLOADING.clear)

    def test_queries_naming_the_model_wait_for_the_unload(self):
        self.s.router("POST", "/models/unload", {"model": "M"})
        with self.assertRaises(RuntimeError):
            self.s.router("GET", "/slots?model=M")
        self.s.router("GET", "/slots?model=OTRO")  # otro modelo, sin problema
        self.s.router("GET", "/models")
        self.assertEqual(self.sent, ["/models/unload", "/slots?model=OTRO", "/models"])

    def test_guard_ends_when_the_router_shows_it_unloaded(self):
        self.s.router("POST", "/models/unload", {"model": "M"})
        with mock.patch.object(self.s, "http_json", return_value={"data": [{"id": "M", "status": {"value": "unloaded"}}]}):
            self.s.router_models()
        self.s.router("GET", "/slots?model=M")
        self.assertEqual(self.sent[-1], "/slots?model=M")

