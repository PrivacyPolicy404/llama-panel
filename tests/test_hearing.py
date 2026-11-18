"""El oído del compañero (hearing.py): vocabulario automático y correcciones aprendidas del modelo."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llama_panel import buddy
from llama_panel import buddy_tools as BT
from llama_panel import config as C
from llama_panel import hearing as H
from llama_panel import voice


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(C, "BUDDY_DIR", Path(d.name))
        p.start()
        self.addCleanup(p.stop)
        H._ha_cache.update(t=0, names=[])
        self.dir = Path(d.name)


class Sound(unittest.TestCase):
    def test_same_sound_different_spelling(self):
        self.assertEqual(H.sound_key("Chás Comús"), H.sound_key("Chascomús"))
        self.assertEqual(H.sound_key("cordova"), H.sound_key("Córdoba"))
        self.assertGreater(H.similarity("Villagesea", "Villa Gesell"), H.SIMILAR)  # lo que escribió small
        self.assertGreater(H.similarity("mano de plata", "Mar del Plata"), H.SIMILAR)
        self.assertLess(H.similarity("la capital", "Buenos Aires"), H.SIMILAR)

    def test_proper_names(self):
        self.assertEqual(H.proper_names("Mar del Plata, Buenos Aires"), ["Mar del Plata", "Buenos Aires"])
        self.assertEqual(H.proper_names("¿cómo anda la RX 580 con Flash-Next?"), ["RX 580", "Flash-Next"])
        self.assertEqual(H.proper_names("partidos de fútbol hoy"), [])


class Learn(Temp):
    def test_learns_after_two_and_applies(self):
        args = '{"ubicacion": "Mar del Plata, Buenos Aires"}'
        self.assertIsNone(H.learn("decime el clima en mano de plata", "clima", args))
        self.assertEqual(H.apply("clima en mano de plata"), "clima en mano de plata")  # una vez no alcanza
        self.assertEqual(H.learn("¿llueve en mano de plata?", "clima", args)["to"], "Mar del Plata")
        self.assertEqual(H.apply("¿y mañana en Mano de Plata?"), "¿y mañana en Mar del Plata?")
        self.assertIn("Mar del Plata", H.vocabulary({"name": "Dani"}))

    def test_glued_words_and_spelling(self):
        for _ in range(2):
            H.learn("¿va a llover en chás comús?", "clima", '{"ubicacion": "Chascomús"}')
        self.assertEqual(H.apply("chás comús mañana"), "Chascomús mañana")

    def test_does_not_learn_inflections_unrelated_or_already_right(self):
        for _ in range(3):
            H.learn("buscame el partido de futbol", "buscar_web", '{"consulta": "partidos de fútbol hoy"}')
            H.learn("¿cómo está el clima en la capital?", "clima", '{"ubicacion": "Buenos Aires"}')
            H.learn("clima en Mar del Plata", "clima", '{"ubicacion": "Mar del Plata"}')
            H.learn("contame algo", "estado_homelab", "{}")
        self.assertEqual(H.corrections(), ([], []))

    def test_forgotten_is_not_learned_again(self):
        for _ in range(2):
            H.learn("clima en mano de plata", "clima", '{"ubicacion": "Mar del Plata"}')
        self.assertTrue(H.forget("mano de plata"))
        for _ in range(3):
            H.learn("clima en mano de plata", "clima", '{"ubicacion": "Mar del Plata"}')
        self.assertEqual(H.corrections(), ([], []))
        self.assertEqual(H.apply("mano de plata"), "mano de plata")


class Vocabulary(Temp):
    def test_sources(self):
        BT.write_memory("- El usuario tiene una perra que se llama Luna.\n- Vive cerca de Tandil.")
        (self.dir / "herramientas.log").write_text("\n".join(json.dumps(e) for e in [
            {"tool": "clima", "ok": True, "args": '{"ubicacion": "Villa Gesell, Buenos Aires"}'},
            {"tool": "clima", "ok": True, "args": '{"ubicacion": "Mano de Plata"}',
             "result": "No encontré la ubicación «Mano de Plata»."},
            {"tool": "clima", "ok": False, "args": '{"ubicacion": "Necochea"}', "result": "Falló: timed out"},
            {"tool": "buscar_web", "ok": True, "args": '{"consulta": "Brave"}'}]))
        with mock.patch.object(H, "_ha_names", return_value=["Luz del quincho"]):
            terms = H.vocabulary({"name": "Dani"}, place="Mar del Plata, Buenos Aires")
        self.assertEqual(terms[:2], ["Dani", "Mar del Plata, Buenos Aires"])
        for t in ("Villa Gesell", "Luz del quincho", "Luna", "Tandil", "Necochea"):
            self.assertIn(t, terms)
        self.assertNotIn("Mano de Plata", terms)  # no existe (Necochea sí: falló la red, no el nombre)
        self.assertNotIn("El", terms)             # comienzo de oración, no nombre propio

    def test_measures_are_not_names(self):
        BT.write_memory("- Tiene una RX 580 de 8 GB y 64 GB de RAM.")
        terms = H.vocabulary({"name": "Dani"})
        self.assertIn("RX 580", terms)
        self.assertNotIn("8 GB", terms)
        self.assertNotIn("64 GB", terms)

    def test_prompt_is_capped(self):
        BT.write_memory("\n".join(f"- Conoce a Persona{i} Apellido{i}" for i in range(200)))
        p = H.prompt({"name": "Dani"})
        self.assertLessEqual(len(p), H.MAX_PROMPT + 1)

    def test_prompt_reaches_whisper(self):
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            if cmd[0] == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"\0" * 44 + b"\0" * 32000)
            return mock.Mock(returncode=0, stdout=" hola Dani ", stderr="")
        with mock.patch.object(voice.subprocess, "run", fake_run), \
                mock.patch.object(voice.AM, "selected", return_value={"stt": "m.bin"}):
            text, _ = voice.transcribe(b"x", prompt="Dani, Luna.", keep_wav=self.dir / "a.wav", nice=True)
        whisper = runs[-1]
        self.assertEqual(text, "hola Dani")
        self.assertEqual(whisper[whisper.index("--prompt") + 1], "Dani, Luna.")
        self.assertEqual(whisper[:3], ["nice", "-n", "19"])
        self.assertTrue((self.dir / "a.wav").is_file())


class Audios(Temp):
    def test_keeps_the_last_ones(self):
        with mock.patch.object(H, "compare_async") as comp:
            for i in range(H.KEEP_AUDIOS + 5):
                wav = H.audios_dir() / f"{1000 + i}.000.wav"
                wav.parent.mkdir(parents=True, exist_ok=True)
                wav.write_bytes(b"RIFF")
                H.record(wav, "crudo", "texto", "Dani.")
        self.assertEqual(len(H.audios()), H.KEEP_AUDIOS)
        self.assertEqual(H.audios()[0]["path"].name, "1005.000.wav")
        self.assertTrue(comp.called)  # cada COMPARE_EVERY nuevos compara solo
        self.assertEqual(H.delete_audios(), H.KEEP_AUDIOS)
        self.assertEqual(H.audios(), [])


class VoiceTurn(Temp):
    """De punta a punta con un modelo falso: una charla por voz donde el modelo entiende el lugar."""
    def test_learns_from_a_voice_turn(self):
        from tests.test_buddy import FakeModel, sse, _replying
        from llama_panel import gate
        import threading
        srv = gate._Server(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        calls = [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
            "name": "clima", "arguments": '{"ubicacion": "Mar del Plata"}'}}]}}]
        answer = [{"delta": {"content": "Nublado."}}]

        def do_post(handler):
            req = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
            body = sse(answer if req["messages"][-1]["role"] == "tool" else calls)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        def fake_transcribe(audio, prompt=None, keep_wav=None, **kw):
            if keep_wav:
                Path(keep_wav).parent.mkdir(parents=True, exist_ok=True)
                Path(keep_wav).write_bytes(b"RIFF")
            return "clima en mano de plata", 2.0

        heard = []
        with mock.patch.object(buddy, "_endpoint_and_model", lambda cfg: (port, "falso")), \
                mock.patch.object(FakeModel, "do_POST", do_post), \
                mock.patch.dict(BT.TOOLS["clima"], {"fn": lambda ubicacion=None: "nublado"}), \
                mock.patch.object(voice, "transcribe", fake_transcribe):
            buddy.reset()
            for _ in range(3):
                events = list(buddy.chat({"audio": "eA==", "speak": False}))
                heard.append(next(e for e in events if e["type"] == "heard"))
        self.assertEqual(heard[0]["text"], "clima en mano de plata")
        self.assertEqual(heard[2]["text"], "clima en Mar del Plata")   # a la tercera ya llega corregido
        self.assertEqual(heard[2]["raw"], "clima en mano de plata")
        self.assertEqual(len(H.audios()), 3)


if __name__ == "__main__":
    unittest.main()
