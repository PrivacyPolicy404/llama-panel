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

- [x] **Sensores**: confirmado, el PATH de los servicios de systemd no incluye `/usr/lib/wsl/lib`. Arreglado:
      `hardware.NVIDIA_SMI` lo busca también ahí. En WSL `nvidia-smi` da `[N/A]` en `power.limit` y `fan.speed`; sí da
      potencia, temperatura, relojes (núcleo, memoria y máximo de memoria), uso y VRAM. El nombre «RTX 2060 with Max-Q
      Design» ahora se acorta a «RTX 2060 Max-Q».
- [x] **KoboldCpp**: en WSL el backend por defecto es `--usecuda` con NVIDIA y `--usehipblas` sin NVIDIA (AMD); fuera
      de WSL sigue Vulkan, y las opciones del modelo mandan (`img_models.default_backend`).
- [ ] **Arranque y apagado**: linger activo (lo activó el usuario con sudo; `panel.py --install` no puede sin
      polkit) y el panel queda habilitado. Falta probar que siga andando al cerrar todas las terminales; `vmIdleTimeout`
      no está definido en `.wslconfig` (vale el de WSL).
- [ ] **Red**: desde Windows `localhost` anda en 8090, 5001 y el puerto de texto (con `networkingMode=mirrored`).
      Falta probar desde otra máquina de la red. **Ojo con los puertos**: en mirrored los puertos de Windows también
      cuentan. Acá el 8080 lo ocupaba un contenedor de Docker y el 8081 un programa de Windows, y llama-server no
      arrancaba («couldn't bind»). Ahora los puertos se cambian en `paths.env` con `LLAMA_CHAT_PORT` / `LLAMA_IMG_PORT`
      (en esta máquina, 8082); los scripts de `tools/bench` leen la misma variable del entorno.
- [x] **Modo IA**: en WSL `ai_mode_state` lo da como no instalado y el botón no aparece.
- [x] **Avisos útiles**: los modelos bajo `/mnt/<letra>/` muestran el aviso «en el disco de Windows» en la pestaña
      Configuración (texto e imágenes). Medido: cargar Qwen3.5-4B Q4_K_M (2.7 GB) desde `/mnt/c` tarda 58–69 s; desde
      el disco de Linux, 12–14 s (unas 4–5 veces más).
- [x] **Tests**: `python3 -m unittest` adentro del WSL: 223 OK. Los tests fijan los puertos 8080/5001 en
      `tests/__init__.py` para no depender del `paths.env` de la máquina.
- [x] **Rendimiento**: ver abajo. No encontré mediciones publicadas de esta combinación (2060 Max-Q, Qwen3.5-4B) para
      comparar.
- [x] **Ayudante de reloj de memoria**: en WSL los units no llevan `llama-gpu-mclk` (es de amdgpu); antes cada
      arranque dejaba fallas de `sudo -n` en el registro.

## Resultados

Máquina: Windows 11, RTX 2060 Max-Q 6 GB (Turing, CUDA 7.5), Ryzen 9 4900HS, WSL2 con Ubuntu 22.04 (8 GB de RAM
y 4 núcleos asignados en `.wslconfig`), driver de Windows con CUDA 13.5.

- **systemd**: ya estaba (`/etc/wsl.conf` con `systemd=true`).
- **CUDA toolkit**: repositorio `wsl-ubuntu` de NVIDIA, `cuda-toolkit` 13.3, más `cmake` 3.22, `build-essential` y
  `libcurl4-openssl-dev`. Pide sudo.
- **llama.cpp oficial** (commit 44d5087), `cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=75`: configurar
  52 s, compilar `llama-server` con `-j 4` 41 min 43 s (los kernels de flash-attention son casi todo). Con la
  compilación andando no queda RAM para otra cosa (falló una copia a `/mnt/c` por «Cannot allocate memory»).
- **Modelos que ya había**: Animagine XL 4.0 en la caché de Hugging Face, en formato diffusers (lo usaba un script
  propio con diffusers, no ComfyUI). KoboldCpp necesita un archivo único: se convirtió con
  `scripts/convert_diffusers_to_original_sdxl.py` de diffusers 0.35.1 (`--half --use_safetensors`), 40 s, 6.9 GB.
  Buscar con `find` o `dir /s` en todo `C:` desde WSL no terminó en más de 30 min: conviene preguntar dónde están.
- **Texto**: Qwen3.5-4B Q4_K_M de unsloth (sha256 verificado contra la API de Hugging Face), cargado por el panel
  (`smoke_models.measure`): carga 16 s, generación **54.8 tok/s**, prompt de ~3200 tokens **1484 tok/s**, 3.3 GiB
  de VRAM.
- **Imágenes**: KoboldCpp 1.125.1 (`koboldcpp-linux-x64`), `--usecuda`, 832×1216, 28 pasos, cfg 5, Euler a:
  - fp16 sin opciones: 146 s por imagen (5.3 GB de VRAM).
  - `--sdquant 1` (q8 al cargar): **129 s** por imagen (2.2 GB de VRAM). Queda como opción del modelo.
  - `--sdquant 1 --sdflashattention --sdclipgpu`: 206 s (peor en Turing).
  - La misma imagen con diffusers (UNet en FP8, VAE fp16-fix) en el mismo WSL: **32 s**. KoboldCpp anda, pero es
    unas 4 veces más lento en esta placa; no se siguió afinando porque el objetivo era la compatibilidad.

## Hallazgo: motor de imágenes con CUDA o ROCm

KoboldCpp se eligió porque con la RX 580 (Polaris) era la única opción: sin ROCm ni PyTorch, solo Vulkan. Con CUDA
o ROCm (la RX 9070 en WSL desde ROCm 7.2.1) hay motores basados en PyTorch bastante más rápidos: en esta máquina
diffusers hace la misma imagen en 32 s contra 129 s de KoboldCpp (stable-diffusion.cpp por dentro).

Propuesta (trabajo aparte, sin empezar):

- KoboldCpp sigue como motor por defecto: un binario, poca RAM, anda con cualquier placa por Vulkan.
- Un segundo motor de imágenes opcional, que el panel ofrezca al detectar CUDA o ROCm:
  - **Forge / reForge o SD.Next** si alcanza con la API `/sdapi/v1` (la de A1111, la misma de KoboldCpp): StableUI
    y los clientes que ya la usan siguen andando sin cambios. SD.Next soporta ROCm de forma explícita.
  - **ComfyUI** si se quieren los modelos nuevos primero y flujos más complejos: su API es propia (flujos de nodos),
    así que el panel tendría que traducirla o habría que adaptar los clientes.
- Texto: seguir con llama.cpp (CUDA o ROCm). vLLM o exllama rinden más en NVIDIA, pero se llevan mal con 6 GB y
  con Turing, y el panel está armado alrededor de `llama-server`.
