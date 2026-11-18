"""clients/buddy.py: escuchar su nombre (--wake). Vosk se reemplaza por un reconocedor de mentira con resultados fijos."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "clients"))
import buddy as dani  # noqa: E402  (clients/buddy.py, no llama_panel/buddy.py)

RATE = 1000  # 1 muestra = 1 ms; feed() recibe pedazos de 100 ms


class FakeRec:
    """Devuelve el resultado `results[n]` cuando recibió n pedazos (como Vosk al detectar el fin de una frase)."""

    def __init__(self, results, partial=""):
        self.results, self.n, self.partial = results, 0, partial

    def AcceptWaveform(self, _chunk):
        self.n += 1
        return self.n in self.results

    def Result(self):
        return json.dumps(self.results[self.n])

    def PartialResult(self):
        return json.dumps({"partial": self.partial})


def utt(*words, alts=()):
    """Un resultado de Vosk: palabras (palabra, inicio, fin) y, si hay, otras lecturas."""
    main = {"result": [{"word": w, "start": a, "end": b} for w, a, b in words]}
    return {"alternatives": [main] + [{"result": [{"word": w, "start": a, "end": b} for w, a, b in alt]}
                                      for alt in alts]}


def feed_all(det, seconds):
    """Pasa `seconds` de audio (muestra i = valor i % 30000, para saber qué tramo se recortó) y junta lo que devuelve."""
    out = []
    for k in range(int(seconds * 10)):
        chunk = b"".join(((k * 100 + i) % 30000).to_bytes(2, "little") for i in range(100))
        got = det.feed(chunk)
        if got is not None:
            out.append(got)
    return out


def span(pcm):
    """Qué segundos del audio original son esos bytes."""
    first = int.from_bytes(pcm[:2], "little")
    return first / RATE, (first + len(pcm) // 2) / RATE


class Sound(unittest.TestCase):
    def test_spellings_that_sound_the_same(self):
        for a, b in (("Dani", "danny"), ("Ximena", "jimena"), ("Vicky", "bicky"), ("Gerardo", "jerardo")):
            self.assertEqual(dani.sound(a), dani.sound(b), (a, b))
        self.assertNotEqual(dani.sound("guerra"), dani.sound("jerra"))

    def test_short_names_must_match_exactly(self):
        self.assertTrue(dani.sounds_like(dani.sound("danny"), "dani"))
        self.assertFalse(dani.sounds_like(dani.sound("dame"), "dani"))
        self.assertTrue(dani.sounds_like(dani.sound("yarvis"), dani.sound("Jarvis")))  # largo: una letra de perdón

    def test_name_split_in_words(self):
        words = [{"word": w} for w in ("hola", "to", "bías", "qué", "tal")]
        self.assertEqual(dani.find_name(words, {dani.sound("Tobías")}), (1, 2))


class Detector(unittest.TestCase):
    def det(self, results, name="Dani", partial=""):
        return dani.WakeDetector(None, RATE, [name], rec=FakeRec(results, partial))

    def test_sends_only_the_sentence_with_the_name(self):
        # «la tarde está linda» (0.5-1.5) · pausa · «Dani qué hora es» (2.0-3.0) · pausa · «me pasás la sal» (3.6-4.4)
        r = utt(("la", .5, .7), ("tarde", .7, 1.0), ("linda", 1.1, 1.5), ("dani", 2.0, 2.3), ("qué", 2.4, 2.6),
                ("hora", 2.6, 2.8), ("es", 2.8, 3.0), ("me", 3.6, 3.7), ("pasás", 3.7, 4.0), ("la", 4.0, 4.1),
                ("sal", 4.1, 4.4))
        out = feed_all(self.det({55: r}), 6)
        self.assertEqual(len(out), 1)
        a, b = span(out[0])
        self.assertAlmostEqual(a, 2.0 - dani.WakeDetector.PRE, places=2)
        self.assertAlmostEqual(b, 3.0 + dani.WakeDetector.POST, places=2)

    def test_greeting_before_the_name_goes_too(self):
        r = utt(("che", 1.0, 1.2), ("dani", 1.3, 1.6), ("cómo", 1.7, 1.9), ("andás", 1.9, 2.3))
        a, _ = span(feed_all(self.det({40: r}), 5)[0])
        self.assertAlmostEqual(a, 1.0 - dani.WakeDetector.PRE, places=2)

    def test_name_alone_waits_for_the_question(self):
        out = feed_all(self.det({20: utt(("dani", 1.0, 1.4)), 50: utt(("va", 2.6, 2.8), ("a", 2.8, 2.9),
                                                                      ("llover", 2.9, 3.4))}), 6)
        self.assertEqual(out[0], "wait")
        a, b = span(out[1])
        self.assertAlmostEqual(a, 1.0 - dani.WakeDetector.PRE, places=2)
        self.assertAlmostEqual(b, 3.4 + dani.WakeDetector.POST, places=2)

    def test_gives_up_if_nothing_follows(self):
        out = feed_all(self.det({20: utt(("dani", 1.0, 1.4))}), 12)
        self.assertEqual(out, ["wait", "timeout"])

    def test_keeps_waiting_while_still_talking(self):
        self.assertEqual(feed_all(self.det({20: utt(("dani", 1.0, 1.4))}, partial="y entonces"), 12), ["wait"])

    def test_no_name_no_send(self):
        r = utt(("dame", 1.0, 1.3), ("un", 1.3, 1.4), ("vaso", 1.4, 1.8))
        self.assertEqual(feed_all(self.det({30: r}), 4), [])

    def test_longer_name_is_not_the_wake_word(self):
        # la lectura principal dice «daniel»; una alternativa dice «dani»: es Daniel
        r = utt(("daniel", 1.0, 1.5), ("vino", 1.6, 1.9), alts=[[("dani", 1.0, 1.4), ("el", 1.4, 1.5)]])
        self.assertEqual(feed_all(self.det({30: r}), 4), [])

    def test_name_found_in_another_reading(self):
        r = utt(("tani", 1.0, 1.4), ("qué", 1.5, 1.7), ("hora", 1.7, 2.0), alts=[[("dani", 1.0, 1.4), ("qué", 1.5, 1.7),
                                                                                     ("hora", 1.7, 2.0)]])
        self.assertEqual(len(feed_all(self.det({30: r}), 4)), 1)

    def test_two_requests_in_one_go_skip_whats_between(self):
        r = utt(("dani", 1.0, 1.3), ("el", 1.4, 1.5), ("clima", 1.5, 1.9), ("dónde", 2.6, 2.9), ("están", 2.9, 3.2),
                ("dani", 4.0, 4.3), ("un", 4.4, 4.5), ("timer", 4.5, 4.9))
        out = feed_all(self.det({60: r}), 7)
        self.assertEqual(len(out), 1)
        gap = int(0.3 * RATE) * 2
        first = (1.9 + dani.WakeDetector.POST - (1.0 - dani.WakeDetector.PRE))
        self.assertAlmostEqual(len(out[0]) / 2 / RATE, first + 0.3 + (4.9 - 4.0 + dani.WakeDetector.PRE + dani.WakeDetector.POST),
                               places=2)
        self.assertEqual(out[0][int(first * RATE) * 2:int(first * RATE) * 2 + gap], b"\0" * gap)

    def test_silent_while_muted(self):
        d = self.det({30: utt(("dani", 1.0, 1.3), ("hola", 1.4, 1.8))})
        d.muted = True
        self.assertEqual(feed_all(d, 4), [])

    def test_any_name(self):
        r = utt(("hola", 1.0, 1.3), ("to", 1.4, 1.6), ("bías", 1.6, 2.0), ("cómo", 2.1, 2.4), ("andás", 2.4, 2.8))
        self.assertEqual(len(feed_all(self.det({40: r}, name="Tobías"), 5)), 1)


if __name__ == "__main__":
    unittest.main()


class WakeSetting(unittest.TestCase):
    """A qué nombres responde según el ajuste del panel y --wake / --no-wake en la PC."""

    def words(self, wake=None, no_wake=False, panel_wake=False, name="Lola"):
        from types import SimpleNamespace
        return dani.wake_words(SimpleNamespace(wake=wake, no_wake=no_wake), {"name": name, "wake": panel_wake})

    def test_follows_the_panel(self):
        self.assertEqual(self.words(panel_wake=True), ["Lola"])
        self.assertEqual(self.words(panel_wake=False), [])

    def test_pc_flags_win(self):
        self.assertEqual(self.words(wake="", panel_wake=False), ["Lola"])
        self.assertEqual(self.words(wake="Kira, Lolita"), ["Kira", "Lolita"])
        self.assertEqual(self.words(no_wake=True, panel_wake=True), [])

    def test_env_old_names(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"DANI_KEY": "f9"}, clear=False):
            self.assertEqual(dani.env("KEY", "ctrl_r"), "f9")
        with mock.patch.dict(os.environ, {"DANI_KEY": "f9", "BUDDY_KEY": "f8"}, clear=False):
            self.assertEqual(dani.env("KEY", "ctrl_r"), "f8")


class MicToggle(unittest.TestCase):
    """Mic: el ajuste del panel prende y apaga el micrófono abierto sin reiniciar el programa."""

    def setUp(self):
        from unittest import mock
        self.opened = []

        class FakeWake:
            def __init__(s, _args, _session, _beep, words):
                s.words, s.closed, s.rate = words, False, 16000
                self.opened.append(s)

            def set_words(s, words):
                s.words = words

            def close(s):
                s.closed = True

            def start(s):
                pass

            def stop(s):
                return b"wake", 1.0

        rec = mock.Mock(rate=16000)
        rec.stop.return_value = (b"tecla", 1.0)
        for p in (mock.patch.object(dani, "WakeListener", FakeWake), mock.patch.object(dani, "Recorder", return_value=rec),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)
        self.mic = dani.Mic(mock.Mock(mic=None), mock.Mock(), lambda f=880: None)

    def test_on_rename_off(self):
        self.mic.set_wake(["Lola"])
        self.assertEqual(len(self.opened), 1)
        self.mic.set_wake(["Lola"])  # la misma respuesta del panel no reabre nada
        self.mic.set_wake(["Kira"])
        self.assertEqual((len(self.opened), self.opened[0].words), (1, ["Kira"]))
        self.mic.start()
        self.assertEqual(self.mic.stop()[0], b"wake")  # con el micrófono abierto la tecla graba de ahí
        self.mic.set_wake([])
        self.assertTrue(self.opened[0].closed)
        self.mic.start()
        self.assertEqual(self.mic.stop()[0], b"tecla")

    def test_not_closed_while_key_is_down(self):
        self.mic.set_wake(["Lola"])
        self.mic.start()
        self.mic.set_wake([])
        self.assertFalse(self.opened[0].closed)
        self.mic.stop()
        self.mic.set_wake([])
        self.assertTrue(self.opened[0].closed)

    def test_without_vosk(self):
        from unittest import mock
        with mock.patch.object(dani, "WakeListener", side_effect=ImportError):
            self.mic.set_wake(["Lola"])
        self.mic.start()
        self.assertEqual(self.mic.stop()[0], b"tecla")
