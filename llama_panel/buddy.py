"""Compañero: un agente con personalidad, memoria, herramientas y voz que usa el modelo de texto cargado.

Una charla: el usuario manda texto o audio (Whisper lo pasa a texto), el modelo contesta en streaming, cada oración
completa va a Piper mientras se genera la siguiente, y si el modelo pide herramientas (buddy_tools) se ejecutan y se
le devuelve el resultado. La interfaz recibe eventos: lo que se escuchó, texto, herramientas, audio y el final.

Todo vive en BUDDY_DIR: config.json, memoria.md, herramientas.log y conversations/ (transcripciones e historial).
La conversación activa es compartida por la web y clients/buddy.py y se restaura al reiniciar el panel.
"""
import http.client
import json
import queue
import re
import threading
import time
import uuid

from . import buddy_prompt as BP
from . import buddy_tools as BT
from . import config as C
from . import voice
from .i18n import _

MAX_STEPS = 5       # rondas de herramientas por mensaje
MAX_TURNS = 12      # mensajes del usuario que se mandan como historial
MAX_TOKENS = 600

# Se guardan las herramientas apagadas (no las prendidas): una herramienta nueva queda disponible sin tocar nada.
# "sections": solo las secciones del prompt que el usuario cambió (buddy_prompt.py); "language": de la charla.
DEFAULT_CONFIG = {"name": "Dani", "model": "", "speak": True, "tools_off": [], "language": "es", "sections": {},
                  "keep_audio": True,  # últimos audios en BUDDY_DIR/oido (hearing.py), solo en esta máquina
                  "wake": False,  # clients/buddy.py escucha siempre y responde a su nombre (necesita vosk en la PC)
                  "alerts": True, "morning": "09:00"}  # avisos del homelab y hora del resumen (proactive.py)
MORNING_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
SECTION_MAX = 6000

_history = []
_spontaneous = []  # lo que Dani dijo por su cuenta mientras contestaba otra cosa: entra al historial después
_session = {"memory": None}  # memoria leída al empezar la charla (ver system_prompt)
_busy = threading.Lock()
_warming = threading.Event()  # el candado lo tiene un precalentado: un mensaje nuevo espera en vez de rebotar
WARM_WAIT_S = 90
_conversation_lock = threading.Lock()
_conversation_state = {"root": None, "active": None, "items": []}


class _LiveEvents:
    """Cola de la respuesta HTTP que además refleja texto/herramientas a los otros clientes conectados."""
    LIVE_TYPES = {"heard", "text", "tool", "error", "done"}

    def __init__(self, client):
        self.queue = queue.Queue()
        self.client = client
        self.chat_id = uuid.uuid4().hex[:12]

    def put(self, event):
        self.queue.put(event)
        if isinstance(event, dict) and event.get("type") in self.LIVE_TYPES:
            self.mirror(event)

    def mirror(self, event):
        from . import proactive
        proactive.HUB.broadcast({"type": "chat", "chat_id": self.chat_id, "client": self.client,
                                 "conversation_id": _conversation_state["active"], "event": event})

    def get(self):
        return self.queue.get()


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
        clean = {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls", "tool_call_id", "spont", "source",
                                                       "at", "voice")}
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
    sections = {k: v for k, v in (cfg.get("sections") or {}).items() if k in BP.EDITABLE and isinstance(v, str)}
    persona = cfg.pop("persona", "")  # versión vieja: una sola personalidad, ahora la sección «identidad»
    if persona and not sections.get("identidad"):
        sections["identidad"] = persona
    cfg["sections"] = sections
    cfg["language"] = BP.lang_of(cfg)
    cfg.pop("tools", None)
    cfg["tools"] = [t for t in BT.TOOLS if t not in cfg["tools_off"]]
    return cfg


def factory_config():
    """La configuración de fábrica, sin lo que guardó el usuario (para medirla: tools/bench/buddy_eval.py)."""
    cfg = {**DEFAULT_CONFIG, "sections": {}}
    cfg["tools"] = list(BT.TOOLS)
    return cfg


