"""Funciones de dibujo del dashboard del modo IA (sin curses ni red)."""
import unittest

from llama_panel import kiosk


class Dibujo(unittest.TestCase):
    def test_bar(self):
        V = kiosk.strip_marks
        self.assertEqual(V(kiosk.bar(50, 100, 10)), "█████░░░░░")
        self.assertEqual(V(kiosk.bar(None, 100, 4)), "░░░░")
        self.assertEqual(V(kiosk.bar(200, 100, 4)), "████")
        self.assertTrue(kiosk.bar(50, 100, 10).startswith(kiosk.GREEN))
        self.assertTrue(kiosk.bar(95, 100, 10).startswith(kiosk.RED))

    def test_visible_length_helpers(self):
        t = kiosk.RED + "hola" + kiosk.RESET + " che"
        self.assertEqual(kiosk.vlen(t), 8)
        self.assertEqual(kiosk.strip_marks(kiosk.vcut(t, 6)), "hola c")
        self.assertEqual(kiosk.vlen(kiosk.vpad(t, 12)), 12)

    def test_graph_colors_by_level(self):
        rows = kiosk.graph([10, 95], width=2, height=1, lo=0, hi=100, color="level")
        self.assertIn(kiosk.RED, rows[0])
        self.assertEqual(kiosk.vlen(rows[0]), 2)

    def test_graph_shape_and_alignment(self):
        rows = kiosk.graph([0, 50, 100], width=5, height=2, lo=0, hi=100)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(r) == 5 for r in rows))
        self.assertEqual(rows[0][-1], "█")          # 100 % llena la fila de arriba
        self.assertEqual(rows[0][:2], "  ")         # sin datos a la izquierda
        self.assertEqual(rows[1][-2], "█")          # 50 % llena la fila de abajo

    def test_box_width(self):
        b = kiosk.box("titulo", ["hola", "x" * 50], 20)
        self.assertTrue(all(kiosk.vlen(line) == 20 for line in b))
        self.assertTrue(kiosk.strip_marks(b[0]).startswith("╭─ titulo"))

    def test_render_fits_and_survives_missing_data(self):
        state = {"svc": {"text": "active", "img": "inactive"}, "text": {"models": {"m": {"status": "loaded"}}},
                 "img": {"selected": "sd"}, "hw": {"vram_used": 7.2, "vram_total": 8.0, "gpu": 55, "temp": 60,
                                                    "ram_used": 30, "ram_total": 62.7, "gtt_used": 29.4, "load": 7.5}}
        lines = kiosk.render(state, None, None, None, width=120, height=50, now=0)
        self.assertTrue(all(kiosk.vlen(t) <= 120 for t, _ in lines))
        self.assertTrue(any("m" in t for t, _ in lines))
        self.assertEqual(kiosk.render(None, None, None, None, width=100)[2][1], "bad")

    def test_live_bars_always_visible_without_layout_jumps(self):
        cases = [{}, {"state": "prompt", "prompt_progress": 0.3, "ctx_size": 65536, "ctx_used": 18000},
                 {"state": "gen", "prompt_progress": 1, "ctx_size": 65536, "ctx_used": 63000},
                 {"state": "idle", "prompt_progress": 1, "ctx_size": 65536, "ctx_used": 64000}]
        boxes = [[kiosk.strip_marks(s) for s in kiosk.live_box(c, {}, [], 79)] for c in cases]
        self.assertEqual(len({len(b) for b in boxes}), 1)
        for b in boxes:
            self.assertTrue("░" in b[8] or "█" in b[8])
            self.assertIn("contexto", b[9])
            self.assertTrue(all(len(s) == 79 for s in b))

    def test_live_shows_request_averages_and_recent_generation_separately(self):
        text = "\n".join(kiosk.strip_marks(s) for s in kiosk.live_box(
            {"state": "gen", "prompt_tps": 108.8, "gen_avg_tps": 16.9, "tps": 18.2}, {}, [], 79))
        self.assertIn("media 108.8 tok/s", text)
        self.assertIn("media 16.9 · 3 s 18.2 tok/s", text)

    def test_wide_layout_keeps_footer_and_process_box_on_shorter_screen(self):
        state = {"svc": {"text": "active", "img": "inactive"}, "text": {"models": {}}, "hw": {}}
        cpu = {"cores": [50] * 16, "procs": [{"name": "llama-server", "pid": n, "cpu": 50,
                                             "threads": 8, "rss": 1024} for n in range(6)]}
        for width, height in ((160, 44), (140, 44), (120, 36)):
            lines = kiosk.render(state, {}, {}, cpu, width=width, height=height)
            self.assertTrue(all(kiosk.vlen(text) <= width for text, _ in lines))
            self.assertIn("Salir del modo IA", lines[-1][0])
            self.assertTrue(any("procesos" in text for text, _ in lines))

    def test_cached_prompt_eta_uses_remaining_new_tokens(self):
        live = {"state": "prompt", "prompt_tokens": 4416, "prompt_cached": 58836,
                "prompt_total": 65300, "prompt_progress": 0.97, "prompt_tps": 86.25}
        text = "\n".join(kiosk.strip_marks(s) for s in kiosk.live_box(live, {}, [], 79))
        self.assertIn("58836 tokens", text)
        self.assertIn("restante ~24s", text)  # 2048 nuevos pendientes, sin volver a contar el prefijo


if __name__ == "__main__":
    unittest.main()
