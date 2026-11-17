"""Compañero: un agente con personalidad, memoria, herramientas y voz que usa el modelo de texto cargado.

Una charla: el usuario manda texto o audio (Whisper lo pasa a texto), el modelo contesta en streaming, cada oración
completa va a Piper mientras se genera la siguiente, y si el modelo pide herramientas (buddy_tools) se ejecutan y se
le devuelve el resultado. La interfaz recibe eventos: lo que se escuchó, texto, herramientas, audio y el final.

Todo vive en BUDDY_DIR: config.json, memoria.md, herramientas.log y conversations/ (transcripciones e historial).
La conversación activa es compartida por la web y clients/dani.py y se restaura al reiniciar el panel.
"""
import http.client
import json
import queue
import re
import threading
import time
import uuid

from . import buddy_tools as BT
from . import config as C
from . import voice
from .i18n import _

MAX_STEPS = 5       # rondas de herramientas por mensaje
MAX_TURNS = 12      # mensajes del usuario que se mandan como historial
MAX_TOKENS = 600

DEFAULT_PERSONA = (
    "Sos {name}, un compañero de IA que vive en el homelab de tu usuario: una PC con una RX 580 que corre modelos "
    "de lenguaje e imágenes. Hablás en castellano rioplatense, con humor y buena onda, como un amigo que sabe de "
    "tecnología. Lo que decís se lee en voz alta: contestá corto (una a tres oraciones), sin listas, sin markdown y "
    "sin emojis, salvo que te pidan algo largo. Si no sabés algo, decilo. Para datos reales (cómo está la máquina, "
    "la hora, la casa) usá primero las herramientas y recién después contestá: no adelantes nada antes de mirar. Usá recordar solo para datos duraderos que el usuario "
    "te cuente de sí mismo, su casa o sus planes, o cuando te pida que te acuerdes de algo; nunca para lo que te pide "
    "en el momento ni para la charla."
)
# Se guardan las herramientas apagadas (no las prendidas): una herramienta nueva queda disponible sin tocar nada
DEFAULT_CONFIG = {"name": "Dani", "persona": DEFAULT_PERSONA, "model": "", "speak": True, "tools_off": [],
                  "alerts": True, "morning": "09:00"}  # avisos del homelab y hora del resumen (proactive.py)
MORNING_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

_history = []
_spontaneous = []  # lo que Dani dijo por su cuenta mientras contestaba otra cosa: entra al historial después
_session = {"memory": None}  # memoria leída al empezar la charla (ver system_prompt)
_busy = threading.Lock()
_conversation_lock = threading.Lock()
_conversation_state = {"root": None, "active": None, "items": []}


# ---------- conversaciones persistentes ----------
def _conversations_dir():
    return C.BUDDY_DIR / "conversations"


def _index_file():
    return C.BUDDY_DIR / "conversations.json"


def _new_item():
    now = round(time.time(), 3)
    return {"id": uuid.uuid4().hex[:12], "title": "Charla nueva", "created_at": now, "updated_at": now}


def _valid_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{12}", value))


def _ensure_conversation():
    """Carga una vez por BUDDY_DIR (los tests lo cambian) la conversación activa, o crea la primera."""
    root = str(C.BUDDY_DIR)
    if _conversation_state["root"] == root and _conversation_state["active"]:
        return
    with _conversation_lock:
        if _conversation_state["root"] == root and _conversation_state["active"]:
            return
        try:
            data = json.loads(_index_file().read_text())
        except (OSError, ValueError):
            data = {}
        items = [x for x in data.get("items", []) if isinstance(x, dict) and _valid_id(x.get("id"))]
        active = data.get("active") if _valid_id(data.get("active")) else None
        if not items:
            items = [_new_item()]
        if active not in {x["id"] for x in items}:
            active = items[0]["id"]
        _conversation_state.update(root=root, active=active, items=items)
        _history.clear()
        _history.extend(_read_conversation(active))
        _write_index()


def _read_conversation(cid):
    try:
        data = json.loads((_conversations_dir() / f"{cid}.json").read_text())
        messages = data.get("messages", [])
        return messages if isinstance(messages, list) else []
    except (OSError, ValueError):
        return []


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _write_index():
    _atomic_json(_index_file(), {"active": _conversation_state["active"],
                                 "items": _conversation_state["items"]})


