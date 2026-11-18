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


class WhisperDevice(unittest.TestCase):
    """voice.plan: Whisper en la GPU si hay binario con Vulkan y VRAM libre; si no, el CPU con un modelo chico."""

    def setUp(self):
        from llama_panel import voice
        self.voice = voice
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.big = self.root / "ggml-large-v3-turbo-q5_0.bin"
        self.small = self.root / "ggml-small-q8_0.bin"
        for p, mib in ((self.big, 547), (self.small, 252)):
            with open(p, "wb") as f:
                f.truncate(mib << 20)
        scan = {"stt": [{"path": str(self.small), "size": 252 << 20, "lang": "multi"},
                        {"path": str(self.big), "size": 547 << 20, "lang": "multi"}]}
        for patch in (mock.patch.object(AM, "scan", return_value=scan),
                      mock.patch.object(C, "WHISPER_CLI_GPU", "/gpu/whisper-cli"),
                      mock.patch.object(C, "WHISPER_CLI", "/cpu/whisper-cli")):
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self, model, free, gpu_bin=True):
        with mock.patch.object(self.voice, "vram_free_mib", return_value=free), \
                mock.patch.object(self.voice.os, "access", return_value=gpu_bin):
            return [(d, m) for d, _cli, m in self.voice.plan(str(model))]

    def test_gpu_when_it_fits(self):
        self.assertEqual(self.plan(self.big, 900), [("gpu", str(self.big)), ("cpu", str(self.small))])

    def test_cpu_with_small_model_when_vram_is_full(self):
        self.assertEqual(self.plan(self.big, 500), [("cpu", str(self.small))])

    def test_no_vulkan_build(self):
        self.assertEqual(self.plan(self.big, 4000, gpu_bin=False), [("cpu", str(self.small))])

    def test_small_model_stays_on_cpu_fallback(self):
        self.assertEqual(self.plan(self.small, 900), [("gpu", str(self.small)), ("cpu", str(self.small))])

    def test_falls_back_when_gpu_fails(self):
        calls = []

        def run(cmd, **_kw):
            if cmd[0] == "ffmpeg":  # deja 1 s de wav de 16 kHz
                Path(cmd[-1]).write_bytes(b"\0" * (44 + 32000))
                return mock.Mock(returncode=0)
            calls.append(cmd[cmd.index("-m") + 1])
            if len(calls) == 1:
                return mock.Mock(returncode=1, stderr="ErrorOutOfDeviceMemory", stdout="")
            return mock.Mock(returncode=0, stderr="", stdout="hola")

        steps = [("gpu", "/gpu/w", str(self.big)), ("cpu", "/cpu/w", str(self.small))]
        with mock.patch.object(self.voice, "plan", return_value=steps), \
                mock.patch.object(self.voice.subprocess, "run", side_effect=run):
            text, _ = self.voice.transcribe(b"audio")
        self.assertEqual(text, "hola")
        self.assertEqual(calls, [str(self.big), str(self.small)])
        self.assertEqual(self.voice.LAST["stt"]["device"], "cpu")
