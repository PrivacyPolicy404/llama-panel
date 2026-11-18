"""Dani desde tu PC: mantené apretada una tecla en cualquier programa y hablale al compañero del panel.

Windows, Linux o macOS, con Python 3.9 o más nuevo (en Windows: winget install Python.Python.3.12). El panel sirve
este archivo en http://<máquina>:8090/clients/dani.py. En PowerShell:

    py -m pip install sounddevice pynput
    iwr http://god.local:8090/clients/dani.py -OutFile dani.py
    py dani.py --server http://god.local:8090              # tecla para hablar: Ctrl derecho
    python dani.py --key f9                                 # otra tecla (f1..f12, ctrl_r, alt_r, una letra…)
    python dani.py --list-devices                           # micrófonos y parlantes; elegir con --mic / --out

Mientras la tecla está apretada graba; al soltarla manda el audio al panel (Whisper lo pasa a texto), muestra lo que
entendió y la respuesta, y reproduce la voz de Dani oración por oración. Apretar de nuevo mientras habla la corta.

Para probar sin micrófono ni teclado: --file pregunta.wav (manda ese audio) y --save carpeta/ (guarda las respuestas
en vez de reproducirlas). Si el panel pide contraseña: --token o la variable DANI_TOKEN.
"""
import argparse
import base64
import http.client
import io
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.parse
import wave

MIN_SECONDS = 0.3
# Id de este programa: el panel le habla los avisos (timers, alertas) al cliente que se usó último
CLIENT = "pc-" + os.urandom(4).hex()


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
            import sounddevice as sd
            with wave.open(io.BytesIO(data)) as w:
                rate, ch, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
            with sd.RawOutputStream(samplerate=rate, channels=ch, dtype="int16", device=self.device) as out:
                step = rate // 10 * 2 * ch  # de a 100 ms, para poder cortar
                for i in range(0, len(frames), step):
                    if self.stopped.is_set():
                        break
                    out.write(frames[i:i + step])


def beep(device=None, freq=880, ms=70):
    """Aviso corto al empezar y terminar de grabar (no hay pantalla que mire)."""
    try:
        import math
        import sounddevice as sd
        rate = 22050
        n = int(rate * ms / 1000)
        pcm = b"".join(int(6000 * math.sin(2 * math.pi * freq * i / rate)).to_bytes(2, "little", signed=True)
                       for i in range(n))
        with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", device=device) as out:
            out.write(pcm)
    except Exception:  # noqa: BLE001
        pass


class Recorder:
    def __init__(self, device=None):
        import sounddevice as sd
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
            self.thread.join()

    def _run(self, wav_bytes, cancel):
        t0 = time.time()
        name, printed = "Dani", False
        for ev in chat(self.args.server, wav_bytes, self.args.token, cancel, on_conn=self._set_conn):
            kind = ev.get("type")
            if kind == "heard":
                print(f"\nVos: {ev.get('text') or '(no se entendió)'}", flush=True)
            elif kind == "tool":
                print(f"  [{ev['name']}]", flush=True)
            elif kind == "text":
                if not printed:
                    print(f"{name}: ", end="", flush=True)
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


# ---------- lo que Dani dice por su cuenta ----------
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
                print(f"\n{ICONS.get(ev['kind'], '💬')} Dani: {ev['text']}", flush=True)
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
    from pynput import keyboard
    name = name.strip().lower()
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        raise SystemExit(f"Tecla desconocida: {name} (probá f9, ctrl_r, alt_r, scroll_lock, pause o una letra)")


def run_hotkey(args, session):
    from pynput import keyboard
    key = parse_key(args.key)
    rec = Recorder(args.mic)
    state = {"down": False}

    def matches(k):
        return k == key or (isinstance(k, keyboard.KeyCode) and isinstance(key, keyboard.KeyCode)
                            and (k.char or "").lower() == (key.char or "").lower())

    def on_press(k):
        if not matches(k) or state["down"]:
            return  # la repetición automática del teclado manda la tecla muchas veces
        state["down"] = True
        session.interrupt()
        # Primero el micrófono y después el pitido, en paralelo: abrir los dos streams (sobre todo en Windows) tarda
        # cientos de ms y, con el pitido antes, se perdía el comienzo de la frase («hola, cómo estás» → «que hace»).
        # Así, cuando suena, ya está grabando.
        rec.start()
        if not args.no_beep:
            threading.Thread(target=beep, args=(args.out, 880), daemon=True).start()

    def on_release(k):
        if not matches(k) or not state["down"]:
            return
        state["down"] = False
        pcm, seconds = rec.stop()
        if not args.no_beep:
            beep(args.out, 660)
        if seconds < MIN_SECONDS:
            return
        session.send(to_wav(pcm, rec.rate))

    threading.Thread(target=listen, args=(args, session.player), daemon=True).start()
    print(f"Dani escucha mientras mantengas apretada la tecla «{args.key}». Ctrl+C para salir.", flush=True)
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def main():
    ap = argparse.ArgumentParser(description="Hablale a Dani (el compañero del panel) con una tecla.")
    ap.add_argument("--server", default=os.environ.get("DANI_SERVER", "http://god.local:8090"))
    ap.add_argument("--key", default=os.environ.get("DANI_KEY", "ctrl_r"), help="tecla para hablar (ctrl_r, f9, …)")
    ap.add_argument("--token", default=os.environ.get("DANI_TOKEN", ""), help="contraseña del panel, si tiene")
    ap.add_argument("--mic", type=int, default=None, help="número de micrófono (--list-devices)")
    ap.add_argument("--out", type=int, default=None, help="número de parlante (--list-devices)")
    ap.add_argument("--no-beep", action="store_true", help="sin pitido al empezar y terminar de grabar")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--file", help="mandar este WAV en vez de grabar (prueba)")
    ap.add_argument("--save", help="guardar las respuestas en esta carpeta en vez de reproducirlas (prueba)")
    ap.add_argument("--listen-only", action="store_true",
                    help="solo escuchar los avisos de Dani (timers, alertas), sin tecla ni micrófono")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostrar tiempos")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return
    if args.save:
        os.makedirs(args.save, exist_ok=True)
    session = Session(args, Player(args.out, args.save))
    if args.file:
        with open(args.file, "rb") as f:
            session.send(f.read(), wait=True)
        while not session.player.q.empty():
            time.sleep(0.1)
        time.sleep(0.3)
        return
    try:
        if args.listen_only:
            print("Escuchando los avisos de Dani. Ctrl+C para salir.", flush=True)
            listen(args, session.player)
            return
        run_hotkey(args, session)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
