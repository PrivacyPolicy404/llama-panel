#!/usr/bin/env python3
"""Panel web para controlar llama-server (texto) y KoboldCpp (imagenes). Solo biblioteca estandar.

Uso:
  python3 panel.py --install   # instala los servicios de systemd (usuario) y arranca el panel
  python3 panel.py             # corre el panel en primer plano (puerto 8090)
"""
import glob
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
CFG_DIR = Path(os.environ.get("LLAMA_PANEL_DIR", HOME / ".config" / "llama-panel"))
SETTINGS_FILE = CFG_DIR / "settings.json"
PRESET_FILE = CFG_DIR / "presets.ini"
IMG_ENV = CFG_DIR / "kobold.env"
MODELS_DIR = Path(os.environ.get("LLAMA_MODELS_DIR", "/mnt/hdd1/gen/models"))
IMG_DIR = Path(os.environ.get("LLAMA_IMG_DIR", "/mnt/hdd1/gen/imgmodels"))
LLAMA_SERVER = os.environ.get("LLAMA_SERVER_BIN", str(HOME / "llama.cpp/build/bin/llama-server"))
KOBOLD_BIN = os.environ.get("KOBOLD_BIN", "/mnt/hdd1/gen/koboldcpp/koboldcpp")
SERVICE = "llama-router"
IMG_SERVICE = "kobold-img"
PANEL_SERVICE = "llama-panel"
PANEL_PORT = int(os.environ.get("LLAMA_PANEL_PORT", "8090"))
CHAT_PORT = 8080
IMG_PORT = 5001

DEFAULT_GLOBAL = "n-gpu-layers = 99\nctx-size = 8192\nreasoning-format = deepseek"
LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\s*=\s*\S.*$")
MTP_RE = re.compile(r"(^|[-_.])MTP([-_.]|$)")
REASONING_OPTS = [("", "por defecto"), ("auto", "auto"), ("on", "activado"), ("off", "apagado")]
IMG_EXT = (".gguf", ".safetensors", ".ckpt")
ARG_RE = re.compile(r"^[A-Za-z0-9_./:=+,-]+$")


# ---------- ajustes ----------
def clean_lines(text):
    """Devuelve (lineas_validas, lineas_con_error). Formato: clave = valor."""
    ok, bad = [], []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if LINE_RE.match(line):
            k, v = [x.strip() for x in line.split("=", 1)]
            ok.append(f"{k} = {v}")
        else:
            bad.append(line)
    return ok, bad


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except Exception:
        data = {}
    data.setdefault("global", DEFAULT_GLOBAL)
    data.setdefault("models", {})
    img = data.setdefault("image", {})
    img.setdefault("selected", "")
    img.setdefault("models", {})
    return data


def save_settings(data):
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------- modelos de texto ----------
def scan_models():
    found = []
    if not MODELS_DIR.is_dir():
        return found
    seen = set()
    for p in sorted(MODELS_DIR.rglob("*.gguf")):
        if p.name.lower().startswith("mmproj"):
            continue
        try:
            real = os.path.realpath(p)
            size = os.path.getsize(real)
        except OSError:
            continue
        if real in seen or size < 50 * 1024 * 1024:
            continue
        seen.add(real)
        found.append({"key": str(p.relative_to(MODELS_DIR)), "path": str(p), "size": size})
    used = {}
    for m in found:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(m["key"]).stem)
        n = used.get(base, 0) + 1
        used[base] = n
        m["id"] = base if n == 1 else f"{base}_{n}"
    return found


def model_cfg(settings, m):
    default = {"mtp": bool(MTP_RE.search(Path(m["key"]).stem)), "nmax": "2", "reasoning": "", "ctx": "", "extra": ""}
    saved = settings["models"].get(m["key"]) or {}
    return {**default, **saved}


def build_preset(settings, models):
    glob_lines, _ = clean_lines(settings["global"])
    out = ["# Generado por llama-panel. No editar a mano: se reescribe al guardar.", "", "[*]"]
    out += glob_lines + [""]
    for m in models:
        cfg = model_cfg(settings, m)
        extra, _ = clean_lines(cfg.get("extra", ""))
        keys = {l.split("=", 1)[0].strip() for l in extra}
        fields = []
        if cfg.get("mtp"):
            fields += ["spec-type = draft-mtp", f"spec-draft-n-max = {cfg.get('nmax') or 2}"]
        if cfg.get("reasoning"):
            fields.append(f"reasoning = {cfg['reasoning']}")
        if cfg.get("ctx"):
            fields.append(f"ctx-size = {cfg['ctx']}")
        fields = [f for f in fields if f.split("=", 1)[0].strip() not in keys]  # lo de "extra" manda
        out += [f"[{m['id']}]", f"model = {m['path']}"] + fields + extra + [""]
    return "\n".join(out)


