"""Herramientas del compañero. Cada una declara su nivel de riesgo:

- "lectura": solo mira (estado de la máquina, la hora). Se ejecuta directo.
- "memoria": escribe en la memoria del compañero (BUDDY_DIR/memoria.md), nada fuera de eso.
- "accion": cambia algo afuera (prender una luz, cargar un modelo). Más adelante: lista blanca y registro.
- "peligrosa": shell libre, escribir archivos. Solo dentro de un contenedor o con confirmación (todavía no hay).

El modelo solo propone llamadas; quien decide qué se ejecuta es este archivo. El texto que llega de afuera (lo que
transcribe Whisper, una página, un dispositivo) puede traer instrucciones: la seguridad tiene que estar acá.
"""
import json
import time
from datetime import datetime

from . import config as C

TOOLS = {}
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]


def tool(name, description, level, params=None, required=(), needs=None):
    """`needs`: función que dice si la herramienta se puede usar ahora (por ejemplo, si hay token de Home Assistant);
    si no, no se le ofrece al modelo."""
    def deco(fn):
        TOOLS[name] = {"fn": fn, "level": level, "needs": needs, "schema": {
            "type": "function", "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": params or {}, "required": list(required)}}}}
        return fn
    return deco


def memory_file():
    return C.BUDDY_DIR / "memoria.md"


def read_memory():
    try:
        return memory_file().read_text().strip()
    except OSError:
        return ""


def write_memory(text):
    C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = memory_file().with_suffix(".tmp")
    tmp.write_text(text.strip() + "\n" if text.strip() else "")
    tmp.replace(memory_file())


# ---------- lectura ----------
@tool("estado_homelab", "Estado actual de la máquina donde vivís: temperatura, uso y memoria de la GPU, RAM, CPU, "
      "y qué servicios y modelo de texto están prendidos.", "lectura")
def estado_homelab():
    from . import hardware, services
    h = hardware.latest() or {}
    st = services.svc_states()
    loaded = [k for k, v in services.router_models().items() if v["status"] in ("loaded", "loading")] \
        if st["text"] == "active" else []
    return {
        "gpu": {"nombre": h.get("gpu_name"), "temperatura_c": h.get("temp"), "uso_pct": h.get("gpu"),
                "vram_gb": f"{h.get('vram_used')} de {h.get('vram_total')}", "consumo_w": h.get("power"),
                "ventilador_rpm": h.get("fan")},
        "cpu": {"nombre": h.get("cpu_name"), "temperatura_c": h.get("cpu_temp"), "carga": h.get("load")},
        "ram_gb": f"{h.get('ram_used')} de {h.get('ram_total')}",
        "servidor_de_texto": st["text"], "generador_de_imagenes": st["img"],
        "modelo_de_texto_cargado": loaded[0] if loaded else None,
    }


@tool("fecha_y_hora", "Fecha y hora actuales.", "lectura")
def fecha_y_hora():
    now = datetime.now()
    return f"{DAYS[now.weekday()]} {now.day} de {MONTHS[now.month - 1]} de {now.year}, {now:%H:%M}"


# ---------- memoria ----------
@tool("recordar", "Guarda un dato que valga la pena recordar en próximas charlas (gustos, planes, datos de la casa "
      "o del usuario). Una oración corta por dato.", "memoria",
      {"dato": {"type": "string", "description": "Lo que hay que recordar, en una oración."}}, ["dato"])
def recordar(dato):
    dato = " ".join(str(dato).split())[:300]
    if not dato:
        return "No había nada para guardar."
    mem = read_memory()
    if dato.lower() in mem.lower():
        return "Ya lo tenía anotado."
    write_memory(f"{mem}\n- {dato}" if mem else f"- {dato}")
    return "Anotado."


@tool("olvidar", "Borra de la memoria los datos que contienen un texto (cuando el usuario pide que olvides algo o "
      "un dato cambió).", "memoria",
      {"texto": {"type": "string", "description": "Parte del dato a borrar."}}, ["texto"])
def olvidar(texto):
    needle = str(texto).strip().lower()
    if not needle:
        return "No dijiste qué olvidar."
    lines = read_memory().splitlines()
    keep = [ln for ln in lines if needle not in ln.lower()]
    if len(keep) == len(lines):
        return "No encontré nada así en la memoria."
    write_memory("\n".join(keep))
    return f"Olvidado ({len(lines) - len(keep)} dato/s)."


