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
