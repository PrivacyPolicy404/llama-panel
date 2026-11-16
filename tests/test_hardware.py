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
