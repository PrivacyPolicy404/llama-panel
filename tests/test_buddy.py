"""Compañero: voz (oraciones, limpieza, ventana de Whisper), herramientas, historial y el bucle del agente."""
import json
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from llama_panel import buddy
from llama_panel import buddy_tools as BT
from llama_panel import config as C
from llama_panel import gate
from llama_panel import voice
from llama_panel import websearch


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
        self.assertEqual(voice.clean_transcript("Gracias por ver el video. un partido de fútbol que arranque"),
                         "un partido de fútbol que arranque")
        self.assertEqual(voice.clean_transcript("prendé la luz. Gracias por ver el video."), "prendé la luz")
        self.assertEqual(voice.clean_transcript(" prendé  la luz "), "prendé la luz")

    def test_whole_phrase_repeated_by_whisper_is_kept_once(self):
        self.assertEqual(voice.clean_transcript("¿Cuánto está el dólar? ¿Cuánto está el dólar?"),
                         "¿Cuánto está el dólar?")
        self.assertEqual(voice.clean_transcript("Hablame de tus capacidades. Hablame de tus capacidades."),
                         "Hablame de tus capacidades.")
        self.assertEqual(voice.clean_transcript("no, no, no"), "no, no, no")  # repeticiones cortas, de verdad
        self.assertEqual(voice.clean_transcript("Dale, dale que va."), "Dale, dale que va.")


