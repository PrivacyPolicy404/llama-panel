"""Selección de los sensores de CPU sin confundirlos con la GPU o el NVMe."""
import tempfile
import unittest
from pathlib import Path

from llama_panel.hardware import cpu_temperatures


class CpuTemperature(unittest.TestCase):
    def test_package_and_hottest_core_ignore_other_devices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, kind, values in (
                ("hwmon0", "amdgpu", [("edge", 90000)]),
                ("hwmon1", "coretemp", [("Package id 0", 65000), ("Core 0", 62000), ("Core 1", 68000)]),
            ):
                dev = root / name
                dev.mkdir()
                (dev / "name").write_text(kind)
                for i, (label, value) in enumerate(values, 1):
                    (dev / f"temp{i}_label").write_text(label)
                    (dev / f"temp{i}_input").write_text(str(value))
            self.assertEqual(cpu_temperatures(root), {"cpu_temp": 65, "cpu_core_temp_max": 68})

    def test_unavailable_is_not_zero_degrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cpu_temperatures(tmp), {"cpu_temp": None, "cpu_core_temp_max": None})


class Names(unittest.TestCase):
    def test_gpu_names(self):
        from llama_panel.hardware import short_gpu_name
        self.assertEqual(short_gpu_name("AMD Radeon RX 580 Series (RADV POLARIS10)"), "RX 580")
        self.assertEqual(short_gpu_name("NVIDIA GeForce RTX 3060"), "RTX 3060")
        self.assertEqual(short_gpu_name("NVIDIA GeForce RTX 2060 with Max-Q Design"), "RTX 2060 Max-Q")
        self.assertEqual(short_gpu_name("Intel(R) Arc(tm) A770 Graphics (DG2)"), "Arc A770")
        self.assertIsNone(short_gpu_name(""))

    def test_cpu_names(self):
        from llama_panel.hardware import short_cpu_name
        self.assertEqual(short_cpu_name("Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz"), "i7-10700")
        self.assertEqual(short_cpu_name("AMD Ryzen 7 5800X 8-Core Processor"), "Ryzen 7 5800X")
        self.assertEqual(short_cpu_name("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz"), "Xeon E5-2680 v4")


class Nvidia(unittest.TestCase):
    def test_parse_line_in_gib_and_without_unsupported_fields(self):
        from llama_panel.hardware import parse_nvidia
        s = parse_nvidia("5120, 12288, 37, 61, [N/A], 170.00, [N/A], 1830, 7500, 7501")
        self.assertEqual((s["vram_used"], s["vram_total"], s["gpu"], s["power_cap"]), (5.0, 12.0, 37, 170))
        self.assertNotIn("power", s)
        self.assertNotIn("fan_pct", s)
        self.assertEqual(parse_nvidia(""), {})
