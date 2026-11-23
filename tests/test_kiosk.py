"""Funciones de dibujo del dashboard del modo IA (sin curses ni red)."""
import unittest

import os

os.environ["LLAMA_PANEL_LANG"] = "es"  # los textos esperados están en castellano, sin depender de la máquina
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

    def test_prompt_speed_falls_back_to_previous_request(self):
        live = {"state": "prompt", "task_id": "7:201", "prompt_tps": None, "prompt_tps_prev": 114.3, "prompt_prev_task": "7:200"}
        text = "\n".join(kiosk.strip_marks(s) for s in kiosk.live_box(live, {}, [], 79))
        self.assertIn("prompt   media 114.3 tok/s (pedido anterior)", text)
        self.assertEqual(kiosk.prompt_sample(live), ("7:200", 114.3))
        # el mismo dato visto como actual y luego como anterior no se grafica dos veces
        self.assertEqual(kiosk.prompt_sample({"task_id": "7:200", "prompt_tps": 114.3}), ("7:200", 114.3))
        self.assertIsNone(kiosk.prompt_sample(None))

    def test_gpu_box_uses_detected_hardware(self):
        nv = {"gpu_name": "RTX 3060", "vram_used": 5.0, "vram_total": 12.0, "gpu": 40, "power": 120.0,
              "fan_pct": 45, "mclk": 7500, "sclk": 1830}
        text = "\n".join(kiosk.strip_marks(s) for s in kiosk.gpu_box(nv, [nv], 70))
        self.assertIn("gpu · RTX 3060", text)
        self.assertNotIn("GTT", text)  # NVIDIA no informa memoria fija
        self.assertIn("ventilador 45 %", text)
        self.assertIn("memoria 7500 MHz ·", text)
        amd = {"gpu_name": "RX 580", "gtt_used": 29.5, "gtt_total": 31.4, "mclk": 2250, "mclk_mode": "manual", "mclk_oc": 2250}
        text = "\n".join(kiosk.strip_marks(s) for s in kiosk.gpu_box(amd, [amd], 70))
        self.assertIn("29.50/31G", text)
        self.assertIn("(fija, OC 2250)", text)
        self.assertIn("─ gpu ─", "\n".join(kiosk.strip_marks(s) for s in kiosk.gpu_box({}, [], 70)))

    def test_footer_tells_how_to_leave_ai_mode(self):
        for w, h in ((160, 45), (80, 30)):
            state = {"svc": {"text": "active", "img": "inactive"}, "text": {"models": {}}, "img": {}, "hw": {}}
            lines = kiosk.render(state, None, None, None, width=w, height=h, now=0)
            text, style = lines[-1]
            self.assertIn("S dos veces", kiosk.strip_marks(text))
            self.assertIn("sudo llama-modo-ia off", kiosk.strip_marks(text))
            self.assertLessEqual(len(text), w)
            self.assertLessEqual(len(lines), h)
        lines = kiosk.render(None, None, None, {}, width=120, height=40, notice="¿Volver al escritorio? Apretá S de nuevo")
        self.assertEqual(lines[-1], (" ¿Volver al escritorio? Apretá S de nuevo", "warn"))

    def test_leave_ai_mode_falls_back_to_the_helper(self):
        from unittest import mock
        import subprocess
        with mock.patch.object(kiosk.urllib.request, "urlopen", side_effect=OSError("panel caído")), \
                mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.assertIsNone(kiosk.leave_ai_mode())
            run.assert_called_once()
            self.assertEqual(run.call_args[0][0], ["sudo", "-n", kiosk.AI_MODE_HELPER, "off"])
        with mock.patch.object(kiosk.urllib.request, "urlopen", side_effect=OSError("panel caído")), \
                mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "sin permiso")):
            self.assertEqual(kiosk.leave_ai_mode(), "sin permiso")


class Idioma(unittest.TestCase):
    def test_language_sources_in_order(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"LLAMA_PANEL_LANG": ""}), \
                mock.patch.object(kiosk.C, "CFG_DIR", Path(d)), mock.patch.dict(kiosk.C._LOCAL, {}, clear=True):
            (Path(d) / "ui-lang").write_text("en\n")
            self.assertEqual(kiosk.kiosk_lang(), "en")  # el de la interfaz web
            with mock.patch.dict(kiosk.C._LOCAL, {"LLAMA_PANEL_LANG": "es"}):
                self.assertEqual(kiosk.kiosk_lang(), "es")  # paths.env manda

    def test_english_dashboard(self):
        from unittest import mock
        state = {"svc": {"text": "active", "img": "inactive"}, "text": {"models": {}}, "img": {}, "hw": {}}
        with mock.patch.dict(os.environ, {"LLAMA_PANEL_LANG": "en"}):
            text = "\n".join(kiosk.strip_marks(t) for t, _ in kiosk.render(state, None, None, None, width=140, height=44, now=0))
        self.assertIn("AI PANEL · AI mode", text)
        self.assertIn("─ live ─", text)
        self.assertIn("Back to the desktop: S twice", text)
        self.assertNotIn("contexto", text)
        kiosk.set_lang("es")


