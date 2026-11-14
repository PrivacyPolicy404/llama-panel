"""Instalacion: escribe los units de systemd (usuario), genera la configuracion inicial y arranca el panel."""
import os
from pathlib import Path

from . import config as C
from .img_models import write_img_env
from .services import run, svc, svc_state
from .text_models import write_preset

UNIT_DIR = C.HOME / ".config" / "systemd" / "user"


def units():
    """{nombre_de_archivo: contenido} de todos los units que maneja el panel."""
    entry = C.APP_DIR / "panel.py"
    pkg = C.APP_DIR / "llama_panel"
    return {
        f"{C.SERVICE}.service": (
            "[Unit]\nDescription=llama.cpp router server (Vulkan)\n"
            f"Conflicts={C.IMG_SERVICE}.service\n\n"
            "[Service]\nEnvironment=GGML_VK_VISIBLE_DEVICES=0\n"
            f"ExecStart={C.LLAMA_SERVER} --models-preset {C.PRESET_FILE} --models-max 1 "
            f"--host 0.0.0.0 --port {C.CHAT_PORT}\n"
            "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
        ),
        f"{C.IMG_SERVICE}.service": (
            "[Unit]\nDescription=KoboldCpp generador de imagenes (Vulkan)\n"
            f"Conflicts={C.SERVICE}.service\n\n"
            "[Service]\nEnvironment=GGML_VK_VISIBLE_DEVICES=0\n"
            f"EnvironmentFile={C.IMG_ENV}\n"
            f"ExecStart={C.KOBOLD_BIN} $KOBOLD_ARGS\n"
            "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n"
        ),
        f"{C.PANEL_SERVICE}.service": (
            "[Unit]\nDescription=Panel de control llama-server\n\n"
            # Sin bytecode: si no, __pycache__ dispararia el watcher de abajo en loop
            f"[Service]\nWorkingDirectory={C.APP_DIR}\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n"
            f"ExecStart=/usr/bin/python3 {entry}\nRestart=on-failure\nRestartSec=3\n\n"
            "[Install]\nWantedBy=default.target\n"
        ),
        # Reinicia el panel cada vez que se guarda codigo Python (static/ se lee en cada pedido)
        f"{C.PANEL_SERVICE}-reload.path": (
            "[Unit]\nDescription=Vigila el codigo del panel para reiniciarlo\n\n"
            f"[Path]\nPathChanged={entry}\nPathModified={pkg}\nUnit={C.PANEL_SERVICE}-reload.service\n\n"
            "[Install]\nWantedBy=default.target\n"
        ),
        f"{C.PANEL_SERVICE}-reload.service": (
            "[Unit]\nDescription=Reinicia el panel tras un cambio de codigo\nStartLimitIntervalSec=0\n\n"
            f"[Service]\nType=oneshot\nExecStart=/usr/bin/systemctl --user restart {C.PANEL_SERVICE}.service\n"
        ),
    }


def install():
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    router_unit = UNIT_DIR / f"{C.SERVICE}.service"
    backup = UNIT_DIR / f"{C.SERVICE}.service.bak"
    if router_unit.exists() and not backup.exists():
        backup.write_text(router_unit.read_text())
    for name, content in units().items():
        (UNIT_DIR / name).write_text(content)

    n = len(write_preset())
    print(f"Configuracion de texto generada con {n} modelos: {C.PRESET_FILE}")
    m = write_img_env()
    print(f"Generador de imagenes configurado con el modelo: {m['id']}" if m
          else f"(No hay modelos de imagen todavia en {C.IMG_DIR}.)")
    if not Path(C.KOBOLD_BIN).exists():
        print(f"AVISO: no existe {C.KOBOLD_BIN}")

    print(run("systemctl", "--user", "daemon-reload") or "daemon-reload ok")
    print(run("systemctl", "--user", "enable", C.PANEL_SERVICE) or "panel habilitado")
    print(run("systemctl", "--user", "enable", f"{C.PANEL_SERVICE}-reload.path") or "auto-reinicio habilitado")
    print(run("systemctl", "--user", "restart", f"{C.PANEL_SERVICE}-reload.path") or "auto-reinicio activo")
    print(run("systemctl", "--user", "restart", C.PANEL_SERVICE) or "panel (re)iniciado")
    if svc_state() == "active":
        svc("restart")
        print("Servidor de modelos de texto reiniciado con la nueva configuracion.")
    print(f"\nListo. Abri http://{os.uname().nodename}.local:{C.PANEL_PORT} desde la laptop.")
