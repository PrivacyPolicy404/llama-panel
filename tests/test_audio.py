"""Motor de audio: búsqueda de modelos de Whisper y voces de Piper, elección, y las rutas compatibles con OpenAI."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llama_panel import audio_api
from llama_panel import audio_models as AM
from llama_panel import config as C


class Scan(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        (self.root / "whisper").mkdir()
        (self.root / "whisper/ggml-small-q8_0.bin").write_bytes(b"x" * 10)
        (self.root / "whisper/ggml-tiny.en.bin").write_bytes(b"x")
        (self.root / "whisper/for-tests-ggml-tiny.bin").write_bytes(b"x")
        (self.root / "piper/es").mkdir(parents=True)
        (self.root / "piper/es/es_AR-daniela-high.onnx").write_bytes(b"x")
        (self.root / "piper/es/es_AR-daniela-high.onnx.json").write_text(
            json.dumps({"language": {"code": "es_AR"}, "audio": {"quality": "high", "sample_rate": 22050}}))
        (self.root / "piper/es/sin-json.onnx").write_bytes(b"x")  # otro modelo ONNX, no una voz
        (self.root / "piper-venv/lib").mkdir(parents=True)  # el venv no se recorre
        (self.root / "piper-venv/lib/ggml-trampa.bin").write_bytes(b"x")
        settings = self.root / "settings.json"
        for p in (mock.patch.object(C, "VOICE_DIR", self.root), mock.patch.object(C, "SETTINGS_FILE", settings),
                  mock.patch.object(C, "WHISPER_MODEL", str(self.root / "whisper/ggml-small-q8_0.bin")),
                  mock.patch.object(C, "PIPER_VOICE", str(self.root / "piper/es/es_AR-daniela-high.onnx"))):
            p.start()
            self.addCleanup(p.stop)

    def test_finds_whisper_models_and_voices(self):
        found = AM.scan()
        self.assertEqual([m["id"] for m in found["stt"]], ["tiny.en", "small-q8_0"])  # por tamaño
        self.assertEqual(found["stt"][0]["lang"], "en")
        self.assertEqual([(m["id"], m["lang"], m["quality"]) for m in found["tts"]],
                         [("es_AR-daniela-high", "es_AR", "high")])

    def test_select_only_what_is_on_disk(self):
        tiny = str(self.root / "whisper/ggml-tiny.en.bin")
        self.assertEqual(AM.selected()["stt"], C.WHISPER_MODEL)  # sin elección: la de paths.env
        self.assertIsNone(AM.select("stt", tiny))
        self.assertEqual(AM.selected()["stt"], tiny)
        self.assertIsNotNone(AM.select("tts", "/etc/passwd"))
        self.assertIsNotNone(AM.select("otro", tiny))


class OpenAIRoutes(unittest.TestCase):
    def test_multipart(self):
        body = (b"--XX\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.webm\"\r\n"
                b"Content-Type: audio/webm\r\n\r\n\x00\x01audio\r\n"
                b"--XX\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nes\r\n--XX--\r\n")
        form = audio_api.parse_multipart("multipart/form-data; boundary=XX", body)
        self.assertEqual(form, {"file": b"\x00\x01audio", "language": b"es"})

    def test_transcription_formats(self):
        body = b"--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n\r\nRIFF\r\n--B--\r\n"
        headers = {"Content-Type": "multipart/form-data; boundary=B"}
        with mock.patch.object(audio_api.voice, "stt_available", lambda: True), \
                mock.patch.object(audio_api.voice, "transcribe", lambda audio, language: ("hola", 1.0)):
            code, ctype, data = audio_api.transcriptions(headers, body)
            self.assertEqual((code, json.loads(data)), (200, {"text": "hola"}))
        self.assertEqual(audio_api.transcriptions({"Content-Type": "application/json"}, b"{}")[0], 400)

    def test_speech_errors_and_wav(self):
        self.assertEqual(audio_api.speech({}, b'{"input": ""}')[0], 400)
        self.assertEqual(audio_api.speech({}, b'{"input": "hola", "response_format": "midi"}')[0], 400)
        with mock.patch.object(audio_api.voice.Speaker, "available", staticmethod(lambda: True)), \
                mock.patch.object(audio_api.voice.SPEAKER, "say", lambda text: b"RIFFwav"):
            self.assertEqual(audio_api.speech({}, b'{"input": "hola", "response_format": "wav"}'),
                             (200, "audio/wav", b"RIFFwav"))


if __name__ == "__main__":
    unittest.main()
