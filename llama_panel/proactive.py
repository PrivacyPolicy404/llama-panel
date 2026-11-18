"""Dani habla sin que le pregunten: timers y recordatorios, avisos del homelab y un resumen a la mañana.

Los clientes (la interfaz web y clients/buddy.py) quedan suscriptos a GET /api/buddy/events (server-sent events). Un
mensaje llega a todos como texto, pero el audio va solo al cliente que se usó último (si están abiertos el navegador y
buddy.py, habla uno solo). Sin nadie conectado, los mensajes esperan (hasta 12 h) al próximo que se conecte.

- Timers: la herramienta avisar los crea con la frase que Dani va a decir, escrita en ese momento: al sonar no hace
  falta el modelo. Se guardan en BUDDY_DIR/avisos.json y sobreviven a los reinicios del panel.
- Avisos del homelab: temperatura de GPU y CPU, servicios caídos, RAM y disco. Cada uno salta al cruzar el umbral,
  se rearma recién al volver a un valor normal (histéresis) y no se repite antes de una hora.
- Resumen de la mañana a la hora de la configuración del compañero ("morning", vacía = nunca). Lo escribe el modelo
  solo si ya hay uno cargado y el compañero está libre: nunca prende el servidor ni apaga las imágenes por esto.
"""
import base64
import collections
import json
import queue
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config as C

PENDING_HOURS = 12
ALERT_COOLDOWN = 3600


# ---------- clientes suscriptos ----------
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = {}                  # id de suscripción -> {"client", "q"}
        self.active = {}                # cliente -> último uso (charla o conexión)
        self.pending = collections.deque(maxlen=20)

    def subscribe(self, client):
        sid = uuid.uuid4().hex
        q = queue.Queue()
        with self.lock:
            self.subs[sid] = {"client": client, "q": q}
            self.active.setdefault(client, time.time())
            cutoff = time.time() - PENDING_HOURS * 3600
            waiting = [m for m in self.pending if m["t"] > cutoff]
            self.pending.clear()
        for m in waiting:  # lo que pasó mientras no había nadie: este cliente lo dice
            q.put(self._event(m, speak=True))
        return sid, q

    def unsubscribe(self, sid):
        with self.lock:
            self.subs.pop(sid, None)

    def touch(self, client):
        if client:
            with self.lock:
                self.active[client] = time.time()

    def speaker(self):
        with self.lock:
            clients = {s["client"] for s in self.subs.values()}
            return max(clients, key=lambda c: self.active.get(c, 0)) if clients else None

    def clients(self):
        with self.lock:
            return sorted({s["client"] for s in self.subs.values()})

    @staticmethod
    def _event(m, speak):
        ev = {"type": "say", "id": m["id"], "t": m["t"], "kind": m["kind"], "text": m["text"], "speak": speak}
        if speak:
            ev["audio"] = m.get("audio") or synth(m["text"])
        return ev

    def publish(self, msg):
        with self.lock:
            subs = list(self.subs.values())
        if not subs:
            self.pending.append(msg)
            return 0
        speaker = self.speaker()
        audio = None
        for s in subs:
            speak = s["client"] == speaker
            if speak and audio is None:
                audio = synth(msg["text"])
            ev = self._event({**msg, "audio": audio}, speak) if speak else self._event(msg, False)
            s["q"].put(ev)
        return len(subs)

    def broadcast(self, event):
        """Evento de estado sin audio (por ejemplo, cambió la conversación activa)."""
        with self.lock:
            subs = list(self.subs.values())
        for sub in subs:
            sub["q"].put(event)
        return len(subs)


HUB = Hub()


def synth(text):
    """Audio de un mensaje (lista de WAV en base64, uno por oración), o [] si no hay voz."""
    from . import voice
    if not voice.Speaker.available():
        return []
    sentences, rest = voice.split_sentences(text, final=True)
    out = []
    for s in sentences + ([rest] if rest else []):
        try:
            wav = voice.SPEAKER.say(s)
        except Exception:  # noqa: BLE001
            return out
        if wav:
            out.append(base64.b64encode(wav).decode())
    return out


def say(text, kind="aviso"):
    """Que Dani diga algo por su cuenta: a los clientes conectados y al historial de la charla."""
    text = " ".join(str(text).split())
    if not text:
        return 0
    from . import buddy
    buddy.add_spontaneous(text)
    print(f"Dani ({kind}): {text}", flush=True)
    return HUB.publish({"id": uuid.uuid4().hex[:8], "t": time.time(), "kind": kind, "text": text})


