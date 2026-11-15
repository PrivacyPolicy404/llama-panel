"""Radiografia de un token: que operaciones corren en la GPU y cuales en el CPU, y cuantos tramos (splits) arma el scheduler.

Uso: python3 sched_profile.py MODELO SALIDA.log [args extra de llama-server...]

Arranca llama-server con GGML_SCHED_DEBUG=2 (lista cada nodo del grafo con su dispositivo) y -lv 4,
genera unos pocos tokens y resume el ultimo grafo de generacion (1 token): tramos por dispositivo y
operaciones que no son expertos pero caen en el CPU (candidatas a portar a Vulkan).
Con PERF=1 tambien activa GGML_VK_PERF_LOGGER (tiempos por operacion en la GPU) y los deja en el log.
Usa el puerto 18083. Requiere la GPU libre.
"""
import collections, json, os, re, subprocess, sys, time, urllib.request

model, out = sys.argv[1], sys.argv[2]
extra = sys.argv[3:]
env = dict(os.environ, GGML_VK_VISIBLE_DEVICES="0", GGML_SCHED_DEBUG="2")
if os.environ.get("PERF"):
    env["GGML_VK_PERF_LOGGER"] = "1"
cmd = [os.path.expanduser("~/llama.cpp/build/bin/llama-server"), "-m", model, "-c", "8192", "-np", "1", "-t", "8",
       "--host", "127.0.0.1", "--port", "18083", "--no-webui", "-lv", "5"] + extra
p = subprocess.Popen(cmd, stdout=open(out, "w"), stderr=subprocess.STDOUT, env=env)
try:
    while True:
        if p.poll() is not None:
            sys.exit(f"el server termino, ver {out}")
        try:
            urllib.request.urlopen("http://127.0.0.1:18083/health", timeout=2); break
        except Exception:
            time.sleep(1)
    r = urllib.request.Request("http://127.0.0.1:18083/completion", headers={"Content-Type": "application/json"},
                               data=json.dumps({"prompt": "El faro de Alejandria fue", "n_predict": 24}).encode())
    t = json.loads(urllib.request.urlopen(r, timeout=600).read())["timings"]
    print(f"generacion: {t['predicted_per_second']:.1f} tok/s (con el debug activo, mas lento de lo normal)")
finally:
    p.terminate(); p.wait(30)

log = open(out, errors="replace").read()
for m in re.finditer(r"sched_reserve: graph \(pp bs=(\d+), tg bs=(\d+)\): nodes = (\d+) / (\d+), splits = (\d+) / (\d+)", log):
    print(f"tramos (splits): prompt {m[5]} | generacion {m[6]}   (nodos {m[3]} / {m[4]})")

# bloques "## SPLIT #n: BACKEND # k inputs" seguidos de las lineas de nodos; nos quedamos con el ultimo grafo chico
graphs, cur = [], []
for line in log.splitlines():
    if re.search(r"## SPLIT #0:", line) and cur:
        graphs.append(cur); cur = []
    if "## SPLIT #" in line or re.search(r"node #\s*\d+ \(", line):
        cur.append(line)
if cur:
    graphs.append(cur)
if not graphs:
    sys.exit("no se encontro el volcado del scheduler en el log")
g = min(graphs[-6:], key=len)  # el de generacion es el mas chico de los ultimos
splits = [l for l in g if "## SPLIT #" in l]
per_backend = collections.Counter(re.search(r"## SPLIT #\d+: (\S+)", l)[1] for l in splits)
print(f"grafo analizado: {len(splits)} tramos -> {dict(per_backend)}")

cpu_ops = collections.Counter()
backend = None
for l in g:
    m = re.search(r"## SPLIT #\d+: (\S+)", l)
    if m:
        backend = m[1]; continue
    if backend and backend.startswith("CPU"):
        nm = re.search(r"node #\s*\d+ \(\s*([A-Z_0-9-]+)\s*\):\s+(\S+)", l)
        if nm and "exps" not in l and "moe" not in nm[2]:
            cpu_ops[(nm[1], re.sub(r"-\d+$", "-N", nm[2]))] += 1
print("operaciones (no expertos) que corren en el CPU:")
for (op, name), n in cpu_ops.most_common(25):
    print(f"  {n:4d} x {op:20s} {name}")
