"""Mide cuanto trabajan el CPU (nucleos ocupados por el server) y la GPU (gpu_busy_percent) durante una generacion.

Uso: python3 utilization.py MODELO [args extra de llama-server...]
Sirve para ver donde esta el cuello de botella. Ojo: los hilos de llama.cpp esperan girando, asi que
"nucleos ocupados" ~= -t aunque no esten calculando; lo informativo es el % de GPU y si cambia con -t.
Usa el puerto 18082 y escribe util.log en la carpeta actual. Requiere la GPU libre.
"""
import json, os, subprocess, sys, threading, time, urllib.request

model, extra = sys.argv[1], sys.argv[2:]
D = "/sys/class/drm/card1/device"
cmd = [os.path.expanduser("~/llama.cpp/build/bin/llama-server"), "-m", model, "-t", "8", "-c", "8192", "-np", "1",
       "--host", "127.0.0.1", "--port", "18082", "--no-webui"] + extra
p = subprocess.Popen(cmd, stdout=open("util.log", "w"), stderr=subprocess.STDOUT, env=dict(os.environ, GGML_VK_VISIBLE_DEVICES="0"))
try:
    while True:
        try:
            urllib.request.urlopen("http://127.0.0.1:18082/health", timeout=2); break
        except Exception:
            time.sleep(1)
    res = {}
    def gen():
        r = urllib.request.Request("http://127.0.0.1:18082/completion", headers={"Content-Type": "application/json"},
                                   data=json.dumps({"prompt": "Escribi una historia larga sobre un faro.", "n_predict": 400, "ignore_eos": True}).encode())
        res["t"] = json.loads(urllib.request.urlopen(r, timeout=600).read())["timings"]
    th = threading.Thread(target=gen); th.start(); time.sleep(6)

    def cpu():
        v = open(f"/proc/{p.pid}/stat").read().split(")")[1].split()
        return int(v[11]) + int(v[12])
    hz = os.sysconf("SC_CLK_TCK"); gpu = []
    c0, t0 = cpu(), time.time()
    for _ in range(40):
        gpu.append(int(open(f"{D}/gpu_busy_percent").read())); time.sleep(0.25)
    n = (cpu() - c0) / hz / (time.time() - t0)
    th.join()
    print(f"args={' '.join(extra)}  gen={res['t']['predicted_per_second']:.1f} tok/s  CPU={n:.1f} nucleos  GPU={sum(gpu)/len(gpu):.0f}% (max {max(gpu)}%)")
finally:
    p.terminate(); p.wait(30)