def events(q):
    """GET /api/buddy/events: suscripción de un cliente. Manda un "ping" cada 15 s (así se nota si se fue)."""
    client = (q.get("client", [""])[0] or uuid.uuid4().hex)[:64]
    sid, inbox = HUB.subscribe(client)
    try:
        yield {"type": "hello", "client": client}
        while True:
            try:
                yield inbox.get(timeout=15)
            except queue.Empty:
                yield {"type": "ping"}
    finally:
        HUB.unsubscribe(sid)


# ---------- timers ----------
_tlock = threading.Lock()


def _timers_file():
    return C.BUDDY_DIR / "avisos.json"


def timers():
    try:
        return json.loads(_timers_file().read_text())
    except (OSError, ValueError):
        return []


def _save_timers(items):
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _timers_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False))
    tmp.replace(_timers_file())


def when(minutes=None, at=None, now=None):
    """Momento (timestamp) de un aviso: en `minutes` minutos o a la hora `at` ("HH:MM"; si ya pasó, mañana)."""
    now = now or time.time()
    if minutes is not None:
        m = float(minutes)
        if not 0 < m <= 7 * 24 * 60:
            raise ValueError("los minutos van de más de 0 a una semana")
        return now + m * 60
    if at:
        h, _, mi = str(at).strip().partition(":")
        target = datetime.fromtimestamp(now).replace(hour=int(h), minute=int(mi or 0), second=0, microsecond=0)
        if target.timestamp() <= now:
            target += timedelta(days=1)
        return target.timestamp()
    raise ValueError("falta cuándo: minutos o una hora HH:MM")


def add_timer(text, minutes=None, at=None):
    t = when(minutes, at)
    item = {"id": uuid.uuid4().hex[:6], "at": round(t), "text": " ".join(str(text).split())[:300],
            "created": round(time.time())}
    if not item["text"]:
        raise ValueError("falta qué decir")
    with _tlock:
        items = timers()
        if len(items) >= 50:
            raise ValueError("ya hay 50 avisos pendientes")
        items.append(item)
        items.sort(key=lambda x: x["at"])
        _save_timers(items)
    return item


def cancel_timer(match):
    """Cancela los avisos con ese id o que contengan ese texto. Devuelve cuántos."""
    match = str(match).strip().lower()
    if not match:
        return 0
    with _tlock:
        items = timers()
        keep = [x for x in items if x["id"] != match and match not in x["text"].lower()]
        _save_timers(keep)
    return len(items) - len(keep)


def due_timers(now=None):
    """Saca y devuelve los avisos que ya tocan."""
    now = now or time.time()
    with _tlock:
        items = timers()
        due = [x for x in items if x["at"] <= now]
        if due:
            _save_timers([x for x in items if x["at"] > now])
    return due


# ---------- avisos del homelab ----------
# (clave, umbral para saltar, umbral para rearmarse): temperaturas en °C, RAM libre en GB, disco libre en %
LIMITS = {"gpu_temp": (85, 78), "cpu_temp": (90, 80), "ram_free_gb": (3, 6), "disk_free_pct": (5, 8)}
SERVICES = {C.SERVICE: "el servidor de texto", C.IMG_SERVICE: "el generador de imágenes",
            "homeassistant": "Home Assistant"}


