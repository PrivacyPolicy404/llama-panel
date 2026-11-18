"""El compañero del panel desde tu PC: mantené apretada una tecla en cualquier programa y hablale. O, si lo
activás, decí su nombre.

Windows, Linux o macOS, con Python 3.9 o más nuevo (en Windows: winget install Python.Python.3.12). El panel sirve
este archivo en http://<máquina>:8090/clients/buddy.py. En PowerShell:

    iwr http://god.local:8090/clients/buddy.py -OutFile buddy.py
    py buddy.py --server http://god.local:8090             # tecla para hablar: Ctrl derecho
    py buddy.py --key f9                                    # otra tecla (f1..f12, ctrl_r, alt_r, una letra…)
    py buddy.py --list-devices                              # micrófonos y parlantes; elegir con --mic / --out

Mientras la tecla está apretada graba; al soltarla manda el audio al panel (Whisper lo pasa a texto), muestra lo que
entendió y la respuesta, y reproduce su voz oración por oración. Apretar de nuevo mientras habla la corta. Ctrl+C sale.

Lo que le falte (sounddevice, pynput y, para escuchar su nombre, Vosk) lo instala solo con pip la primera vez.

Escuchar su nombre: se prende en el panel (pestaña Compañero, ajustes, Voz); el reconocedor (Vosk y su modelo de 40 MB)
se baja solo la primera vez. El micrófono queda abierto y Vosk reconoce acá,
en la PC: al panel va solo la frase que tiene el nombre que le pusiste («Lola, ¿qué hora es?», «che Lola, …»). Si
decís solo el nombre, pita y espera la pregunta; mientras habla no se despierta con su propia voz. Este programa mira el
ajuste cada 15 s; --wake lo prende siempre en esta PC (--wake Otro para otro nombre) y --no-wake lo apaga.

Para probar sin micrófono ni teclado: --file pregunta.wav (manda ese audio) y --save carpeta/ (guarda las respuestas
en vez de reproducirlas). Si el panel pide contraseña: --token o la variable BUDDY_TOKEN.
"""
import argparse
import base64
import importlib
import difflib
import hashlib
import http.client
import io
import json
import os
import queue
import re
import site
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import wave
import zipfile

MIN_SECONDS = 0.3
# Lo que se sabe del compañero en el panel (follow_panel lo actualiza): su nombre y si escucha su nombre
BUDDY = {"name": "Compañero", "wake": False}
POLL_S = 15
# Id de este programa: el panel le habla los avisos (timers, alertas) al cliente que se usó último
CLIENT = "pc-" + os.urandom(4).hex()


# ---------- lo que necesita instalado ----------
# Probados: sounddevice 0.5, pynput 1.8, vosk 0.3.45 (Windows y Linux). Se instalan solos la primera vez que hacen
# falta, para que alcance con bajar este archivo y correrlo.
PACKAGES = {"sounddevice": "sounddevice", "pynput": "pynput", "vosk": "vosk==0.3.45"}


def need(module):
    """Importa el módulo; si no está, lo instala con pip (para este usuario) y lo vuelve a intentar."""
    try:
        return importlib.import_module(module)
    except ImportError:
        pass
    print(f"Instalando {module} con pip (una sola vez)…", flush=True)
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", PACKAGES[module]]
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    r = subprocess.run(cmd if in_venv else cmd[:4] + ["--user"] + cmd[4:])  # con su salida: que se vea que avanza
    if r.returncode != 0:
        raise ImportError(f"no se pudo instalar {module} (pip terminó con {r.returncode})")
    print(f"{module} instalado.", flush=True)
    user_site = site.getusersitepackages()
    if user_site not in sys.path:  # la carpeta recién creada no estaba al arrancar
        sys.path.append(user_site)
    importlib.invalidate_caches()
    return importlib.import_module(module)


# ---------- arranque ----------
STAGE = {"what": None, "since": 0.0}
SLOW_S = 4  # un paso del arranque que tarda más que esto se avisa


def stage(what):
    """Marca en qué paso del arranque está (None = arrancó). Si uno tarda, watchdog avisa cuál."""
    STAGE.update(what=what, since=time.time())