# ---------- Home Assistant ----------
# Solo estos tipos de dispositivo se pueden controlar: nada de script/automation/shell_command (hacen cualquier cosa)
HA_CONTROL = ("light", "switch", "fan", "scene", "media_player")
HA_SHOW = HA_CONTROL + ("sensor", "binary_sensor", "climate", "weather")
COLORS = {"rojo": "red", "verde": "green", "azul": "blue", "amarillo": "yellow", "naranja": "orange",
          "violeta": "violet", "morado": "purple", "rosa": "pink", "blanco": "white", "celeste": "lightblue",
          "turquesa": "turquoise", "cian": "cyan", "magenta": "magenta"}


def ha_ready():
    from . import homeassistant as HA
    return HA.configured()


@tool("casa_dispositivos", "Lista los dispositivos de la casa (luces, enchufes, escenas, sensores) con su estado e id.",
      "lectura", needs=ha_ready)
def casa_dispositivos():
    from . import homeassistant as HA
    out = []
    for e in HA.states():
        domain = e["entity_id"].split(".")[0]
        if domain not in HA_SHOW or e["state"] in ("unavailable", "unknown") and domain.endswith("sensor"):
            continue
        a = e.get("attributes") or {}
        item = {"id": e["entity_id"], "nombre": a.get("friendly_name"), "estado": e["state"]}
        if domain == "light" and a.get("brightness") is not None:
            item["brillo_pct"] = round(a["brightness"] * 100 / 255)
        if a.get("unit_of_measurement"):
            item["unidad"] = a["unit_of_measurement"]
        out.append(item)
    return out or "No hay dispositivos todavía en Home Assistant."


@tool("casa_controlar", "Prende, apaga o cambia una luz, enchufe, ventilador, escena o reproductor de la casa. Usá el "
      "id de casa_dispositivos.", "accion",
      {"id": {"type": "string", "description": "id del dispositivo, por ejemplo light.living"},
       "accion": {"type": "string", "enum": ["prender", "apagar", "alternar"]},
       "brillo_pct": {"type": "integer", "description": "Solo luces: brillo de 1 a 100."},
       "color": {"type": "string", "description": "Solo luces: color en castellano (rojo, azul, blanco cálido…)."}},
      ["id", "accion"], needs=ha_ready)
def casa_controlar(id, accion, brillo_pct=None, color=None):
    from . import homeassistant as HA
    domain = str(id).split(".")[0]
    if domain not in HA_CONTROL:
        return f"No tengo permiso para controlar {id}: solo {', '.join(HA_CONTROL)}."
    service = {"prender": "turn_on", "apagar": "turn_off", "alternar": "toggle"}.get(accion)
    if not service:
        return "Acción inválida: prender, apagar o alternar."
    data = {"entity_id": id}
    if domain == "light" and service == "turn_on":
        if brillo_pct is not None:
            data["brightness_pct"] = max(1, min(100, int(brillo_pct)))
        if color:
            c = str(color).lower().strip()
            if "cálid" in c or "calid" in c:
                data["color_temp_kelvin"] = 2700
            elif "frí" in c or "fri" in c:
                data["color_temp_kelvin"] = 5500
            else:
                data["color_name"] = COLORS.get(c, c)
    HA.call(domain, service, data)
    return f"Listo: {accion} {id}."


# ---------- ejecución ----------
def available(name):
    needs = TOOLS[name].get("needs")
    try:
        return needs is None or bool(needs())
    except Exception:  # noqa: BLE001
        return False


def schemas(enabled=None):
    return [t["schema"] for name, t in TOOLS.items() if (enabled is None or name in enabled) and available(name)]


def run(name, arguments, enabled=None):
    """Ejecuta una llamada que propuso el modelo. Devuelve el resultado como texto (va de vuelta al modelo) y la
    registra en BUDDY_DIR/herramientas.log."""
    t0 = time.time()
    entry = TOOLS.get(name)
    if not entry or (enabled is not None and name not in enabled):
        result, ok = f"No existe la herramienta {name}.", False
    elif not available(name):
        result, ok = f"La herramienta {name} no está disponible ahora.", False
    else:
        try:
            args = json.loads(arguments or "{}") if isinstance(arguments, str) else (arguments or {})
            if not isinstance(args, dict):
                raise ValueError("los argumentos tienen que ser un objeto")
            out = entry["fn"](**args)
            result, ok = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False), True
        except (ValueError, TypeError) as e:
            result, ok = f"Argumentos inválidos: {e}", False
        except Exception as e:  # noqa: BLE001
            result, ok = f"Falló: {e}", False
    _log({"t": round(t0, 2), "tool": name, "args": arguments, "ok": ok, "result": result[:500],
          "ms": round((time.time() - t0) * 1000)})
    return result, ok


def _log(entry):
    try:
        C.BUDDY_DIR.mkdir(parents=True, exist_ok=True)
        with open(C.BUDDY_DIR / "herramientas.log", "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