def save_config(body):
    cfg = load_config()
    name = " ".join(str(body.get("name", cfg["name"])).split())[:40]
    if not name:
        return False, _("El compañero necesita un nombre.")
    language = body.get("language", cfg["language"])
    if language not in BP.LANGUAGES:
        return False, _("Idioma desconocido.")
    sections = dict(cfg["sections"])
    incoming = body.get("sections", {})
    if "persona" in body:  # clientes viejos
        incoming = {**incoming, "identidad": body["persona"]}
    if not isinstance(incoming, dict) or any(k not in BP.EDITABLE for k in incoming):
        return False, _("Sección desconocida.")
    for k, v in incoming.items():
        sections[k] = str(v or "").strip()[:SECTION_MAX]
    # Sin editar (vacía o igual a la de fábrica): no se guarda, así le llegan las mejoras de fábrica
    sections = {k: v for k, v in sections.items() if v and v != BP.DEFAULTS[language][k]}
    tools = body.get("tools", cfg["tools"])
    if not isinstance(tools, list) or any(t not in BT.TOOLS for t in tools):
        return False, _("Herramienta desconocida.")
    morning = str(body.get("morning", cfg["morning"]) or "").strip()
    if morning and not MORNING_RE.match(morning):
        return False, _("La hora del resumen va como HH:MM (vacía = nunca).")
    cfg.update(name=name, language=language, sections=sections, model=str(body.get("model", cfg["model"])).strip(),
               speak=bool(body.get("speak", cfg["speak"])), tools_off=sorted(set(BT.TOOLS) - set(tools)),
               alerts=bool(body.get("alerts", cfg["alerts"])), morning=morning,
               keep_audio=bool(body.get("keep_audio", cfg["keep_audio"])),
               wake=bool(body.get("wake", cfg.get("wake", False))))
    cfg.pop("tools")
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = C.BUDDY_DIR / "config.tmp"
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    tmp.replace(C.BUDDY_DIR / "config.json")
    if any(k in body for k in ("sections", "persona", "language", "name", "tools")):
        warm_async()  # el prompt cambió: que la próxima respuesta no pague el reprocesado
    return True, _("Compañero guardado.")


def system_prompt(cfg, memory):
    """Las secciones (buddy_prompt.py) y la memoria. No lleva la hora ni nada que cambie seguido: así el servidor
    reutiliza el prompt ya procesado entre mensajes. La memoria se lee al empezar la charla y queda fija hasta la
    próxima (lo que se anota en el medio ya está en el historial): si cambiara, el servidor reprocesaría todo el
    prompt (con las herramientas, ~1700 tokens: ~20 s en Flash-Next)."""
    return BP.build(cfg, memory)