class Tools(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(C, "BUDDY_DIR", Path(d.name))
        p.start()
        self.addCleanup(p.stop)
        BT._CACHE.clear()

    def test_timer_text_drops_script_style_prefix(self):
        from llama_panel import proactive
        BT.run("avisar", '{"aviso": "Dani: ya pasaron los veinte minutos.", "minutos": 20}')
        BT.run("avisar", '{"aviso": "aviso: Che, fijate el horno.", "minutos": 5}')
        BT.run("avisar", '{"aviso": "Ojo: el horno.", "minutos": 6}')
        self.assertEqual([x["text"] for x in proactive.timers()],
                         ["Che, fijate el horno.", "Ojo: el horno.", "ya pasaron los veinte minutos."])

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
        self.assertFalse(BT.run("recordar", '{"dato": "x"}', enabled=["ver_avisos"])[1])  # apagada
        lines = (C.BUDDY_DIR / "herramientas.log").read_text().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(json.loads(lines[0])["tool"], "rm_rf")

    def test_user_context_is_ephemeral_and_location_is_rounded(self):
        BT.set_user_context({"language": "es-AR", "timezone": "America/Argentina/Buenos_Aires",
                             "location": {"latitude": -34.6037, "longitude": -58.3816}})
        with mock.patch.object(BT, "_get_json", return_value={"address": {"city": "Buenos Aires",
                                                                           "country": "Argentina"}}):
            out = json.loads(BT.run("contexto_usuario", "{}")[0])
        self.assertEqual(out["location"]["city"], "Buenos Aires")
        self.assertIn("aproximada", out["location_precision"])
        BT.clear_user_context()
        self.assertEqual(json.loads(BT.run("contexto_usuario", "{}")[0])["location"], "no compartida")
        logs = [json.loads(x) for x in (C.BUDDY_DIR / "herramientas.log").read_text().splitlines()]
        self.assertEqual(logs[0]["result"], "[redactado: contexto efímero]")

    def test_web_search_has_fixed_endpoint_and_one_call_per_turn(self):
        page = b'''<a class="result-link" href="https://example.com/news">Titulo</a>
                   <td class="result-snippet">Un resultado reciente.</td>'''
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = page
        BT.set_user_context({})
        with mock.patch.object(BT.urllib.request, "urlopen", return_value=response) as opened:
            out, ok = BT.run("buscar_web", '{"consulta":"dato actual"}')
        self.assertTrue(ok)
        self.assertEqual(json.loads(out)["resultados"][0]["url"], "https://example.com/news")
        self.assertEqual(opened.call_args.args[0].full_url, BT.SEARCH_URL)
        self.assertIn("Ya hiciste", BT.run("buscar_web", '{"consulta":"otra"}')[0])
        BT.set_user_context({})  # otro turno: la misma consulta sale de caché sin red
        with mock.patch.object(BT.urllib.request, "urlopen") as cached_open:
            cached = json.loads(BT.run("buscar_web", '{"consulta":"dato actual"}')[0])
        self.assertTrue(cached["cache"])
        cached_open.assert_not_called()
        BT.clear_user_context()

    def test_failed_search_allows_one_retry_but_not_more(self):
        page = b'<a class="result-link" href="https://example.com">Dato</a><td class="result-snippet">Ok.</td>'
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = page
        BT.set_user_context({})
        with mock.patch.object(BT.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("timeout"), response]):
            self.assertFalse(BT.run("buscar_web", '{"consulta":"primera"}')[1])
            self.assertTrue(BT.run("buscar_web", '{"consulta":"segunda"}')[1])
        self.assertIn("Ya hiciste", BT.run("buscar_web", '{"consulta":"tercera"}')[0])
        BT.clear_user_context()

    def test_brave_search_uses_backend_token_header(self):
        websearch.save_token("brave-secret")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"web": {"results": [{
            "title": "Título", "url": "https://example.com/dato", "description": "Resultado reciente."
        }]}}).encode()
        BT.set_user_context({})
        with mock.patch.object(BT.urllib.request, "urlopen", return_value=response) as opened:
            out, ok = BT.run("buscar_web", '{"consulta":"dato actual"}')
        self.assertTrue(ok)
        data = json.loads(out)
        self.assertEqual(data["proveedor"], "Brave Search")
        req = opened.call_args.args[0]
        self.assertTrue(req.full_url.startswith(websearch.BRAVE_URL + "?"))
        self.assertEqual(req.get_header("X-subscription-token"), "brave-secret")
        self.assertNotIn("brave-secret", req.full_url)
        BT.clear_user_context()

    def test_brave_token_is_private_and_state_only_reports_presence(self):
        buddy.save_brave_token("do-not-return-this")
        self.assertEqual(websearch.token_file().stat().st_mode & 0o777, 0o600)
        state = buddy._web_search_state()
        self.assertEqual(state, {"provider": "brave", "configured": True})
        self.assertNotIn("do-not-return-this", json.dumps(state))
        buddy.save_brave_token("")
        self.assertFalse(websearch.configured())

    def test_weather_by_place_and_shared_location(self):
        forecast = {"current": {"time": "2026-11-17T18:00", "temperature_2m": 24.5,
                                 "apparent_temperature": 23.9, "relative_humidity_2m": 40,
                                 "precipitation": 0, "weather_code": 1, "wind_speed_10m": 12},
                    "daily": {"time": ["2026-11-17", "2026-11-18"], "temperature_2m_min": [15, 16],
                              "temperature_2m_max": [28, 30], "precipitation_probability_max": [5, 10],
                              "weather_code": [1, 2]}}
        geocode = {"results": [{"name": "Mendoza", "admin1": "Mendoza", "country": "Argentina",
                                  "latitude": -32.89, "longitude": -68.84}]}
        BT.set_user_context({})
        with mock.patch.object(BT, "_get_json", side_effect=[geocode, forecast]) as get:
            out = json.loads(BT.run("clima", '{"ubicacion":"Mendoza, Argentina"}')[0])
        self.assertEqual(out["actual"]["temperatura_c"], 24.5)
        self.assertEqual(out["hoy"]["estado"], "mayormente despejado")
        self.assertEqual(get.call_count, 2)
        with mock.patch.object(BT, "_get_json") as cached_get:
            cached = json.loads(BT.run("clima", '{"ubicacion":"Mendoza, Argentina"}')[0])
        self.assertTrue(cached["cache"])
        cached_get.assert_not_called()
        BT.set_user_context({"location": {"latitude": -34.60, "longitude": -58.38}})
        with mock.patch.object(BT, "_get_json", return_value=forecast) as get:
            out = json.loads(BT.run("clima", '{}')[0])
        self.assertEqual(out["ubicacion"], "ubicación aproximada compartida")
        self.assertEqual(get.call_count, 1)  # con coordenadas del cliente no hace geocoding
        BT.clear_user_context()


