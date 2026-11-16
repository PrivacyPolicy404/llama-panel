"""Estadisticas y registros sacados de journalctl.

Las funciones parse_* son puras (reciben lineas) para poder testearlas sin journald.
"""
import os
import re
import threading
import time
from pathlib import Path

from . import config as C
from .services import run, router, router_models
from .text_models import scan_models

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# llama-server: "[pid] ... task N | prompt eval time = X ms / N tokens (... Y tokens per second)"
TIMING_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| +(prompt eval|eval) time = +([\d.]+) ms / +(\d+) tokens"
                       r".*?([\d.]+) tokens per second")
DRAFT_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| draft acceptance = ([\d.]+) \( *(\d+) accepted / *(\d+) generated")
LOADM_RE = re.compile(r"^\[(\d+)\].*load_model: loading model '(.+)'")
# KoboldCpp: "[HH:MM:SS] Generating Image (27 steps)" ... "[HH:MM:SS] Generating Media Complete"
KIMG_START = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] Generating Image \((\d+) steps\)")
KIMG_END = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] Generating Media Complete")
PROGRESS_RE = re.compile(r"^([A-Za-z ]+): \d+/(\d+) steps")
CACHE_SECONDS = 5
# progreso en vivo de llama-server (cada ~3 s) y fin de cada respuesta
NGEN_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| n_gen = +(\d+), tg = +([\d.]+) t/s, tg_3s = +([\d.]+) t/s")
PROMPT_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| prompt processing, n_tokens = +(\d+), progress = ([\d.]+),"
                       r" t = +([\d.]+) s / +([\d.]+) tokens per second")
RELEASE_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| stop processing: n_tokens = (\d+)")
NCTX_RE = re.compile(r"^\[(\d+)\].*n_ctx_slot = (\d+)")
DRAFT_LEN_RE = re.compile(r"mean len = +([\d.]+)")
LIVE_FRESH = 8  # segundos sin novedades -> señalar dato atrasado, conservando la fase y las barras
LIVE_CACHE = 1.5


# ---------- parseo (puro) ----------
def clean_log_line(line):
    if "\x1b[K" in line:  # barras de progreso que se reescriben: queda el ultimo estado
        parts = [p for p in line.split("\x1b[K") if p.strip()]
        line = parts[-1] if parts else ""
    return ANSI_RE.sub("", line).replace("\r", "")


def parse_journal_unix(text):
    """Lineas de 'journalctl -o short-unix' -> [(timestamp, mensaje)]."""
    out = []
    for line in text.splitlines():
        ts, _, rest = line.partition(" ")
        _, _, msg = rest.partition(": ")
        try:
            out.append((float(ts), msg))
        except ValueError:
            continue
    return out


def parse_text_stats(entries, path_to_id=None, limit=60):
    """[(ts, msg)] del router -> una entrada por respuesta con tokens/s de prompt y generacion."""
    path_to_id = path_to_id or {}
    pid_model, reqs = {}, {}
    for ts, msg in entries:
        if (mm := LOADM_RE.match(msg)):
            pid_model[mm[1]] = path_to_id.get(mm[2]) or Path(mm[2]).stem
        elif (mm := TIMING_RE.match(msg)):
            r = reqs.setdefault((mm[1], mm[2]), {"t": ts, "model": pid_model.get(mm[1], "?"), "p_tok": None,
                                                 "p_tps": None, "g_tok": None, "g_tps": None, "draft": None})
            if mm[3] == "prompt eval":
                r["p_tok"], r["p_tps"] = int(mm[5]), float(mm[6])
            else:
                r["g_tok"], r["g_tps"], r["t"] = int(mm[5]), float(mm[6]), ts
        elif (mm := DRAFT_RE.match(msg)):
            if (key := (mm[1], mm[2])) in reqs:
                reqs[key]["draft"] = {"rate": float(mm[3]), "acc": int(mm[4]), "gen": int(mm[5])}
    return [r for r in reqs.values() if r["g_tps"] is not None][-limit:]


LAUNCH_RE = re.compile(r"^\[(\d+)\].*?task (\d+) \| processing task")


