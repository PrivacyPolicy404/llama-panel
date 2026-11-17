"""Voz del compañero: whisper.cpp pasa el audio a texto (en el CPU, la GPU es del modelo) y Piper lo lee en voz alta.

Whisper corre como un proceso por pedido (carga el modelo en ~0.1 s desde el caché de páginas y no ocupa RAM entre
pedidos). Piper vive en su propio venv: se lo arranca una vez (tts_worker.py) y se le pasan oraciones.
"""
import json
import os
import re
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import audio_models as AM
from . import config as C

# Últimos tiempos, para la tarjeta Audio del panel
LAST = {"stt": None, "tts": None}

# Whisper "escucha" estas frases en el silencio o el ruido (vienen de los subtítulos con que se entrenó)
HALLUCINATIONS = ("subtítulos realizados por la comunidad de amara.org", "subtítulos por la comunidad de amara.org",
                  "gracias por ver el video", "¡gracias por ver!", "gracias por ver", "suscríbete",
                  "ming pao canada", "[música]", "(música)", "[sonido]")


def audio_ctx(seconds):
    """Ventana de audio que procesa Whisper: siempre arma 30 s (1500 cuadros, 50 por segundo) aunque la orden dure 3.
    Ajustarla al largo real baja small de 1.7 s a ~0.7 s para 4 s de audio. Muy justa, repite la frase (2.8 s de
    audio con 256 cuadros salió dos veces): 1.5 veces el largo + 128, mínimo 384, en múltiplos de 64; 0 = entera."""
    frames = max(384, int(seconds * 75) + 128)
    frames = -(-frames // 64) * 64
    return 0 if frames >= 1500 else frames


def clean_transcript(text):
    t = " ".join(text.split()).strip()
    if t.lower().strip(" .!¡") in [h.strip(" .!¡") for h in HALLUCINATIONS]:
        return ""
    return t


def stt_available():
    return os.access(C.WHISPER_CLI, os.X_OK) and Path(AM.selected()["stt"]).is_file()


def transcribe(audio, threads=8, language="es"):
    """Audio en cualquier formato que entienda ffmpeg (el navegador manda webm/opus). Devuelve (texto, segundos)."""
    with tempfile.TemporaryDirectory(prefix="buddy-") as d:
        src, wav = Path(d, "in"), Path(d, "in.wav")
        src.write_bytes(audio)
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", str(wav)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg: {r.stderr.strip()[:200]}")
        seconds = max(0.0, (wav.stat().st_size - 44) / 32000)
        if seconds < 0.3:
            return "", seconds
        model = AM.selected()["stt"]
        t0 = time.time()
        r = subprocess.run([C.WHISPER_CLI, "-m", model, "-f", str(wav), "-l", language, "-t", str(threads),
                            "-ac", str(audio_ctx(seconds)), "-nt", "-np"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"whisper: {r.stderr.strip()[-200:]}")
        LAST["stt"] = {"t": time.time(), "audio_s": round(seconds, 1), "s": round(time.time() - t0, 2),
                       "model": Path(model).name}
        return clean_transcript(r.stdout), seconds


# ---------- hablar ----------
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def speakable(text):
    """Lo que tiene sentido leer en voz alta: sin markdown, emojis ni bloques de código."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # [texto](url)
    t = re.sub(r"[*_#>~|]+", " ", t)
    t = EMOJI_RE.sub("", t)
    return " ".join(t.split())


SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+|\n+")
COMMA = re.compile(r",\s+")


def split_sentences(buffer, final=False, min_len=24, first=False):
    """Corta lo generado hasta ahora en oraciones listas para leer. Devuelve (oraciones, resto). Junta las cortas
    con la siguiente para que la voz no suene entrecortada, salvo la primera (`first`): esa sale apenas haya algo
    ("¡Hola!" suena enseguida) y, si viene larga, se corta en una coma. Lo que importa es cuándo empieza a hablar;
    mientras suena la primera parte se sintetiza la siguiente."""
    if first:
        min_len = 1
        if not final and not SENTENCE_END.search(buffer):
            m = list(COMMA.finditer(buffer))
            if m and m[-1].start() >= 30:
                return [buffer[:m[-1].start() + 1].strip()], buffer[m[-1].end():]
    parts = SENTENCE_END.split(buffer)
    rest = "" if final else parts.pop()
    out, acc = [], ""
    for p in parts:
        acc = f"{acc} {p}".strip() if acc else p.strip()
        if len(acc) >= min_len:
            out.append(acc)
            acc = ""
    if acc:
        if final:
            out.append(acc)
        else:
            rest = f"{acc} {rest}".strip()
    return [o for o in out if o], rest


class Speaker:
    """Proceso de Piper que queda vivo: recibe una oración por línea (JSON) y devuelve un WAV con su largo adelante."""

    def __init__(self):
        self.proc = None
        self.voice = None  # voz con la que arrancó el proceso: si se elige otra, se lo reinicia
        self.fixed = None  # voz fija (para escuchar una antes de elegirla); None = la elegida
        self.track = True  # anotar los tiempos en LAST (las pruebas de voces no son uso real)
        self.lock = threading.Lock()

    def warm(self):
        """Arranca Piper de antemano (cargar la voz tarda ~0.6 s): se llama al empezar una charla con voz."""
        with self.lock:
            self._ensure()

    @staticmethod
    def available():
        return os.access(C.PIPER_PYTHON, os.X_OK) and Path(AM.selected()["tts"]).is_file()

    def _ensure(self):
        voice = self.fixed or AM.selected()["tts"]
        if self.proc is not None and self.proc.poll() is None and self.voice == voice:
            return
        self.stop()
        worker = Path(__file__).with_name("tts_worker.py")
        self.proc = subprocess.Popen([C.PIPER_PYTHON, str(worker), voice], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.voice = voice

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc = None

    def say(self, text):
        """WAV (bytes) de `text`, o None si no hay nada que leer."""
        text = speakable(text)
        if not text:
            return None
        with self.lock:
            for attempt in (1, 2):  # si el proceso murió, se lo arranca de nuevo una vez
                self._ensure()
                t0 = time.time()
                try:
                    self.proc.stdin.write((json.dumps({"text": text}) + "\n").encode())
                    self.proc.stdin.flush()
                    head = self.proc.stdout.read(4)
                    if len(head) == 4:
                        wav = self.proc.stdout.read(struct.unpack(">I", head)[0])
                        if self.track:
                            LAST["tts"] = {"t": time.time(), "s": round(time.time() - t0, 2),
                                           "chars": len(text), "voice": Path(self.voice).stem}
                        return wav
                except OSError:
                    pass
                self.stop()
            raise RuntimeError("Piper no respondió")


SPEAKER = Speaker()
PREVIEW = Speaker()  # para "Escuchar" una voz en la pestaña Modelos de audio, sin tocar la del compañero
PREVIEW.track = False


def preview(path, text):
    with PREVIEW.lock:
        PREVIEW.fixed = path
    return PREVIEW.say(text)
