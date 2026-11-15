"""Funciones de dibujo del dashboard del modo IA (sin curses ni red)."""
import unittest

from llama_panel import kiosk


class Dibujo(unittest.TestCase):
    def test_bar(self):
        self.assertEqual(kiosk.bar(50, 100, 10), "█████░░░░░")
        self.assertEqual(kiosk.bar(None, 100, 4), "░░░░")
        self.assertEqual(kiosk.bar(200, 100, 4), "████")

    def test_graph_shape_and_alignment(self):
        rows = kiosk.graph([0, 50, 100], width=5, height=2, lo=0, hi=100)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(r) == 5 for r in rows))
        self.assertEqual(rows[0][-1], "█")          # 100 % llena la fila de arriba
        self.assertEqual(rows[0][:2], "  ")         # sin datos a la izquierda
        self.assertEqual(rows[1][-2], "█")          # 50 % llena la fila de abajo

    def test_box_width(self):
        b = kiosk.box("titulo", ["hola", "x" * 50], 20)
        self.assertTrue(all(len(line) == 20 for line in b))
        self.assertTrue(b[0].startswith("╭─ titulo"))

    def test_render_fits_and_survives_missing_data(self):
        state = {"svc": {"text": "active", "img": "inactive"}, "text": {"models": {"m": {"status": "loaded"}}},
                 "img": {"selected": "sd"}, "hw": {"vram_used": 7.2, "vram_total": 8.0, "gpu": 55, "temp": 60,
                                                    "ram_used": 30, "ram_total": 62.7, "gtt_used": 29.4, "load": 7.5}}
        lines = kiosk.render(state, None, None, None, width=120, height=50, now=0)
        self.assertTrue(all(len(t) <= 120 for t, _ in lines))
        self.assertTrue(any("m" in t for t, _ in lines))
        self.assertEqual(kiosk.render(None, None, None, None, width=100)[2][1], "bad")


if __name__ == "__main__":
    unittest.main()
