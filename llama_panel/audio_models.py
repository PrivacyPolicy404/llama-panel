"""Modelos de audio: los de Whisper (oído) y las voces de Piper que haya en disco, y cuál se usa.

Se buscan en la carpeta de voz del panel (VOICE_DIR, donde instala scripts/install-voice.sh), en LLAMA_AUDIO_DIRS
(paths.env, separadas por ":") y en las carpetas de WHISPER_MODEL / PIPER_VOICE. La elección queda en settings.json
("audio": {"stt": ruta, "tts": ruta}); sin elección, valen WHISPER_MODEL y PIPER_VOICE.
"""
import json
import os
import re
from pathlib import Path

from . import config as C

WHISPER_RE = re.compile(r"^ggml-.+\.bin$")
MAX_DEPTH = 3


def audio_dirs():
    extra = [Path(p).expanduser() for p in C._env("LLAMA_AUDIO_DIRS", "", allow_empty=True).split(":") if p]
    dirs = [C.VOICE_DIR, *extra, Path(C.WHISPER_MODEL).parent, Path(C.PIPER_VOICE).parent]
    out = []
    for d in dirs:
        if d.is_dir() and d.resolve() not in [o.resolve() for o in out]:
            out.append(d)
    return out


def _walk(root, depth=0):
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    for e in entries:
        if e.is_dir(follow_symlinks=False) and depth < MAX_DEPTH and not e.name.startswith("."):
            if e.name.endswith("venv"):  # el venv de Piper trae miles de archivos
                continue
            yield from _walk(e.path, depth + 1)
        elif e.is_file():
            yield Path(e.path)


def scan():
    """{"stt": [...], "tts": [...]}: cada uno con id, ruta, tamaño y lo que se sepa (idioma, calidad)."""
    stt, tts, seen = [], [], set()
    for d in audio_dirs():
        for p in _walk(d):
            real = p.resolve()
            if real in seen:
                continue
            if WHISPER_RE.match(p.name) and "for-tests" not in p.name:
                seen.add(real)
                stt.append({"id": p.name[5:-4], "path": str(p), "size": p.stat().st_size,
                            "lang": "multi" if not p.name.endswith(".en.bin") and ".en-" not in p.name else "en"})
            elif p.suffix == ".onnx" and p.with_name(p.name + ".json").is_file():
                seen.add(real)
                try:
                    meta = json.loads(p.with_name(p.name + ".json").read_text())
                except (OSError, ValueError):
                    meta = {}
                lang = (meta.get("language") or {}).get("code") or p.name.split("-")[0]
                tts.append({"id": p.stem, "path": str(p), "size": p.stat().st_size, "lang": lang,
                            "quality": (meta.get("audio") or {}).get("quality"),
                            "sample_rate": (meta.get("audio") or {}).get("sample_rate"),
                            "speakers": meta.get("num_speakers", 1)})
    stt.sort(key=lambda m: m["size"])
    tts.sort(key=lambda m: m["id"])
    return {"stt": stt, "tts": tts}


def selected():
    """{"stt": ruta, "tts": ruta} en uso."""
    sel = C.load_settings().get("audio") or {}
    return {"stt": sel.get("stt") or C.WHISPER_MODEL, "tts": sel.get("tts") or C.PIPER_VOICE}


def select(kind, path):
    """Elige el modelo de Whisper ("stt") o la voz de Piper ("tts"). Devuelve un error o None."""
    if kind not in ("stt", "tts"):
        return "tipo inválido"
    if path not in {m["path"] for m in scan()[kind]}:
        return "no está en las carpetas de audio"
    with C.SETTINGS_LOCK:
        s = C.load_settings()
        s.setdefault("audio", {})[kind] = path
        C.save_settings(s)
    return None
