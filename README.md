# llama-panel

Panel web casero para levantar y bajar modelos on demand en una GPU con poca VRAM (Vulkan):

- **Texto:** `llama-server` (llama.cpp) en modo router, puerto 8080.
- **Imágenes:** KoboldCpp con StableUI, puerto 5001.
- **Panel:** puerto 8090. Texto e imágenes se excluyen mutuamente para compartir la VRAM.

Solo usa la biblioteca estándar de Python 3.

## Instalación

```bash
python3 panel.py --install
```

Crea los servicios systemd de usuario (`llama-panel`, `llama-router`, `kobold-img`) y arranca el panel.
La configuración del usuario vive en `~/.config/llama-panel/` (fuera del repo).

## Desarrollo

El servicio corre `panel.py` directamente desde esta carpeta, y `llama-panel-reload.path` reinicia el panel
cada vez que se guarda el archivo: editar y recargar el navegador, nada más.

Solo hace falta volver a correr `--install` si cambian los units de systemd (la función `install()`).

```bash
journalctl --user -u llama-panel -f   # ver errores del panel
```

Variables de entorno opcionales: `LLAMA_MODELS_DIR`, `LLAMA_IMG_DIR`, `LLAMA_SERVER_BIN`, `KOBOLD_BIN`, `LLAMA_PANEL_PORT`, `LLAMA_PANEL_DIR`.