def write_preset():
    settings = load_settings()
    models = scan_models()
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    PRESET_FILE.write_text(build_preset(settings, models))
    return models


# ---------- modelos de imagen ----------
def _img_role(name):
    """Detecta si un archivo es un acompanante (VAE / codificador de texto) y con que opcion se carga."""
    n = name.lower()
    if "vae" in n:
        return "sdvae"
    if re.search(r"clip[-_]?l(?![a-z])", n):
        return "sdclip1"
    if re.search(r"clip[-_]?g(?![a-z])|t5", n):
        return "sdclip2"
    if re.search(r"qwen|llm|gemma|mistral|encoder", n):
        return "sdllm"
    return None


def scan_img_models():
    if not IMG_DIR.is_dir():
        return []
    files = []
    for p in sorted(IMG_DIR.rglob("*")):
        if p.suffix.lower() not in IMG_EXT or not p.is_file():
            continue
        if re.search(r"\s", str(p)):  # rutas con espacios no se pueden pasar por systemd de forma simple
            continue
        try:
            size = os.path.getsize(os.path.realpath(p))
        except OSError:
            continue
        files.append((p, size, _img_role(p.name)))
    mains = []
    for p, size, role in files:
        if role or size < 100 * 1024 * 1024:
            continue
        comps = []
        if p.parent != IMG_DIR:  # los acompanantes se toman de la misma subcarpeta
            seen = {}
            for q, _qs, qr in files:
                if qr and q.parent == p.parent and qr not in seen:
                    seen[qr] = q
            comps = [(flag, str(q)) for flag, q in sorted(seen.items())]
        mains.append({"key": str(p.relative_to(IMG_DIR)), "path": str(p), "size": size, "comps": comps})
    used = {}
    for m in mains:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(m["key"]).stem)
        n = used.get(base, 0) + 1
        used[base] = n
        m["id"] = base if n == 1 else f"{base}_{n}"
    return mains


def pick_img(settings, models):
    sel = settings["image"].get("selected", "")
    for m in models:
        if m["key"] == sel:
            return m
    return models[0] if models else None


def build_kobold_args(m, extra=""):
    args = ["--sdmodel", m["path"]]
    for flag, path in m["comps"]:
        args += [f"--{flag}", path]
    args += ["--usevulkan", "0", "--host", "0.0.0.0", "--port", str(IMG_PORT)]
    try:
        args += shlex.split(extra or "")
    except ValueError:
        pass
    return args


def write_img_env():
    settings = load_settings()
    m = pick_img(settings, scan_img_models())
    if not m:
        return None
    cfg = settings["image"]["models"].get(m["key"]) or {}
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    IMG_ENV.write_text("KOBOLD_ARGS=" + " ".join(build_kobold_args(m, cfg.get("extra", ""))) + "\n")
    return m


