"""El oído del compañero: que Whisper entienda los nombres de esta casa, sin que nadie cargue nada a mano.

- Vocabulario automático (`--prompt` de Whisper): el nombre del compañero, la zona del usuario, los lugares que
  consultó para el clima, los dispositivos de Home Assistant, los nombres propios de la memoria y lo aprendido. Se
  arma en cada pedido con lo que haya, así que sigue solo a sus fuentes. Con voces de Piper ensuciadas, small pasa de
  29 % a 71 % de nombres bien escritos (tools/bench/whisper_vocab.py).
- Correcciones aprendidas del propio modelo: si Whisper escribió «mano de plata» y el modelo igual entendió y llamó a
  clima("Mar del Plata"), el par se anota cuando suenan parecido (clave fonética del castellano). Con dos veces se
  aplica solo a lo que transcribe Whisper. Solo nombres propios (lo que el modelo escribe con mayúscula, o con
  números o guiones): «partido de fútbol» no se «corrige» por el «partidos de fútbol» de una búsqueda. Las que el
  usuario borra no se vuelven a aprender.
- Últimos audios (BUDDY_DIR/oido/audios, 50, solo en esta máquina) y la comparación con y sin vocabulario sobre la
  voz real, que corre sola cada 20 audios nuevos (con nice, en el CPU) o a pedido.
"""
import json
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from . import config as C

LEARN_AFTER = 2          # veces que hay que ver un par para aplicarlo
KEEP_AUDIOS = 50
COMPARE_EVERY = 20       # audios nuevos para volver a comparar solo
MAX_TERMS = 40
MAX_PROMPT = 400         # caracteres: Whisper acepta hasta la mitad de su contexto de texto (224 tokens)
SIMILAR = 0.75
CONNECTORS = {"de", "del", "la", "las", "los", "el", "y", "da", "do"}
STOP = CONNECTORS | {"en", "a", "al", "con", "por", "para", "que", "un", "una", "me", "mi", "te", "lo", "le", "se",
                     "es", "no", "si", "como", "cual", "cuanto", "hoy", "ya"}
ACRONYM = re.compile(r"^[A-Z]{1,4}$")  # GB, RAM, CPU: Whisper ya las conoce y gastan lugar
UNIT = re.compile(r"^\d+([.,]\d+)?\s*°?\s*[A-Za-z]{1,3}$")  # 8 GB, 20 °C: medidas, no nombres
LEARN_FROM = {"clima", "buscar_web", "recordar", "avisar", "casa_controlar"}
_lock = threading.Lock()
_ha_cache = {"t": 0, "names": []}
_compare = {"running": False}


def folder():
    return C.BUDDY_DIR / "oido"


