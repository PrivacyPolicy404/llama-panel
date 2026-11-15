"""Estadisticas y registros sacados de journalctl.

Las funciones parse_* son puras (reciben lineas) para poder testearlas sin journald.
"""
import os
import re
import time
from pathlib import Path

from . import config as C
from .services import run
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
LIVE_FRESH = 8  # segundos sin novedades -> ya no esta trabajando
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


def parse_live(entries, now=None):
    """[(ts, msg)] recientes del router -> que esta haciendo ahora (para el dashboard).

    state: "idle" | "prompt" (procesando el prompt) | "gen" (generando). Ademas: progreso, tok/s de los ultimos 3 s,
    contexto usado/tamano y la aceptacion del MTP de la ultima respuesta que lo uso."""
    now = now or time.time()
    out = {"state": "idle", "n_gen": None, "tps": None, "prompt_progress": None, "prompt_tps": None,
           "prompt_tokens": None, "ctx_used": None, "ctx_size": None, "draft_rate": None, "draft_len": None, "t": None}
    task, last_ts, released = None, None, set()
    for ts, msg in entries:
        if (m := NCTX_RE.match(msg)):
            out["ctx_size"] = int(m[2])
        elif (m := PROMPT_RE.match(msg)):
            task, last_ts = (m[1], m[2]), ts  # el numero de tarea se reinicia en cada proceso: va con el pid
            out.update(state="prompt", prompt_tokens=int(m[3]), prompt_progress=float(m[4]), prompt_tps=float(m[6]),
                       n_gen=None, tps=None)
        elif (m := NGEN_RE.match(msg)):
            task, last_ts = (m[1], m[2]), ts
            out.update(state="gen", n_gen=int(m[3]), tps=float(m[5]))
        elif (m := DRAFT_RE.match(msg)):
            out["draft_rate"] = float(m[3])
            dl = DRAFT_LEN_RE.search(msg)
            out["draft_len"] = float(dl[1]) if dl else None
        elif (m := RELEASE_RE.match(msg)):
            released.add((m[1], m[2]))
            out["ctx_used"] = int(m[3])
            last_ts = ts
    out["t"] = last_ts
    if task is None or task in released or last_ts is None or now - last_ts > LIVE_FRESH:
        out["state"] = "idle"
    return out


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


_live = {"t": 0, "data": None}


def live():
    if time.time() - _live["t"] > LIVE_CACHE:
        _live["data"] = parse_live(parse_journal_unix(journal(C.SERVICE, 150, "short-unix")))
        _live["t"] = time.time()
    return _live["data"]


def logs(which, n=300):
    name = {"text": C.SERVICE, "img": C.IMG_SERVICE, "panel": C.PANEL_SERVICE}.get(which, C.SERVICE)
    return collapse_progress(journal(name, n).splitlines())