if __name__ == "__main__":
    unittest.main()


class CajasNuevas(unittest.TestCase):
    """Discos, servicios, red y energía (la caja «servicios · red» y las filas de discos)."""

    def test_disks_from_mounts_without_pseudo_filesystems(self):
        mounts = ("/dev/nvme0n1p2 / ext4 rw 0 0\n/dev/nvme0n1p1 /boot/efi vfat rw 0 0\n"
                  "/dev/sdb1 /mnt/hdd1 ext4 rw 0 0\n/dev/loop3 /snap/x squashfs ro 0 0\n"
                  "tmpfs /run tmpfs rw 0 0\n/dev/sdb1 /otra/vez ext4 rw 0 0\n")
        self.assertEqual([(d, m) for d, m, _ in kiosk.list_disks(mounts)], [("nvme0n1p2", "/"), ("sdb1", "/mnt/hdd1")])
        stats = "   8      17 sdb1 100 0 2048 0 0 0 0 0 0 0 0\n"
        self.assertEqual(kiosk.read_sectors(stats), {"sdb1": 2048})

    def test_tcp_clients_ipv4_and_ipv6_mapped(self):
        head = "  sl  local_address rem_address   st\n"
        v4 = head + ("   0: 5A89A8C0:1F9A 0189A8C0:C8DF 01 0\n"      # 192.168.137.1 -> :8090, establecida
                     "   1: 0100007F:1F9A 0100007F:D000 01 0\n"      # local: no cuenta
                     "   2: 5A89A8C0:1F9A 0289A8C0:C8E0 0A 0\n")     # LISTEN: no cuenta
        v6 = head + "   0: 0000000000000000FFFF00005A89A8C0:1F9A 0000000000000000FFFF00000389A8C0:D1D1 01 0\n"
        got = kiosk.tcp_clients([v4, v6], {8090})
        self.assertEqual(got, {"192.168.137.1": {8090}, "192.168.137.3": {8090}})

    def test_wide_layout_shows_services_disks_and_energy(self):
        state = {"svc": {"text": "inactive", "img": "inactive"}, "text": {"models": {}}, "img": {}, "hw": {},
                 "audio": {"stt": "ggml-large-v3-turbo-q5_0.bin", "tts": "es_AR-daniela-high", "stt_ok": True, "tts_ok": True},
                 "kiosk": {"units": {"llama-panel": {"state": "active", "up": 7300}, "homeassistant": {"state": "failed", "up": None}},
                           "energy": {"day_kwh": 0.42, "month_kwh": 8.1, "month_days": [0.3, 0.42], "price": 100,
                                      "day_cost": 42.0, "month_cost": 810.0},
                           "gate": {"clients": [], "last": {"ip": "192.168.0.7", "side": "text", "path": "/v1/chat/completions",
                                                            "model": "qwen", "t": 0}},
                           "listening": ["text", "img"]}}
        cpu = {"cores": [5] * 16, "procs": [], "disks": [
            {"dev": "sdb1", "mount": "/mnt/hdd1", "kind": "HDD", "models": True, "used": 69e9, "total": 983e9, "read_mbs": 140.0}]}
        lines = kiosk.render(state, {}, {}, cpu, width=160, height=45, now=60, clients={"192.168.0.7": {8080}})
        self.assertTrue(all(kiosk.vlen(t) <= 160 for t, _ in lines))
        self.assertIn("Salir del modo IA", lines[-1][0])
        text = "\n".join(kiosk.strip_marks(t) for t, _ in lines)
        for want in ("servicios · red", "panel        2 h 01", "Home Assistant Error", "large-v3-turbo · daniela",
                     "HDD  /mnt/hdd1", "140.0 MB/s", "hoy 0.42 kWh · mes 8.1 kWh · $ 42.00 / $ 810",
                     "192.168.0.7 texto", "192.168.0.7 · texto · qwen · hace 1 min", "compuerta      :8080 :5001",
                     "koboldcpp"):
            self.assertIn(want, text)

    def test_units_from_systemctl_show(self):
        from llama_panel import api
        text = ("Id=llama-panel.service\nLoadState=loaded\nActiveState=active\nActiveEnterTimestampMonotonic=100000000\n\n"
                "Id=kobold-img.service\nLoadState=loaded\nActiveState=inactive\nActiveEnterTimestampMonotonic=0\n\n"
                "Id=homeassistant.service\nLoadState=not-found\nActiveState=inactive\n")
        self.assertEqual(api.parse_units(text, 160), {"llama-panel": {"state": "active", "up": 60},
                                                      "kobold-img": {"state": "inactive", "up": None}})

    def test_uptime(self):
        self.assertEqual([kiosk.uptime(s) for s in (5, 600, 3 * 3600 + 300, 2 * 86400 + 4 * 3600)],
                         ["5 s", "10 min", "3 h 05", "2 d 4 h"])
