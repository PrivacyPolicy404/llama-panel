#!/usr/bin/env python3
"""Panel web para controlar llama-server (texto) y KoboldCpp (imagenes). Solo biblioteca estandar.

Uso:
  python3 panel.py --install   # instala los servicios de systemd (usuario) y arranca el panel
  python3 panel.py             # corre el panel en primer plano (puerto 8090)

El codigo esta en llama_panel/ y la interfaz en static/.
"""
import sys

sys.dont_write_bytecode = True  # __pycache__ en llama_panel/ dispararia el auto-reinicio


def main():
    if "--install" in sys.argv:
        from llama_panel.install import install
        return install()
    from llama_panel.server import serve
    serve()


if __name__ == "__main__":
    main()
