"""Rutas de la API JSON: que devuelve cada GET y que funcion atiende cada POST."""
from pathlib import Path

from . import actions
from . import config as C
from . import hardware
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
            m["prompt"] = PC.info(mid)  # prompt inicial guardado para la configuracion vigente, o None
    return {
        "svc": st,
        "hw": hardware.latest(),
        "text": {"models": models},
        "img": img,
        "ai_mode": ai_mode_state(),
    }


def config():
    """Modelos en disco y sus ajustes guardados (se pide al cargar y despues de guardar)."""
    settings = C.load_settings()
    imgs = IM.scan_img_models()
    sel = IM.pick_img(settings, imgs)
    return {
        "global": settings["global"],
        "models_dir": "  ·  ".join(str(d) for d in C.MODELS_DIRS if d.is_dir()) or str(C.MODELS_DIR),
        "img_dir": str(C.IMG_DIR),
        "vram_total": hardware.latest().get("vram_total"),
        "reasoning_opts": C.REASONING_OPTS,
        "ports": {"chat": C.CHAT_PORT, "img": C.IMG_PORT},
        "text_models": [{"key": m["key"], "id": m["id"], "size": m["size"], "cfg": TM.model_cfg(settings, m)}
                        for m in TM.scan_models()],
        "img_models": [{"key": m["key"], "id": m["id"], "size": m["size"],
                        "comps": [(f, Path(p).name) for f, p in m["comps"]],
                        "extra": (settings["image"]["models"].get(m["key"]) or {}).get("extra", "")}
                       for m in imgs],
        "img_selected": sel["key"] if sel else "",
        "server_opts": TM.server_options(),
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
    "/api/stats": lambda q: stats.stats(),
    "/api/live": lambda q: stats.live(),
    "/api/logs": _logs,
}

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
}