def parse_live(entries, now=None, previous=None):
    """Estado incremental. Una tanda larga sin logs no significa que el pedido terminó."""
    now = time.time() if now is None else now
    empty = {"state": "idle", "n_gen": None, "tps": None, "gen_avg_tps": None,
             "prompt_progress": None, "prompt_tps": None, "prompt_elapsed": None, "gen_elapsed": None,
             "prompt_tokens": None, "prompt_total": None, "prompt_cached": 0, "ctx_used": None, "ctx_size": None,
             "prompt_log_tokens": None, "prompt_log_progress": None,
             "draft_rate": None, "draft_len": None, "task_id": None, "model_pid": None,
             "t": None, "stale": False, "age": None}
    out = {**empty, **(previous or {})}

    def task(pid, tid):
        key = f"{pid}:{tid}"
        if key != out["task_id"]:
            size = out["ctx_size"] if out["model_pid"] in (None, pid) else None
            out.update(empty, ctx_size=size, task_id=key, model_pid=pid)

    for ts, msg in entries:
        if (m := NCTX_RE.match(msg)):
            out.update(empty, ctx_size=int(m[2]), model_pid=m[1])
        elif (m := LAUNCH_RE.match(msg)):
            task(m[1], m[2])
            out.update(state="prompt", prompt_progress=0.0, prompt_tokens=0, ctx_used=0, t=ts)
        elif (m := PROMPT_RE.match(msg)):
            task(m[1], m[2])
            n, progress = int(m[3]), float(m[4])
            out.update(state="prompt", prompt_tokens=n, prompt_progress=progress, prompt_tps=float(m[6]),
                       prompt_log_tokens=n, prompt_log_progress=progress,
                       prompt_elapsed=float(m[5]), prompt_total=round((out["prompt_cached"] + n) / progress) if progress else None,
                       ctx_used=out["prompt_cached"] + n, t=ts)
        elif (m := NGEN_RE.match(msg)):
            task(m[1], m[2])
            n, avg = int(m[3]), float(m[4])
            out.update(state="gen", n_gen=n, tps=float(m[5]), gen_avg_tps=avg,
                       gen_elapsed=n / avg if avg else None, t=ts)
            if out["prompt_tokens"] is not None:
                out["prompt_progress"] = 1.0
                out["ctx_used"] = (out["prompt_total"] or out["prompt_tokens"]) + n
        elif (m := TIMING_RE.match(msg)):
            task(m[1], m[2])
            if m[3] == "prompt eval":
                out.update(prompt_tps=float(m[6]), prompt_tokens=int(m[5]), prompt_total=out["prompt_cached"] + int(m[5]),
                           prompt_elapsed=float(m[4]) / 1000, prompt_progress=1.0, t=ts)
            else:
                out.update(n_gen=int(m[5]), gen_avg_tps=float(m[6]), gen_elapsed=float(m[4]) / 1000, t=ts)
        elif (m := DRAFT_RE.match(msg)):
            if out["task_id"] == f"{m[1]}:{m[2]}":
                dl = DRAFT_LEN_RE.search(msg)
                out.update(draft_rate=float(m[3]), draft_len=float(dl[1]) if dl else None)
        elif (m := RELEASE_RE.match(msg)):
            if out["task_id"] == f"{m[1]}:{m[2]}":
                out.update(state="idle", ctx_used=int(m[3]), t=ts)
        elif "Stopped llama-router.service" in msg or re.search(r"instance name=.* exited with status", msg):
            out.update(empty)
    out["age"] = max(0, now - out["t"]) if out["t"] is not None else None
    out["stale"] = out["state"] != "idle" and out["age"] is not None and out["age"] > LIVE_FRESH
    return out


