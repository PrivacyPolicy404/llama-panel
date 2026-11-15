# llama-panel

Panel web casero para levantar y bajar modelos on demand en una GPU con poca VRAM (Vulkan):

- **Texto:** `llama-server` (llama.cpp) en modo router, puerto 8080.
- **Imágenes:** KoboldCpp con StableUI, puerto 5001.
- **Panel:** puerto 8090. Texto e imágenes se excluyen mutuamente para compartir la VRAM.

Solo usa la biblioteca estándar de Python 3, sin dependencias ni paso de build.

## Qué hace

- Monitor de VRAM, uso de GPU, temperatura/consumo/ventilador y RAM, con historia de los últimos minutos.
- Encender, reiniciar y apagar texto e imágenes; al encender uno, el otro se apaga para liberar VRAM.
- Cargar y descargar un modelo de texto puntual (API del router de llama-server) sin reiniciar nada.
- Ajustes por modelo (MTP, razonamiento, contexto, opciones extra); "Guardar y aplicar" recarga solo lo necesario.
- Elegir el modelo de imagen y sus flags de KoboldCpp, con progreso de la generación en curso.
- Rendimiento: tokens/s por respuesta (prompt y generación, aceptación MTP) y duración de cada imagen.
- Registros en vivo de cada servicio, con filtro.

## Estructura

```
panel.py              punto de entrada: corre el servidor o --install
llama_panel/
  config.py           rutas, puertos, settings.json
  text_models.py      escaneo de .gguf, presets.ini del router, validación
  img_models.py       escaneo de imagen, acompañantes (VAE/codificadores), kobold.env
  services.py         systemctl y clientes HTTP del router y de KoboldCpp
  hardware.py         sensores de GPU/RAM y muestreo en segundo plano
  stats.py            parseo de journalctl: tokens/s, imágenes, registros
  actions.py          lo que hacen los botones (cargar, guardar, aplicar, encender)
  api.py              rutas /api/*
  server.py           servidor HTTP
  install.py          units de systemd
static/
  index.html, app.css
  js/                 módulos ES nativos, sin build (main.js arranca todo)
tests/                unittest de la stdlib
```

## Instalación

```bash
python3 panel.py --install
```

Crea los servicios systemd de usuario (`llama-panel`, `llama-router`, `kobold-img`) y arranca el panel.
La configuración del usuario vive en `~/.config/llama-panel/` (fuera del repo).

## llama.cpp: por qué un fork

El servidor de texto usa el fork [PrivacyPolicy404/llama.cpp](https://github.com/PrivacyPolicy404/llama.cpp)
(rama `ot-host-buffers`): el llama.cpp oficial más un parche chico.

El parche existe por los modelos MoE grandes que no entran en la VRAM, como Qwen3.8-Flash-Next (125B):

- Sus expertos viven en la RAM. Si están en memoria *fija* (pinned), la GPU los copia más rápido y el prompt se procesa ~20 % más rápido.
- En amdgpu la memoria fija tiene un tope (GTT) de la mitad de la RAM: 31 GiB en esta máquina. Los expertos de Flash-Next ocupan 31.6 GiB:
  fijarlos todos tira el driver ("device lost").
- llama.cpp oficial solo permite fijar todo o nada. El parche deja elegir qué capas fijar con `-ot ...=Vulkan_Host`:
  así entran 44 de 48 capas en el tope.

Sin el parche ese modelo no carga con la configuración del panel; el resto de los modelos no lo necesita.
Detalles, números y cómo reaplicarlo: [patches/README.md](patches/README.md).

Para actualizar llama.cpp sin perder el parche: `scripts/update-llama.sh` (con `--check` solo informa cuántos commits nuevos hay).
La versión queda fija hasta que se corre el script.

## Desarrollo

El servicio corre el código directamente desde esta carpeta y `llama-panel-reload.path` reinicia el panel cada vez
que se guarda `panel.py` o algo en `llama_panel/`. Los archivos de `static/` se leen en cada pedido, así que alcanza
con recargar el navegador.

Solo hace falta volver a correr `--install` si cambian los units de systemd (`llama_panel/install.py`).

```bash
python3 -m unittest            # tests
journalctl --user -u llama-panel -f   # ver errores del panel
```

Los modelos de texto se buscan en `LLAMA_MODELS_DIR` y en las carpetas de `LLAMA_EXTRA_MODELS_DIRS` (separadas por `:`, por defecto `~/models-nvme`). Los modelos partidos en shards (`-00001-of-0000N.gguf`) cuentan como uno solo.

Variables de entorno opcionales: `LLAMA_MODELS_DIR`, `LLAMA_EXTRA_MODELS_DIRS`, `LLAMA_IMG_DIR`, `LLAMA_SERVER_BIN`, `KOBOLD_BIN`, `LLAMA_PANEL_PORT`, `LLAMA_PANEL_DIR`.