class Alerts:
    """Decide qué avisar a partir de las mediciones; sin efectos, para poder probarlo."""

    def __init__(self):
        self.armed = {}       # clave -> False mientras esté avisado y no haya vuelto a lo normal
        self.last = {}        # clave -> último aviso
        self.svc = {}         # servicio -> último estado

    def _cross(self, key, value, high_is_bad, now):
        trip, reset = LIMITS[key.split(":")[0]]
        bad = value >= trip if high_is_bad else value <= trip
        ok = value < reset if high_is_bad else value > reset
        if ok:
            self.armed[key] = True
        last = self.last.get(key)
        if bad and self.armed.get(key, True) and (last is None or now - last >= ALERT_COOLDOWN):
            self.armed[key], self.last[key] = False, now
            return True
        return False

    def check(self, hw, services, disks, now=None):
        now = now or time.time()
        out = []
        if hw.get("temp") is not None and self._cross("gpu_temp", hw["temp"], True, now):
            out.append(f"Ojo, la GPU está a {hw['temp']:.0f} grados. Si sigue subiendo, conviene darle un respiro.")
        if hw.get("cpu_temp") is not None and self._cross("cpu_temp", hw["cpu_temp"], True, now):
            out.append(f"El procesador está a {hw['cpu_temp']:.0f} grados, bastante más caliente de lo normal.")
        if hw.get("ram_total") and hw.get("ram_used") is not None:
            free = hw["ram_total"] - hw["ram_used"]
            if self._cross("ram_free_gb", free, False, now):
                out.append(f"Me estoy quedando sin RAM: quedan {free:.1f} GB libres.")
        for mount, pct, free_gb in disks:
            if self._cross(f"disk_free_pct:{mount}", pct, False, now):
                out.append(f"El disco de {mount} está casi lleno: quedan {free_gb:.0f} GB.")
        for name, state in services.items():
            before = self.svc.get(name)
            self.svc[name] = state
            if state == "failed" and before not in (None, "failed"):
                out.append(f"Se cayó {SERVICES.get(name, name)}. Fijate en la pestaña Registros del panel.")
        return out


def _disks():
    seen, out = set(), []
    for path in ["/", *map(str, C.MODELS_DIRS)]:
        try:
            u = shutil.disk_usage(path)
        except OSError:
            continue
        if (u.total, u.used) in seen:
            continue
        seen.add((u.total, u.used))
        out.append((path, u.free * 100 / u.total, u.free / 1e9))
    return out


def _services():
    from .services import run
    names = list(SERVICES)
    states = run("systemctl", "--user", "is-active", *names).splitlines()
    return {n: s for n, s in zip(names, states)}


# ---------- resumen de la mañana ----------
def _state_file():
    return C.BUDDY_DIR / "estado.json"


def _read_state():
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _write_state(data):
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(data))


def morning_due(morning, last_day, now=None):
    """¿Toca el resumen? Desde la hora configurada y durante 6 h, una vez por día."""
    if not morning:
        return False
    now = datetime.fromtimestamp(now or time.time())
    h, _, m = morning.partition(":")
    start = now.replace(hour=int(h), minute=int(m or 0), second=0, microsecond=0)
    return start <= now < start + timedelta(hours=6) and last_day != now.strftime("%Y-%m-%d")


def morning_summary():
    from . import buddy, buddy_tools as BT
    today = [x for x in timers() if datetime.fromtimestamp(x["at"]).date() == datetime.now().date()]
    pend = "; ".join(f"a las {datetime.fromtimestamp(x['at']):%H:%M}: {x['text']}" for x in today) or "ninguno"
    text = buddy.monologue(
        "Es la mañana y el usuario recién llega. Saludalo con buena onda y contá en dos o tres oraciones cortas cómo "
        f"está el homelab (mirá estado_homelab) y los avisos que hay para hoy: {pend}.")
    if not text:  # sin modelo cargado o el compañero ocupado: resumen armado con los datos
        hw = json.loads(BT.run("estado_homelab", "{}")[0])
        gpu = hw["gpu"]
        text = (f"Buen día. Son las {datetime.now():%H:%M}. La GPU está a {gpu['temperatura_c']:.0f} grados y la "
                f"RAM en {hw['ram_gb']} GB. " + ("No hay avisos para hoy." if not today else f"Para hoy: {pend}."))
    say(text, "resumen")


# ---------- el hilo ----------
ALERTS = Alerts()


def loop(interval=5):
    from . import buddy, hardware
    tick = 0
    while True:
        time.sleep(interval)
        tick += 1
        try:
            for item in due_timers():
                say(item["text"], "timer")
            if tick % 6:  # lo demás, cada ~30 s
                continue
            cfg = buddy.load_config()
            if cfg.get("alerts", True):
                for text in ALERTS.check(hardware.latest() or {}, _services(), _disks()):
                    say(text, "alerta")
            st = _read_state()
            if morning_due(cfg.get("morning", ""), st.get("morning_day")):
                st["morning_day"] = datetime.now().strftime("%Y-%m-%d")
                _write_state(st)
                morning_summary()
        except Exception as e:  # noqa: BLE001
            print(f"avisos de Dani: {e}", flush=True)


def start():
    threading.Thread(target=loop, daemon=True).start()
