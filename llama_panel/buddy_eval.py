"""Pruebas del comportamiento del compañero: qué herramientas pide en la primera ronda (no ejecuta nada).

Cada caso es un mensaje y lo que se espera de la primera respuesta: qué herramienta pide, o que no pida ninguna y
no invente números. Se corre con dos historiales sintéticos y fijos: vacío y «contaminado» (turnos anteriores que
llamaron herramientas de más, como en una charla real: el modelo imita lo que ve). La memoria también es fija.
Usa el único slot del servidor: pisa el caché de la charla real.

Lo usan tools/bench/buddy_eval.py (el set completo, para medir los cambios de fábrica) y el botón «Probar» de la
pestaña Compañero (QUICK, con la configuración del usuario).
"""
import collections
import json
import re
import threading
import time
from datetime import date

from . import buddy
from . import buddy_tools as BT

MEMORY = "- El usuario tiene una perra que se llama Luna.\n- Trabaja de programador desde la casa."
DIGITS = re.compile(r"\d")


# ---------- expectativas: (calls, text) -> bool ----------
def calls_(*names, arg=None):
    """Pide alguna de estas herramientas (y, si `arg`, sus argumentos contienen ese texto)."""
    def check(calls, text):
        hit = [c for c in calls if c["name"] in names]
        return bool(hit) and (arg is None or any(arg.lower() in (c["arguments"] or "").lower() for c in hit))
    check.desc = "llama " + "|".join(names) + (f" con «{arg}»" if arg else "")
    return check


def none_(no_digits=False, must=None):
    """No pide herramientas (y no da números, o menciona `must`)."""
    def check(calls, text):
        return not calls and not (no_digits and DIGITS.search(text)) and (must is None or must in text.lower())
    check.desc = "sin herramientas" + (", sin números" if no_digits else "") + (f", dice «{must}»" if must else "")
    return check


def not_(*names):
    def check(calls, text):
        return not any(c["name"] in names for c in calls)
    check.desc = "no llama " + "|".join(names)
    return check


TODAY = BT.DAYS[date.today().weekday()]

# (categoría, mensaje, expectativa, opciones). voice: vino por Whisper; place: zona compartida
CASES = [
    ("charla", "hola", none_(True), {}),
    ("charla", "¿cómo estás?", none_(True), {"voice": True}),
    ("charla", "¿qué hacés?", none_(True), {"voice": True}),
    ("charla", "contame un chiste", none_(), {}),
    ("charla", "gracias, sos una genia", none_(), {}),
    ("charla", "¿qué cosas podés hacer?", none_(), {}),
    ("charla", "dame una idea para sumarle al homelab", none_(), {}),
    ("charla", "¿qué es un modelo MoE?", none_(), {}),
    ("charla", "buenas noches", none_(), {"voice": True}),
    ("hora", "¿qué hora es?", none_(), {"voice": True}),
    ("hora", "¿qué día es hoy?", none_(must=TODAY), {}),
    ("maquina", "¿cómo está la placa?", calls_("estado_homelab"), {"voice": True}),
    ("maquina", "¿la placa de video está caliente?", calls_("estado_homelab"), {}),
    ("maquina", "¿cuánta RAM queda libre?", calls_("estado_homelab"), {}),
    ("maquina", "¿qué modelo de texto está cargado?", calls_("estado_homelab"), {}),
    ("maquina", "¿cómo anda la máquina?", calls_("estado_homelab"), {"voice": True}),
    ("clima", "¿va a llover hoy?", calls_("clima", "contexto_usuario"), {"place": "Mar del Plata, Buenos Aires"}),
    ("clima", "¿qué temperatura hace en Córdoba?", calls_("clima", arg="córdoba"), {}),
    ("clima", "decime el clima en mano de plata", calls_("clima", arg="mar del plata"),
     {"voice": True, "place": "Mar del Plata, Buenos Aires"}),
    ("web", "¿a cuánto está el dólar blue?", calls_("buscar_web"), {}),
    ("web", "¿quién ganó el partido de Argentina anoche?", calls_("buscar_web"), {}),
    ("avisos", "avisame en 10 minutos que saque la pizza", calls_("avisar"), {"voice": True}),
    ("avisos", "recordame a las 20 que llame a mi vieja", calls_("avisar"), {}),
    ("avisos", "¿qué avisos tengo pendientes?", calls_("ver_avisos"), {}),
    ("avisos", "cancelá el aviso de la pizza", calls_("cancelar_aviso", "ver_avisos"), {}),
    ("avisos", "tengo que sacar la pizza en un rato", not_("avisar"), {}),
    ("memoria", "mi gato se llama Ramón", calls_("recordar"), {}),
    ("memoria", "acordate de que soy alérgico a las nueces", calls_("recordar"), {}),
    ("memoria", "olvidate de lo de la perra", calls_("olvidar"), {}),
    ("memoria", "hoy almorcé milanesas", not_("recordar", "avisar"), {}),
    ("voz", "es bastante como hay culo esto", none_(True), {"voice": True}),
    ("voz", "que pasa puedes hacer", none_(), {"voice": True}),
]
QUICK = ["hola", "¿qué hacés?", "¿qué día es hoy?", "¿cómo está la placa?", "¿cuánta RAM queda libre?",
         "¿va a llover hoy?", "¿a cuánto está el dólar blue?", "avisame en 10 minutos que saque la pizza",
         "mi gato se llama Ramón", "hoy almorcé milanesas"]


