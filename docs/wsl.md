# llama-panel en WSL2 (prueba)

Estado: **en prueba**. Primera máquina: notebook con Windows 11, RTX 2060 Max-Q (6 GB, Turing, CUDA 7.5), WSL2 con
Ubuntu. El objetivo final es que ande también con AMD reciente por ROCm (RX 9070), que acá no se puede probar.

Esta guía la sigue una sesión de Claude Code abierta adentro del WSL, que no vio la charla donde se planeó.
Al terminar, lo que funcione pasa a una sección «WSL» del README (en los dos idiomas) y esto queda como bitácora.

## Lo que se sabe antes de empezar

- En WSL no hay `amdgpu` ni `/sys/class/drm`: la GPU llega por `/dev/dxg` desde el driver de Windows. No hay que
  instalar driver de NVIDIA adentro del WSL (lo rompe); solo el CUDA toolkit «WSL-Ubuntu» si se compila.
- Vulkan en WSL es Mesa Dozen (Vulkan sobre D3D12): una traducción, sin mediciones conocidas con llama.cpp. Para
  NVIDIA usar **CUDA**; para AMD, **ROCm** (la RX 9070 figura soportada en WSL desde ROCm 7.2.1).
- El panel no depende del backend: corre el `llama-server` que se le indique. Las variables `GGML_VK_*` de los
  units se ignoran en una build CUDA o ROCm.
- El panel necesita systemd: `/etc/wsl.conf` con `[boot]` / `systemd=true` y `wsl --shutdown` desde Windows.

## Pasos

1. systemd: `systemctl --user status` tiene que responder. Si no, el punto de arriba.
2. llama.cpp con CUDA en `~/llama.cpp` (puede ser el oficial: el fork solo hace falta para Flash-Next, que no entra
   en 6 GB): `cmake -B build -DGGML_CUDA=ON && cmake --build build -j --target llama-server`. Anotar versión y tiempos.
3. Modelos: **primero buscar los que ya hay** en la máquina (el usuario ya generó imágenes con Animagine). Lugares
   típicos: `find /mnt/c/Users -iname "*.gguf" -o -iname "*.safetensors" 2>/dev/null`, y adentro del WSL `~`.
   Herramientas que los guardan: ComfyUI (`models/checkpoints`), Stable Diffusion WebUI / Forge
   (`models/Stable-diffusion`), LM Studio (`~/.lmstudio/models`). Anotar con qué herramienta se usaban: si es
   ComfyUI, esta máquina sirve después para probar el soporte de ComfyUI en el panel.
   - Copiarlos (no enlazarlos) **al disco de Linux** (`~/models`, `~/imgmodels`): leer de `/mnt/c` es mucho más
     lento. Medir una vez la diferencia al cargar (sirve para el aviso de la pestaña Configuración).
   - En 6 GB entra Animagine XL en `.safetensors` fp16 (~6.5 GB en disco, justo) o mejor en GGUF Q8_0 (~3.5 GB).
   - Para texto, si no hay ninguno, uno chico que entre en 6 GB (un 4B u 8B en Q4_K_M). Al bajar, verificar el
     sha256 contra la API de Hugging Face.
4. `git clone https://github.com/PrivacyPolicy404/llama-panel.git ~/llama-panel`, rama `experimental/wsl`,
   `python3 panel.py --install`. Abrir `http://localhost:8090` desde el navegador de Windows.
5. Cargar el modelo desde el panel y medir con `python3 tools/bench/smoke_models.py`.
6. Imágenes con KoboldCpp (binario de Linux con CUDA) y `--usecuda` en las opciones del modelo, con el Animagine
   que ya había. Comparar el tiempo por imagen con lo que daba la herramienta de Windows, mismos pasos y tamaño.

## Qué revisar (anotar el resultado de cada uno)

- [ ] **Sensores**: `nvidia-smi` está en `/usr/lib/wsl/lib`, que quizás no esté en el PATH del servicio de systemd.
      Si el panel no muestra la GPU, `hardware.NVIDIA_SMI` tiene que buscarlo también ahí. ¿Qué campos da
      `nvidia-smi` en WSL (potencia, ventilador, reloj suelen salir `[N/A]`)?
- [ ] **KoboldCpp**: `img_models.py` pone `--usevulkan` salvo que el modelo elija otro backend. En WSL con NVIDIA
      conviene `--usecuda` por defecto (y en AMD `--usehipblas`).
- [ ] **Arranque y apagado**: `loginctl enable-linger` y si el panel sigue andando al cerrar todas las terminales
      (WSL apaga la VM al quedar sin uso; ver `vmIdleTimeout` en `.wslconfig`).
- [ ] **Red**: desde Windows `localhost:8090` debería andar solo. Desde otra máquina de la red hace falta
      `networkingMode=mirrored` en `%UserProfile%\.wslconfig` o un `netsh interface portproxy`. Probar 8080 y 5001.
- [ ] **Modo IA**: no tiene sentido en WSL (no hay escritorio de Linux ni tty1). El panel debería esconderlo.
- [ ] **Avisos útiles**: si los modelos están bajo `/mnt/` (disco de Windows), avisarlo en la pestaña Configuración.
- [ ] **Tests**: `python3 -m unittest` adentro del WSL.
- [ ] **Rendimiento**: tok/s de generación y de prompt; comparar con lo publicado para la 2060 si hay datos.

## Resultados

(completar)
