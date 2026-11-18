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
from . import hardware as HW

# Últimos tiempos, para la tarjeta Audio del panel
LAST = {"stt": None, "tts": None}

# Whisper "escucha" estas frases en el silencio o el ruido (vienen de los subtítulos con que se entrenó)
HALLUCINATIONS = ("subtítulos realizados por la comunidad de amara.org", "subtítulos por la comunidad de amara.org",
                  "gracias por ver el video", "¡gracias por ver!", "gracias por ver", "suscríbete",
                  "ming pao canada", "[música]", "(música)", "[sonido]",
                  # large-v3-turbo con silencio y vocabulario (bench whisper_vocab, 2026-11-18)
                  "sous-titrage société radio-canada", "sous-titres réalisés par la communauté d'amara.org")


def audio_ctx(seconds):
    """Ventana de audio que procesa Whisper: siempre arma 30 s (1500 cuadros, 50 por segundo) aunque la orden dure 3.
    Ajustarla al largo real baja small de 1.7 s a ~0.7 s para 4 s de audio. Muy justa, repite la frase (2.8 s de
    audio con 256 cuadros salió dos veces): 1.5 veces el largo + 128, mínimo 384, en múltiplos de 64; 0 = entera."""
    frames = max(384, int(seconds * 75) + 128)
    frames = -(-frames // 64) * 64
    return 0 if frames >= 1500 else frames


def clean_transcript(text):
    t = " ".join(text.split()).strip()
    # Con ruido o silencio Whisper escribe anotaciones de subtítulos: «(Para el video de la cámara)», «[Música]»,
    # o los restos de una («de la puerta]»). Lo dicho de verdad no viene entre paréntesis ni corchetes.
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"^[^\[\]()]*[\])]|[\[(][^\[\]()]*$", " ", t)
    t = " ".join(t.split())
    # Whisper suele pegar una frase de subtítulos antes o después de algo que sí se dijo. No alcanza con descartar
    # sólo la coincidencia exacta: sacamos estas alucinaciones conocidas únicamente de los bordes.
    for hallucination in sorted(HALLUCINATIONS, key=len, reverse=True):
        phrase = re.escape(hallucination.strip(" .!¡[]()"))
        t = re.sub(rf"^\s*[¡!¿?\[\(]*{phrase}[\]\) .,!¡¿?;:—-]*", "", t, flags=re.I)
        t = re.sub(rf"[¡!¿?\[\( .,!;:—-]*{phrase}[\]\) .,!¡¿?;:—-]*$", "", t, flags=re.I)
    if not t.strip(" .!¡¿?"):
        return ""
    # Con la ventana de audio justa (audio_ctx) Whisper a veces escribe la frase entera dos veces
    # («¿Cuánto está el dólar? ¿Cuánto está el dólar?»): queda una
    m = re.fullmatch(r"(.{8,}?[.!?…]?)(?:\s+\1)+", t, flags=re.I | re.S)
    return m.group(1) if m else t


def stt_available():
    return os.access(C.WHISPER_CLI, os.X_OK) and Path(AM.selected()["stt"]).is_file()


# Whisper en la GPU (2026-11-18, RX 580 con Flash-Next cargado): large-v3-turbo q5_0 entra en lo que deja libre --fit
# (~850 MiB: el pico fue +780) y tarda ~0.8 s con la carga incluida, contra 2.1 s en el CPU (11 s con la ventana
# entera). Con 384 cuadros repite la frase; con 768, no. Cada transcripción es un proceso aparte: la VRAM queda
# ocupada solo ese segundo, así que no hay nada que descargar cuando otro programa usa el modelo de texto.
GPU_MIN_AC = 768
GPU_SPARE_MIB = 300  # buffers de cálculo además del archivo del modelo
BIG_MODEL = 300 << 20  # más que esto (medium, large, turbo) es lento en el CPU: ahí se usa el más chico que haya


def vram_free_mib():
    if not HW.GPU_DIR:
        return None
    try:
        used = int(Path(HW.GPU_DIR, "mem_info_vram_used").read_text())
        total = int(Path(HW.GPU_DIR, "mem_info_vram_total").read_text())
    except (OSError, ValueError):
        return None
    return (total - used) >> 20


def plan(model):
    """En qué orden probar: [(dispositivo, whisper-cli, modelo)]. La GPU primero si hay binario con Vulkan y la VRAM
    libre alcanza (si no, o si falla, el CPU)."""
    out = []
    try:
        size = Path(model).stat().st_size
    except OSError:
        size = 0
    free = vram_free_mib()
    if os.access(C.WHISPER_CLI_GPU, os.X_OK) and free is not None and free >= (size >> 20) + GPU_SPARE_MIB:
        out.append(("gpu", C.WHISPER_CLI_GPU, model))
    cpu_model = model
    if size > BIG_MODEL:
        small = [m for m in AM.scan()["stt"] if m["size"] <= BIG_MODEL and m["lang"] == "multi"]
        if small:
            cpu_model = small[-1]["path"]  # el más grande de los chicos
    out.append(("cpu", C.WHISPER_CLI, cpu_model))
    return out


def transcribe(audio, threads=8, language="es", prompt=None, keep_wav=None, nice=False):
    """Audio en cualquier formato que entienda ffmpeg (el navegador manda webm/opus). Devuelve (texto, segundos).
    `prompt`: vocabulario para Whisper (nombres que tiene que escribir bien, ver hearing.py). `keep_wav`: ruta donde
    dejar una copia del audio ya convertido (16 kHz mono), para las pruebas con la voz real. `nice`: con la menor
    prioridad (las comparaciones en segundo plano no le sacan CPU a una charla)."""
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
        if keep_wav:
            Path(keep_wav).parent.mkdir(parents=True, exist_ok=True)
            Path(keep_wav).write_bytes(wav.read_bytes())
        model = AM.selected()["stt"]
        errors = []
        for device, cli, use in plan(model):
            t0 = time.time()
            ac = audio_ctx(seconds)
            if device == "gpu" and ac:
                ac = max(ac, GPU_MIN_AC)
            cmd = [cli, "-m", use, "-f", str(wav), "-l", language, "-t", str(threads), "-ac", str(ac), "-nt", "-np"]
            if prompt:
                cmd += ["--prompt", prompt]
            if nice:
                cmd = ["nice", "-n", "19"] + cmd
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                break
            errors.append(f"{device}: {r.stderr.strip()[-200:]}")
        else:
            raise RuntimeError("whisper: " + " · ".join(errors))
        LAST["stt"] = {"t": time.time(), "audio_s": round(seconds, 1), "s": round(time.time() - t0, 2),
                       "model": Path(use).name, "device": device}
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
