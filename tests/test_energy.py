import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from llama_panel import energy


def ts(day, h, m=0, s=0):
    return datetime(2026, 11, day, h, m, s).timestamp()


class Meter(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "energia.json"

    def test_integrates_watts_per_local_day_and_skips_gaps(self):
        m = energy.Meter(self.path)
        m.add(100, ts(21, 23, 59, 50))
        m.add(100, ts(21, 23, 59, 58))   # 8 s a 100 W
        m.add(100, ts(22, 0, 0, 6))      # 8 s más, ya del día siguiente
        m.add(100, ts(22, 1))            # hueco de una hora (panel apagado): no cuenta
        m.add(None, ts(22, 1, 0, 2))     # sin sensor: no suma
        self.assertAlmostEqual(m.days["2026-11-21"], 800 / 3600)
        self.assertAlmostEqual(m.days["2026-11-22"], 800 / 3600)
        s = m.summary(ts(22, 12))
        self.assertAlmostEqual(s["month_kwh"], 1600 / 3600 / 1000, places=4)
        self.assertEqual(len(s["month_days"]), 22)

    def test_survives_restarts_and_prices_from_paths_env(self):
        m = energy.Meter(self.path)
        m.add(3600, ts(22, 10))
        m.add(3600, ts(22, 10, 0, 10))  # 10 Wh; la primera suma ya guarda
        again = energy.Meter(self.path)
        self.assertAlmostEqual(again.days["2026-11-22"], 10, places=2)
        with mock.patch.dict(energy.C._LOCAL, {"LLAMA_KWH_PRICE": "100,0"}):
            s = again.summary(ts(22, 12))
        self.assertEqual((s["price"], s["day_cost"]), (100.0, 1.0))
        with mock.patch.dict(energy.C._LOCAL, {"LLAMA_KWH_PRICE": "caro"}):
            self.assertIsNone(again.summary(ts(22, 12))["day_cost"])


if __name__ == "__main__":
    unittest.main()
