"""Rutas de la API JSON: que devuelve cada GET y que funcion atiende cada POST."""
from pathlib import Path

from . import actions
from . import audio_models as AM
from . import auth
from . import buddy
from . import proactive
from . import config as C
from . import energy
from . import gate
from . import hardware
from . import paths
from .i18n import _
from . import img_models as IM
from . import prompt_cache as PC
from . import stats
from . import text_models as TM
from .services import ai_mode_state, kobold_status, router_models, svc_states


def state():
    """Lo que la interfaz consulta cada 2 s: servicios, sensores y modelos cargados."""
    st = svc_states()
    sel = IM.pick_img(C.load_settings(), IM.scan_img_models())
    img = {"selected": sel["id"] if sel else None, "selected_key": sel["key"] if sel else "",
           "progress": None, "perf": None}
    if st["img"] == "active":
        img["progress"], img["perf"] = kobold_status()
    models = router_models() if st["text"] == "active" else {}
    for mid, m in models.items():
        if m["status"] == "loaded":
            m["prompts"] = PC.profiles(mid)  # prompts iniciales guardados (perfiles) de la configuracion vigente
    return {
        "svc": st,
        "hw": hardware.latest(),
        "text": {"models": models},
        "img": img,
        "ai_mode": ai_mode_state(),
        "auto": gate.status(),
        "audio": audio_state(),
    }


KIOSK_UNITS = (C.PANEL_SERVICE, C.SERVICE, C.IMG_SERVICE, "homeassistant")


def parse_units(text, mono_now):
    """Salida de `systemctl show -p Id,LoadState,ActiveState,ActiveEnterTimestampMonotonic` -> {unidad: {...}}.
    Las que no existen (LoadState=not-found) no aparecen; "up" son los segundos desde que arrancó (None si no anda)."""
    out = {}
    for block in text.strip().split("\n\n"):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not kv.get("Id") or kv.get("LoadState") != "loaded":
            continue
        since = int(kv.get("ActiveEnterTimestampMonotonic") or 0)
        active = kv.get("ActiveState", "unknown")
        out[kv["Id"].removesuffix(".service")] = {
            "state": active, "up": round(mono_now - since / 1e6) if active == "active" and since else None}
    return out


def kiosk():
    """Lo que el dashboard del modo IA suma a /api/state: servicios con su tiempo encendido, energía y clientes."""
    import time
    from .services import run
    text = run("systemctl", "--user", "show", "-p", "Id,LoadState,ActiveState,ActiveEnterTimestampMonotonic",
               *(f"{u}.service" for u in KIOSK_UNITS))
    return {"units": parse_units(text, time.monotonic()), "energy": energy.meter().summary(),
            "gate": gate.CLIENTS.snapshot(), "listening": sorted(gate._servers)}


def audio_state():
    """El motor de audio (CPU): qué modelo de Whisper y qué voz de Piper se usan, si están, y los últimos tiempos."""
    from . import voice
    sel = AM.selected()
    return {"stt": Path(sel["stt"]).name, "tts": Path(sel["tts"]).stem, "stt_ok": voice.stt_available(),
            "tts_ok": voice.Speaker.available(), "last": voice.LAST}


