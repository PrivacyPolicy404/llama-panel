import socket
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler

from llama_panel.netserver import DualStackServer, plain_ip


class EchoIP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = self.client_address[0].encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class NetServerTest(unittest.TestCase):
    def test_plain_ip(self):
        self.assertEqual(plain_ip("::ffff:127.0.0.1"), "127.0.0.1")
        self.assertEqual(plain_ip("192.168.1.5"), "192.168.1.5")
        self.assertEqual(plain_ip("fe80::1%eno1"), "fe80::1%eno1")
        self.assertEqual(plain_ip("basura"), "basura")

    @unittest.skipUnless(socket.has_ipv6, "sin IPv6")
    def test_serves_ipv4_and_ipv6(self):
        srv = DualStackServer(("0.0.0.0", 0), EchoIP)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            self.assertEqual(r.read(), b"127.0.0.1")  # sin el ::ffff: (auth.is_local depende de esto)
        if srv.address_family == socket.AF_INET6:
            with urllib.request.urlopen(f"http://[::1]:{port}/", timeout=5) as r:
                self.assertEqual(r.read(), b"::1")


if __name__ == "__main__":
    unittest.main()