# ---------- sistema ----------
def run(*cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def svc(action, name=SERVICE):
    return run("systemctl", "--user", action, name)


def svc_state(name=SERVICE):
    return run("systemctl", "--user", "is-active", name) or "unknown"


def vram():
    for d in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        try:
            used = int(Path(d, "mem_info_vram_used").read_text())
            total = int(Path(d, "mem_info_vram_total").read_text())
            return used / 2**30, total / 2**30
        except Exception:  # noqa: BLE001
            continue
    return None


def vram_text():
    v = vram()
    return f"{v[0]:.1f} / {v[1]:.1f} GiB" if v else "n/d"


def stats():
    raw = run("journalctl", "--user", "-u", SERVICE, "-n", "400", "--no-pager", "-o", "cat")
    lines = [l for l in raw.splitlines() if re.search(r"print_timing|draft acceptance|eval time", l)]
    return "\n".join(lines[-8:]) or "Todavia no hay estadisticas (genera una respuesta primero)."


def logs(name=SERVICE):
    return run("journalctl", "--user", "-u", name, "-n", "30", "--no-pager", "-o", "cat")


# ---------- pagina ----------
CSS = """
body{font-family:system-ui,sans-serif;background:#111;color:#e6e6e6;margin:0;padding:24px;max-width:980px;margin:auto}
h1{margin:0 0 16px}h2{margin:28px 0 8px;font-size:1.05rem;color:#bbb}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px;background:#1b1b1b;border-radius:10px;margin-bottom:8px}
.bar b{min-width:64px}
.pill{padding:3px 10px;border-radius:99px;font-weight:600;background:#444}
.pill.active{background:#1e7e34}.pill.failed{background:#a71d2a}.pill.activating{background:#9a6700}
button{background:#2d6cdf;color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:.95rem;cursor:pointer}
button.red{background:#b3342f}button.grey{background:#444}
a{color:#7db4ff}textarea{width:100%;box-sizing:border-box;background:#161616;color:#e6e6e6;border:1px solid #333;border-radius:8px;padding:8px;font-family:ui-monospace,monospace;font-size:.85rem}
table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #2a2a2a;vertical-align:top;text-align:left}
.small{color:#999;font-size:.8rem}.msg{margin:14px 0;padding:10px 12px;background:#26323f;border-radius:8px;white-space:pre-wrap}
input,select{background:#161616;color:#e6e6e6;border:1px solid #333;border-radius:6px;padding:5px}
input[type=text]{width:100%;box-sizing:border-box;font-family:ui-monospace,monospace;font-size:.85rem}
pre{background:#161616;padding:10px;border-radius:8px;overflow:auto;font-size:.78rem;max-height:340px}
"""

POLL_JS = """
async function tick(){
  try{
    const r=await fetch('/status',{cache:'no-store'});const s=await r.json();
    for(const k of ['text','img']){
      const el=document.getElementById('pill_'+k);
      if(el){el.textContent=s[k];el.className='pill '+s[k];}
    }
    const v=document.getElementById('vram');if(v){v.textContent=s.vram;}
  }catch(e){}
}
setInterval(tick,3000);
"""


def page(msg=""):
    e = html.escape
    settings = load_settings()
    models = scan_models()
    img_models = scan_img_models()
    sel = pick_img(settings, img_models)
    st_text = svc_state(SERVICE)
    st_img = svc_state(IMG_SERVICE)
    parts = [
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Panel IA</title><style>", CSS, "</style></head><body>",
        "<h1>Panel IA</h1>",
        f"<div class='bar'><b>VRAM</b><span id='vram'>{e(vram_text())}</span>",
        "<form method='post' action='/action' style='margin:0'>"
        "<button name='do' value='stop_all' class='red'>Apagar todo</button></form></div>",
        "<div class='bar'><b>Texto</b>",
        f"<span id='pill_text' class='pill {e(st_text)}'>{e(st_text)}</span>",
        "<form method='post' action='/action' style='display:flex;gap:8px;margin:0'>",
        "<button name='do' value='start'>Encender</button>",
        "<button name='do' value='restart' class='grey'>Reiniciar</button>",
        "<button name='do' value='stop' class='red'>Apagar</button></form>",
        "<a id='chat' href='#'>Abrir chat</a></div>",
        "<div class='bar'><b>Imagenes</b>",
        f"<span id='pill_img' class='pill {e(st_img)}'>{e(st_img)}</span>",
        "<form method='post' action='/action' style='display:flex;gap:8px;margin:0'>",
        "<button name='do' value='img_start'>Encender</button>",
        "<button name='do' value='img_restart' class='grey'>Reiniciar</button>",
        "<button name='do' value='img_stop' class='red'>Apagar</button></form>",
        "<a id='sdui' href='#'>Abrir StableUI</a>",
        f"<span class='small'>modelo: {e(sel['id']) if sel else 'ninguno'}</span></div>",
        "<script>"
        "document.getElementById('chat').href='http://'+location.hostname+':" + str(CHAT_PORT) + "';"
        "document.getElementById('sdui').href='http://'+location.hostname+':" + str(IMG_PORT) + "/sdui';"
        "</script>",
        "<div class='small'>Texto e imagenes comparten la VRAM: al encender uno, el otro se apaga solo.</div>",
    ]
    if msg:
        parts.append(f"<div class='msg'>{e(msg)}</div>")
    parts += [
        "<form method='post' action='/save'>",
        "<h2>Ajustes globales de texto (valen para todos los modelos)</h2>",
        "<textarea name='global' rows='5'>", e(settings["global"]), "</textarea>",
        "<div class='small'>Una opcion por linea: <code>clave = valor</code>, con el nombre largo de la opcion de llama-server "
        "sin los guiones (ej. <code>ctx-size = 8192</code>).</div>",
        "<h2>Modelos de texto</h2>",
    ]
    if not models:
        parts.append(f"<p class='small'>No hay archivos .gguf en {e(str(MODELS_DIR))}.</p>")
    else:
        parts.append("<table><tr><th>Modelo</th><th>MTP</th><th>Razonamiento</th><th>Contexto</th>"
                     "<th>Opciones extra</th></tr>")
        for i, m in enumerate(models):
            cfg = model_cfg(settings, m)
            chk = "checked" if cfg.get("mtp") else ""
            opts = "".join(
                f"<option value='{v}' {'selected' if v == cfg.get('reasoning') else ''}>{t}</option>"
                for v, t in REASONING_OPTS)
            parts += [
                "<tr><td>", f"<b>{e(m['id'])}</b><br><span class='small'>{m['size'] / 1e9:.2f} GB</span>",
                f"<input type='hidden' name='key_{i}' value='{e(m['key'], quote=True)}'></td>",
                f"<td><input type='checkbox' name='mtp_{i}' {chk}> "
                f"<input type='number' min='1' max='16' name='nmax_{i}' value='{e(str(cfg.get('nmax') or 2))}' "
                "style='width:52px' title='borradores por paso'></td>",
                f"<td><select name='rs_{i}'>{opts}</select></td>",
                f"<td><input type='number' min='256' step='256' name='ctx_{i}' value='{e(str(cfg.get('ctx') or ''))}' "
                "placeholder='global' style='width:84px'></td>",
                f"<td><textarea name='extra_{i}' rows='2'>{e(cfg.get('extra', ''))}</textarea></td></tr>",
            ]
        parts.append("</table>")
    parts += [
        f"<input type='hidden' name='count' value='{len(models)}'>",
        "<p><button name='mode' value='apply'>Guardar y aplicar (reinicia el servidor)</button> ",
        "<button name='mode' value='save' class='grey'>Solo guardar</button></p></form>",
        "<form method='post' action='/save_img'>",
        "<h2>Modelos de imagen</h2>",
    ]
    if not img_models:
        parts.append(f"<p class='small'>No hay modelos (.gguf / .safetensors de mas de 100 MB) en {e(str(IMG_DIR))}.</p>")
    else:
        parts.append("<table><tr><th>Usar</th><th>Modelo</th><th>Archivos que carga con el</th>"
                     "<th>Opciones extra</th></tr>")
        for i, m in enumerate(img_models):
            cfg = settings["image"]["models"].get(m["key"]) or {}
            checked = "checked" if sel and m["key"] == sel["key"] else ""
            comps = "<br>".join(f"<span class='small'>{e(f)}: {e(Path(p).name)}</span>" for f, p in m["comps"]) \
                or "<span class='small'>solo el modelo</span>"
            parts += [
                f"<tr><td><input type='radio' name='img_selected' value='{e(m['key'], quote=True)}' {checked}></td>",
                f"<td><b>{e(m['id'])}</b><br><span class='small'>{m['size'] / 1e9:.2f} GB</span>",
                f"<input type='hidden' name='imgkey_{i}' value='{e(m['key'], quote=True)}'></td>",
                f"<td>{comps}</td>",
                f"<td><input type='text' name='imgextra_{i}' value='{e(cfg.get('extra', ''), quote=True)}' "
                "placeholder='ej: --sdoffloadcpu --sdtiledvae 768'></td></tr>",
            ]
        parts.append("</table>")
        parts.append("<div class='small'>Las opciones extra son flags de KoboldCpp separados por espacios "
                     "(ej. <code>--sdoffloadcpu</code>, <code>--sdlora /ruta/archivo.safetensors</code>).</div>")
    parts += [
        f"<input type='hidden' name='img_count' value='{len(img_models)}'>",
        "<p><button name='mode' value='apply'>Guardar y aplicar (reinicia el generador si esta encendido)</button> ",
        "<button name='mode' value='save' class='grey'>Solo guardar</button></p></form>",
        "<h2>Estadisticas recientes (texto)</h2><pre>", e(stats()), "</pre>",
        "<h2>Registro del servidor de texto</h2><pre>", e(logs(SERVICE)), "</pre>",
        "<h2>Registro del generador de imagenes</h2><pre>", e(logs(IMG_SERVICE)), "</pre>",
        "<p class='small'>Los modelos nuevos aparecen al recargar esta pagina; para que el servidor los use, "
        "toca <b>Reiniciar</b> o <b>Guardar y aplicar</b>.</p>",
        "<script>", POLL_JS, "</script></body></html>",
    ]
    return "".join(parts)


# ---------- acciones ----------
def do_action(action):
    if action == "stop":
        svc("stop")
        return "Servidor de texto apagado."
    if action in ("start", "restart"):
        note = ""
        if svc_state(IMG_SERVICE) == "active":
            svc("stop", IMG_SERVICE)
            note = " Generador de imagenes apagado para liberar VRAM."
        n = len(write_preset())
        out = svc("restart" if action == "restart" else "start")
        return (f"Configuracion regenerada ({n} modelos). "
                f"{'Reiniciado' if action == 'restart' else 'Encendido'}.{note} {out}").strip()
    if action in ("img_start", "img_restart"):
        m = write_img_env()
        if not m:
            return f"No hay modelos de imagen en {IMG_DIR}."
        note = ""
        if svc_state(SERVICE) == "active":
            svc("stop")
            note = " Servidor de texto apagado para liberar VRAM."
        out = svc("restart" if action == "img_restart" else "start", IMG_SERVICE)
        return f"Generador de imagenes {'reiniciado' if action == 'img_restart' else 'encendido'} con {m['id']}.{note} {out}".strip()
    if action == "img_stop":
        svc("stop", IMG_SERVICE)
        return "Generador de imagenes apagado."
    if action == "stop_all":
        svc("stop")
        svc("stop", IMG_SERVICE)
        return "Todo apagado: la VRAM queda libre."
    return "Accion desconocida."


def do_save(form):
    settings = load_settings()
    errors = []
    gl = form.get("global", [""])[0]
    _, bad = clean_lines(gl)
    errors += [f"global: {b}" for b in bad]
    models_cfg = {}
    try:
        count = int(form.get("count", ["0"])[0])
    except ValueError:
        count = 0
    valid_rs = {v for v, _ in REASONING_OPTS}
    for i in range(count):
        key = form.get(f"key_{i}", [""])[0]
        extra = form.get(f"extra_{i}", [""])[0]
        nmax = form.get(f"nmax_{i}", ["2"])[0].strip() or "2"
        rs = form.get(f"rs_{i}", [""])[0]
        ctx = form.get(f"ctx_{i}", [""])[0].strip()
        _, bad = clean_lines(extra)
        errors += [f"{key}: {b}" for b in bad]
        if not (nmax.isdigit() and 1 <= int(nmax) <= 16):
            errors.append(f"{key}: borradores MTP debe ser un numero entre 1 y 16 (puso '{nmax}')")
        if ctx and not (ctx.isdigit() and int(ctx) >= 256):
            errors.append(f"{key}: contexto debe ser un numero de 256 o mas (puso '{ctx}')")
        if rs not in valid_rs:
            errors.append(f"{key}: razonamiento invalido '{rs}'")
        models_cfg[key] = {"mtp": f"mtp_{i}" in form, "nmax": nmax, "reasoning": rs, "ctx": ctx, "extra": extra}
    if errors:
        return "No se guardo. Revisa estos campos (las opciones van como clave = valor):\n" + "\n".join(errors)
    settings["global"] = gl
    settings["models"] = models_cfg
    save_settings(settings)
    n = len(write_preset())
    if form.get("mode", ["save"])[0] == "apply":
        if svc_state() == "active":
            svc("restart")
            return f"Guardado ({n} modelos) y servidor reiniciado."
        return f"Guardado ({n} modelos). El servidor estaba apagado: tocalo en Encender cuando quieras."
    return f"Guardado ({n} modelos). Se aplica en el proximo reinicio del servidor."


def do_save_img(form):
    settings = load_settings()
    models = scan_img_models()
    valid_keys = {m["key"] for m in models}
    errors = []
    selected = form.get("img_selected", [""])[0]
    if selected and selected not in valid_keys:
        errors.append(f"modelo '{selected}' no existe en {IMG_DIR}")
    try:
        count = int(form.get("img_count", ["0"])[0])
    except ValueError:
        count = 0
    cfgs = {}
    for i in range(count):
        key = form.get(f"imgkey_{i}", [""])[0]
        extra = form.get(f"imgextra_{i}", [""])[0].strip()
        try:
            toks = shlex.split(extra)
        except ValueError as ex:
            errors.append(f"{key}: opciones extra mal escritas ({ex})")
            continue
        for t in toks:
            if not ARG_RE.match(t):
                errors.append(f"{key}: '{t}' tiene caracteres no permitidos (solo letras, numeros y . _ / : = + , -)")
        cfgs[key] = {"extra": extra}
    if errors:
        return "No se guardo.\n" + "\n".join(errors)
    settings["image"]["selected"] = selected or (models[0]["key"] if models else "")
    settings["image"]["models"] = cfgs
    save_settings(settings)
    m = write_img_env()
    name = m["id"] if m else "ninguno"
    if form.get("mode", ["save"])[0] == "apply":
        if svc_state(IMG_SERVICE) == "active":
            svc("restart", IMG_SERVICE)
            return f"Guardado (modelo de imagen: {name}) y generador reiniciado."
        return f"Guardado (modelo de imagen: {name}). El generador estaba apagado: tocalo en Encender cuando quieras."
    return f"Guardado (modelo de imagen: {name}). Se aplica en el proximo arranque del generador."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencio
        pass

    def _send(self, code, body, headers=None, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/status":
            body = json.dumps({"text": svc_state(SERVICE), "img": svc_state(IMG_SERVICE), "vram": vram_text()})
            return self._send(200, body, {"Cache-Control": "no-store"}, "application/json")
        if u.path != "/":
            return self._send(404, "no encontrado")
        q = urllib.parse.parse_qs(u.query)
        self._send(200, page(q.get("msg", [""])[0]))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n > 1_000_000:
            return self._send(413, "demasiado grande")
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)
        if self.path == "/action":
            msg = do_action(form.get("do", [""])[0])
        elif self.path == "/save":
            msg = do_save(form)
        elif self.path == "/save_img":
            msg = do_save_img(form)
        else:
            return self._send(404, "no encontrado")
        self._send(303, "", {"Location": "/?msg=" + urllib.parse.quote(msg)})


# ---------- instalacion ----------
def install():
    dest_dir = HOME / "llama-panel"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / "panel.py"
    if Path(__file__).resolve() != dest.resolve():
        shutil.copy2(__file__, dest)
    unit_dir = HOME / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    router = unit_dir / f"{SERVICE}.service"
    if router.exists() and not (unit_dir / f"{SERVICE}.service.bak").exists():
        shutil.copy2(router, unit_dir / f"{SERVICE}.service.bak")
    router.write_text(
        "[Unit]\nDescription=llama.cpp router server (Vulkan)\n"
        f"Conflicts={IMG_SERVICE}.service\n\n"
        "[Service]\nEnvironment=GGML_VK_VISIBLE_DEVICES=0\n"
        f"ExecStart={LLAMA_SERVER} --models-preset {PRESET_FILE} --models-max 1 --host 0.0.0.0 --port {CHAT_PORT}\n"
        "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
    )
    (unit_dir / f"{IMG_SERVICE}.service").write_text(
        "[Unit]\nDescription=KoboldCpp generador de imagenes (Vulkan)\n"
        f"Conflicts={SERVICE}.service\n\n"
        "[Service]\nEnvironment=GGML_VK_VISIBLE_DEVICES=0\n"
        f"EnvironmentFile={IMG_ENV}\n"
        f"ExecStart={KOBOLD_BIN} $KOBOLD_ARGS\n"
        "Restart=on-failure\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    (unit_dir / f"{PANEL_SERVICE}.service").write_text(
        "[Unit]\nDescription=Panel de control llama-server\n\n"
        f"[Service]\nExecStart=/usr/bin/python3 {dest}\nRestart=on-failure\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    n = len(write_preset())
    print(f"Configuracion de texto generada con {n} modelos: {PRESET_FILE}")
    m = write_img_env()
    if m:
        print(f"Generador de imagenes configurado con el modelo: {m['id']}")
    else:
        print(f"(No hay modelos de imagen todavia en {IMG_DIR}.)")
    if not Path(KOBOLD_BIN).exists():
        print(f"AVISO: no existe {KOBOLD_BIN}")
    print(run("systemctl", "--user", "daemon-reload") or "daemon-reload ok")
    print(run("systemctl", "--user", "enable", PANEL_SERVICE) or "panel habilitado")
    print(run("systemctl", "--user", "restart", PANEL_SERVICE) or "panel (re)iniciado")
    if svc_state() == "active":
        svc("restart")
        print("Servidor de modelos de texto reiniciado con la nueva configuracion.")
    print(f"\nListo. Abri http://god.local:{PANEL_PORT} desde la laptop.")


def main():
    if "--install" in sys.argv:
        return install()
    ThreadingHTTPServer(("0.0.0.0", PANEL_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()