"""Carga tipo agente por el router: llamadas a herramientas, reuso de la caché del prompt entre turnos y costo del
razonamiento. Requiere Flash-Next cargado. Uso: python3 tools/bench/agent_check.py (tarda ~5 minutos)."""
import json,time,urllib.request,os,glob
M="Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-hcq8"
def chat(messages, **kw):
    body={"model":M,"messages":messages,"temperature":0.6,"max_tokens":kw.pop("max_tokens",700),**kw}
    t=time.time()
    r=json.loads(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:"+os.environ.get("LLAMA_CHAT_PORT","8080")+"/v1/chat/completions",data=json.dumps(body).encode(),headers={"Content-Type":"application/json"}),timeout=1200).read())
    m=r["choices"][0]["message"]; tm=r.get("timings",{})
    return m, tm, time.time()-t
def show(tag,m,tm,w):
    print(f"{tag}: pared {w:.1f}s | prompt nuevo {tm.get('prompt_n')} tok ({tm.get('prompt_ms',0)/1000:.1f}s), de caché {tm.get('cache_n')} | salida {tm.get('predicted_n')} tok a {tm.get('predicted_per_second',0):.1f} tok/s | razonamiento {len(m.get('reasoning_content') or '')} car, respuesta {len(m.get('content') or '')} car, tool_calls {len(m.get('tool_calls') or [])}",flush=True)
# prefijo tipo agente: ~6K tokens de documentación como prompt de sistema
docs="".join(open(p,errors="ignore").read() for p in sorted(glob.glob(os.path.expanduser("~/llama.cpp/docs/*.md")))[:6])[:24000]
SYS="Sos un asistente de programación. Documentación de referencia:\n"+docs
tools=[{"type":"function","function":{"name":"read_file","description":"Lee un archivo del repositorio","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
       {"type":"function","function":{"name":"run_tests","description":"Corre los tests del proyecto","parameters":{"type":"object","properties":{"pattern":{"type":"string"}}}}}]
q1={"role":"user","content":"Necesito ver qué hace la función build_preset en llama_panel/text_models.py. Usá las herramientas."}
m1,tm,w=chat([{"role":"system","content":SYS},q1],tools=tools); show("1 frío, con herramientas",m1,tm,w)
print("   tool_calls:",json.dumps(m1.get("tool_calls"),ensure_ascii=False)[:300])
hist=[{"role":"system","content":SYS},q1,m1]
if m1.get("tool_calls"):
    src=open(os.path.expanduser("~/llama-panel/llama_panel/text_models.py")).read()[2500:5200]
    hist.append({"role":"tool","tool_call_id":m1["tool_calls"][0]["id"],"content":src})
else:
    hist.append({"role":"user","content":"Seguí."})
m2,tm,w=chat(hist,tools=tools); show("2 turno siguiente (prefijo igual)",m2,tm,w)
hist+= [m2,{"role":"user","content":"Resumilo en dos líneas."}]
m3,tm,w=chat(hist,tools=tools); show("3 otro turno",m3,tm,w)
hist2=[dict(hist[0],content="Fecha y hora: 2026-11-16 18:01:07\n"+SYS)]+hist[1:]+[m3,{"role":"user","content":"¿Y qué pasa si ai_mode es False?"}]
m4,tm,w=chat(hist2,tools=tools); show("4 mismo historial con una hora al inicio del sistema",m4,tm,w)
Q=[{"role":"user","content":"Escribí en Python una función que fusione intervalos solapados de una lista de tuplas, con un test corto."}]
for name,kw in (("razonamiento on",{}),("razonamiento off",{"chat_template_kwargs":{"enable_thinking":False}})):
    m,tm,w=chat(Q,max_tokens=3000,**kw); show("5 código, "+name,m,tm,w)
