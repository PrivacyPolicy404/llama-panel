"""Test de humo de los modelos pesados a traves del panel (el mismo camino que el boton "Cargar").

Uso:
  python3 smoke_models.py                  # compara contra baselines.json
  python3 smoke_models.py --save-baseline  # mide y guarda las velocidades actuales como referencia
  python3 smoke_models.py --yes            # no pregunta si el generador de imagenes esta encendido

Por cada modelo de baselines.json: lo carga por la API del panel, mide una respuesta corta (generacion)
y un prompt largo (~3200 tokens), lo descarga y compara. Avisa si algo rinde menos del 85% de la referencia.
Al final deja el servidor de texto como estaba. Cargar texto apaga el generador de imagenes.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PANEL = "http://127.0.0.1:8090"
ROUTER = "http://127.0.0.1:" + os.environ.get("LLAMA_CHAT_PORT", "8080")  # el de paths.env si se cambió
BASELINES = Path(__file__).with_name("baselines.json")
TOLERANCE = 0.85
DEFAULT_MODELS = ["Huihui-Qwen3.6-35B-A3B-abliterated-Q4_K", "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-hcq8"]


def call(url, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def state():
    return call(f"{PANEL}/api/state")


def chat(model, content, max_tokens):
    r = call(f"{ROUTER}/v1/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False}})
    return r["timings"]


def measure(model):
    res = call(f"{PANEL}/api/text/load", {"id": model})
    if not res.get("ok"):
        return {"error": res.get("msg")}
    t0 = time.time()
    while time.time() - t0 < 240:
        m = state()["text"]["models"].get(model) or {}
        if m.get("status") == "loaded":
            break
        if m.get("failed"):
            return {"error": "fallo al cargar (ver Registros en el panel)"}
        time.sleep(2)
    else:
        return {"error": "no termino de cargar en 4 minutos"}
    load_s = round(time.time() - t0)
    gens = [chat(model, q, 300)["predicted_per_second"] for q in (
        "Contame en un párrafo la historia del faro de Alejandría.",
        "Explicá en un párrafo qué es la fotosíntesis.")]
    largo = ("El faro de Alejandría fue una de las siete maravillas del mundo antiguo, construido en la isla de Faros. "
             * 120) + "\nResumí el texto anterior en una oración."
    t = chat(model, largo, 120)
    hw = state()["hw"]
    call(f"{PANEL}/api/text/unload", {"id": model})
    return {"carga_s": load_s, "gen_tps": round(sum(gens) / len(gens), 1),
            "prompt_largo_tps": round(t["prompt_per_second"], 1), "vram_gib": hw.get("vram_used")}


def main():
    save = "--save-baseline" in sys.argv
    baselines = json.loads(BASELINES.read_text()) if BASELINES.exists() else {}
    models = list(baselines) or DEFAULT_MODELS
    st = state()["svc"]
    if st["img"] == "active" and "--yes" not in sys.argv:
        if input("El generador de imagenes esta encendido y se va a apagar. ¿Seguir? [s/N] ").lower() != "s":
            return 1
    text_was_on = st["text"] == "active"
    problems = 0
    try:
        for model in models:
            print(f"\n== {model}")
            r = measure(model)
            if "error" in r:
                print(f"   ERROR: {r['error']}")
                problems += 1
                continue
            print(f"   carga {r['carga_s']} s | generación {r['gen_tps']} tok/s | prompt largo {r['prompt_largo_tps']} tok/s"
                  f" | VRAM {r['vram_gib']} GiB")
            base = baselines.get(model)
            if save:
                baselines[model] = {k: r[k] for k in ("gen_tps", "prompt_largo_tps", "vram_gib")}
                baselines[model]["fecha"] = time.strftime("%Y-%m-%d")
            elif base:
                bad = [k for k in ("gen_tps", "prompt_largo_tps") if r[k] < base[k] * TOLERANCE]
                for k in bad:
                    print(f"   ATENCION: {k} {r[k]} < 85% de la referencia {base[k]} ({base.get('fecha')})")
                problems += len(bad)
                if not bad:
                    print(f"   ok (referencia: {base['gen_tps']} / {base['prompt_largo_tps']} tok/s)")
    finally:
        if not text_was_on:
            call(f"{PANEL}/api/action", {"do": "stop"})
    if save:
        BASELINES.write_text(json.dumps(baselines, indent=2, ensure_ascii=False) + "\n")
        print(f"\nReferencias guardadas en {BASELINES}")
    print("\nResultado:", "OK" if problems == 0 else f"{problems} problema(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