def _safe_messages(messages):
    """Copia persistible: nunca audio ni el resultado del contexto efímero del navegador."""
    private_ids = set()
    out = []
    for msg in messages:
        clean = {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "tool_call_id", "spont", "source")}
        calls = clean.get("tool_calls") or []
        for call in calls:
            if (call.get("function") or {}).get("name") == "contexto_usuario":
                private_ids.add(call.get("id"))
        if clean.get("role") == "tool" and clean.get("tool_call_id") in private_ids:
            clean["content"] = "Contexto efímero no guardado."
        elif clean.get("role") == "tool" and isinstance(clean.get("content"), str):
            clean["content"] = clean["content"][:4000]
        out.append(clean)
    return out


def _save_conversation():
    _ensure_conversation()
    cid = _conversation_state["active"]
    now = round(time.time(), 3)
    users = [m for m in _history if m.get("role") == "user" and m.get("content")]
    for item in _conversation_state["items"]:
        if item["id"] == cid:
            item["updated_at"] = now
            if users and item.get("title") == "Charla nueva":
                item["title"] = " ".join(users[0]["content"].split())[:70]
            break
    _atomic_json(_conversations_dir() / f"{cid}.json", {"id": cid, "messages": _safe_messages(_history)})
    _conversation_state["items"].sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    _write_index()


def _broadcast_conversation():
    from . import proactive
    proactive.HUB.broadcast({"type": "conversation", "conversation_id": _conversation_state["active"]})


def conversation_list():
    _ensure_conversation()
    return [{k: x.get(k) for k in ("id", "title", "created_at", "updated_at")}
            for x in _conversation_state["items"]]


def use_conversation(cid):
    _ensure_conversation()
    if _busy.locked():
        return False, _("El compañero está ocupado.")
    if cid not in {x["id"] for x in _conversation_state["items"]}:
        return False, _("No existe esa conversación.")
    _save_conversation()
    _conversation_state["active"] = cid
    _history.clear()
    _history.extend(_read_conversation(cid))
    _session["memory"] = None
    _write_index()
    _broadcast_conversation()
    return True, _("Conversación abierta.")


def delete_conversation(cid):
    _ensure_conversation()
    if _busy.locked():
        return False, _("El compañero está ocupado.")
    items = [x for x in _conversation_state["items"] if x["id"] != cid]
    if len(items) == len(_conversation_state["items"]):
        return False, _("No existe esa conversación.")
    try:
        (_conversations_dir() / f"{cid}.json").unlink()
    except OSError:
        pass
    _conversation_state["items"] = items or [_new_item()]
    if cid == _conversation_state["active"]:
        _conversation_state["active"] = _conversation_state["items"][0]["id"]
        _history.clear()
        _history.extend(_read_conversation(_conversation_state["active"]))
        _session["memory"] = None
    _write_index()
    _broadcast_conversation()
    return True, _("Conversación eliminada.")


# ---------- configuración ----------
def load_config():
    try:
        data = json.loads((C.BUDDY_DIR / "config.json").read_text())
    except (OSError, ValueError):
        data = {}
    cfg = {**DEFAULT_CONFIG, **data}
    cfg["persona"] = cfg["persona"] or DEFAULT_PERSONA  # vacía = la de fábrica (que puede mejorar con el tiempo)
    cfg.pop("tools", None)
    cfg["tools"] = [t for t in BT.TOOLS if t not in cfg["tools_off"]]
    return cfg


def save_config(body):
    cfg = load_config()
    name = " ".join(str(body.get("name", cfg["name"])).split())[:40]
    if not name:
        return False, _("El compañero necesita un nombre.")
    persona = str(body.get("persona", cfg["persona"])).strip()[:6000] or DEFAULT_PERSONA
    tools = body.get("tools", cfg["tools"])
    if not isinstance(tools, list) or any(t not in BT.TOOLS for t in tools):
        return False, _("Herramienta desconocida.")
    morning = str(body.get("morning", cfg["morning"]) or "").strip()
    if morning and not MORNING_RE.match(morning):
        return False, _("La hora del resumen va como HH:MM (vacía = nunca).")
    cfg.update(name=name, persona=persona, model=str(body.get("model", cfg["model"])).strip(),
               speak=bool(body.get("speak", cfg["speak"])), tools_off=sorted(set(BT.TOOLS) - set(tools)),
               alerts=bool(body.get("alerts", cfg["alerts"])), morning=morning)
    cfg.pop("tools")
    if cfg["persona"] == DEFAULT_PERSONA:
        cfg["persona"] = ""  # sin editar: no congelar una copia de la personalidad de fábrica
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = C.BUDDY_DIR / "config.tmp"
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    tmp.replace(C.BUDDY_DIR / "config.json")
    return True, _("Compañero guardado.")