def drop_old_tools(history):
    """El historial sin las llamadas a herramientas ni sus resultados de los turnos anteriores. Se probó mandarlo así
    (para que no imite herramientas de más ni repita datos viejos) y se descartó (2026-11-17): no mejora (82 % contra
    ~80 %) y el modelo imita las respuestas sin herramientas: «avisame en 10 minutos» → «dale, te aviso» sin avisar.
    Queda como historial de comparación."""
    users = [i for i, m in enumerate(history) if m.get("role") == "user"]
    cut = users[-1] if users else len(history)
    out = []
    for i, m in enumerate(history):
        if i < cut and (m.get("role") == "tool" or m.get("tool_calls")):
            if m.get("role") == "assistant" and m.get("content"):
                out.append({k: v for k, v in m.items() if k != "tool_calls"})
            continue
        out.append(m)
    return out


def histories(now):
    """Turnos anteriores fijos (misma hora en cada pedido: el servidor reutiliza el caché dentro de la corrida)."""
    t = now - 3600

    def tc(i, name, args="{}"):
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"h{i}", "type": "function", "function": {"name": name, "arguments": args}}]}

    machine = json.dumps({"gpu": {"nombre": "RX 580", "temperatura_c": 55.0, "uso_pct": 3},
                          "ram_gb": "40.1 de 62.7", "servidor_de_texto": "active"}, ensure_ascii=False)
    dirty = [
        {"role": "user", "content": "hola", "at": t},
        tc(1, "estado_homelab"), {"role": "tool", "tool_call_id": "h1", "content": machine},
        {"role": "assistant", "content": "Hola, acá ando, la placa a 55 grados y tranquila."},
        {"role": "user", "content": "jaja bien", "at": t + 60, "voice": True},
        tc(2, "estado_homelab"), {"role": "tool", "tool_call_id": "h2", "content": machine},
        {"role": "assistant", "content": "Todo en orden por acá."},
        {"role": "user", "content": "¿qué onda con el clima?", "at": t + 120},
        tc(3, "clima", '{"ubicacion": "Buenos Aires, Argentina"}'),
        {"role": "tool", "tool_call_id": "h3", "content": '{"actual": {"estado": "nublado", "temperatura_c": 19}}'},
        {"role": "assistant", "content": "Nublado y 19 grados en Buenos Aires."},
    ]
    return [("limpio", []), ("contaminado", dirty), ("contaminado-sin-herramientas", drop_old_tools(dirty))]




def run(cfg, quick=False, reps=3, only=(), progress=None, hists=("limpio", "contaminado"), temperature=None):
    """Corre los casos con `cfg`. `progress(hechos, total)` se llama después de cada pedido. Devuelve el resumen
    por categoría ({clave: [aciertos, total]}), los resultados y el prompt de sistema usado."""
    port, model = buddy._endpoint_and_model(cfg)
    schemas = BT.schemas(cfg["tools"])
    offered = {s["function"]["name"] for s in schemas}
    lang = cfg.get("language", "es")

    def usable(expect):
        need = re.findall(r"llama ([\w|]+)", expect.desc)
        return not need or bool(set(need[0].split("|")) & offered)  # p. ej. Home Assistant sin token

    cases = [c for c in CASES if (not only or c[0] in only) and (not quick or c[1] in QUICK) and usable(c[2])]
    reps = 1 if quick else reps
    now = time.time()
    hists = histories(now)[:1] if quick else [h for h in histories(now) if h[0] in hists]
    system = buddy.system_prompt(cfg, MEMORY)
    total, results = len(cases) * reps * len(hists), []
    for hname, hist in hists:
        for cat, msg, expect, opts in cases:
            for _ in range(reps):
                user = {"role": "user", "content": msg, "at": now, **opts}
                messages = [{"role": "system", "content": system}] + buddy._for_model(hist + [user], lang)
                req = {"model": model, "messages": messages, "stream": True, "max_tokens": 120,
                       "chat_template_kwargs": {"enable_thinking": False}, "tools": schemas}
                if temperature is not None:
                    req["temperature"] = temperature
                t0, text, calls = time.time(), "", []
                for kind, data in buddy.stream_completion(port, req, threading.Event()):
                    if kind == "text":
                        text += data
                    else:
                        calls = data
                results.append({"hist": hname, "cat": cat, "msg": msg, "expect": expect.desc,
                                "ok": expect(calls, text),
                                "calls": [f"{c['name']}{c['arguments'] or ''}" for c in calls],
                                "text": text[:200], "s": round(time.time() - t0, 2)})
                if progress:
                    progress(len(results), total, results[-1])
    summary = collections.defaultdict(lambda: [0, 0])
    for r in results:
        for key in (r["cat"], f"historial {r['hist']}", "TOTAL"):
            summary[key][0] += r["ok"]
            summary[key][1] += 1
    return {"model": model, "system": system, "summary": dict(summary), "results": results}