# ---------- comparar cómo suenan ----------
def norm(s):
    s = unicodedata.normalize("NFKD", str(s).lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def sound_key(s):
    """Clave fonética simple del castellano rioplatense: lo que se escribe distinto pero suena igual da lo mismo
    (b/v, s/z/c, y/ll, h muda, qu/k...). Sin espacios: «Chás Comús» y «Chascomús» suenan igual."""
    n = norm(s).replace(" ", "")
    for a, b in (("ch", "ç"), ("qu", "k"), ("ll", "y"), ("x", "ks"), ("w", "u"), ("v", "b"), ("z", "s")):
        n = n.replace(a, b)
    n = re.sub(r"c(?=[ei])", "s", n).replace("c", "k")
    n = re.sub(r"g(?=[ei])", "j", n).replace("h", "")
    return re.sub(r"(.)\1+", r"\1", n)


def similarity(a, b):
    return SequenceMatcher(None, sound_key(a), sound_key(b)).ratio()


def proper_names(text):
    """Tramos con mayúscula (con conectores en el medio: «Mar del Plata») o con números/guiones («RX 580»)."""
    out, run = [], []

    def close():
        while run and run[-1].lower() in CONNECTORS:
            run.pop()
        if run:
            out.append(" ".join(run))
        run.clear()

    for chunk in re.split(r"[,;:!?¿¡()\n]|\.(?:\s|$)", str(text)):  # la puntuación corta los nombres
        for w in re.findall(r"[\w\-.]+", chunk):
            w = w.strip(".-")
            if not w:
                continue
            if w.isdigit():  # un número sigue a un nombre pegado («RX 580»), no después de «de» («8 GB»)
                if run and run[-1].lower() not in CONNECTORS:
                    run.append(w)
                else:
                    close()
                continue
            special = w[0].isupper() or any(ch.isdigit() for ch in w) or "-" in w
            if special:
                run.append(w)
            elif run and w.lower() in CONNECTORS:
                run.append(w)
            else:
                close()
        close()
    return out


# ---------- correcciones ----------
def _file():
    return folder() / "correcciones.json"


def _load():
    try:
        data = json.loads(_file().read_text())
    except (OSError, ValueError):
        data = {}
    return {"items": [x for x in data.get("items", []) if isinstance(x, dict) and x.get("from") and x.get("to")],
            "blocked": [b for b in data.get("blocked", []) if isinstance(b, str)]}


def _save(data):
    folder().mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(_file())


def corrections():
    """(aplicadas, en espera). Cada una: {"from", "to", "count", "last"}."""
    data = _load()
    on = [x for x in data["items"] if x["count"] >= LEARN_AFTER]
    return on, [x for x in data["items"] if x["count"] < LEARN_AFTER]


def _pattern(phrase):
    words = phrase.split()
    return re.compile(r"(?<!\w)" + r"[\s,.]+".join(re.escape(w) for w in words) + r"(?!\w)", re.I)


def apply(text):
    """Lo que transcribió Whisper, con las correcciones aprendidas."""
    for x in sorted(corrections()[0], key=lambda x: -len(x["from"])):
        text = _pattern(x["from"]).sub(x["to"], text)
    return text


def _best_window(tokens, target):
    """El tramo de la transcripción que más se parece a `target` (mismo largo en palabras, ±1)."""
    n = len(target.split())
    best = (0.0, None)
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(len(tokens) - size + 1):
            window = tokens[i:i + size]
            if window[0].lower() in STOP or window[-1].lower() in STOP:
                continue
            phrase = " ".join(window)
            score = similarity(phrase, target)
            if score > best[0]:
                best = (score, phrase)
    return best


def learn(transcript, tool, arguments):
    """Mira una llamada que hizo el modelo después de un mensaje por voz. Si un nombre propio de sus argumentos no
    está en la transcripción pero algo suena parecido, anota el par. Devuelve el par si quedó aplicado ahora."""
    if tool not in LEARN_FROM:
        return None
    try:
        args = json.loads(arguments or "{}") if isinstance(arguments, str) else (arguments or {})
    except ValueError:
        return None
    values = [v for v in args.values() if isinstance(v, str)] if isinstance(args, dict) else []
    tokens = re.findall(r"[\w\-]+", transcript)
    heard = f" {norm(transcript)} "
    learned = None
    for value in values:
        for target in proper_names(value):
            if len(norm(target)) < 4 or f" {norm(target)} " in heard:
                continue  # ya lo entendió bien
            score, phrase = _best_window(tokens, target)
            if not phrase or score < SIMILAR or norm(phrase) == norm(target) or len(norm(phrase)) < 4:
                continue
            learned = _note(phrase, target) or learned
    return learned


def _note(heard, target):
    with _lock:
        data = _load()
        key = norm(heard)
        if key in data["blocked"]:
            return None
        for x in data["items"]:
            if norm(x["from"]) == key and x["to"] == target:
                x["count"] += 1
                x["last"] = round(time.time())
                _save(data)
                return x if x["count"] == LEARN_AFTER else None
        data["items"].append({"from": heard, "to": target, "count": 1, "last": round(time.time())})
        _save(data)
        return None


def forget(heard):
    """Borra una corrección y no la vuelve a aprender."""
    with _lock:
        data = _load()
        key = norm(heard)
        before = len(data["items"])
        data["items"] = [x for x in data["items"] if norm(x["from"]) != key]
        if key not in data["blocked"]:
            data["blocked"].append(key)
        _save(data)
        return before != len(data["items"])


# ---------- vocabulario ----------
def _ha_names():
    from . import homeassistant as HA
    from .buddy_tools import HA_CONTROL
    if time.time() - _ha_cache["t"] < 600:
        return _ha_cache["names"]
    names = []
    try:
        if HA.configured():
            names = [(e.get("attributes") or {}).get("friendly_name") for e in HA.states()
                     if e["entity_id"].split(".")[0] in HA_CONTROL]
    except Exception:  # noqa: BLE001
        names = []
    _ha_cache.update(t=time.time(), names=[n for n in names if n][:20])
    return _ha_cache["names"]


def _weather_places(limit=8):
    """Lugares que el modelo consultó para el clima (del registro de herramientas), los más recientes primero."""
    try:
        lines = (C.BUDDY_DIR / "herramientas.log").read_text().splitlines()[-400:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
            # lo que interpretó el modelo, aunque haya fallado la red; no si el lugar no existe
            if e.get("tool") == "clima" and not str(e.get("result", "")).startswith("No encontré"):
                place = (json.loads(e.get("args") or "{}").get("ubicacion") or "").split(",")[0].strip()
                if place and place not in out:
                    out.append(place)
        except (ValueError, AttributeError):
            continue
        if len(out) >= limit:
            break
    return out


def vocabulary(cfg, place=None):
    """Lista de términos, de más a menos importante, sin repetidos ni pasarse del largo que acepta Whisper."""
    from . import buddy_tools as BT
    memory = []
    for line in BT.read_memory().splitlines():
        words = line.lstrip("-* ").split(" ", 1)
        memory += proper_names(words[1]) if len(words) > 1 else []
    on, _ = corrections()
    candidates = [cfg.get("name") or "", place or ""] + [x["to"] for x in on] + _weather_places() + _ha_names() + memory
    out, seen, size = [], set(), 0
    for term in candidates:
        term = " ".join(str(term).split())
        if len(term) < 2 or UNIT.match(term) or ACRONYM.match(term) or norm(term) in seen:
            continue
        if len(out) >= MAX_TERMS or size + len(term) + 2 > MAX_PROMPT:
            break
        seen.add(norm(term))
        out.append(term)
        size += len(term) + 2
    return out


def prompt(cfg, place=None):
    terms = vocabulary(cfg, place)
    return ", ".join(terms) + "." if terms else None


# ---------- últimos audios ----------
def audios_dir():
    return folder() / "audios"


def new_audio_path():
    return audios_dir() / f"{time.time():.3f}.wav"


def record(wav_path, raw, text, used_prompt):
    """Anota la transcripción junto al audio y deja solo los últimos KEEP_AUDIOS. Cada COMPARE_EVERY nuevos, compara."""
    wav_path = Path(wav_path)
    if not wav_path.is_file():
        return
    wav_path.with_suffix(".json").write_text(json.dumps(
        {"t": round(time.time()), "raw": raw, "text": text, "prompt": used_prompt}, ensure_ascii=False))
    wavs = sorted(audios_dir().glob("*.wav"))
    for old in wavs[:-KEEP_AUDIOS]:
        old.unlink(missing_ok=True)
        old.with_suffix(".json").unlink(missing_ok=True)
    last = _last_compare()
    if len(audios()) - last.get("audios_at", 0) >= COMPARE_EVERY or (len(wavs) >= COMPARE_EVERY and not last):
        compare_async()


def audios():
    out = []
    for wav in sorted(audios_dir().glob("*.wav")):
        try:
            meta = json.loads(wav.with_suffix(".json").read_text())
        except (OSError, ValueError):
            meta = {}
        out.append({"path": wav, **meta})
    return out


def delete_audios():
    n = 0
    for f in list(audios_dir().glob("*.wav")) + list(audios_dir().glob("*.json")):
        f.unlink(missing_ok=True)
        n += f.suffix == ".wav"
    (folder() / "comparacion.json").unlink(missing_ok=True)
    return n


# ---------- comparar con y sin vocabulario sobre la voz real ----------
def _last_compare():
    try:
        return json.loads((folder() / "comparacion.json").read_text())
    except (OSError, ValueError):
        return {}


def compare(cfg, limit=COMPARE_EVERY, nice=True):
    """Vuelve a transcribir los últimos audios sin vocabulario y con el de ahora. Sin una transcripción de referencia
    no hay un «bien» absoluto: cuenta en cuántos cambia, cuántos términos del vocabulario aparece en cada versión, y
    guarda los pares para mirarlos."""
    from . import voice
    items = audios()[-limit:]
    terms = vocabulary(cfg)
    vocab_prompt = ", ".join(terms) + "." if terms else None
    rows = []
    for a in items:
        audio = a["path"].read_bytes()
        without, _ = voice.transcribe(audio, prompt=None, nice=nice)
        with_vocab, _ = voice.transcribe(audio, prompt=vocab_prompt, nice=nice)
        hits = lambda text: sum(f" {norm(t)} " in f" {norm(text)} " for t in terms)  # noqa: E731
        rows.append({"t": a.get("t"), "sin": without, "con": with_vocab, "terms_sin": hits(without),
                     "terms_con": hits(with_vocab)})
    result = {"t": round(time.time()), "audios_at": len(audios()), "n": len(rows), "prompt": vocab_prompt,
              "changed": sum(norm(r["sin"]) != norm(r["con"]) for r in rows),
              "terms_sin": sum(r["terms_sin"] for r in rows), "terms_con": sum(r["terms_con"] for r in rows),
              "rows": rows}
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / "comparacion.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def compare_async(cfg=None):
    if _compare["running"]:
        return False

    def work():
        from . import buddy
        try:
            compare(cfg or buddy.load_config())
        except Exception as e:  # noqa: BLE001
            print(f"oído, comparar: {e}", flush=True)
        finally:
            _compare["running"] = False

    _compare["running"] = True
    threading.Thread(target=work, daemon=True).start()
    return True


def state(cfg):
    on, waiting = corrections()
    last = _last_compare()
    return {"vocabulary": vocabulary(cfg), "corrections": on, "pending": waiting, "audios": len(audios()),
            "keep_audio": bool(cfg.get("keep_audio", True)), "comparing": _compare["running"],
            "compare": {k: last.get(k) for k in ("t", "n", "changed", "terms_sin", "terms_con")} if last else None,
            "compare_rows": [r for r in last.get("rows", []) if norm(r["sin"]) != norm(r["con"])][-8:]}