def system_prompt(cfg, memory):
    """La personalidad y la memoria. No lleva la hora ni nada que cambie seguido: así el servidor reutiliza el
    prompt ya procesado entre mensajes. La memoria se lee al empezar la charla y queda fija hasta la próxima (lo que
    se anota en el medio ya está en el historial): si cambiara, el servidor reprocesaría todo el prompt, ~1200
    tokens con las herramientas, que en Flash-Next son ~20 s."""
    text = cfg["persona"].replace("{name}", cfg["name"])
    if memory:
        text += "\n\nLo que recordás de charlas anteriores:\n" + memory
    return text


def trim_history(history, max_turns=MAX_TURNS):
    """Con más de `max_turns` mensajes del usuario, borra la mitad más vieja (con sus respuestas y herramientas).
    De a mitades y no de a uno: cada recorte cambia el comienzo del prompt y el servidor lo reprocesa entero."""
    users = [i for i, m in enumerate(history) if m["role"] == "user"]
    if len(users) > max_turns:
        del history[:users[-(max_turns // 2)]]
    return history


def state():
    _ensure_conversation()
    cfg = load_config()
    from . import proactive
    shown = [{"role": m["role"], "content": m.get("content") or "", "spont": bool(m.get("spont")),
              "source": m.get("source") or "",
              "tools": [c["function"]["name"] for c in m.get("tool_calls") or []]}
             for m in _history if m["role"] in ("user", "assistant") and (m.get("content") or m.get("tool_calls"))]
    return {"config": cfg, "default_persona": DEFAULT_PERSONA, "memory": BT.read_memory(), "history": shown,
            "conversations": conversation_list(), "active_conversation": _conversation_state["active"],
            "tools": [{"name": n, "level": t["level"], "description": t["schema"]["function"]["description"],
                       "available": BT.available(n)} for n, t in BT.TOOLS.items()],
            "voice": {"stt": voice.stt_available(), "tts": voice.Speaker.available()},
            "ha": _ha_state(),
            "timers": proactive.timers(), "clients": proactive.HUB.clients(),
            "busy": _busy.locked()}


def _ha_state():
    from . import homeassistant as HA
    good, msg = HA.check()
    return {"url": HA.HA_URL, "configured": HA.configured(), "ok": good, "msg": msg}


def save_ha_token(value):
    from . import homeassistant as HA
    value = str(value or "").strip()
    if not value:
        HA.token_file().unlink(missing_ok=True)
        return True, _("Token de Home Assistant borrado.")
    HA.save_token(value)
    good, msg = HA.check()
    return (True, _("Home Assistant conectado: {msg}", msg=msg)) if good \
        else (False, _("Token guardado, pero Home Assistant no responde: {msg}", msg=msg))


def add_spontaneous(text):
    """Algo que Dani dijo por su cuenta (un timer, un aviso): queda en el historial para poder seguir la charla
    ("¿por qué?"). Si está contestando, espera: meterlo entre una llamada a herramienta y su resultado la rompería."""
    msg = {"role": "assistant", "content": text, "spont": True}
    if _busy.acquire(blocking=False):
        try:
            _ensure_conversation()
            _history.append(msg)
            _save_conversation()
            _broadcast_conversation()
        finally:
            _busy.release()
    else:
        _spontaneous.append(msg)


def _for_model(history):
    """El historial tal como lo recibe el modelo (sin las marcas propias del panel)."""
    keys = ("role", "content", "tool_calls", "tool_call_id")
    return [{k: m[k] for k in keys if k in m} for m in history]


def monologue(instruction, max_steps=3):
    """Dani escribe algo por su cuenta (el resumen de la mañana) con las herramientas de solo lectura. Solo si ya hay
    un modelo cargado y no está contestando: nunca prende el servidor ni apaga las imágenes por esto. Devuelve el
    texto o None (no lo agrega al historial: eso lo hace proactive.say)."""
    from . import services
    _ensure_conversation()
    if services.svc_state() != "active":
        return None
    loaded = [k for k, v in services.router_models().items() if v["status"] == "loaded"]
    if not loaded or not _busy.acquire(blocking=False):
        return None
    try:
        cfg = load_config()
        model = cfg["model"] if cfg["model"] in loaded else loaded[0]
        tools = [n for n in cfg["tools"] if BT.TOOLS[n]["level"] == "lectura"]
        messages = [{"role": "system", "content": system_prompt(cfg, _session["memory"] or BT.read_memory())}]
        messages += _for_model(trim_history(list(_history)))
        messages.append({"role": "user", "content": f"[Mensaje automático del panel, no lo escribió el usuario.] "
                                                    f"{instruction}"})
        text = ""
        for _step in range(max_steps):
            req = {"model": model, "messages": messages, "stream": True, "max_tokens": MAX_TOKENS,
                   "chat_template_kwargs": {"enable_thinking": False}, "tools": BT.schemas(tools)}
            text, calls = "", None
            for kind, data in stream_completion(C.backend_port("text"), req, threading.Event()):
                if kind == "text":
                    text += data
                else:
                    calls = data
            if not calls:
                break
            messages.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"] or f"m{i}", "type": "function", "function": {"name": c["name"],
                                                                         "arguments": c["arguments"] or "{}"}}
                for i, c in enumerate(calls)]})
            for i, c in enumerate(calls):
                result, _ok = BT.run(c["name"], c["arguments"], tools)
                messages.append({"role": "tool", "tool_call_id": c["id"] or f"m{i}", "content": result})
        return text.strip() or None
    except Exception as e:  # noqa: BLE001
        print(f"monólogo: {e}", flush=True)
        return None
    finally:
        _busy.release()