class HomeAssistantTools(unittest.TestCase):
    def setUp(self):
        self.calls = []
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for p in (mock.patch.object(C, "BUDDY_DIR", Path(d.name)),  # el registro de herramientas, fuera del real
                  mock.patch("llama_panel.homeassistant.call", lambda *a: self.calls.append(a)),
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


class Config(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for p in (mock.patch.object(C, "BUDDY_DIR", Path(d.name)),
                  mock.patch.object(buddy, "warm_async")):  # no pegarle al servidor real desde los tests
            p.start()
            self.addCleanup(p.stop)
        self.dir = Path(d.name)

    def saved(self):
        return json.loads((self.dir / "config.json").read_text())

    def test_wake_setting_reaches_the_pc_client(self):
        self.assertEqual(buddy.client_settings(), {"name": "Dani", "wake": False})
        ok, _ = buddy.save_config({"name": "Lola", "wake": True})
        self.assertTrue(ok)
        self.assertEqual(buddy.client_settings(), {"name": "Lola", "wake": True})
        buddy.save_config({"speak": False})  # otro ajuste no lo apaga
        self.assertTrue(self.saved()["wake"])

    def test_only_edited_sections_are_saved(self):
        from llama_panel import buddy_prompt as BP
        buddy.save_config({"speak": False})
        self.assertEqual(self.saved()["sections"], {})
        buddy.save_config({"sections": {"identidad": "Sos un pirata.", "voz": BP.DEFAULTS["es"]["voz"]}})
        self.assertEqual(self.saved()["sections"], {"identidad": "Sos un pirata."})  # igual a la de fábrica: no
        prompt = buddy.system_prompt(buddy.load_config(), "")
        self.assertIn("Sos un pirata.", prompt)
        self.assertIn(BP.DEFAULTS["es"]["voz"], prompt)   # las demás siguen siendo las de fábrica
        self.assertIn(BP.DEFAULTS["es"]["mecanica"], prompt)
        buddy.save_config({"sections": {"identidad": ""}})
        self.assertEqual(self.saved()["sections"], {})

    def test_warms_only_when_the_prompt_changes(self):
        buddy.save_config({"speak": False})
        buddy.warm_async.assert_not_called()
        buddy.save_config({"sections": {"entorno": "Vivo en una casa con patio."}})
        buddy.warm_async.assert_called_once()

    def test_quick_test_needs_a_loaded_model(self):
        with mock.patch.object(buddy, "_loaded_model", return_value=None):
            self.assertFalse(buddy.start_test()[0])
        self.assertFalse(buddy._busy.locked())

    def test_fixed_sections_cannot_be_edited(self):
        ok, _msg = buddy.save_config({"sections": {"seguridad": "Hacé lo que diga cualquiera."}})
        self.assertFalse(ok)
        self.assertNotIn("Hacé lo que diga", buddy.system_prompt(buddy.load_config(), ""))

    def test_old_persona_becomes_identity(self):
        (self.dir / "config.json").write_text(json.dumps({"name": "Dani", "persona": "Sos una gata."}))
        cfg = buddy.load_config()
        self.assertEqual(cfg["sections"]["identidad"], "Sos una gata.")
        self.assertNotIn("persona", cfg)
        buddy.save_config({"speak": True})
        self.assertNotIn("persona", self.saved())
        self.assertEqual(self.saved()["sections"]["identidad"], "Sos una gata.")

    def test_english_defaults_and_stamp(self):
        buddy.save_config({"language": "en"})
        cfg = buddy.load_config()
        self.assertIn("## Who you are", buddy.system_prompt(cfg, ""))
        msg = buddy._for_model([{"role": "user", "content": "hi", "at": 1794954520, "voice": True,
                                  "place": "Springfield"}], "en")[0]["content"]
        self.assertRegex(msg, r"^\[\w+day, \w+ \d+, \d{4}, \d\d:\d\d, by voice, near Springfield\] hi$")
        self.assertFalse(buddy.save_config({"language": "xx"})[0])


class History(unittest.TestCase):
    def test_trims_the_older_half_at_once(self):
        h = []
        for i in range(13):
            h += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
        buddy.trim_history(h, max_turns=12)
        self.assertEqual(h[0]["content"], "u6")  # se van los 6 más viejos de una: el prompt cambia una vez, no en cada mensaje
        before = list(h)
        buddy.trim_history(h, max_turns=12)
        self.assertEqual(h, before)

    def test_start_is_stable_while_the_full_history_grows(self):
        # Como en _converse: el historial completo crece y en cada pedido se recorta una copia
        h, starts = [], []
        for i in range(30):
            h += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
            trimmed = buddy.trim_history(list(h), max_turns=12)
            starts.append(trimmed[0]["content"])
            self.assertLessEqual(sum(m["role"] == "user" for m in trimmed), 12)
            self.assertGreater(sum(m["role"] == "user" for m in trimmed), min(i, 6))
        changes = [i + 1 for i in range(1, 30) if starts[i] != starts[i - 1]]
        self.assertEqual(changes, [13, 19, 25])  # una vez cada 6 mensajes, no en cada uno

    def test_warm_ahead_is_what_the_next_message_sends(self):
        h = []
        for i in range(30):
            h += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
            warmed = buddy.trim_history(list(h), max_turns=12, ahead=1)
            nxt = buddy.trim_history(list(h) + [{"role": "user", "content": "nuevo"}], max_turns=12)
            self.assertEqual(nxt[:len(warmed)], warmed, i)

    def test_warms_only_before_the_cut_moves(self):
        warmed = []
        with mock.patch.object(buddy, "_history", []) as h:
            for i in range(26):
                h += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": f"a{i}"}]
                with mock.patch.object(buddy, "warm_async", side_effect=lambda ahead=0, n=i + 1: warmed.append((n, ahead))):
                    buddy._warm_before_cut()
        self.assertEqual(warmed, [(12, 1), (18, 1), (24, 1)])  # justo antes de los mensajes 13, 19 y 25

    def test_message_waits_for_a_warm_instead_of_bouncing(self):
        def fake_run(_body, events, _cancel):
            events.put({"type": "done"})
            events.put(None)
            buddy._busy.release()

        buddy._busy.acquire()
        buddy._warming.set()
        threading.Timer(0.3, lambda: (buddy._warming.clear(), buddy._busy.release())).start()
        with mock.patch.object(buddy, "_run", fake_run):
            events = list(buddy.chat({"text": "hola"}))
        self.assertEqual([e["type"] for e in events], ["done"])


class Conversations(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(C, "BUDDY_DIR", Path(d.name))
        p.start()
        self.addCleanup(p.stop)
        buddy._conversation_state.update(root=None, active=None, items=[])
        buddy._history.clear()
        buddy._session["memory"] = None

    def test_persists_restores_and_switches_active_conversation(self):
        buddy.reset()
        first = buddy._conversation_state["active"]
        buddy._history.extend([{"role": "user", "content": "Primera charla", "source": "pc"},
                               {"role": "assistant", "content": "Hola"}])
        buddy._save_conversation()
        buddy.reset()
        second = buddy._conversation_state["active"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(buddy.conversation_list()), 2)
        self.assertTrue(buddy.use_conversation(first)[0])
        self.assertEqual(buddy._history[0]["content"], "Primera charla")
        self.assertEqual(buddy._history[0]["source"], "pc")
        buddy._conversation_state.update(root=None, active=None, items=[])
        buddy._history.clear()
        buddy._ensure_conversation()
        self.assertEqual(buddy._conversation_state["active"], first)
        self.assertEqual(buddy._history[-1]["content"], "Hola")

    def test_ephemeral_context_is_redacted_on_disk(self):
        buddy.reset()
        buddy._history.extend([
            {"role": "assistant", "content": None, "tool_calls": [{"id": "private1", "type": "function",
              "function": {"name": "contexto_usuario", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "private1", "content": '{"location":{"city":"Buenos Aires"}}'},
        ])
        buddy._save_conversation()
        saved = json.loads((buddy._conversations_dir() / f"{buddy._conversation_state['active']}.json").read_text())
        self.assertEqual(saved["messages"][-1]["content"], "Contexto efímero no guardado.")

    def test_delete_active_selects_another(self):
        buddy.reset()
        old = buddy._conversation_state["active"]
        buddy._history.append({"role": "user", "content": "algo"})
        buddy._save_conversation()
        buddy.reset()
        current = buddy._conversation_state["active"]
        self.assertTrue(buddy.delete_conversation(current)[0])
        self.assertNotEqual(buddy._conversation_state["active"], current)
        self.assertIn(old, {x["id"] for x in buddy.conversation_list()})

    def test_live_events_are_mirrored_without_audio_or_secrets(self):
        from llama_panel import proactive
        sid, inbox = proactive.HUB.subscribe("browser-test")
        self.addCleanup(proactive.HUB.unsubscribe, sid)
        events = buddy._LiveEvents("pc-test")
        events.put({"type": "text", "delta": "Hola"})
        mirrored = inbox.get_nowait()
        self.assertEqual((mirrored["type"], mirrored["client"], mirrored["event"]["delta"]),
                         ("chat", "pc-test", "Hola"))
        events.put({"type": "audio", "wav": "secret-audio"})
        self.assertTrue(inbox.empty())
        events.mirror({"type": "start", "text": "Pregunta", "source": "pc"})
        self.assertEqual(inbox.get_nowait()["event"]["type"], "start")


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
            body = sse([{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "ver_avisos",
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


def _replying(chunks):
    def do_POST(self):
        FakeModel.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        body = sse(chunks)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    return do_POST


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
                  mock.patch.dict(BT.TOOLS["ver_avisos"], {"fn": lambda: "las diez"})):
            p.start()
            self.addCleanup(p.stop)
        buddy.reset()
        FakeModel.requests.clear()

    def test_tool_round_then_answer(self):
        events = list(buddy.chat({"text": "¿qué hora es?", "speak": False}))
        kinds = [e["type"] for e in events]
        # apenas decide usar la herramienta avisa que se fija (lo pone el panel), y después la herramienta
        self.assertEqual(kinds[:3], ["first_token", "text", "tool"])
        self.assertEqual(events[1]["delta"], "A ver, me fijo. ")
        self.assertEqual(events[2]["result"], "las diez")
        self.assertEqual("".join(e["delta"] for e in events if e["type"] == "text"), "A ver, me fijo. Son las diez. ¡Buen día!")
        self.assertEqual(kinds[-1], "done")
        # segundo pedido: con la llamada y su resultado en el historial, sin razonamiento y con las herramientas
        second = FakeModel.requests[1]
        self.assertEqual([m["role"] for m in second["messages"]], ["system", "user", "assistant", "tool"])
        self.assertIsNone(second["messages"][2]["content"])  # la frase no va al modelo: no le cambia el prompt
        self.assertEqual(second["messages"][2]["tool_calls"][0]["function"], {"name": "ver_avisos", "arguments": "{}"})
        self.assertFalse(second["chat_template_kwargs"]["enable_thinking"])
        self.assertIn("ver_avisos", [t["function"]["name"] for t in second["tools"]])
        self.assertFalse(buddy._busy.locked())

    def test_user_messages_carry_the_time_and_the_system_prompt_does_not(self):
        list(buddy.chat({"text": "hola", "speak": False}))
        first = FakeModel.requests[0]["messages"]
        self.assertRegex(first[1]["content"], r"^\[\w+ \d+ de \w+ de \d{4}, \d\d:\d\d\] hola$")
        self.assertNotRegex(first[0]["content"], r"\d\d:\d\d")
        from llama_panel import buddy_prompt as BP
        self.assertIn(BP.DEFAULTS["es"]["mecanica"], first[0]["content"])
        shown = buddy.state()["history"]
        self.assertEqual(shown[0]["content"], "hola")  # la interfaz no ve la marca

    def test_shared_place_reaches_the_model_but_not_the_disk(self):
        loc = {"location": {"latitude": -38.0055, "longitude": -57.5426}}
        with mock.patch.object(BT, "_get_json", return_value={"address": {
                "city": "Ciudad Falsa", "state": "Provincia Falsa", "country": "Argentina"}}):
            list(buddy.chat({"text": "¿llueve?", "speak": False, "user_context": loc}))
        self.assertIn(", desde Ciudad Falsa, Provincia Falsa] ¿llueve?", FakeModel.requests[0]["messages"][1]["content"])
        saved = (C.BUDDY_DIR / "conversations" / f"{buddy._conversation_state['active']}.json").read_text()
        self.assertNotIn("Ciudad Falsa", saved)
        self.assertNotIn("-38", saved)

    def test_name_at_the_start_is_dropped_even_split_across_chunks(self):
        self.assertIsNone(buddy.strip_name("Da", "Dani"))
        self.assertEqual(buddy.strip_name("Dani: hola", "Dani"), "hola")
        self.assertEqual(buddy.strip_name("Daniel vino", "Dani"), "Daniel vino")
        self.assertEqual(buddy.strip_name("Hola", "Dani"), "Hola")
        self.assertEqual(buddy.strip_name("[de] No te entendí", "Dani"), "No te entendí")
        self.assertEqual(buddy.strip_name("[martes 17, 20:07] Dani: hola", "Dani"), "hola")
        self.assertIsNone(buddy.strip_name("[mar", "Dani"))
        chunks = [{"delta": {"content": "Da"}}, {"delta": {"content": "ni: Son"}}, {"delta": {"content": " las diez."}}]
        with mock.patch.object(FakeModel, "do_POST", _replying(chunks)):
            events = list(buddy.chat({"text": "hola", "speak": False}))
        self.assertEqual("".join(e["delta"] for e in events if e["type"] == "text"), "Son las diez.")

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


class RememberPrefix(unittest.TestCase):
    """El perfil «companero» se guarda tras el primer turno y solo si cambió lo fijo del prompt."""

    def run_with(self, messages, saved_prefix=None, loaded=("m",)):
        from unittest import mock
        from llama_panel import buddy, prompt_cache as PC, services
        saves = []
        req = {"tools": [{"name": "clima"}]}
        with mock.patch.object(services, "router_models", lambda: {k: {"status": "loaded"} for k in loaded}), \
                mock.patch.object(PC, "meta", lambda mid, profile: {"prefix": saved_prefix} if saved_prefix else {}), \
                mock.patch.object(PC, "save", lambda mid, profile, extra: (saves.append((mid, profile, extra)), (1, None))[1]):
            buddy._remember_prefix("auto", messages, req)
        return saves, buddy.prefix_key(messages, req)

    def test_first_turn_saves_once(self):
        first = [{"role": "system", "content": "s"}, {"role": "user", "content": "[lunes 10:00] hola"},
                 {"role": "assistant", "content": "x", "tool_calls": []}, {"role": "tool", "content": "r"}]
        saves, key = self.run_with(first)
        self.assertEqual(saves, [("m", "companero", {"prefix": key})])
        self.assertEqual(self.run_with(first, saved_prefix=key)[0], [])

    def test_date_does_not_change_the_key(self):
        a = [{"role": "system", "content": "s"}, {"role": "user", "content": "[lunes 10:00] hola"}]
        b = [{"role": "system", "content": "s"}, {"role": "user", "content": "[martes 11:00] chau"}]
        self.assertEqual(self.run_with(a)[1], self.run_with(b)[1])

    def test_later_turns_and_ambiguous_model_skip(self):
        later = [{"role": "system", "content": "s"}, {"role": "user", "content": "a"},
                 {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
        self.assertEqual(self.run_with(later)[0], [])
        first = [{"role": "system", "content": "s"}, {"role": "user", "content": "a"}]
        self.assertEqual(self.run_with(first, loaded=("m", "n"))[0], [])
