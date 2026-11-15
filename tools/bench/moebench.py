"""Benchmark de llama-server con distintas configuraciones (pensado para MoE con expertos en RAM).

Uso:
  python3 moebench.py MODELO "etiqueta|args extra" ["etiqueta|args extra" ...]

Por cada configuracion arranca un llama-server en el puerto 18081 (aparte del router del panel),
hace 1 corrida de calentamiento + 2 medidas y imprime una linea JSON con prompt_tps, gen_tps y VRAM.
Escribe un log bench-<etiqueta>.log en la carpeta actual: correrlo desde una carpeta temporal.

Variables de entorno (REPS: cuántos pedidos, el primero de calentamiento; por defecto 3):
  PROMPT_MULT=8     largo del prompt (8 ~ 335 tokens, 64 ~ 2600 tokens)
  N_PREDICT=256     tokens a generar por corrida
  PROMPT_FILE=...   usar el texto de un archivo como prompt (crudo, igual en las 3 corridas)
  TEMP=0.7          temperatura de muestreo (0 = determinista, para comparar sin ruido)
  CTX=8192          tamano de contexto del server
  LD_LIBRARY_PATH=  carpeta con otra libggml-vulkan.so.1 para comparar versiones sin recompilar
  WRAP="systemd-run --user --scope --quiet -p MemoryMax=56G -p MemorySwapMax=0"
                    envuelve el server en un limite de memoria (recomendado al probar -lm none:
                    si algo se carga entero en RAM, muere el server y no el escritorio)
Si los args no traen -t, usa -t 8. Requiere la GPU libre: apagar texto e imagenes en el panel antes.
"""
import json, os, re, shlex, subprocess, sys, time, urllib.request

SERVER = os.path.expanduser("~/llama.cpp/build/bin/llama-server")
PORT = 18081
model = sys.argv[1]
PROMPT = ("Explica con detalle, en español, cómo funciona un motor de combustión interna de cuatro tiempos, "
          "sus partes principales, la historia de su desarrollo y sus ventajas y desventajas frente a un motor eléctrico. ") * int(os.environ.get("PROMPT_MULT", "8"))
N_PREDICT = int(os.environ.get("N_PREDICT", "256"))
if os.environ.get("PROMPT_FILE"):  # prompt propio (texto crudo, sin plantilla de chat)
    PROMPT = open(os.environ["PROMPT_FILE"]).read()


def req(path, body=None, timeout=900):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def vram():
    try:
        return int(open("/sys/class/drm/card1/device/mem_info_vram_used").read()) / 2**30
    except Exception:
        return float("nan")


results = []
for spec in sys.argv[2:]:
    label, _, extra = spec.partition("|")
    cmd = [SERVER, "-m", model, "--host", "127.0.0.1", "--port", str(PORT), "-c", os.environ.get("CTX", "8192"), "-np", "1",
           "--no-webui"] + ([] if " -t " in f" {extra} " else ["-t", "8"]) + shlex.split(extra)
    cmd = shlex.split(os.environ.get("WRAP", "")) + cmd  # ej: systemd-run --user --scope -p MemoryMax=54G
    log = open(f"bench-{re.sub(r'[^A-Za-z0-9]+', '_', label)}.log", "w")
    env = dict(os.environ, GGML_VK_VISIBLE_DEVICES="0")
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        while True:
            if p.poll() is not None:
                raise RuntimeError(f"el server termino (codigo {p.returncode}), ver {log.name}")
            try:
                req("/health", timeout=2); break
            except Exception:
                time.sleep(1)
        load_s = time.time() - t0
        runs = []
        for i in range(max(2, int(os.environ.get("REPS", "3")))):  # la primera es de calentamiento
            r = req("/completion", {"prompt": PROMPT if os.environ.get("PROMPT_FILE") else PROMPT + f" (variante {i})", "n_predict": N_PREDICT, "temperature": float(os.environ.get("TEMP", "0.7")),
                                    "ignore_eos": True, "cache_prompt": False})
            t = r["timings"]
            runs.append((t["prompt_per_second"], t["predicted_per_second"], t["prompt_n"]))
        pp = sum(r[0] for r in runs[1:]) / len(runs[1:])
        tg = sum(r[1] for r in runs[1:]) / len(runs[1:])
        res = {"config": label, "args": extra, "carga_s": round(load_s), "prompt_tps": round(pp, 1),
               "gen_tps": round(tg, 2), "gen_primera": round(runs[0][1], 2), "vram_gib": round(vram(), 2),
               "prompt_tokens": runs[1][2]}
        print(json.dumps(res, ensure_ascii=False), flush=True)
        results.append(res)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"config": label, "error": str(e)}, ensure_ascii=False), flush=True)
    finally:
        p.terminate()
        try:
            p.wait(30)
        except Exception:
            p.kill()
        log.close()
        time.sleep(3)