def _cut(n_users, max_turns=MAX_TURNS):
    """Cuántos mensajes del usuario se recortan del principio cuando hay `n_users`."""
    if n_users <= max_turns:
        return 0
    half = max(1, max_turns // 2)
    return ((n_users - max_turns - 1) // half + 1) * half


def trim_history(history, max_turns=MAX_TURNS, ahead=0):
    """Con más de `max_turns` mensajes del usuario, borra los más viejos (con sus respuestas y herramientas) de a
    bloques de media ventana: cada recorte cambia el comienzo del prompt y el servidor lo reprocesa entero.
    El corte depende solo de cuántos mensajes hay, no de lo que se recortó antes (el historial completo queda
    guardado y se recorta una copia en cada pedido): con 13 a 18 se sacan los 6 primeros, con 19 a 24 los 12...
    Así el comienzo queda igual durante 6 mensajes seguidos, en vez de moverse en cada uno.
    `ahead`: recortar como si ya hubiera esos mensajes más (para precalentar lo que va a mandar el próximo)."""
    users = [i for i, m in enumerate(history) if m["role"] == "user"]
    drop = _cut(len(users) + ahead, max_turns)
    if 0 < drop < len(users):
        del history[:users[drop]]
    return history


def client_settings():
    """Lo que necesita clients/buddy.py (lo consulta cada tanto): el nombre y si tiene que escuchar su nombre."""
    cfg = load_config()
    return {"name": cfg["name"], "wake": bool(cfg.get("wake"))}


def state():
    _ensure_conversation()
    cfg = load_config()
    from . import proactive
    shown = [{"role": m["role"], "content": m.get("content") or "", "spont": bool(m.get("spont")),
              "source": m.get("source") or "",
              "tools": [c["function"]["name"] for c in m.get("tool_calls") or []]}
             for m in _history if m["role"] in ("user", "assistant") and (m.get("content") or m.get("tool_calls"))]
    return {"config": cfg, "sections": BP.describe(cfg), "languages": list(BP.LANGUAGES), "memory": BT.read_memory(), "history": shown,
            "conversations": conversation_list(), "active_conversation": _conversation_state["active"],
            "tools": [{"name": n, "level": t["level"], "description": t["schema"]["function"]["description"],
                       "available": BT.available(n)} for n, t in BT.TOOLS.items()],
            "voice": {"stt": voice.stt_available(), "tts": voice.Speaker.available()},
            "ha": _ha_state(), "web_search": _web_search_state(),
            "timers": proactive.timers(), "clients": proactive.HUB.clients(),
            "busy": _busy.locked(), "test": test_state(), "hearing": _hearing_state(cfg)}


def _hearing_state(cfg):
    from . import hearing
    try:
        return hearing.state(cfg)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def hearing_forget(heard):
    from . import hearing
    return (True, _("Corrección borrada; no se va a volver a aprender.")) if hearing.forget(heard) \
        else (False, _("No existe esa corrección."))


def hearing_compare():
    from . import hearing
    if not hearing.audios():
        return False, _("Todavía no hay audios guardados.")
    return (True, _("Comparando en segundo plano…")) if hearing.compare_async(load_config()) \
        else (False, _("Ya se está comparando."))


def hearing_delete_audios():
    from . import hearing
    return True, _("Audios borrados: {n}.", n=hearing.delete_audios())


def _ha_state():
    from . import homeassistant as HA
    good, msg = HA.check()
    return {"url": HA.HA_URL, "configured": HA.configured(), "ok": good, "msg": msg}


def _web_search_state():
    from . import websearch
    return {"provider": "brave" if websearch.configured() else "duckduckgo",
            "configured": websearch.configured()}


def save_brave_token(value):
    from . import websearch
    value = str(value or "").strip()
    if not value:
        websearch.token_file().unlink(missing_ok=True)
        return True, _("Clave de Brave Search borrada; se usará la búsqueda experimental.")
    websearch.save_token(value)
    return True, _("Clave de Brave Search guardada.")


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


def _for_model(history, lang="es"):
    """El historial tal como lo recibe el modelo: sin las marcas propias del panel y con la marca (BP.stamp: fecha,
    hora, si vino por voz, la zona) adelante de cada mensaje del usuario. Va en el mensaje y no en el prompt de
    sistema: los mensajes ya mandados no cambian, así que el servidor reutiliza lo procesado."""
    keys = ("role", "content", "tool_calls", "tool_call_id")
    out = []
    for m in history:
        msg = {k: m[k] for k in keys if k in m}
        if m.get("role") == "user" and m.get("at") and isinstance(m.get("content"), str):
            when = BP.stamp(m["at"], lang, voice=bool(m.get("voice")), place=m.get("place"))
            msg["content"] = f"{when} {m['content']}"
        out.append(msg)
    return out


def strip_name(text, name):
    """Saca un «Dani:» o un «[…]» del comienzo de la respuesta (el modelo a veces imita la marca de los mensajes:
    «[de] No te entendí»). None si todavía no se sabe (puede estar llegando de a partes)."""
    lead = text.lstrip()
    if lead.startswith("["):
        if "]" not in lead:
            return None if len(lead) < 80 else text
        rest = lead[lead.index("]") + 1:]
        if not rest.strip():
            return None
        clean = strip_name(rest, name)
        return clean.lstrip() if clean is not None else None
    target = f"{name.lower()}:"
    if target.startswith(lead.lower()) and len(lead) < len(target):
        return None
    return lead[len(target):].lstrip() if lead.lower().startswith(target) else text


def _loaded_model(cfg):
    """(puerto, modelo) si ya hay un modelo de texto cargado; None si no. Para lo que corre por su cuenta (resumen,
    precalentar, probar): nunca prende el servidor ni apaga las imágenes."""
    from . import services
    if services.svc_state() != "active":
        return None
    loaded = [k for k, v in services.router_models().items() if v["status"] == "loaded"]
    if not loaded:
        return None
    return C.backend_port("text"), cfg["model"] if cfg["model"] in loaded else loaded[0]


def _warm_locked(cfg, ahead=0):
    """Manda la charla activa tal como la mandaría el próximo mensaje (sin él, 1 token): el servidor la deja procesada
    y la respuesta siguiente arranca enseguida. Con el candado ya tomado. `ahead`: ver trim_history."""
    target = _loaded_model(cfg)
    if not target:
        return False
    port, model = target
    lang = BP.lang_of(cfg)
    memory = _session["memory"] if _session["memory"] is not None else BT.read_memory()
    messages = [{"role": "system", "content": system_prompt(cfg, memory)}] + \
        _for_model(trim_history(list(_history), ahead=ahead), lang)
    if messages[-1]["role"] == "system":
        messages.append({"role": "user", "content": "."})  # charla vacía: al menos el prompt de sistema
    req = {"model": model, "messages": messages, "stream": True, "max_tokens": 1,
           "chat_template_kwargs": {"enable_thinking": False}}
    if cfg["tools"]:
        req["tools"] = BT.schemas(cfg["tools"])
    for _ev in stream_completion(port, req, threading.Event()):
        pass
    return True


def warm_async(ahead=0):
    """Precalienta en segundo plano (después de cambiar las instrucciones, o antes de que el próximo mensaje recorte
    el historial), si hay modelo y el compañero está libre. Un mensaje que llega mientras tanto espera (_warming)."""
    def work():
        if not _busy.acquire(blocking=False):
            return
        _warming.set()
        try:
            _ensure_conversation()
            _warm_locked(load_config(), ahead)
        except Exception as e:  # noqa: BLE001
            print(f"precalentar: {e}", flush=True)
        finally:
            _warming.clear()
            _busy.release()
    threading.Thread(target=work, daemon=True).start()


def _warm_before_cut():
    """El recorte de a mitades (trim_history) mueve el comienzo de la charla cada 6 mensajes y el servidor reprocesa
    todo lo que sigue (~2500 tokens, ~26 s con Flash-Next: lo vio el usuario como una respuesta lenta sin
    herramientas). Si el próximo mensaje lo va a mover, se recorta y procesa ya, mientras el usuario lee."""
    n = sum(1 for m in _history if m["role"] == "user")
    if _cut(n + 1) != _cut(n):
        warm_async(ahead=1)


# ---------- «Probar»: la versión corta de las pruebas (buddy_eval.py) con lo guardado ----------
_test = {"running": False, "done": 0, "total": 0, "result": None, "error": None}


def test_state():
    return dict(_test)


def start_test():
    cfg = load_config()
    if _test["running"]:
        return False, _("Ya se está probando.")
    if not _loaded_model(cfg):
        return False, _("Cargá un modelo de texto para probar.")
    if not _busy.acquire(blocking=False):
        return False, _("El compañero está ocupado.")
    _test.update(running=True, done=0, total=0, result=None, error=None)

    def work():
        from . import buddy_eval
        try:
            out = buddy_eval.run(cfg, quick=True, progress=lambda done, total, _r: _test.update(done=done, total=total))
            ok, n = out["summary"]["TOTAL"]
            _test["result"] = {"ok": ok, "total": n, "fails": [
                {"msg": r["msg"], "expect": r["expect"], "got": ", ".join(r["calls"]) or r["text"][:160]}
                for r in out["results"] if not r["ok"]]}
            _ensure_conversation()
            _warm_locked(cfg)  # las pruebas pisaron el caché de la charla real
        except Exception as e:  # noqa: BLE001
            _test["error"] = str(e)
        finally:
            _test["running"] = False
            _busy.release()

    threading.Thread(target=work, daemon=True).start()
    return True, _("Probando…")


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
        lang = BP.lang_of(cfg)
        messages += _for_model(trim_history(list(_history)), lang)
        messages.append({"role": "user", "content": f"{BP.stamp(time.time(), lang)} {BP.auto_mark(lang)} "
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
    """Pide una respuesta en streaming y va devolviendo ("text", delta), ("tool_start", nombre) apenas el modelo
    empieza a escribir una llamada a una herramienta y, al final, ("tool_calls", [...]). Corta (cierra la conexión,
    el servidor deja de generar) si `cancel` se activa."""
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
                if fn.get("name") and not c["name"]:
                    yield "tool_start", fn["name"]
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
        # precalentando lo mismo que este mensaje va a necesitar: esperar sale más rápido que rebotar
        if not (_warming.is_set() and _busy.acquire(timeout=WARM_WAIT_S)):
            yield {"type": "error", "msg": _("El compañero está contestando otra cosa; esperá un momento.")}
            return
    from . import proactive
    proactive.HUB.touch(str(body.get("client") or "")[:64])  # este cliente es el que habla los avisos
    events = _LiveEvents(str(body.get("client") or "")[:64])
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
    tts_started = False
    try:
        text = str(body.get("text") or "").strip()
        if body.get("audio"):
            import base64
            t0 = time.time()
            # Vocabulario de la casa y correcciones aprendidas (hearing.py); el audio queda para las comparaciones
            from . import hearing
            vocab = hearing.prompt(cfg, BT.user_place())
            keep = hearing.new_audio_path() if cfg.get("keep_audio", True) else None
            raw, seconds = voice.transcribe(base64.b64decode(body["audio"]), prompt=vocab, keep_wav=keep)
            text = hearing.apply(raw)
            if keep:
                hearing.record(keep, raw, text, vocab)
            events.put({"type": "heard", "text": text, "audio_s": round(seconds, 1),
                        "stt_s": round(time.time() - t0, 2), **({"raw": raw} if raw != text else {})})
            if not text:
                events.put({"type": "error", "msg": _("No se entendió nada; probá de nuevo.")})
                return
        if not text:
            events.put({"type": "error", "msg": _("Mensaje vacío.")})
            return
        source = "pc" if str(body.get("client") or "").startswith("pc-") else "web"
        events.mirror({"type": "start", "text": text, "source": source})
        if tts:
            # Whisper también usa el CPU: no cargar Piper mientras intenta entender el audio. Desde acá sí puede
            # calentarse en paralelo con el modelo de texto y estar listo antes de la primera oración.
            threading.Thread(target=voice.SPEAKER.warm, daemon=True).start()
            tts.start()
            tts_started = True
        _converse(cfg, text, events, cancel, sentences if speak else None, source, bool(body.get("audio")))
    except Exception as e:  # noqa: BLE001
        events.put({"type": "error", "msg": str(e)})
    finally:
        _save_conversation()
        _broadcast_conversation()
        BT.clear_user_context()
        if tts_started:
            sentences.put(None)
            tts.join()
        events.put({"type": "done"})
        events.put(None)
        _busy.release()
        _warm_before_cut()


def _converse(cfg, text, events, cancel, sentences, source="web", by_voice=False):
    port, model = _endpoint_and_model(cfg)
    if _session["memory"] is None or not _history:
        _session["memory"] = BT.read_memory()
    while _spontaneous:
        _history.append(_spontaneous.pop(0))
    msg = {"role": "user", "content": text, "source": source, "at": round(time.time())}
    if by_voice:
        msg["voice"] = True  # el modelo sabe que puede venir mal transcripto (sección «mecanica»)
    place = BT.user_place()
    if place:
        msg["place"] = place  # solo en memoria: _safe_messages no lo guarda en disco
    _history.append(msg)
    _save_conversation()
    _broadcast_conversation()
    t0 = time.time()
    first = None
    announced = False  # «dejame que me fijo»: una vez por mensaje
    for _step in range(MAX_STEPS):
        model_history = trim_history(list(_history))
        messages = [{"role": "system", "content": system_prompt(cfg, _session["memory"])}] + _for_model(model_history, BP.lang_of(cfg))
        req = {"model": model, "messages": messages, "stream": True, "max_tokens": MAX_TOKENS,
               "chat_template_kwargs": {"enable_thinking": False}}
        if cfg["tools"]:
            req["tools"] = BT.schemas(cfg["tools"])
        reply, calls, pending, lead = "", None, "", ""
        spoken_first = False

        def emit(data):
            nonlocal reply, pending, spoken_first, first
            if not data:
                return
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

        for kind, data in stream_completion(port, req, cancel):
            if kind == "tool_start":
                # Mientras se busca el dato, decirlo; no va al historial del modelo (no le cambia el prompt)
                phrase = None if (announced or reply) else BP.checking(data, BP.lang_of(cfg), len(_history))
                if phrase:
                    announced = True
                    if first is None:
                        first = time.time() - t0
                        events.put({"type": "first_token", "s": round(first, 2)})
                    events.put({"type": "text", "delta": phrase + " "})
                    if sentences is not None:
                        sentences.put(phrase)
                continue
            if kind == "text":
                if lead is None:  # ya se sabe que no empieza con el nombre
                    emit(data)
                    continue
                lead += data
                clean = strip_name(lead, cfg["name"])
                if clean is not None:
                    lead = None
                    emit(clean)
            else:
                calls = data
        if lead:  # terminó mientras todavía podía ser el nombre
            emit(lead)
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
            # Si el modelo entendió un nombre que Whisper escribió mal, aprenderlo: cuenta lo que interpretó aunque
            # la herramienta falle por la red, pero no si el lugar no existe (BT.NOT_FOUND)
            if by_voice and not result.startswith(BT.NOT_FOUND):
                from . import hearing
                if hearing.learn(text, c["name"], c["arguments"]):
                    events.put({"type": "learned", "name": c["name"]})
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
