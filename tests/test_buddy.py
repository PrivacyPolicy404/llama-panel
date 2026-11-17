"""Compañero: voz (oraciones, limpieza, ventana de Whisper), herramientas, historial y el bucle del agente."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from llama_panel import buddy
from llama_panel import buddy_tools as BT
from llama_panel import config as C
from llama_panel import gate
from llama_panel import voice


class Voice(unittest.TestCase):
    def test_split_sentences_while_streaming(self):
        ready, rest = voice.split_sentences("Hola. ¿Cómo andás vos hoy? Yo bien, che")
        self.assertEqual(ready, ["Hola. ¿Cómo andás vos hoy?"])  # "Hola." sola es muy corta: va con la siguiente
        self.assertEqual(rest, "Yo bien, che")
        self.assertEqual(voice.split_sentences(rest, final=True), (["Yo bien, che"], ""))
        self.assertEqual(voice.split_sentences("Sin punto todavía"), ([], "Sin punto todavía"))

    def test_first_piece_goes_out_early(self):
        self.assertEqual(voice.split_sentences("¡Hola! Acá puedo mirar", first=True), (["¡Hola!"], "Acá puedo mirar"))
        long = "Acá puedo mirar cómo está la máquina y la hora, y guardarme datos tuyos"
        self.assertEqual(voice.split_sentences(long, first=True),
                         (["Acá puedo mirar cómo está la máquina y la hora,"], "y guardarme datos tuyos"))
        self.assertEqual(voice.split_sentences("Corto, sin más", first=True), ([], "Corto, sin más"))

    def test_speakable_drops_markdown_and_emoji(self):
        self.assertEqual(voice.speakable("**Listo** 😄 mirá [esto](http://x) y `ls -la`"), "Listo mirá esto y ls -la")
        self.assertEqual(voice.speakable("```\ncodigo\n```"), "")

    def test_audio_ctx_follows_the_length(self):
        self.assertEqual(voice.audio_ctx(4), 448)    # 1.5 x 200 cuadros + 128, en múltiplos de 64
        self.assertEqual(voice.audio_ctx(2.8), 384)  # con 256 repetía la frase
        self.assertEqual(voice.audio_ctx(0.5), 384)
        self.assertEqual(voice.audio_ctx(29), 0)     # casi 30 s: la ventana entera

    def test_known_hallucinations_are_dropped(self):
        self.assertEqual(voice.clean_transcript(" Subtítulos realizados por la comunidad de Amara.org "), "")
        self.assertEqual(voice.clean_transcript(" prendé  la luz "), "prendé la luz")


class Tools(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(C, "BUDDY_DIR", Path(d.name))
        p.start()
        self.addCleanup(p.stop)

    def test_remember_and_forget(self):
        self.assertEqual(BT.run("recordar", '{"dato": "Le gusta el mate amargo."}')[0], "Anotado.")
        self.assertEqual(BT.run("recordar", '{"dato": "le gusta el mate amargo."}')[0], "Ya lo tenía anotado.")
        BT.run("recordar", '{"dato": "Tiene luces Hue."}')
        self.assertEqual(BT.read_memory(), "- Le gusta el mate amargo.\n- Tiene luces Hue.")
        BT.run("olvidar", '{"texto": "mate"}')
        self.assertEqual(BT.read_memory(), "- Tiene luces Hue.")

    def test_bad_calls_do_not_raise_and_are_logged(self):
        self.assertFalse(BT.run("rm_rf", "{}")[1])
        self.assertFalse(BT.run("recordar", "no es json")[1])
        self.assertFalse(BT.run("recordar", '{"otro": 1}')[1])
        self.assertFalse(BT.run("recordar", '{"dato": "x"}', enabled=["fecha_y_hora"])[1])  # apagada
        lines = (C.BUDDY_DIR / "herramientas.log").read_text().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(json.loads(lines[0])["tool"], "rm_rf")


class HomeAssistantTools(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for p in (mock.patch("llama_panel.homeassistant.call", lambda *a: self.calls.append(a)),
                  mock.patch("llama_panel.homeassistant.configured", lambda: True)):
            p.start()
            self.addCleanup(p.stop)

    def test_only_allowed_domains(self):
        out, ok = BT.run("casa_controlar", '{"id": "script.borrar_todo", "accion": "prender"}')
        self.assertIn("No tengo permiso", out)
        self.assertEqual(self.calls, [])

    def test_light_with_brightness_and_spanish_color(self):
        BT.run("casa_controlar", '{"id": "light.living", "accion": "prender", "brillo_pct": 140, "color": "Rojo"}')
        BT.run("casa_controlar", '{"id": "light.living", "accion": "prender", "color": "blanco cálido"}')
        self.assertEqual(self.calls[0], ("light", "turn_on", {"entity_id": "light.living", "brightness_pct": 100,
                                                              "color_name": "red"}))
        self.assertEqual(self.calls[1][2]["color_temp_kelvin"], 2700)

    def test_not_offered_without_token(self):
        with mock.patch("llama_panel.homeassistant.configured", lambda: False):
            names = [t["function"]["name"] for t in BT.schemas()]
            self.assertNotIn("casa_controlar", names)
            self.assertIn("no está disponible", BT.run("casa_controlar", '{"id": "light.x", "accion": "apagar"}')[0])


class History(unittest.TestCase):
    def test_trims_the_older_half_at_once(self):
        h = []
        for i in range(13):
            h += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
        buddy.trim_history(h, max_turns=12)
        self.assertEqual(h[0]["content"], "u7")  # quedan los últimos 6: el prompt cambia una vez, no en cada mensaje
        before = list(h)
        buddy.trim_history(h, max_turns=12)
        self.assertEqual(h, before)


# ---------- el bucle, contra un modelo falso ----------
def sse(chunks):
    return b"".join(b"data: " + json.dumps({"choices": [c]}).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


class FakeModel(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.requests.append(req)
        if req["messages"][-1]["role"] == "user":  # primero pide la hora, en dos pedazos
            body = sse([{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "fecha_y_hora",
                                                                                         "arguments": "{"}}]}},
                        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}},
                        {"delta": {}, "finish_reason": "tool_calls"}])
        else:
            body = sse([{"delta": {"content": "Son las "}}, {"delta": {"content": "diez. ¡Buen día!"}}])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AgentLoop(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.srv = gate._Server(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        port = self.srv.server_address[1]
        for p in (mock.patch.object(C, "BUDDY_DIR", Path(d.name)),
                  mock.patch.object(buddy, "_endpoint_and_model", lambda cfg: (port, "falso")),
                  mock.patch.dict(BT.TOOLS["fecha_y_hora"], {"fn": lambda: "las diez"})):
            p.start()
            self.addCleanup(p.stop)
        buddy.reset()
        FakeModel.requests.clear()

    def test_tool_round_then_answer(self):
        events = list(buddy.chat({"text": "¿qué hora es?", "speak": False}))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "tool")
        self.assertEqual(events[0]["result"], "las diez")
        self.assertEqual("".join(e["delta"] for e in events if e["type"] == "text"), "Son las diez. ¡Buen día!")
        self.assertEqual(kinds[-1], "done")
        # segundo pedido: con la llamada y su resultado en el historial, sin razonamiento y con las herramientas
        second = FakeModel.requests[1]
        self.assertEqual([m["role"] for m in second["messages"]], ["system", "user", "assistant", "tool"])
        self.assertEqual(second["messages"][2]["tool_calls"][0]["function"], {"name": "fecha_y_hora", "arguments": "{}"})
        self.assertFalse(second["chat_template_kwargs"]["enable_thinking"])
        self.assertIn("fecha_y_hora", [t["function"]["name"] for t in second["tools"]])
        self.assertFalse(buddy._busy.locked())

    def test_memory_is_fixed_during_a_chat(self):
        BT.write_memory("- dato viejo")
        list(buddy.chat({"text": "hola", "speak": False}))
        BT.write_memory("- dato nuevo")
        list(buddy.chat({"text": "otra", "speak": False}))
        systems = {r["messages"][0]["content"] for r in FakeModel.requests}
        self.assertEqual(len(systems), 1)  # mismo prompt de sistema: el servidor reutiliza lo procesado
        self.assertIn("dato viejo", systems.pop())


if __name__ == "__main__":
    unittest.main()
