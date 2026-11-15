"""Texto de referencia a temperatura 0 (sin MTP) para detectar errores de cálculo, por ejemplo tras un overclock.

Uso: python3 gen_ref.py MODELO SALIDA.txt [args de llama-server...]
Arranca llama-server en el puerto 18085, genera 256 tokens de un prompt fijo a temp 0 y guarda el texto.
Comparar dos salidas con `cmp`: tienen que ser idénticas (mismo binario y mismos args).
"""
import json, os, signal, subprocess, sys, time, urllib.request

model, out, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
cmd = [os.path.expanduser("~/llama.cpp/build/bin/llama-server"), "-m", model, "-c", "4096", "-np", "1", "-t", "8",
       "--host", "127.0.0.1", "--port", "18085", "--no-webui"] + extra
p = subprocess.Popen(cmd, stdout=open(out + ".log", "w"), stderr=subprocess.STDOUT)
try:
    while True:
        if p.poll() is not None:
            sys.exit(f"el server termino, ver {out}.log")
        try:
            urllib.request.urlopen("http://127.0.0.1:18085/health", timeout=2); break
        except Exception:
            time.sleep(1)
    texts = []
    for prompt in ("La historia de la computación empieza", "def quicksort(arr):", "The theory of relativity states"):
        r = urllib.request.Request("http://127.0.0.1:18085/completion", headers={"Content-Type": "application/json"},
                                   data=json.dumps({"prompt": prompt, "n_predict": 256, "temperature": 0, "seed": 1}).encode())
        texts.append(json.loads(urllib.request.urlopen(r, timeout=900).read())["content"])
    open(out, "w").write("\n=====\n".join(texts))
finally:
    p.send_signal(signal.SIGINT)
    try:
        p.wait(60)
    except subprocess.TimeoutExpired:
        p.kill()
