"""Desglose del tiempo por token: cuanto es GPU (por tipo de operacion) y cuanto queda para CPU + sincronizaciones.

Uso: python3 vk_timings.py MODELO [args extra de llama-server...]

Arranca llama-server con GGML_VK_PERF_LOGGER (tiempo de cada operacion en la GPU), genera N tokens con un prompt
minimo (casi todo es generacion) y al cerrar suma los tiempos. El medidor agrega algo de overhead: sirve para
comparar proporciones, no velocidades absolutas.
Variables: N_PREDICT (200), LD_LIBRARY_PATH (otra libggml-vulkan para comparar versiones). Puerto 18084.
"""
import collections, json, os, re, signal, subprocess, sys, time, urllib.request

model, extra = sys.argv[1], sys.argv[2:]
n_predict = int(os.environ.get("N_PREDICT", "200"))
log_path = "vk_timings.log"
env = dict(os.environ, GGML_VK_VISIBLE_DEVICES="0", GGML_VK_PERF_LOGGER="1", GGML_VK_PERF_LOGGER_FREQUENCY="100000000")
cmd = [os.path.expanduser("~/llama.cpp/build/bin/llama-server"), "-m", model, "-c", "8192", "-np", "1", "-t", "8",
       "--host", "127.0.0.1", "--port", "18084", "--no-webui"] + extra
p = subprocess.Popen(cmd, stdout=open(log_path, "w"), stderr=subprocess.STDOUT, env=env)
try:
    while True:
        if p.poll() is not None:
            sys.exit(f"el server termino, ver {log_path}")
        try:
            urllib.request.urlopen("http://127.0.0.1:18084/health", timeout=2); break
        except Exception:
            time.sleep(1)
    r = urllib.request.Request("http://127.0.0.1:18084/completion", headers={"Content-Type": "application/json"},
                               data=json.dumps({"prompt": "El faro", "n_predict": n_predict, "temperature": 0,
                                                "ignore_eos": True}).encode())
    t = json.loads(urllib.request.urlopen(r, timeout=900).read())["timings"]
finally:
    p.send_signal(signal.SIGINT)  # cierre ordenado: el medidor imprime los totales al destruir el contexto
    try:
        p.wait(60)
    except subprocess.TimeoutExpired:
        p.kill()

log = open(log_path, errors="replace").read()
blocks = log.split("Vulkan Timings:")
if len(blocks) < 2:
    sys.exit("el medidor no imprimio tiempos (ver vk_timings.log)")
ops = collections.Counter()
calls = collections.Counter()
for b in blocks[1:]:
    for m in re.finditer(r"^(.+?): (\d+) x [\d.]+ us = ([\d.]+) us", b, re.M):
        ops[m[1].strip()] += float(m[3]); calls[m[1].strip()] += int(m[2])
gpu_us = sum(ops.values())
n_tok = t["predicted_n"]
ms_tok = t["predicted_ms"] / n_tok
gpu_ms_tok = gpu_us / 1000 / n_tok  # incluye el prompt minimo: despreciable
print(f"generacion: {t['predicted_per_second']:.1f} tok/s con el medidor activo -> {ms_tok:.1f} ms por token")
print(f"GPU (suma de operaciones): {gpu_ms_tok:.1f} ms por token ({gpu_ms_tok / ms_tok * 100:.0f} %)")
print(f"resto (CPU, sincronizaciones, copias): {ms_tok - gpu_ms_tok:.1f} ms por token ({(ms_tok - gpu_ms_tok) / ms_tok * 100:.0f} %)")
print("operaciones de GPU que mas tiempo ocupan (ms por token, llamadas por token):")
for name, us in ops.most_common(18):
    print(f"  {us / 1000 / n_tok:6.2f} ms  {calls[name] / n_tok:6.1f}x  {name[:90]}")