def reset():
    _ensure_conversation()
    if _busy.locked():
        return False, _("El compañero está ocupado.")
    if not _history:
        _session["memory"] = None
        _broadcast_conversation()
        return True, _("Charla nueva.")
    _save_conversation()
    item = _new_item()
    _conversation_state["items"].insert(0, item)
    _conversation_state["active"] = item["id"]
    _history.clear()
    _session["memory"] = None
    _save_conversation()
    _broadcast_conversation()
    return True, _("Charla nueva.")


def save_memory(text):
    BT.write_memory(str(text)[:20000])
    return True, _("Memoria guardada.")


# ---------- modelo ----------
def _endpoint_and_model(cfg):
    """A quién pedirle y con qué modelo. Con el encendido a demanda se pasa por el proxy (8080): si se estaban
    generando imágenes espera y cambia, y un modelo vacío va al cargado o al último usado. Sin él, al router."""
    from . import actions, services
    if C.auto_settings().get("swap"):
        return C.CHAT_PORT, cfg["model"] or "auto"
    good, note = actions.ensure_router()
    if not good:
        raise RuntimeError(note.strip())
    if cfg["model"]:
        return C.backend_port("text"), cfg["model"]
    loaded = [k for k, v in services.router_models().items() if v["status"] in ("loaded", "loading")]
    if not loaded:
        raise RuntimeError(_("No hay un modelo de texto cargado: cargá uno o elegí cuál usar en los ajustes del "
                             "compañero."))
    return C.backend_port("text"), loaded[0]


