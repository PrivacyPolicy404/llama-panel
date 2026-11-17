"""Dani por su cuenta: a quién le habla, timers, avisos del homelab sin repetirse y el resumen de la mañana."""
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from llama_panel import config as C
from llama_panel import proactive as P


class HubTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(P, "synth", lambda text: ["wav"])
        p.start()
        self.addCleanup(p.stop)

    def test_audio_only_for_the_last_used_client(self):
        hub = P.Hub()
        _, web = hub.subscribe("web")
        _, pc = hub.subscribe("pc")
        hub.touch("pc")  # el último que se usó
        hub.publish({"id": "1", "t": time.time(), "kind": "timer", "text": "el horno"})
        a, b = web.get_nowait(), pc.get_nowait()
        self.assertEqual((a["speak"], "audio" in a), (False, False))
        self.assertEqual((b["speak"], b["audio"]), (True, ["wav"]))
        hub.touch("web")
        hub.publish({"id": "2", "t": time.time(), "kind": "timer", "text": "otra"})
        self.assertTrue(web.get_nowait()["speak"])

    def test_waits_for_someone_to_connect(self):
        hub = P.Hub()
        self.assertEqual(hub.publish({"id": "1", "t": time.time(), "kind": "alerta", "text": "calor"}), 0)
        hub.publish({"id": "2", "t": time.time() - 13 * 3600, "kind": "alerta", "text": "viejo"})  # vencido
        _, q = hub.subscribe("pc")
        ev = q.get_nowait()
        self.assertEqual((ev["text"], ev["speak"]), ("calor", True))
        self.assertTrue(q.empty())
        self.assertEqual(len(hub.pending), 0)

    def test_broadcasts_state_events_without_audio(self):
        hub = P.Hub()
        _, web = hub.subscribe("web")
        _, pc = hub.subscribe("pc")
        event = {"type": "conversation", "conversation_id": "abc"}
        self.assertEqual(hub.broadcast(event), 2)
        self.assertEqual(web.get_nowait(), event)
        self.assertEqual(pc.get_nowait(), event)


class Timers(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(C, "BUDDY_DIR", Path(d.name))
        p.start()
        self.addCleanup(p.stop)

    def test_when(self):
        now = datetime(2026, 11, 17, 10, 30).timestamp()
        self.assertEqual(P.when(minutes=20, now=now), now + 1200)
        self.assertEqual(datetime.fromtimestamp(P.when(at="18:05", now=now)), datetime(2026, 11, 17, 18, 5))
        self.assertEqual(datetime.fromtimestamp(P.when(at="9:00", now=now)), datetime(2026, 11, 18, 9, 0))
        for bad in ({"minutes": 0}, {"minutes": 99999}, {}):
            with self.assertRaises(ValueError):
                P.when(now=now, **bad)

    def test_add_due_cancel(self):
        a = P.add_timer("sacá la pizza", minutes=1)
        P.add_timer("llamar a la abuela", minutes=60)
        self.assertEqual(P.due_timers(now=time.time()), [])
        self.assertEqual([x["id"] for x in P.due_timers(now=time.time() + 120)], [a["id"]])
        self.assertEqual(P.cancel_timer(""), 0)  # vacío no borra todo
        self.assertEqual(P.cancel_timer("ABUELA"), 1)
        self.assertEqual(P.timers(), [])


class AlertsTest(unittest.TestCase):
    def test_trips_once_rearms_after_normal_and_respects_cooldown(self):
        al = P.Alerts()
        hot, ok = {"temp": 88}, {"temp": 60}
        self.assertEqual(len(al.check(hot, {}, [], now=1000)), 1)
        self.assertEqual(al.check(hot, {}, [], now=1100), [])           # sigue caliente: no repite
        al.check(ok, {}, [], now=1200)                                  # vuelve a lo normal: se rearma
        self.assertEqual(al.check(hot, {}, [], now=1300), [])           # pero no antes de una hora
        self.assertEqual(len(al.check(hot, {}, [], now=1000 + P.ALERT_COOLDOWN)), 1)

    def test_ram_disk_and_services(self):
        al = P.Alerts()
        out = al.check({"ram_total": 62, "ram_used": 60}, {}, [("/", 3.0, 13.0)], now=1000)
        self.assertEqual(len(out), 2)
        self.assertIn("RAM", out[0])
        self.assertEqual(al.check({}, {C.SERVICE: "failed"}, [], now=1000), [])  # ya estaba caído al arrancar
        al.check({}, {C.IMG_SERVICE: "active"}, [], now=1000)
        self.assertIn("generador de imágenes", al.check({}, {C.IMG_SERVICE: "failed"}, [], now=1100)[0])


class Morning(unittest.TestCase):
    def test_once_a_day_within_six_hours(self):
        at = lambda h, m=0: datetime(2026, 11, 17, h, m).timestamp()  # noqa: E731
        self.assertFalse(P.morning_due("09:00", None, at(8, 59)))
        self.assertTrue(P.morning_due("09:00", None, at(9, 0)))
        self.assertTrue(P.morning_due("09:00", "2026-11-16", at(14, 59)))
        self.assertFalse(P.morning_due("09:00", "2026-11-17", at(10)))
        self.assertFalse(P.morning_due("09:00", None, at(15, 1)))
        self.assertFalse(P.morning_due("", None, at(10)))


if __name__ == "__main__":
    unittest.main()
