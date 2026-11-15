# llama-panel

Panel web casero (puerto 8090) para levantar y bajar modelos on demand en una RX 580 de 8 GB (solo Vulkan) con 64 GB de RAM:
llama-server en modo router para texto (:8080) y KoboldCpp + StableUI para imágenes (:5001), que se excluyen para compartir la VRAM.

Responder en castellano rioplatense. El usuario trabaja por SSH en la máquina.

## Leer primero

- `docs/notas.md`: hardware y sus límites (GTT 31 GiB, HDD vs NVMe), mediciones, por qué cada configuración de modelo, trampas conocidas.
- `docs/analisis-rendimiento.md`: bitácora de cada experimento (hipótesis, método, números, conclusión) y cómo medir sin
  engañarse. Leerlo antes de proponer una optimización: puede que ya se haya probado.
- `patches/README.md`: el fork de llama.cpp y sus 5 parches locales.
- `README.md`: estructura del código.

## Código

- `panel.py` es solo el punto de entrada; la lógica está en `llama_panel/` y la interfaz en `static/` (JS con módulos ES, sin build).
- Guardar cualquier `.py` reinicia el panel solo (`llama-panel-reload.path`); `static/` se lee en cada pedido. No hace falta reinstalar.
  Reinstalar (`python3 panel.py --install`) solo si cambian los units de systemd (`llama_panel/install.py`).
- Tests: `python3 -m unittest`. Errores del panel: `journalctl --user -u llama-panel -f`.
- La configuración del usuario vive fuera del repo, en `~/.config/llama-panel/` (`settings.json`, `presets.ini` generado, `kobold.env`).
  Al tocar ajustes por la API, conservar los de modelos que no están en disco.
- Modo IA (`llama_panel/kiosk.py` + `scripts/install-modo-ia.sh`): apaga el escritorio KDE y muestra un dashboard en tty1.
  Activarlo **cierra la sesión gráfica del usuario**: nunca hacerlo sin que lo pida. Probar el dashboard por SSH con `--once`.

## llama.cpp

- `~/llama.cpp` sale del fork `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers` (oficial + 5 parches). La versión está fijada a propósito:
  **no actualizar sin que el usuario lo pida**. Para revisar novedades o actualizar, usar la skill `llama-cpp-update`.
- La configuración de Qwen3.8-Flash-Next en el panel necesita el parche 1 (`-ot ...=Vulkan_Host`); el modelo `-hcq8` se generó con el parche 3.
- Para comparar cambios chicos de rendimiento: `TEMP=0`, sin MTP, versiones alternadas (el MTP con muestreo mete ±5 % de ruido).

## Probar cambios con modelos

- Mediciones: `tools/bench/` (ver `docs/notas.md`). Verificación rápida: `python3 tools/bench/smoke_models.py`.
- Antes de usar la GPU para pruebas, revisar que texto e imágenes no estén en uso; cargar texto apaga el generador de imágenes.
- No matar procesos con `pkill -f` usando patrones que aparezcan en el propio comando (mata la shell): buscar el PID y verificarlo.
- Al descargar modelos: verificar el sha256 contra la API de Hugging Face; los que leen del disco por token van al NVMe (`~/models-nvme`).
- Al buscar modelos, revisar siempre la versión más nueva en la API de Hugging Face, no solo blogs. El usuario prefiere variantes
  sin censura (huihui-ai, Heretic) y elegir por reputación / descargas.

## Commits

- El usuario es el único autor: **sin** `Co-Authored-By`. Terminar el mensaje con `Written with AI assistance (Claude).`
- El asunto describe el cambio (no una instrucción). Commits en castellano en este repo.
- Nunca hacer push al repo oficial de llama.cpp. Los issues/PRs a llama.cpp los escribe el usuario.
