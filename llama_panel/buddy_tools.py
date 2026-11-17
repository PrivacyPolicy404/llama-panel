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


def tool(name, description, level, params=None, required=()):
    def deco(fn):
        TOOLS[name] = {"fn": fn, "level": level, "schema": {
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


# ---------- ejecución ----------
def schemas(enabled=None):
    return [t["schema"] for name, t in TOOLS.items() if enabled is None or name in enabled]


def run(name, arguments, enabled=None):
    """Ejecuta una llamada que propuso el modelo. Devuelve el resultado como texto (va de vuelta al modelo) y la
    registra en BUDDY_DIR/herramientas.log."""
    t0 = time.time()
    entry = TOOLS.get(name)
    if not entry or (enabled is not None and name not in enabled):
        result, ok = f"No existe la herramienta {name}.", False
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