def config():
    """Modelos en disco y sus ajustes guardados (se pide al cargar y despues de guardar)."""
    settings = C.load_settings()
    imgs = IM.scan_img_models()
    sel = IM.pick_img(settings, imgs)
    return {
        "global": settings["global"],
        "global_fields": settings["global_fields"],
        "global_field_ids": TM.GLOBAL_FIELD_IDS,
        "models_dir": "  ·  ".join(str(d) for d in C.MODELS_DIRS if d.is_dir()) or str(C.MODELS_DIR),
        "img_dir": str(C.IMG_DIR),
        "vram_total": hardware.latest().get("vram_total"),
        "reasoning_opts": [(v, _(label)) for v, label in C.REASONING_OPTS],
        "ports": {"chat": C.CHAT_PORT, "img": C.IMG_PORT},
        "text_models": [{"key": m["key"], "id": m["id"], "size": m["size"], "cfg": TM.model_cfg(settings, m),
                         "windows_disk": C.on_windows_disk(m["path"])}
                        for m in TM.scan_models()],
        "img_models": [{"key": m["key"], "id": m["id"], "size": m["size"],
                        "comps": [(f, Path(p).name) for f, p in m["comps"]],
                        "windows_disk": C.on_windows_disk(m["path"]),
                        "extra": (settings["image"]["models"].get(m["key"]) or {}).get("extra", "")}
                       for m in imgs],
        "img_selected": sel["key"] if sel else "",
        "audio_models": AM.scan(),
        "audio_selected": AM.selected(),
        "audio_dirs": [str(d) for d in AM.audio_dirs()],
        "server_opts": TM.server_options(),
        "ai_mode_installed": ai_mode_state()["installed"],
        "fields": TM.FIELDS,  # la interfaz traduce los textos (castellano = clave)
    }


def _logs(q):
    try:
        n = min(2000, int(q.get("n", ["300"])[0] or 300))
    except ValueError:
        n = 300
    return {"lines": stats.logs(q.get("svc", ["text"])[0], n)}


GET_ROUTES = {
    "/api/state": lambda q: state(),
    "/api/config": lambda q: config(),
    "/api/history": lambda q: {"samples": list(hardware.HIST)},
    "/api/kiosk": lambda q: kiosk(),
    "/api/stats": lambda q: stats.stats(),
    "/api/live": lambda q: stats.live(),
    "/api/logs": _logs,
    "/api/paths": lambda q: paths.current(),
    "/api/paths/dirs": lambda q: {"dirs": paths.suggest_dirs(q.get("prefix", [""])[0])},
    "/api/buddy": lambda q: buddy.state(),
    "/api/auth/tokens": lambda q: {"tokens": auth.tokens()},
}


def _result(pair):
    good, msg = pair
    return {"ok": good, "msg": msg}


POST_ROUTES = {
    "/api/action": lambda b: actions.do_action(str(b.get("do", ""))),
    "/api/text/load": actions.text_load,
    "/api/text/unload": actions.text_unload,
    "/api/text/prompt/save": actions.text_prompt_save,
    "/api/text/prompt/restore": actions.text_prompt_restore,
    "/api/text/prompt/delete": actions.text_prompt_delete,
    "/api/text/rescan": actions.text_rescan,
    "/api/text/settings": actions.text_settings,
    "/api/img/settings": actions.img_settings,
    "/api/img/use": actions.img_use,
    "/api/paths/check": lambda b: paths.check(b.get("values") or {}),
    "/api/paths": paths.save,
    "/api/auto": actions.auto_settings,
    "/api/buddy/config": lambda b: _result(buddy.save_config(b)),
    "/api/buddy/test": lambda b: _result(buddy.start_test()),
    "/api/buddy/memory": lambda b: _result(buddy.save_memory(b.get("text", ""))),
    "/api/buddy/reset": lambda b: _result(buddy.reset()),
    "/api/buddy/conversation/use": lambda b: _result(buddy.use_conversation(str(b.get("id", "")))),
    "/api/buddy/conversation/delete": lambda b: _result(buddy.delete_conversation(str(b.get("id", "")))),
    "/api/buddy/ha": lambda b: _result(buddy.save_ha_token(b.get("token", ""))),
    "/api/buddy/brave": lambda b: _result(buddy.save_brave_token(b.get("token", ""))),
    "/api/audio/use": actions.audio_use,
    "/api/buddy/say": actions.buddy_say,
    "/api/buddy/timer/cancel": actions.buddy_timer_cancel,
    "/api/auth/token": actions.auth_token,
    "/api/auth/revoke": actions.auth_revoke,
    "/api/audio/preview": actions.audio_preview,
}

# Rutas que contestan de a partes (server-sent events)
GET_STREAM_ROUTES = {
    "/api/buddy/events": proactive.events,  # lo que Dani dice por su cuenta
}

POST_STREAM_ROUTES = {
    "/api/buddy/chat": buddy.chat,
}
