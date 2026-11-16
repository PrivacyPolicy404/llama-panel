"""Estabilidad por el router real: 62K de entrada + 2048 de salida, memoria DRM y recargas.

Uso: python3 tools/bench/ctx_stability.py --ai-mode --output DIRECTORIO_NUEVO
--ai-mode autoriza cerrar KDE y restaurarlo al terminar. Requiere servidores apagados.
No cambia settings.json. Conserva respuestas, logs y muestras fuera de /tmp.
La salida forzada prueba estabilidad y capacidad, no calidad semántica.
"""
import argparse
import hashlib
import json
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

PANEL = "http://127.0.0.1:8090"
ROUTER = "http://127.0.0.1:8080"
MODEL = "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-hcq8"


def call(base, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def action(name):
    r = call(PANEL, "/api/action", {"do": name}, timeout=120)
    if not r.get("ok"):
        raise RuntimeError(r)
    return r


def wait_model(loaded):
    end = time.monotonic() + 300
    while time.monotonic() < end:
        models = call(ROUTER, "/models")["data"]
        status = next(m.get("status", {}) for m in models if m["id"] == MODEL)
        if status.get("failed"):
            raise RuntimeError(status)
        if status.get("value") == ("loaded" if loaded else "unloaded"):
            return
        time.sleep(2)
    raise TimeoutError("El modelo no terminó de cargar/descargar")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ai-mode", action="store_true", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    out = args.output.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = time.time()
    state = call(PANEL, "/api/state")
    if state["ai_mode"]["active"] or any(v != "inactive" for v in state["svc"].values()):
        raise RuntimeError("Requiere modo IA y servidores apagados para poder restaurar el estado inicial")
    settings = Path.home() / ".config/llama-panel/settings.json"
    before_hash = hashlib.sha256(settings.read_bytes()).hexdigest()
    limits = dict(line.split("=", 1) for line in command(
        "systemctl", "--user", "show", "llama-router", "-p", "MemoryMax", "-p", "MemorySwapMax").splitlines())
    metadata = {"started": started, "initial_state": state, "settings_sha256": before_hash,
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "panel_commit": command("git", "rev-parse", "HEAD"),
                "fork_commit": command("git", "-C", str(Path.home()/"llama.cpp"), "rev-parse", "HEAD"),
                "kernel": command("uname", "-r"), "initial_limits": limits}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))
    stop = threading.Event()
    phase = ["setup"]
    gpu = next(Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_total")).parent

    def monitor():
        with (out / "memory.jsonl").open("w") as f:
            while not stop.is_set():
                sample = {"t": time.time(), "phase": phase[0], "processes": []}
                for key in ("mem_info_vram_used", "mem_info_gtt_used", "gpu_busy_percent"):
                    try:
                        sample[key] = int((gpu / key).read_text())
                    except OSError:
                        pass
                for proc in Path("/proc").glob("[0-9]*"):
                    try:
                        if (proc / "comm").read_text().strip() != "llama-server":
                            continue
                        clients = {}
                        for fd in (proc / "fdinfo").iterdir():
                            text = fd.read_text()
                            cid = re.search(r"^drm-client-id:\s*(\d+)", text, re.M)
                            if cid:
                                clients[cid[1]] = {k: int(v) * 1024 for k, v in re.findall(
                                    r"^(drm-memory-vram|drm-memory-gtt):\s*(\d+)\s+KiB", text, re.M)}
                        sample["processes"].append({"pid": int(proc.name), "clients": clients})
                    except (OSError, ValueError):
                        continue
                f.write(json.dumps(sample) + "\n")
                f.flush()
                stop.wait(2)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    results = []

    def completion(label, prompt, n, temp):
        phase[0] = label
        print(f"{label}: comienzo ({n} tokens de salida)", flush=True)
        r = call(ROUTER, "/completion", {"model": MODEL, "prompt": prompt, "n_predict": n,
                 "temperature": temp, "seed": 1234, "ignore_eos": True, "cache_prompt": False}, timeout=2400)
        (out / f"{label}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2))
        result = {"label": label, "timings": r["timings"], "truncated": r.get("truncated"),
                  "stop_type": r.get("stop_type"), "tokens_predicted": r.get("tokens_predicted")}
        results.append(result)
        (out / "results.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(result), flush=True)
        if r.get("truncated") or r.get("stop_type") != "limit" or r["timings"]["predicted_n"] < n:
            raise RuntimeError(f"{label}: salida incompleta o contexto truncado")
        return r

    ai_entered = False
    limits_changed = False
    try:
        command("systemctl", "--user", "set-property", "--runtime", "llama-router.service",
                "MemoryMax=58G", "MemorySwapMax=0")
        limits_changed = True
        ai_entered = True  # restaurar también si la llamada cambia el estado y luego falla
        action("ai_mode_on")
        phase[0] = "load-1"
        r = call(PANEL, "/api/text/load", {"id": MODEL}, timeout=120)
        if not r.get("ok"):
            raise RuntimeError(r)
        wait_model(True)
        (out / "presets-ai.ini").write_bytes((settings.parent / "presets.ini").read_bytes())
        props = call(ROUTER, "/props?model=" + MODEL)
        (out / "props.json").write_text(json.dumps(props, indent=2))
        if props["default_generation_settings"]["n_ctx"] != 65536 or props["total_slots"] != 1:
            raise RuntimeError("El router no aplicó 64K con un solo slot")
        completion("short-load-1", "Explicá cómo funciona una memoria caché.\n", 128, 0)
        source = "\n".join(p.read_text(errors="replace") for p in sorted(
            (Path.home()/"llama.cpp/docs").rglob("*.md")))
        source = source[:600000] + "\nExplicá en español los temas técnicos del texto y sus relaciones, con ejemplos.\n"
        tokens = call(ROUTER, "/tokenize", {"model": MODEL, "content": source, "add_special": True}, timeout=120)["tokens"]
        if len(tokens) < 62000:
            raise RuntimeError(f"Fuente insuficiente: {len(tokens)} tokens")
        prompt = tokens[:62000]
        (out / "prompt-tokens.json").write_text(json.dumps(prompt))
        long = completion("near-64k", prompt, 2048, 0.8)
        if long["timings"]["prompt_n"] < 62000:
            raise RuntimeError("No se procesaron los 62000 tokens solicitados")
        for cycle in (2, 3):
            phase[0] = f"unload-{cycle-1}"
            r = call(PANEL, "/api/text/unload", {"id": MODEL})
            if not r.get("ok"):
                raise RuntimeError(r)
            wait_model(False)
            time.sleep(6)
            phase[0] = f"load-{cycle}"
            r = call(PANEL, "/api/text/load", {"id": MODEL}, timeout=120)
            if not r.get("ok"):
                raise RuntimeError(r)
            wait_model(True)
            completion(f"short-load-{cycle}", "Explicá cómo funciona una memoria caché.\n", 128, 0)
    finally:
        phase[0] = "cleanup"
        # Intentar cada restauración aunque otra falle; no dejar el escritorio apagado por un error HTTP.
        errors = []
        for name in (["stop", "ai_mode_off"] if ai_entered else ["stop"]):
            try:
                action(name)
            except Exception as e:
                errors.append(f"{name}: {e}")
        if limits_changed:
            try:
                command("systemctl", "--user", "set-property", "--runtime", "llama-router.service",
                        *[f"{k}={v}" for k, v in limits.items()])
            except Exception as e:
                errors.append(f"limits: {e}")
        stop.set()
        watcher.join(5)
        since = f"@{int(started)}"
        for filename, cmd in (("router.log", ["journalctl", "--user", "-u", "llama-router", "--since", since, "--no-pager", "-o", "short-unix"]),
                              ("kernel.log", ["journalctl", "-k", "--since", since, "--no-pager"])):
            try:
                (out / filename).write_text(command(*cmd) + "\n")
            except Exception as e:
                errors.append(f"{filename}: {e}")
        if hashlib.sha256(settings.read_bytes()).hexdigest() != before_hash:
            errors.append("settings.json cambió durante la prueba")
        (out / "cleanup.json").write_text(json.dumps({"errors": errors, "finished": time.time()}, indent=2))
        if errors:
            raise RuntimeError(errors)
    faults = []
    for filename in ("router.log", "kernel.log"):
        for line in (out / filename).read_text().splitlines():
            if re.search(r"VK_ERROR|device lost|out of memory|oom-kill|GPU reset|ring .*timeout|GPU fault", line, re.I):
                faults.append(line)
    (out / "faults.json").write_text(json.dumps(faults, indent=2))
    if faults:
        raise RuntimeError("Se encontraron fallos en los registros; ver faults.json")
    samples = [json.loads(line) for line in (out / "memory.jsonl").read_text().splitlines()]
    long_samples = [s for s in samples if s["phase"] == "near-64k"]
    clients = [p["clients"] for s in long_samples for p in s["processes"] if p["clients"]]
    if not clients:
        raise RuntimeError("Faltan muestras DRM de la carga larga")
    short_texts = [json.loads((out / f"short-load-{n}.json").read_text())["content"] for n in (1, 2, 3)]
    summary = {"requests": results, "short_outputs_identical": len(set(short_texts)) == 1,
               "peak_total_vram_gib": max(s["mem_info_vram_used"] for s in long_samples) / 2**30,
               "peak_process_gib": {k: max(sum(c.get(k, 0) for c in cl.values()) for cl in clients) / 2**30
                                    for k in ("drm-memory-vram", "drm-memory-gtt")},
               "unload_min_vram_gib": {phase: min(s["mem_info_vram_used"] for s in samples if s["phase"] == phase) / 2**30
                                       for phase in ("unload-1", "unload-2")},
               "detected_faults": faults}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (out / "PASS").write_text("Carga larga completa y tres cargas con generación, sin truncamiento ni fallos detectados.\n")
    print(f"Resultados en {out}", flush=True)


if __name__ == "__main__":
    main()