def watchdog():
    told = None
    while STAGE["what"] is not None or told is None:
        what = STAGE["what"]
        if what is None:
            return
        if what != told and time.time() - STAGE["since"] > SLOW_S:
            print(f"(todavía {what}…)", flush=True)
            told = what
        time.sleep(0.5)


# ---------- panel ----------
def _conn(server, timeout):
    u = urllib.parse.urlsplit(server)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    return conn_cls(u.hostname, u.port or (443 if u.scheme == "https" else 80), timeout=timeout)


def chat(server, audio_wav, token="", cancel=None, on_conn=None):
    """Manda el audio y va devolviendo los eventos de la respuesta (dicts). `cancel` corta la lectura."""
    conn = _conn(server, 600)
    if on_conn:
        on_conn(conn)
    headers = {"Content-Type": "application/json", "X-Lang": "es"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"audio": base64.b64encode(audio_wav).decode(), "speak": True, "client": CLIENT})
    try:
        conn.request("POST", "/api/buddy/chat", body, headers)
        resp = conn.getresponse()
        if resp.status != 200:
            yield {"type": "error", "msg": f"HTTP {resp.status}: {resp.read()[:200].decode(errors='replace')}"}
            return
        for line in resp:
            if cancel is not None and cancel.is_set():
                return
            if line.startswith(b"data: "):
                yield json.loads(line[6:])
    except OSError as e:
        if cancel is None or not cancel.is_set():
            yield {"type": "error", "msg": f"no se pudo hablar con el panel: {e}"}
    finally:
        conn.close()


def panel_settings(server, token=""):
    """{"name", "wake"} del compañero (pestaña Compañero del panel), o None si no se pudo preguntar."""
    for path in ("/api/buddy/client", "/api/buddy"):  # /api/buddy: paneles anteriores a /api/buddy/client
        conn = _conn(server, 10)
        try:
            conn.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {})
            resp = conn.getresponse()
            if resp.status == 404:
                continue
            if resp.status != 200:
                return None
            d = json.loads(resp.read())
            d = d.get("config", d)
            return {"name": d.get("name") or BUDDY["name"], "wake": bool(d.get("wake"))}
        except (OSError, ValueError):
            return None
        finally:
            conn.close()
    return None


