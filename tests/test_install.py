import unittest

from llama_panel import config as C
from llama_panel import install


class UnitsTest(unittest.TestCase):
    def test_model_servers_pin_gpu_memory_clock(self):
        units = install.units()
        for name in (f"{C.SERVICE}.service", f"{C.IMG_SERVICE}.service"):
            text = units[name]
            # se ignora si el ayudante no está instalado ("-"), y nunca pide contraseña (-n)
            self.assertIn(f"ExecStartPre=-/usr/bin/sudo -n {install.MCLK_HELPER} high", text)
            self.assertIn(f"ExecStopPost=-/usr/bin/sudo -n {install.MCLK_HELPER} auto", text)
            self.assertLess(text.index("ExecStartPre"), text.index("ExecStart="))

    def test_panel_does_not_touch_gpu_clock(self):
        self.assertNotIn("llama-gpu-mclk", install.units()[f"{C.PANEL_SERVICE}.service"])


if __name__ == "__main__":
    unittest.main()


class ClientGone(unittest.TestCase):
    def test_send_to_a_closed_connection_is_not_an_error(self):
        from llama_panel.server import Handler

        class Gone:
            def write(self, _):
                raise BrokenPipeError(32, "Broken pipe")

        h = Handler.__new__(Handler)
        h.wfile, h.request_version, h.requestline, h.command = Gone(), "HTTP/1.1", "POST /api/action HTTP/1.1", "POST"
        h.client_address, h.close_connection = ("127.0.0.1", 0), False
        h._send(200, {"ok": True})
        self.assertTrue(h.close_connection)