def merge_live_slot(live, slot):
    """El log cuenta tokens nuevos; /slots aporta el prefijo reutilizado y la secuencia residente."""
    task_id = (live.get("task_id") or "").rsplit(":", 1)[-1]
    if not slot.get("is_processing") or task_id != str(slot.get("id_task")):
        return live
    resident = slot.get("n_prompt_tokens")
    processed = slot.get("n_prompt_tokens_processed")
    cached = slot.get("n_prompt_tokens_cache")
    if resident is None or processed is None or cached is None:
        return live
    decoded = slot.get("next_token") or []
    n_gen = decoded[0].get("n_decoded", 0) if decoded else 0
    phase = "gen" if n_gen or live["state"] == "gen" else "prompt"
    # n_prompt_tokens es la secuencia residente, incluida la tanda en curso: no el largo total del pedido.
    progress = live.get("prompt_log_progress")
    logged = live.get("prompt_log_tokens")
    if phase == "gen":
        total = cached + processed
    elif progress and logged is not None:
        total = round((cached + logged) / progress)
    else:
        total = None
    return {**live, "state": phase, "ctx_size": slot.get("n_ctx", live.get("ctx_size")),
            "prompt_tokens": processed, "prompt_cached": cached, "prompt_total": total,
            "ctx_used": resident, "prompt_progress": 1.0 if phase == "gen" else min(1, (cached + processed) / total) if total else 0}


def parse_img_stats(entries, limit=40):
    """[(ts, msg)] de KoboldCpp -> una entrada por imagen con pasos y duracion."""
    gens, start = [], None
    for ts, msg in entries:
        msg = clean_log_line(msg)
        if (mm := KIMG_START.match(msg)):
            start = (int(mm[1]) * 3600 + int(mm[2]) * 60 + int(mm[3]), int(mm[4]))
        elif (mm := KIMG_END.match(msg)) and start:
            end = int(mm[1]) * 3600 + int(mm[2]) * 60 + int(mm[3])
            gens.append({"t": ts, "steps": start[1], "secs": (end - start[0]) % 86400})  # % por medianoche
            start = None
    return gens[-limit:]


def collapse_progress(lines):
    """Limpia lineas y deja solo la ultima de cada barra de progreso ("Decoding: 3/27", "Decoding: 4/27"...)."""
    out, prev = [], None
    for raw in lines:
        line = clean_log_line(raw)
        if not line.strip():
            continue
        mm = PROGRESS_RE.match(line)
        key = (mm[1], mm[2]) if mm else None
        if key and key == prev:
            out[-1] = line
        else:
            out.append(line)
        prev = key
    return out


# ---------- lectura de journald ----------
def journal(name, n, fmt="cat"):
    return run("journalctl", "--user", "-u", name, "-n", str(n), "--no-pager", "-o", fmt, timeout=10)


def text_stats():
    paths = {}
    for m in scan_models():
        paths[m["path"]] = m["id"]
        paths[os.path.realpath(m["path"])] = m["id"]
    return parse_text_stats(parse_journal_unix(journal(C.SERVICE, 6000, "short-unix")), paths)


def img_stats():
    return parse_img_stats(parse_journal_unix(journal(C.IMG_SERVICE, 4000, "short-unix")))


_cache = {"t": 0, "data": None}


def stats():
    if time.time() - _cache["t"] > CACHE_SECONDS:
        _cache["data"] = {"text": text_stats(), "img": img_stats()}
        _cache["t"] = time.time()
    return _cache["data"]


_live = {"t": 0, "data": None, "last_entry": 0}
_live_lock = threading.Lock()


def live():
    with _live_lock:
        if time.time() - _live["t"] > LIVE_CACHE:
            entries = parse_journal_unix(journal(C.SERVICE, 6000 if _live["data"] is None else 150, "short-unix"))
            new = [(ts, msg) for ts, msg in entries if ts > _live["last_entry"]]
            _live["data"] = parse_live(new, previous=_live["data"])
            # Sólo consultar slots del modelo cargado; nunca provocar una carga desde la telemetría.
            for mid, model in router_models().items():
                if model["status"] != "loaded":
                    continue
                try:
                    from urllib.parse import quote
                    slots = router("GET", "/slots?model=" + quote(mid, safe=""), timeout=1)
                    for slot in slots:
                        _live["data"] = merge_live_slot(_live["data"], slot)
                except Exception:  # backend apagado o versión sin /slots: conservar los logs
                    pass
            if new:
                _live["last_entry"] = new[-1][0]
            _live["t"] = time.time()
        return _live["data"]


def logs(which, n=300):
    name = {"text": C.SERVICE, "img": C.IMG_SERVICE, "panel": C.PANEL_SERVICE}.get(which, C.SERVICE)
    return collapse_progress(journal(name, n).splitlines())