def stream_completion(port, body, cancel):
    """Pide una respuesta en streaming y va devolviendo ("text", delta) y, al final, ("tool_calls", [...]) si el
    modelo pidió herramientas. Corta (cierra la conexión, el servidor deja de generar) si `cancel` se activa."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=900)
    try:
        conn.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            raw = resp.read()
            try:
                msg = (json.loads(raw).get("error") or {}).get("message") or raw[:300].decode(errors="replace")
            except ValueError:
                msg = raw[:300].decode(errors="replace")
            raise RuntimeError(f"HTTP {resp.status}: {msg}")
        calls = {}
        for line in resp:
            if cancel.is_set():
                return
            if not line.startswith(b"data: {"):
                continue
            choice = (json.loads(line[6:]).get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                yield "text", delta["content"]
            for tc in delta.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                c["id"] = tc.get("id") or c["id"]
                fn = tc.get("function") or {}
                c["name"] += fn.get("name") or ""
                c["arguments"] += fn.get("arguments") or ""
        if calls:
            yield "tool_calls", [calls[i] for i in sorted(calls)]
    finally:
        conn.close()


# ---------- una charla ----------
def chat(body):
    """Generador de eventos para la interfaz. `body`: {"text"} o {"audio": base64, "speak": bool}."""
    if not _busy.acquire(blocking=False):
        yield {"type": "error", "msg": _("El compañero está contestando otra cosa; esperá un momento.")}
        return
    from . import proactive
    proactive.HUB.touch(str(body.get("client") or "")[:64])  # este cliente es el que habla los avisos
    events = queue.Queue()
    cancel = threading.Event()
    worker = threading.Thread(target=_run, args=(body, events, cancel), daemon=True)
    started = False
    try:
        worker.start()
        started = True  # desde acá el candado lo suelta _run al terminar (no antes: escribe el historial)
        while True:
            ev = events.get()
            if ev is None:
                break
            yield ev
    finally:
        cancel.set()  # la interfaz cortó (el usuario habló encima): dejar de generar y de sintetizar
        if not started:
            _busy.release()


def _run(body, events, cancel):
    _ensure_conversation()
    cfg = load_config()
    BT.set_user_context(body.get("user_context"))
    speak = bool(body.get("speak", cfg["speak"])) and voice.Speaker.available()
    sentences = queue.Queue()
    tts = threading.Thread(target=_speak_loop, args=(sentences, events, cancel), daemon=True) if speak else None
    if tts:
        threading.Thread(target=voice.SPEAKER.warm, daemon=True).start()  # en paralelo con el modelo
        tts.start()
    try:
        text = str(body.get("text") or "").strip()
        if body.get("audio"):
            import base64
            t0 = time.time()
            text, seconds = voice.transcribe(base64.b64decode(body["audio"]))
            events.put({"type": "heard", "text": text, "audio_s": round(seconds, 1),
                        "stt_s": round(time.time() - t0, 2)})
            if not text:
                events.put({"type": "error", "msg": _("No se entendió nada; probá de nuevo.")})
                return
        if not text:
            events.put({"type": "error", "msg": _("Mensaje vacío.")})
            return
        source = "pc" if str(body.get("client") or "").startswith("pc-") else "web"
        _converse(cfg, text, events, cancel, sentences if speak else None, source)
    except Exception as e:  # noqa: BLE001
        events.put({"type": "error", "msg": str(e)})
    finally:
        _save_conversation()
        _broadcast_conversation()
        BT.clear_user_context()
        if tts:
            sentences.put(None)
            tts.join()
        events.put({"type": "done"})
        events.put(None)
        _busy.release()


def _converse(cfg, text, events, cancel, sentences, source="web"):
    port, model = _endpoint_and_model(cfg)
    if _session["memory"] is None or not _history:
        _session["memory"] = BT.read_memory()
    while _spontaneous:
        _history.append(_spontaneous.pop(0))
    _history.append({"role": "user", "content": text, "source": source})
    _save_conversation()
    _broadcast_conversation()
    t0 = time.time()
    first = None
    for _step in range(MAX_STEPS):
        model_history = trim_history(list(_history))
        messages = [{"role": "system", "content": system_prompt(cfg, _session["memory"])}] + _for_model(model_history)
        req = {"model": model, "messages": messages, "stream": True, "max_tokens": MAX_TOKENS,
               "chat_template_kwargs": {"enable_thinking": False}}
        if cfg["tools"]:
            req["tools"] = BT.schemas(cfg["tools"])
        reply, calls, pending = "", None, ""
        spoken_first = False
        for kind, data in stream_completion(port, req, cancel):
            if kind == "text":
                if first is None:
                    first = time.time() - t0
                    events.put({"type": "first_token", "s": round(first, 2)})
                reply += data
                events.put({"type": "text", "delta": data})
                if sentences is not None:
                    ready, pending = voice.split_sentences(pending + data, first=not spoken_first)
                    for s in ready:
                        sentences.put(s)
                        spoken_first = True
            else:
                calls = data
        if cancel.is_set():
            if reply:
                _history.append({"role": "assistant", "content": reply + " …"})
            return
        if sentences is not None and pending.strip():
            for s in voice.split_sentences(pending, final=True)[0]:
                sentences.put(s)
        if not calls:
            _history.append({"role": "assistant", "content": reply})
            return
        _history.append({"role": "assistant", "content": reply or None, "tool_calls": [
            {"id": c["id"] or f"call_{i}", "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for i, c in enumerate(calls)]})
        for i, c in enumerate(calls):
            result, ok = BT.run(c["name"], c["arguments"], cfg["tools"])
            events.put({"type": "tool", "name": c["name"], "args": c["arguments"], "ok": ok, "result": result[:400]})
            _history.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": result})
    events.put({"type": "error", "msg": _("Demasiadas herramientas seguidas; corté acá.")})


def _speak_loop(sentences, events, cancel):
    import base64
    while True:
        s = sentences.get()
        if s is None or cancel.is_set():
            return
        try:
            wav = voice.SPEAKER.say(s)
        except Exception as e:  # noqa: BLE001
            events.put({"type": "error", "msg": _("Sin voz: {err}", err=e)})
            return
        if wav and not cancel.is_set():
            events.put({"type": "audio", "text": s, "wav": base64.b64encode(wav).decode()})
