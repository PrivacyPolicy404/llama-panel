"""A/B de slots para subagentes: velocidad individual y tiempo de dos trabajos.

python3 tools/bench/parallel_test.py --output DIRECTORIO_NUEVO
Requiere modo IA ya activo y ningún pedido en curso. Descarga el modelo del panel
(se pierde su caché), prueba un router aislado en :18081 y restaura la carga inicial.
No modifica settings ni presets del usuario. Mantiene 64K totales: comprobar contexto por slot.
Generación forzada de 256 tokens por trabajo: mide rendimiento, no calidad del agente.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import configparser
import hashlib
import json
import threading
import time
from pathlib import Path

from ctx_stability import PANEL, ROUTER, MODEL, action, call, command, wait_model

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:18081"
UNIT = "llama-parallel-bench.service"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--followup", action="store_true", help="Una ronda por variante, con continuación de las dos conversaciones")
    args = ap.parse_args()
    state = call(PANEL, "/api/state")
    if not state["ai_mode"]["active"] or state["svc"]["img"] != "inactive":
        raise RuntimeError("Requiere modo IA activo e imágenes apagadas")
    loaded = [k for k,v in state["text"]["models"].items() if v["status"] == "loaded"]
    for mid in loaded:
        if any(s["is_processing"] for s in call(ROUTER, "/slots?model="+mid)):
            raise RuntimeError("Hay un pedido activo; no interrumpirlo")
    if state["svc"]["text"] != "active" or loaded != [MODEL]:
        raise RuntimeError("Esta prueba espera Flash-Next cargado para restaurarlo al terminar")
    out = args.output.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    cfg_dir = Path.home()/".config/llama-panel"
    original = (cfg_dir/"presets.ini").read_bytes()
    (out/"original-presets.ini").write_bytes(original)
    settings_hash = hashlib.sha256((cfg_dir/"settings.json").read_bytes()).hexdigest()
    started = time.time()
    (out/"metadata.json").write_text(json.dumps({"started": started, "initial_state": state,
        "settings_sha256": settings_hash, "panel_commit": command("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        "fork_commit": command("git", "-C", str(Path.home()/"llama.cpp"), "rev-parse", "HEAD")},indent=2)+"\n")
    gpu = next(Path("/sys/class/drm").glob("card[0-9]*/device/mem_info_vram_total")).parent
    phase = ["setup"]
    stop = threading.Event()
    safety = []

    def monitor():
        with (out/"memory.jsonl").open("w") as f:
            while not stop.is_set():
                r = {"t": time.time(), "phase": phase[0]}
                for k in ("mem_info_vram_used", "mem_info_gtt_used"):
                    r[k] = int((gpu/k).read_text())
                procs=[]
                for p in Path("/proc").glob("[0-9]*"):
                    try:
                        if (p/"comm").read_text().strip() != "llama-server":
                            continue
                        clients={}
                        for fd in (p/"fdinfo").iterdir():
                            lines=fd.read_text().splitlines()
                            cid=next((x.split()[1] for x in lines if x.startswith("drm-client-id:")),None)
                            if cid:
                                clients[cid]={x.split()[0].rstrip(":"):int(x.split()[1])*1024 for x in lines
                                              if x.startswith(("drm-memory-vram:","drm-memory-gtt:"))}
                        procs.append({"pid":int(p.name),"clients":clients})
                    except (OSError,ValueError):
                        continue
                r["processes"]=procs
                if phase[0].endswith(("/warmup","/single","/pair","/followup")):
                    try:
                        r["slots"]=[{k:s.get(k) for k in ("id","is_processing","id_task","n_ctx")}
                                    for s in call(BASE,"/slots?model="+MODEL,timeout=1)]
                    except Exception:
                        pass
                f.write(json.dumps(r)+"\n");f.flush()
                if r["mem_info_gtt_used"] > 30.5*2**30:
                    safety.append(r)
                    command("systemctl","--user","stop",UNIT)
                    break
                stop.wait(1)

    # Dos trabajos de igual presupuesto, con contenido distinto y sin reuso entre pedidos.
    prompts = ["Explicá en español el flujo y los casos límite del siguiente código. Sé concreto.\n"+
               (ROOT/name).read_text()[:5000] for name in ("llama_panel/stats.py","llama_panel/actions.py")]
    (out/"prompts.json").write_text(json.dumps(prompts,ensure_ascii=False,indent=2)+"\n")
    watcher=threading.Thread(target=monitor,daemon=True)
    results=[]
    stopped=False
    try:
        action("stop");stopped=True
        command("sudo","-n","/usr/local/sbin/llama-gpu-mclk","high")
        watcher.start()
        variants = ((1,"p1-conversation"),(2,"p2-conversation")) if args.followup else ((1,"p1-a"),(2,"p2-a"),(1,"p1-b"),(2,"p2-b"))
        for np, label in variants:
            phase[0]=label+"/load"
            cfg=configparser.ConfigParser(interpolation=None)
            cfg.read_string(original.decode())
            cfg.set(MODEL,"parallel",str(np))
            cfg.set(MODEL,"ctx-size","65536")
            preset=out/(label+".ini")
            with preset.open("w") as f:cfg.write(f)
            log=out/(label+".log")
            print(label+": cargando",flush=True)
            command("systemd-run","--user","--collect","--unit="+UNIT,
                    "--property=MemoryMax=58G","--property=MemorySwapMax=0",
                    "--property=StandardOutput=append:"+str(log),"--property=StandardError=inherit",
                    "--setenv=GGML_VK_VISIBLE_DEVICES=0","--setenv=GGML_VK_MAX_NODES_PER_SUBMIT=16",
                    str(Path.home()/"llama.cpp/build/bin/llama-server"),"--models-preset",str(preset),
                    "--models-max","1","--host","127.0.0.1","--port","18081")
            end=time.monotonic()+300
            while True:
                try:
                    call(BASE,"/health",timeout=2);break
                except Exception:
                    if time.monotonic()>end:raise TimeoutError("Router de prueba")
                    time.sleep(1)
            call(BASE,"/models/load",{"model":MODEL},timeout=30)
            while True:
                data=call(BASE,"/models")["data"]
                st=next(m['status'] for m in data if m['id']==MODEL)
                if st.get('failed'):raise RuntimeError(st)
                if st['value']=='loaded':break
                if time.monotonic()>end:raise TimeoutError("Carga del modelo")
                time.sleep(1)
            props=call(BASE,"/props?model="+MODEL)
            slots=call(BASE,"/slots?model="+MODEL)
            (out/(label+"-props.json")).write_text(json.dumps(props,indent=2)+"\n")
            (out/(label+"-slots.json")).write_text(json.dumps(slots,indent=2)+"\n")
            contexts=[s['n_ctx'] for s in slots]
            if len(slots)!=np:raise RuntimeError("Cantidad de slots inesperada")
            print(label+": contextos por slot "+str(contexts),flush=True)

            def job(tag,prompt,slot,n=256,cache=False):
                begin=time.monotonic()
                r=call(BASE,"/completion",{"model":MODEL,"prompt":prompt,"id_slot":slot,
                       "temperature":0,"seed":1234,"n_predict":n,"ignore_eos":True,"cache_prompt":cache},timeout=600)
                (out/(label+"-"+tag+".json")).write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n")
                if r.get('truncated') or r['timings']['predicted_n']!=n:raise RuntimeError("Salida incompleta/truncada")
                return {"job":tag,"wall_s":time.monotonic()-begin,"timings":r['timings']}

            phase[0]=label+"/warmup"
            for i in range(np):job("warm"+str(i),"Explicá cómo funciona una caché.",i,32)
            phase[0]=label+"/single"
            single=job("single",prompts[0],0)
            def run_pair(tag,texts,cache=False):
                phase[0]=label+"/"+tag
                begin=time.monotonic()
                if np==1:
                    jobs=[job(tag+"-A",texts[0],0,cache=cache),job(tag+"-B",texts[1],0,cache=cache)]
                else:
                    gate=threading.Barrier(2)
                    def run_concurrent(i):
                        gate.wait()
                        return job(tag+"-"+"AB"[i],texts[i],i,cache=cache)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        jobs=list(pool.map(run_concurrent,(0,1)))
                return jobs,time.monotonic()-begin
            jobs,elapsed=run_pair("pair",prompts)
            result={"label":label,"parallel":np,"contexts":contexts,"single":single,"pair":jobs,
                    "pair_wall_s":elapsed,"pair_effective_tps":512/elapsed}
            if args.followup:
                follow=[]
                for i,letter in enumerate("AB"):
                    response=json.loads((out/(label+"-pair-"+letter+".json")).read_text())
                    follow.append(prompts[i]+response["content"]+"\nContinuá la explicación, con ejemplos de casos límite.\n")
                more,wall=run_pair("followup",follow,cache=True)
                result.update(followup=more,followup_wall_s=wall,followup_effective_tps=512/wall)
            results.append(result)
            (out/"results.json").write_text(json.dumps(results,indent=2)+"\n")
            print(json.dumps(result),flush=True)
            command("systemctl","--user","stop",UNIT)
            time.sleep(3)
    finally:
        # No dejar el router de prueba ocupando la GPU si falla una medición.
        import subprocess
        subprocess.run(["systemctl","--user","stop",UNIT],capture_output=True)
        stop.set()
        if watcher.is_alive():watcher.join(5)
        if stopped:
            action("start")
            r=call(PANEL,"/api/text/load",{"id":MODEL},timeout=120)
            if not r.get("ok"):raise RuntimeError(r)
            wait_model(True)
        (out/"kernel.log").write_text(command("journalctl","-k","--since",f"@{int(started)}","--no-pager")+"\n")
        cleanup={"settings_unchanged":hashlib.sha256((cfg_dir/"settings.json").read_bytes()).hexdigest()==settings_hash,
                 "final_state":call(PANEL,"/api/state"),"gtt_safety_stops":safety}
        (out/"cleanup.json").write_text(json.dumps(cleanup,indent=2)+"\n")
        if not cleanup["settings_unchanged"] or safety:raise RuntimeError("Revisar cleanup.json")
    print("Resultados: "+str(out),flush=True)


if __name__=="__main__":
    main()
