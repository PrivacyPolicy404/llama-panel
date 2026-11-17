"""Rutas de audio compatibles con la API de OpenAI, en el puerto del panel: cualquier cliente (Open WebUI, un script)
puede usar la voz del panel con base_url http://<máquina>:8090/v1.

- POST /v1/audio/speech         {"input": "...", "response_format": "mp3"} -> audio (Piper, la voz elegida)
- POST /v1/audio/transcriptions multipart con "file" (y "language", "response_format") -> {"text": "..."} (Whisper)

El campo "model" y el "voice" de OpenAI ("alloy"…) se ignoran: se usa lo elegido en la pestaña Modelos de audio.
"""
import json
import subprocess
from email.parser import BytesParser
from email.policy import HTTP

from . import voice

FORMATS = {"wav": ("audio/wav", None), "mp3": ("audio/mpeg", ["-f", "mp3", "-b:a", "96k"]),
           "opus": ("audio/ogg", ["-f", "ogg", "-c:a", "libopus", "-b:a", "48k"]),
           "flac": ("audio/flac", ["-f", "flac"]), "aac": ("audio/aac", ["-f", "adts", "-c:a", "aac"]),
           "pcm": ("audio/pcm", ["-f", "s16le", "-ar", "24000", "-ac", "1"])}


def _error(code, msg):
    return code, "application/json", json.dumps({"error": {"message": msg, "type": "invalid_request_error"}}).encode()


def convert(wav, fmt):
    ctype, args = FORMATS[fmt]
    if args is None:
        return ctype, wav
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", *args, "pipe:1"], input=wav,
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[:200])
    return ctype, r.stdout


def speech(headers, raw):
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return _error(400, "JSON inválido")
    text = str(body.get("input") or "").strip()
    fmt = str(body.get("response_format") or "mp3")
    if not text:
        return _error(400, "falta 'input'")
    if fmt not in FORMATS:
        return _error(400, f"response_format: {', '.join(FORMATS)}")
    if not voice.Speaker.available():
        return _error(503, "Piper no está instalado (scripts/install-voice.sh)")
    wav = voice.SPEAKER.say(text)
    if not wav:
        return _error(400, "no hay nada para leer en 'input'")
    ctype, data = convert(wav, fmt)
    return 200, ctype, data


def parse_multipart(ctype, raw):
    """{nombre: bytes} de un formulario multipart/form-data."""
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
    if not msg.is_multipart():
        return {}
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name:
            out[name] = part.get_payload(decode=True) or b""
    return out


def transcriptions(headers, raw):
    ctype = headers.get("Content-Type", "")
    if not ctype.startswith("multipart/form-data"):
        return _error(400, "se espera multipart/form-data con un campo 'file'")
    form = parse_multipart(ctype, raw)
    audio = form.get("file")
    if not audio:
        return _error(400, "falta 'file'")
    if not voice.stt_available():
        return _error(503, "whisper.cpp no está instalado (scripts/install-voice.sh)")
    lang = (form.get("language") or b"es").decode(errors="replace").strip() or "es"
    fmt = (form.get("response_format") or b"json").decode(errors="replace").strip()
    try:
        text, seconds = voice.transcribe(audio, language=lang)
    except RuntimeError as e:
        return _error(400, str(e))
    if fmt == "text":
        return 200, "text/plain", text.encode()
    data = {"text": text}
    if fmt == "verbose_json":
        data.update(language=lang, duration=seconds)
    return 200, "application/json", json.dumps(data, ensure_ascii=False).encode()


RAW_POST_ROUTES = {"/v1/audio/speech": speech, "/v1/audio/transcriptions": transcriptions}