def to_wav(pcm, rate, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ---------- audio ----------
class Player:
    """Reproduce los WAV de la respuesta en orden, en un hilo aparte. stop() corta y vacía la cola."""

    def __init__(self, device=None, save_dir=None):
        self.q = queue.Queue()
        self.device, self.save_dir = device, save_dir
        self.stopped = threading.Event()
        self.n = 0
        self.playing, self.quiet_since = False, 0.0
        threading.Thread(target=self._loop, daemon=True).start()

    def play(self, wav_bytes):
        self.q.put(wav_bytes)

    def stop(self):
        self.stopped.set()
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def _loop(self):
        while True:
            data = self.q.get()
            self.stopped.clear()
            if self.save_dir:
                self.n += 1
                path = os.path.join(self.save_dir, f"respuesta-{self.n:02d}.wav")
                with open(path, "wb") as f:
                    f.write(data)
                continue
            sd = need("sounddevice")
            with wave.open(io.BytesIO(data)) as w:
                rate, ch, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
            self.playing = True
            try:
                with sd.RawOutputStream(samplerate=rate, channels=ch, dtype="int16", device=self.device) as out:
                    step = rate // 10 * 2 * ch  # de a 100 ms, para poder cortar
                    for i in range(0, len(frames), step):
                        if self.stopped.is_set():
                            break
                        out.write(frames[i:i + step])
            finally:
                self.playing, self.quiet_since = not self.q.empty(), time.time()


class Beeper:
    """Pitido al empezar y terminar de grabar (no hay pantalla que mire). La salida queda abierta en silencio todo el
    tiempo: abrir un stream en cada pitido tarda cientos de ms en Windows. Usa la frecuencia de la placa para que el
    sistema no tenga que convertir. Una salida abierta no prende ningún ícono ni graba nada."""

    def __init__(self, device=None):
        sd = need("sounddevice")
        self.rate = int(sd.query_devices(device, "output")["default_samplerate"])
        self.lock, self.pending = threading.Lock(), bytearray()
        self.tones = {f: self._tone(f) for f in (880, 660)}
        self.stream = sd.RawOutputStream(samplerate=self.rate, channels=1, dtype="int16", device=device,
                                         latency="low", callback=self._fill)
        self.stream.start()

    def _tone(self, freq, ms=70):
        import math
        n = int(self.rate * ms / 1000)
        fade = max(1, n // 10)  # sin clic al empezar y terminar
        return b"".join(int(6000 * min(1, i / fade, (n - i) / fade) * math.sin(2 * math.pi * freq * i / self.rate))
                        .to_bytes(2, "little", signed=True) for i in range(n))

    def _fill(self, out, frames, _time, _status):
        want = frames * 2
        with self.lock:
            chunk = bytes(self.pending[:want])
            del self.pending[:want]
        out[:len(chunk)] = chunk
        out[len(chunk):] = b"\0" * (want - len(chunk))

    def __call__(self, freq=880):
        with self.lock:
            self.pending[:] = self.tones.get(freq) or self._tone(freq)


def make_beeper(args):
    """Devuelve la función de pitido; si no hay salida de audio o se pidió --no-beep, una que no hace nada."""
    if args.no_beep:
        return lambda freq=880: None
    try:
        return Beeper(args.out)
    except Exception as e:  # noqa: BLE001
        print(f"[pitido] sin salida de audio: {e}", flush=True)
        return lambda freq=880: None


class Recorder:
    def __init__(self, device=None):
        sd = need("sounddevice")
        self.sd, self.device = sd, device
        self.rate = int(sd.query_devices(device, "input")["default_samplerate"])
        self.stream, self.chunks, self.t0 = None, [], 0

    def start(self):
        self.chunks, self.t0 = [], time.time()
        self.stream = self.sd.RawInputStream(samplerate=self.rate, channels=1, dtype="int16", device=self.device,
                                             callback=lambda data, *_: self.chunks.append(bytes(data)))
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()
        self.stream = None
        return b"".join(self.chunks), time.time() - self.t0

# ---------- palabra clave (--wake) ----------
# Vosk con su modelo chico en castellano reconoce todo lo que escucha, acá, en la PC; solo cuando aparece la palabra
# clave se manda esa frase al panel. Medido con voces de Piper (docs/notas.md): reconocimiento libre con 5 lecturas
# detecta 42 de 48 frases con «Dani» y da 1 falsa alarma en 76 que no lo dicen («Danielle»); restringirlo a una
# gramática («dani» o cualquier cosa) daba 44 falsas alarmas de 76.
WAKE_MODEL = ("vosk-model-small-es-0.42", "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip",
              "09b239888f633ef2f0b4e09736e3d9936acfd810bc65d53fad45261762c6511f")


def sound(word):
    """Cómo suena una palabra en castellano, para comparar sin importar cómo la escribió el reconocedor
    («danny» = «dani», «bicky» = «vicky»)."""
    w = "".join(c for c in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(c))
    w = re.sub(r"[^a-z]", "", w)
    for a, b in (("ch", "X"), ("ll", "y"), ("qu", "k"), ("gu(?=[ei])", "G"), ("g(?=[ei])", "j"), ("G", "g"),
                 ("^x", "j"), ("c(?=[ei])", "s"), ("c", "k"), ("z", "s"), ("v", "b"), ("w", "u"), ("h", ""),
                 ("x", "ks"), ("(.)\\1+", "\\1"), ("y$", "i")):
        w = re.sub(a, b, w)
    return w


def sounds_like(heard, key):
    """¿El sonido `heard` (una palabra o varias pegadas) es el nombre? Los nombres cortos tienen que sonar igual
    («dame» no es «dani»); a los largos se les perdona una letra («jarbis» ≈ «yarbis»)."""
    if heard == key:
        return True
    if len(key) < 5 or abs(len(heard) - len(key)) > 1:
        return False
    return difflib.SequenceMatcher(None, heard, key).ratio() >= 0.8


def find_name(words, keys, start=0):
    """Dónde está el nombre en las palabras de Vosk: (primera, última) o None. Vosk solo escribe palabras que conoce,
    así que un nombre raro llega partido («ximena» → «hi mena»): se prueban también 2 y 3 palabras seguidas."""
    snd = [sound(w["word"]) for w in words]
    for i in range(start, len(words)):
        for n in (1, 2, 3):
            if i + n <= len(words) and any(sounds_like("".join(snd[i:i + n]), k) for k in keys):
                return i, i + n - 1
    return None


def wake_model_dir():
    """El modelo de Vosk (40 MB): se baja la primera vez y se verifica el sha256."""
    base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache"), "llama-panel-buddy")
    name, url, sha = WAKE_MODEL
    path = os.path.join(base, name)
    if os.path.isdir(path):
        return path
    os.makedirs(base, exist_ok=True)
    tmp = path + ".zip.part"
    print(f"Bajando el reconocedor para escuchar su nombre ({name}, 40 MB, una sola vez)…", flush=True)
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as f:
        total, done, shown = int(resp.headers.get("Content-Length") or 0), 0, -1
        for chunk in iter(lambda: resp.read(1 << 16), b""):
            h.update(chunk)
            f.write(chunk)
            done += len(chunk)
            pct = done * 100 // total if total else 0
            if pct // 10 != shown:
                shown = pct // 10
                print(f"  {pct} % ({done >> 20} de {total >> 20} MB)", flush=True)
    if h.hexdigest() != sha:
        os.remove(tmp)
        raise RuntimeError("el modelo bajado no coincide con el esperado (sha256); no lo uso")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(base)
    os.remove(tmp)
    return path


class WakeDetector:
    """Recibe el audio del micrófono de a pedazos y avisa cuando hay una frase con la palabra clave.

    feed() devuelve None, "wait" (dijo solo el nombre: pitar y esperar la pregunta), "timeout" (no siguió) o los bytes
    de la frase. Vosk da una frase por terminada recién tras ~1 s de silencio, así que en una charla seguida junta
    varias; con los tiempos de cada palabra se recorta solo la que tiene el nombre: para atrás lo pegado al nombre
    («che», «hola», «qué onda»), para adelante hasta la primera pausa de GAP s."""
    PRE, POST, KEEP, WAIT = 0.25, 0.3, 30, 8
    GAP, LEAD = 0.45, 1.2  # con 0.3 s no se pega nada ajeno, pero corta las pausas de quien piensa

    def __init__(self, model, rate, words, rec=None):
        if rec is None:
            vosk = need("vosk")
            rec = vosk.KaldiRecognizer(model, rate)
            rec.SetWords(True)
            rec.SetMaxAlternatives(5)
        self.rec = rec
        self.rate, self.keys = rate, {sound(w) for w in words}
        self.ring, self.base, self.total = bytearray(), 0, 0  # últimos KEEP s; base = muestras ya descartadas
        self.pending = None  # segundo en que se dijo el nombre solo, esperando la pregunta
        self.muted = False  # mientras habla el compañero o se usa la tecla

    def feed(self, chunk):
        self.ring += chunk
        self.total += len(chunk) // 2
        extra = len(self.ring) // 2 - self.KEEP * self.rate
        if extra > 0:
            del self.ring[:extra * 2]
            self.base += extra
        if not self.rec.AcceptWaveform(chunk):
            # sin la pregunta: vence solo si de verdad se calló (si sigue hablando, Vosk todavía no cerró la frase)
            if (self.pending is not None and self.total / self.rate - self.pending > self.WAIT
                    and not json.loads(self.rec.PartialResult()).get("partial")):
                self.pending = None
                return "timeout"
            return None
        res = json.loads(self.rec.Result())
        alts = res.get("alternatives") or [res]
        if self.muted:
            self.pending = None
            return None
        parts = []
        if self.pending is not None:
            words = alts[0].get("result") or []
            if not words:
                return None
            # la pregunta es lo primero que dijo después del nombre; si después siguió con otra cosa, no va
            parts.append((self.pending, words[self._run(words, 0)]["end"]))
            self.pending = None
            alts = [{"result": words[self._run(words, 0) + 1:]}]
        # las otras lecturas de Vosk encuentran el nombre cuando la principal lo escribió mal, pero si la principal
        # entendió un nombre más largo que empieza igual («Daniel» para «Dani») es ese y no el de la palabra clave
        main = [sound(w["word"]) for w in alts[0].get("result") or []]
        if any(m.startswith(k) and len(m) > len(k) + 1 for m in main for k in self.keys):
            alts = alts[:1]
        words = next((a.get("result") for a in alts if find_name(a.get("result") or [], self.keys)), None) or []
        i = 0
        while True:
            found = find_name(words, self.keys, i)
            if found is None:
                break
            hit, hit_end = found
            first = hit
            while (first > i and words[first]["start"] - words[first - 1]["end"] < self.GAP
                   and words[hit]["start"] - words[first - 1]["start"] <= self.LEAD):
                first -= 1
            last = self._run(words, hit_end)
            if first == hit and last == hit_end:  # el nombre solo: la pregunta viene después de la pausa
                if hit_end + 1 == len(words):
                    self.pending = words[hit]["start"]
                    break
                last = self._run(words, hit_end + 1)
            parts.append((words[first]["start"], words[last]["end"]))
            i = last + 1
        if not parts:
            return "wait" if self.pending is not None else None
        # varios pedidos pegados («Dani, el clima… Dani, poné un timer») van juntos, sin lo que se dijo en el medio
        gap = b"\0\0" * int(0.3 * self.rate)
        return gap.join(self.cut(a, b) for a, b in parts)

    def _run(self, words, i):
        """Hasta dónde sigue hablando sin pausa larga a partir de la palabra i."""
        while i + 1 < len(words) and words[i + 1]["start"] - words[i]["end"] < self.GAP:
            i += 1
        return i

    def cut(self, start, end=None):
        """El audio entre esos segundos (con un poco de margen para no comerse sílabas); sin `end`, hasta ahora."""
        a = max(0, int((start - self.PRE) * self.rate) - self.base)
        b = len(self.ring) // 2 if end is None else max(a, int((end + self.POST) * self.rate) - self.base)
        return bytes(self.ring[a * 2:b * 2])

    def mark(self):
        return self.total / self.rate


class WakeListener:
    """Micrófono siempre abierto (solo con --wake). Lo que escucha queda en memoria unos segundos y se descarta; al
    panel va únicamente la frase que tiene la palabra clave, o lo que se grabe con la tecla."""

    def __init__(self, args, session, beep, words):
        sd = need("sounddevice")
        vosk = need("vosk")
        vosk.SetLogLevel(-1)
        self.session, self.beep, self.device = session, beep, args.mic
        self.rate = int(sd.query_devices(args.mic, "input")["default_samplerate"])
        self.det = WakeDetector(vosk.Model(wake_model_dir()), self.rate, words)
        self.q, self.lock = queue.Queue(), threading.Lock()
        self.ptt_from, self.t0 = None, 0.0
        self.stream = sd.RawInputStream(samplerate=self.rate, channels=1, dtype="int16", device=args.mic,
                                        blocksize=self.rate // 10, callback=lambda data, *_: self.q.put(bytes(data)))
        threading.Thread(target=self._loop, daemon=True).start()
        self.stream.start()

    def set_words(self, words):
        with self.lock:
            self.det.keys = {sound(w) for w in words}

    def close(self):
        self.stream.stop()
        self.stream.close()
        self.q.put(None)

    def _loop(self):
        while True:
            chunk = self.q.get()
            if chunk is None:
                return
            p = self.session.player
            with self.lock:
                # su propia voz por los parlantes no la despierta
                self.det.muted = self.ptt_from is not None or p.playing or time.time() - p.quiet_since < 0.8
                got = self.det.feed(chunk)
            if got == "wait":
                self.session.interrupt()
                self.beep(880)
            elif got == "timeout":
                self.beep(660)
            elif got:
                self.beep(660)
                self.session.interrupt()
                self.session.send(to_wav(got, self.rate))

    # la tecla también graba de acá: el micrófono ya está abierto, así que arranca al instante
    def start(self):
        with self.lock:
            self.ptt_from, self.t0 = self.det.mark(), time.time()

    def stop(self):
        time.sleep(0.15)  # lo que todavía no llegó del micrófono
        with self.lock:
            pcm = self.det.cut(self.ptt_from - 0.1)  # con 0.35 s de antes de apretar
            self.ptt_from = None
        return pcm, time.time() - self.t0


# ---------- una charla ----------
class Session:
    def __init__(self, args, player):
        self.args, self.player = args, player
        self.cancel = threading.Event()
        self.conn = None
        self.thread = None

    def interrupt(self):
        """Hablarle encima: callar y dejar de leer la respuesta anterior."""
        self.player.stop()
        self.cancel.set()
        if self.conn is not None and self.conn.sock is not None:
            try:
                self.conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def send(self, wav_bytes, wait=False):
        self.cancel = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(wav_bytes, self.cancel), daemon=True)
        self.thread.start()
        if wait:
            wait_for(self.thread)

    def _run(self, wav_bytes, cancel):
        t0 = time.time()
        printed = False
        for ev in chat(self.args.server, wav_bytes, self.args.token, cancel, on_conn=self._set_conn):
            kind = ev.get("type")
            if kind == "heard":
                print(f"\nVos: {ev.get('text') or '(no se entendió)'}", flush=True)
            elif kind == "tool":
                print(f"  [{ev['name']}]", flush=True)
            elif kind == "text":
                if not printed:
                    print(f"{BUDDY['name']}: ", end="", flush=True)
                    printed = True
                print(ev["delta"], end="", flush=True)
            elif kind == "audio":
                self.player.play(base64.b64decode(ev["wav"]))
            elif kind == "error":
                print(f"\n[error] {ev['msg']}", flush=True)
            elif kind == "first_token" and self.args.verbose:
                print(f"  (primera palabra a los {ev['s']} s)", flush=True)
        if printed:
            print(f"   ({time.time() - t0:.1f} s)" if self.args.verbose else "", flush=True)

    def _set_conn(self, conn):
        self.conn = conn


# ---------- lo que dice por su cuenta ----------
ICONS = {"timer": "⏰", "alerta": "⚠️", "resumen": "☀️"}


def listen(args, player):
    """Queda suscripto a los avisos del panel (timers, alertas del homelab, el resumen de la mañana). Si el panel se
    reinicia o se corta la red, vuelve a conectarse solo."""
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    seen, backoff = set(), 1
    while True:
        conn = _conn(args.server, 60)  # el panel manda un "ping" cada 15 s
        try:
            conn.request("GET", f"/api/buddy/events?client={CLIENT}", headers=headers)
            resp = conn.getresponse()
            if resp.status == 401:
                print("[avisos] el panel pide contraseña: usá --token", flush=True)
                return
            for line in resp:
                backoff = 1
                if not line.startswith(b"data: "):
                    continue
                ev = json.loads(line[6:])
                if ev.get("type") != "say" or ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                print(f"\n{ICONS.get(ev['kind'], '💬')} {BUDDY['name']}: {ev['text']}", flush=True)
                if ev.get("speak"):
                    for w in ev.get("audio") or []:
                        player.play(base64.b64decode(w))
        except (OSError, ValueError):
            pass
        finally:
            conn.close()
        time.sleep(backoff)
        backoff = min(30, backoff * 2)


# ---------- tecla ----------
def parse_key(name):
    keyboard = need("pynput").keyboard
    name = name.strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        raise SystemExit(f"Tecla desconocida: {name} (probá f9, ctrl_r, alt_r, scroll_lock, pause o una letra)")


def wait_for(thread):
    """Esperar a un hilo de a ratos: en Windows, un join() sin tiempo no deja que llegue el Ctrl+C."""
    while thread.is_alive():
        thread.join(0.3)


class Mic:
    """Graba con la tecla y, si está prendido, escucha su nombre. Se prende y apaga en marcha (el ajuste del panel):
    apagado, el micrófono se abre solo mientras la tecla está apretada."""

    def __init__(self, args, session, beep):
        self.args, self.session, self.beep = args, session, beep
        self.plain = Recorder(args.mic)
        self.rate = self.plain.rate
        self.wake, self.active, self.words, self.warned = None, None, None, False
        self.lock = threading.Lock()

    def set_wake(self, words):
        """words: nombres a los que responde, o vacío para dejar de escuchar. La primera vez instala Vosk, baja su
        modelo y lo carga (hasta un minuto): eso va sin el candado, para que la tecla siga andando mientras tanto."""
        if words and self.wake is None:
            try:
                wake = WakeListener(self.args, self.session, self.beep, words)
            except Exception as e:  # noqa: BLE001  sin Vosk, sin red o sin micrófono: sigue con la tecla
                if not self.warned:
                    print(f"No puedo escuchar su nombre en esta PC ({e}); sigue andando con la tecla. "
                          "Lo vuelvo a intentar solo.", flush=True)
                    self.warned = True
                return
            with self.lock:
                self.wake, self.words, self.warned = wake, words, False
            print(f"Escuchando su nombre («{', '.join(words)}»). El micrófono queda abierto; lo que no lo nombra "
                  "no sale de esta PC.", flush=True)
            return
        with self.lock:
            if words and self.wake is not None and words != self.words:
                self.wake.set_words(words)
                print(f"Ahora responde a «{', '.join(words)}».", flush=True)
            elif not words and self.wake is not None:
                if self.active is self.wake:
                    return  # se está usando la tecla: se apaga en la próxima vuelta
                self.wake.close()
                self.wake = None
                print("Ya no escucha su nombre: el micrófono se abre solo con la tecla.", flush=True)
            self.words = words

    def start(self):
        with self.lock:
            self.active = self.wake or self.plain
        self.active.start()

    def stop(self):
        pcm, seconds = self.active.stop()
        self.active = None
        return pcm, seconds


def wake_words(args, settings):
    """A qué nombres responde: --no-wake nunca; --wake siempre (con el nombre del panel u otro); si no, el ajuste."""
    if args.no_wake:
        return []
    if args.wake:
        return [w.strip() for w in args.wake.split(",") if w.strip()]
    if args.wake == "" or settings["wake"]:
        return [settings["name"]]
    return []


def follow_panel(args, mic):
    """Cada POLL_S segundos: el nombre del compañero y si tiene que escuchar su nombre."""
    while True:
        try:
            got = panel_settings(args.server, args.token)
            if got:
                BUDDY.update(got)
            if mic is not None:
                mic.set_wake(wake_words(args, BUDDY))
        except Exception as e:  # noqa: BLE001  que un error no deje de mirar el panel
            print(f"[aviso] {e}", flush=True)
        time.sleep(POLL_S)


def run_hotkey(args, session):
    stage("cargando el teclado (pynput)")
    keyboard = need("pynput").keyboard
    key = parse_key(args.key)
    stage("abriendo la salida de audio para el pitido")
    beep = make_beeper(args)
    stage("buscando el micrófono")
    rec = Mic(args, session, beep)
    state = {"down": False}

    def matches(k):
        return k == key or (isinstance(k, keyboard.KeyCode) and isinstance(key, keyboard.KeyCode)
                            and (k.char or "").lower() == (key.char or "").lower())

    # pynput llama a estas funciones desde el hook de teclado de Windows: si tardan (abrir el micrófono son cientos de
    # ms), se traba el teclado de toda la PC. Solo anotan la tecla; el trabajo lo hace key_worker.
    keys = queue.Queue()

    def on_press(k):
        if not matches(k) or state["down"]:
            return  # la repetición automática del teclado manda la tecla muchas veces
        state["down"] = True
        keys.put("down")

    def on_release(k):
        if not matches(k) or not state["down"]:
            return
        state["down"] = False
        keys.put("up")

    def key_worker():
        recording = False
        while True:
            what = keys.get()
            try:
                if what == "down" and not recording:
                    session.interrupt()
                    # Primero el micrófono y después el pitido: abrir el micrófono (sobre todo en Windows) tarda
                    # cientos de ms y, con el pitido antes, se perdía el comienzo de la frase («hola, cómo estás» →
                    # «que hace»). Así, cuando suena, ya está grabando. El pitido sale al instante (Beeper).
                    rec.start()
                    recording = True
                    beep(880)
                elif what == "up" and recording:
                    recording = False
                    pcm, seconds = rec.stop()
                    beep(660)
                    if seconds >= MIN_SECONDS:
                        session.send(to_wav(pcm, rec.rate))
            except Exception as e:  # noqa: BLE001  un micrófono que no abre no tiene que matar la tecla
                recording = False
                print(f"[micrófono] {e}", flush=True)

    threading.Thread(target=key_worker, daemon=True).start()

    stage(f"conectando con el panel en {args.server}")
    got = panel_settings(args.server, args.token)  # el nombre, antes de mostrar nada
    if got:
        BUDDY.update(got)
    else:
        print(f"[aviso] no pude hablar con el panel en {args.server}; sigo intentando.", flush=True)
    threading.Thread(target=listen, args=(args, session.player), daemon=True).start()
    threading.Thread(target=follow_panel, args=(args, rec), daemon=True).start()
    stage("activando la tecla")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        stage(None)
        print(f"{BUDDY['name']} escucha mientras mantengas apretada la tecla «{args.key}». Ctrl+C para salir.",
              flush=True)
        wait_for(listener)


def env(name, default):
    """BUDDY_<name>; DANI_<name> es el nombre viejo (cuando el programa se llamaba dani.py)."""
    return os.environ.get(f"BUDDY_{name}", os.environ.get(f"DANI_{name}", default))


def main():
    ap = argparse.ArgumentParser(description="Hablale al compañero del panel con una tecla o con su nombre.")
    ap.add_argument("--server", default=env("SERVER", "http://god.local:8090"))
    ap.add_argument("--key", default=env("KEY", "ctrl_r"), help="tecla para hablar (ctrl_r, f9, …)")
    ap.add_argument("--token", default=env("TOKEN", ""), help="contraseña del panel, si tiene")
    ap.add_argument("--mic", type=int, default=None, help="número de micrófono (--list-devices)")
    ap.add_argument("--out", type=int, default=None, help="número de parlante (--list-devices)")
    ap.add_argument("--wake", nargs="?", const="", default=env("WAKE", None), metavar="NOMBRE",
                    help="escuchar siempre su nombre en esta PC aunque en el panel esté apagado (por defecto el nombre "
                    "del panel; otros separados por coma). Necesita: py -m pip install vosk")
    ap.add_argument("--no-wake", action="store_true", help="no escuchar su nombre en esta PC aunque el panel lo pida")
    ap.add_argument("--no-beep", action="store_true", help="sin pitido al empezar y terminar de grabar")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--file", help="mandar este WAV en vez de grabar (prueba)")
    ap.add_argument("--save", help="guardar las respuestas en esta carpeta en vez de reproducirlas (prueba)")
    ap.add_argument("--listen-only", action="store_true",
                    help="solo escuchar sus avisos (timers, alertas), sin tecla ni micrófono")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostrar tiempos")
    args = ap.parse_args()

    if args.list_devices:
        sd = need("sounddevice")
        print(sd.query_devices())
        return
    if args.save:
        os.makedirs(args.save, exist_ok=True)
    stage("arrancando")
    threading.Thread(target=watchdog, daemon=True).start()
    session = Session(args, Player(args.out, args.save))
    if args.file:
        stage(None)
        with open(args.file, "rb") as f:
            session.send(f.read(), wait=True)
        while not session.player.q.empty():
            time.sleep(0.1)
        time.sleep(0.3)
        return
    try:
        if args.listen_only:
            stage(None)
            threading.Thread(target=follow_panel, args=(args, None), daemon=True).start()
            print("Escuchando sus avisos. Ctrl+C para salir.", flush=True)
            t = threading.Thread(target=listen, args=(args, session.player), daemon=True)
            t.start()
            wait_for(t)  # leer del socket tampoco deja pasar el Ctrl+C
            return
        run_hotkey(args, session)
    except KeyboardInterrupt:
        print("\nChau.", flush=True)
        os._exit(0)  # sin esperar a los streams de audio, que en Windows a veces traban el cierre


if __name__ == "__main__":
    sys.exit(main())
