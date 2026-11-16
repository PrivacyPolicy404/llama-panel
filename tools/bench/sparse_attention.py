"""Ensayo aislado del umbral sparse Q8 en Polaris, con restauración del router.

Requiere modo IA activo, Flash-Next cargado e inactivo e imágenes apagadas.
Descarga el modelo (pierde su caché), conserva ajustes y lo recarga al terminar.
--mode ops verifica kernels contra CPU; --mode decode compara generación desde
estados guardados idénticos, sin incluir restauración/prefill en los tok/s.
La biblioteca experimental debe estar sola en --library-dir.
"""
import argparse
import configparser
import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path

from ctx_stability import PANEL, ROUTER, MODEL, action, call, command, wait_model

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:18081"
UNIT = "llama-sparse-bench.service"
SERVER = Path.home() / "llama.cpp/build/bin/llama-server"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--library-dir", type=Path, required=True)
    ap.add_argument("--test-bin", type=Path)
    ap.add_argument("--server", type=Path, default=SERVER, help="llama-server a usar (por defecto, el estable)")
    ap.add_argument("--ops-perf", action="store_true")
    ap.add_argument("--mode", choices=("ops", "decode"), required=True)
    ap.add_argument("--contexts", type=int, nargs="+", default=[20480, 30720])
    ap.add_argument("--ratios", nargs="+", default=["16", "4", "16", "8"], help="valor de cada brazo")
    ap.add_argument("--arm-env", default="GGML_VK_FA_Q8_SPARSE_RATIO", help="variable de entorno que recibe el valor de cada brazo")
    ap.add_argument("--arm-preset", help="clave del preset del modelo que recibe el valor de cada brazo (en vez de --arm-env)")
    ap.add_argument("--preset", nargs="*", default=[], metavar="CLAVE=VALOR", help="claves del preset para todos los brazos")
    ap.add_argument("--env", nargs="*", default=[], metavar="CLAVE=VALOR", help="variables de entorno para todos los brazos")
    ap.add_argument("--predict", type=int, default=128)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--mtp", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.0, help="con > 0 la semilla cambia por repetición (igual entre brazos)")
    ap.add_argument("--reuse-states", type=Path, help="corrida previa con ctx-N.bin y prompts.json (mismo modo MTP)")
    args = ap.parse_args()
    if any(n < 4096 or n + args.predict + 32 > 65536 for n in args.contexts):
        ap.error("Los contextos deben entrar en la ventana de 64K")
    if args.predict < 1 or args.reps < 1:
        ap.error("Presupuestos inválidos")
    lib = args.library_dir.expanduser().resolve()
    if not (lib / "libggml-vulkan.so.1").is_file():
        ap.error("Falta libggml-vulkan.so.1")
    if args.mode == "ops" and (not args.test_bin or not args.test_bin.is_file()):
        ap.error("--mode ops requiere --test-bin")
    state = call(PANEL, "/api/state")
    loaded = [k for k, v in state["text"]["models"].items() if v["status"] == "loaded"]
    if not state["ai_mode"]["active"] or state["svc"]["img"] != "inactive" or loaded != [MODEL]:
        raise RuntimeError("Se requiere modo IA activo, sólo Flash-Next cargado e imágenes apagadas")
    if any(s["is_processing"] for s in call(ROUTER, "/slots?model=" + MODEL)):
        raise RuntimeError("Hay un pedido activo; no interrumpirlo")
    out = args.output.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    cfg_dir = Path.home() / ".config/llama-panel"
    originals = {n: (cfg_dir / n).read_bytes() for n in ("settings.json", "presets.ini")}
    (out / "original-presets.ini").write_bytes(originals["presets.ini"])
    started = time.time()
    write_json(out / "metadata.json", {"started": started, "initial_state": state,
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "hashes": {k: hashlib.sha256(v).hexdigest() for k, v in originals.items()},
        "vulkan_sha256": hashlib.sha256((lib / "libggml-vulkan.so.1").read_bytes()).hexdigest()})
    gpu = next(Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_total")).parent
    phase = ["setup"]
    stop = threading.Event()
    safety = []

    def monitor():
        with (out / "memory.jsonl").open("w") as f:
            while not stop.is_set():
                row = {"t": time.time(), "phase": phase[0], "processes": []}
                for name in ("mem_info_vram_used", "mem_info_gtt_used", "gpu_busy_percent"):
                    row[name] = int((gpu / name).read_text())
                for proc in Path("/proc").glob("[0-9]*"):
                    try:
                        if (proc / "comm").read_text().strip() != "llama-server":
                            continue
                        clients = {}
                        for fd in (proc / "fdinfo").iterdir():
                            lines = fd.read_text().splitlines()
                            cid = next((x.split()[1] for x in lines if x.startswith("drm-client-id:")), None)
                            if cid:
                                clients[cid] = {x.split()[0].rstrip(":"): int(x.split()[1]) * 1024
                                    for x in lines if x.startswith(("drm-memory-vram:", "drm-memory-gtt:"))}
                        row["processes"].append({"pid": int(proc.name), "clients": clients})
                    except (OSError, ValueError):
                        continue
                f.write(json.dumps(row) + "\n")
                f.flush()
                if row["mem_info_gtt_used"] > 30.5 * 2**30:
                    safety.append(row)
                    subprocess.run(["systemctl", "--user", "stop", UNIT], capture_output=True)
                    return
                stop.wait(1)

    def launch(label, ratio, executable, extra, wait=False):
        phase[0] = label
        cmd = ["systemd-run", "--user", "--collect", "--unit=" + UNIT,
            "--property=MemoryMax=58G", "--property=MemorySwapMax=0",
            "--property=StandardOutput=append:" + str(out / (label + ".log")),
            "--property=StandardError=inherit", "--setenv=LD_LIBRARY_PATH=" + str(lib),
            "--setenv=GGML_VK_VISIBLE_DEVICES=0", "--setenv=GGML_VK_MAX_NODES_PER_SUBMIT=16", "--setenv=GGML_VK_FIRST_SUBMIT_NODES=2",
            *["--setenv=" + e for e in args.env],
            *([] if args.arm_preset else ["--setenv=" + args.arm_env + "=" + str(ratio)])]
        if wait:
            cmd.append("--wait")
        command(*cmd, str(executable), *extra)

    def halt():
        subprocess.run(["systemctl", "--user", "stop", UNIT], capture_output=True)

    def load(label, ratio, preset):
        print(label + ": cargando", flush=True)
        launch(label, ratio, args.server, ["--models-preset", str(preset), "--models-max", "1",
            "--host", "127.0.0.1", "--port", "18081", "--slot-save-path", str(out) + "/"])
        end = time.monotonic() + 300
        while True:
            try:
                call(BASE, "/health", timeout=2)
                break
            except Exception:
                if time.monotonic() > end:
                    raise TimeoutError(label)
                time.sleep(1)
        call(BASE, "/models/load", {"model": MODEL}, timeout=30)
        while time.monotonic() < end:
            status = next(m["status"] for m in call(BASE, "/models")["data"] if m["id"] == MODEL)
            if status.get("failed"):
                raise RuntimeError(status)
            if status["value"] == "loaded":
                slots = call(BASE, "/slots?model=" + MODEL)
                if len(slots) != 1 or slots[0]["n_ctx"] != 65536:
                    raise RuntimeError(slots)
                return
            time.sleep(1)
        raise TimeoutError(label)

    def completion(prompt, n, cache, rep=0):
        return call(BASE, "/completion", {"model": MODEL, "prompt": prompt, "id_slot": 0,
            "temperature": args.temperature, "seed": 1234 + (rep if args.temperature > 0 else 0),
            "n_predict": n, "ignore_eos": True,
            "cache_prompt": cache}, timeout=1200)

    watcher = threading.Thread(target=monitor, daemon=True)
    stopped = False
    results = []
    try:
        action("stop")
        stopped = True
        command("sudo", "-n", "/usr/local/sbin/llama-gpu-mclk", "high")
        watcher.start()
        if args.mode == "ops":
            pattern = "hsk=256.*nh=2.*type_K=q8_0.*n_kv_max=20(48|51)"
            for i, ratio in enumerate(args.ratios):
                label = f"ops-{i}-r{ratio}"
                print(label, flush=True)
                launch(label, ratio, args.test_bin, ["-b", "Vulkan0", "-o", "FLASH_ATTN_EXT", "-p", pattern], wait=True)
                log = (out / (label + ".log")).read_text()
                counts = re.search(r"(\d+)/(\d+) tests passed", log)
                if not counts or int(counts[1]) < 9 or counts[1] != counts[2] or "FAIL" in log:
                    raise RuntimeError("Revisar " + label)
                if args.ops_perf:
                    launch(label + "-perf", ratio, args.test_bin,
                        ["perf", "-b", "Vulkan0", "-o", "FLASH_ATTN_EXT", "-p", pattern], wait=True)
            return

        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(originals["presets.ini"].decode())
        if not args.mtp:
            for key in list(cfg[MODEL]):
                if key.startswith("spec-"):
                    cfg.remove_option(MODEL, key)
            cfg.set(MODEL, "spec-type", "none")
        for item in args.preset:
            key, _, value = item.partition("=")
            cfg.set(MODEL, key, value)
        preset = out / "bench.ini"
        with preset.open("w") as f:
            cfg.write(f)
        if args.reuse_states:
            prompts = {int(n): p for n, p in json.loads((args.reuse_states / "prompts.json").read_text()).items()}
            prompts = {n: prompts[n] for n in args.contexts}
            for n in prompts:
                (out / f"ctx-{n}.bin").hardlink_to(args.reuse_states / f"ctx-{n}.bin")
        else:
            load("prepare", 16, preset)
            write_json(out / "props.json", call(BASE, "/props?model=" + MODEL))
            corpus = "\n".join("=== " + str(p.relative_to(ROOT)) + " ===\n" + p.read_text()
                for p in sorted((ROOT / "llama_panel").glob("*.py")))
            corpus += "\n" + (ROOT / "docs/analisis-rendimiento.md").read_text()
            tokens = call(BASE, "/tokenize", {"model": MODEL, "content": corpus * 3, "add_special": True})["tokens"]
            suffix = call(BASE, "/tokenize", {"model": MODEL, "content":
                "\nFIN DEL MATERIAL. Explicá en español cómo carga modelos este panel y cómo reparte el trabajo "
                "entre CPU y GPU. Incluí ejemplos concretos y los principales casos límite.\nRespuesta:\n",
                "add_special": False})["tokens"]
            prompts = {n: tokens[:n - len(suffix)] + suffix for n in args.contexts}
            if any(len(p) != n for n, p in prompts.items()):
                raise RuntimeError("Corpus insuficiente")
            write_json(out / "prompts.json", prompts)
            for n, prompt in prompts.items():
                phase[0] = f"prepare/{n}"
                print(phase[0] + ": prefill", flush=True)
                response = completion(prompt, 1, False)
                write_json(out / f"prepare-{n}.json", response)
                if response.get("truncated") or response["timings"]["prompt_n"] != n:
                    raise RuntimeError("Prefill incorrecto")
                saved = call(BASE, "/slots/0?action=save&model=" + MODEL, {"model": MODEL, "filename": f"ctx-{n}.bin"}, timeout=120)
                write_json(out / f"save-{n}.json", saved)
            halt()

        for i, ratio in enumerate(args.ratios):
            label = f"arm-{i}-r{Path(ratio).name}"  # el valor puede ser una ruta
            arm_preset = preset
            if args.arm_preset:
                cfg.set(MODEL, args.arm_preset, ratio)
                arm_preset = out / (label + ".ini")
                with arm_preset.open("w") as f:
                    cfg.write(f)
            load(label, ratio, arm_preset)
            for n, prompt in prompts.items():
                for rep in range(args.reps + 1):
                    phase[0] = f"{label}/{n}/{rep}"
                    restored = call(BASE, "/slots/0?action=restore&model=" + MODEL,
                        {"model": MODEL, "filename": f"ctx-{n}.bin"}, timeout=120)
                    begin = time.monotonic()
                    response = completion(prompt, args.predict, True, rep)
                    elapsed = time.monotonic() - begin
                    write_json(out / f"{label}-{n}-{rep}.json", response)
                    timings = response["timings"]
                    if response.get("truncated") or timings["predicted_n"] != args.predict:
                        raise RuntimeError("Respuesta incompleta")
                    if timings["prompt_n"] > 8:  # el servidor reevalúa los últimos tokens tras restaurar
                        raise RuntimeError("Se reprocesó el prompt: revisar restauración")
                    result = {"arm": i, "ratio": ratio, "context": n, "rep": rep,
                        "warmup": rep == 0, "wall_s": elapsed, "restore": restored,
                        "timings": timings, "content_sha256": hashlib.sha256(response["content"].encode()).hexdigest()}
                    results.append(result)
                    write_json(out / "results.json", results)
                    print(json.dumps(result), flush=True)
            halt()
    finally:
        halt()
        stop.set()
        if watcher.is_alive():
            watcher.join(5)
        if stopped:
            action("start")
            loaded_again = call(PANEL, "/api/text/load", {"id": MODEL}, timeout=120)
            if not loaded_again.get("ok"):
                raise RuntimeError(loaded_again)
            wait_model(True)
        write_json(out / "cleanup.json", {"unchanged": {n: (cfg_dir / n).read_bytes() == v for n, v in originals.items()},
            "safety": safety, "final_state": call(PANEL, "/api/state")})
        (out / "kernel.log").write_text(command("journalctl", "-k", "--since", f"@{int(started)}", "--no-pager") + "\n")
        if safety or any((cfg_dir / n).read_bytes() != v for n, v in originals.items()):
            raise RuntimeError("Revisar memoria/ajustes en cleanup.json")


if __name__ == "__main__":
    main()
