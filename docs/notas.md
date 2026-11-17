# Notas: hardware, modelos y lo que aprendimos

> **English summary.** Working notes for this machine: hardware limits, the current settings of each model and
> the traps already found. The rest of the document is in Spanish. Key points:
>
> - **Machine:** AMD RX 580 8 GB (Polaris, Vulkan/RADV only, no `shaderFloat16`, PCIe 3.0), i7-10700, 64 GB RAM.
> - **Limits:** amdgpu lets the GPU pin at most half the RAM (GTT, 31 GiB here); going over gives `device lost`.
>   `memlock` is 8 GB, so `--load-mode mlock` is useless for large models. Models read from disk per token
>   (n-gram tables) must be on the NVMe, not the HDD.
> - **Models:** Qwen3.6-35B-A3B with experts in RAM, pinned, and an expert cache in VRAM; Qwen3.8-Flash-Next with
>   44 of 48 expert layers pinned (fork patch 1), a 2-token MTP draft and `parallel = 1`. Dense 8–9B models fit
>   entirely in VRAM. The exact settings are in the code blocks below.
> - **Traps:** the router's default `parallel = 4` halves the speed of large MoE models; a global
>   `n-gpu-layers = 99` without `cpu-moe` makes the GPU read weights over PCIe (~2 tok/s); without a desktop session
>   logind drops GPU access and Vulkan falls back to `llvmpipe` (fixed with the `render`/`video` groups).
> - The detailed experiments are in [`analisis-rendimiento.md`](analisis-rendimiento.md) (English summary at the top).

Contexto acumulado para no tener que redescubrirlo. Primera versión: 2026-11-14.
La bitácora detallada de cada experimento (método y números) está en `analisis-rendimiento.md`.

## Identidad del proyecto

- Nombre elegido con el usuario: **The Poor GPU Engine (TPGE)**.
- Lema: **«Presupuesto arqueológico. Contexto de 64K.»**
- El motor sigue siendo nuestro fork de llama.cpp. Si adaptamos ideas o código de Strata, documentar su origen;
  al incorporar código, conservar los avisos de licencia y los créditos correspondientes. La revisión de 5.19
  todavía no incorpora código de Strata.

## La máquina

| | |
|---|---|
| GPU | AMD RX 580 8 GB (Polaris / GCN4). Solo Vulkan (RADV). Sin cooperative matrix. PCIe 3.0 x16 |
| CPU | Intel i7-10700, 8 núcleos / 16 hilos, AVX2 |
| RAM | 64 GB DDR4 (62.7 GiB) |
| Discos | NVMe Samsung 970 EVO Plus (`/`, ~400 GB libres) · SSD Kingston (`/mnt/ssd`) · **HDD** WD 1 TB (`/mnt/hdd1`, modelos) · HDD (`/mnt/hdd2`) |
| SO | Kubuntu, kernel 6.8, se usa por SSH |

Límites que importan:
- **GTT (memoria del sistema que la GPU puede usar/fijar): 31 GiB**, la mitad de la RAM (default de amdgpu).
  Pasarse fijando memoria da `radv/amdgpu: Not enough memory for command submission` y `VK_ERROR_DEVICE_LOST`.
  Se podría subir con `amdgpu.gttsize` / `ttm.pages_limit` en GRUB (sudo + reinicio); no se hizo.
- **memlock: 8 GB** (`ulimit -l`), así que `--load-mode mlock` no sirve para modelos grandes.
- `/mnt/hdd1` es disco mecánico: cargar 20 GB desde ahí tarda ~2 min si no está en la caché de RAM.
  Los modelos que leen del disco por token (tablas de n-gramas) tienen que ir al NVMe (`~/models-nvme`).

## Servicios

- `llama-panel` (:8090): este panel. Se reinicia solo al guardar código Python (`llama-panel-reload.path`).
- `llama-router` (:8080): llama-server en modo router, `--models-max 1`, lee `~/.config/llama-panel/presets.ini`.
- `kobold-img` (:5001): KoboldCpp + StableUI. Excluyente con el router (`Conflicts=`).
- **Encendido a demanda** (`gate.py`, ajuste `auto.swap` en settings.json): el router y KoboldCpp pasan a
  `127.0.0.1:18080` y `:15001` y el panel atiende el 8080 y el 5001. Al cambiarlo se reescriben los units y
  `kobold.env` y se reinician los servicios encendidos. Un pedido de un lado espera a que el otro no tenga pedidos en
  curso (hasta 15 min, después 503). Las consultas que una interfaz repite (`/v1/models`, `/props`, el progreso de
  StableUI) no prenden nada; con el texto apagado, `/v1/models` lo responde el panel con los modelos en disco.
  - **Trampa:** `/models/sse` (la interfaz web de llama-server abierta en algún navegador) es una suscripción que no
    termina nunca. Si contara como pedido en curso, bloquearía las imágenes para siempre; por eso todos los GET de la
    API del router son pasivos. Al agregar rutas, revisar si terminan.
  - Reiniciar el panel (guardar un `.py`, guardar rutas) corta lo que esté pasando por el proxy en ese momento.
  - Descarga por inactividad (`auto.idle_min`): cuenta los pedidos del proxy, las cargas desde el panel y lo que
    se ve trabajando en `/slots` y en el progreso de KoboldCpp. El router sin modelo usa 0.01 GB de VRAM: se descarga
    el modelo y el router queda prendido.

## Modelos MoE y sus configuraciones

Velocidades medidas por el router del panel (`tools/bench/baselines.json`). Prompt largo ≈ 3200 tokens.

| Modelo | Dónde | Generación | Prompt largo | VRAM |
|---|---|---|---|---|
| Huihui-Qwen3.6-35B-A3B-abliterated Q4_K (21.7 GB) | `/mnt/hdd1/gen/models` | 27.8 tok/s | 225 tok/s | 7.1 GB |
| Qwen3.8-Flash-Next GSQ-RCO abliterated Q2_0, **hc en Q8_0** (66 GB en 2 shards) | `~/models-nvme/qwen3.8-flash-next-hcq8` | 15.0–15.4 tok/s | 132 tok/s | 7.2 GB |
| (el original, hc en BF16: queda como respaldo, comparte el shard de n-gramas por enlace duro) | `~/models-nvme/qwen3.8-flash-next` | 14.7 tok/s | 107 tok/s | 7.2 GB |

Los densos de 8–9B (Qwen3, Qwen3.5) entran enteros en la VRAM y van a ~23–31 tok/s.

### Qwen3.6-35B-A3B

```
cpu-moe = 1                  # expertos en RAM
load-mode = none             # expertos fijos (pinned): sin esto el cache EMPEORA (14.6 tok/s)
moe-cache-mib = 4608         # cache de expertos en VRAM (5184 da +2% pero deja la VRAM en 7.47/8)
moe-cache-cpu-share = 0.8    # el CPU calcula el 80% de los expertos que faltan en el cache
```

Mediciones (benchmark directo): por defecto 26.2 · expertos en RAM sin cache 23.1 · con cache bien configurado 27.9–28.5 tok/s.
El script oficial `~/llama.cpp/scripts/moe-cache-tune.py` midió: subida por PCIe 9.2 GiB/s, lectura del CPU 68 GiB/s, 90 % de aciertos.

### Qwen3.8-Flash-Next (125B MoE, 6B activos + 51B de tabla de n-gramas)

```
load-mode = none
lazy-mode = on               # la tabla de n-gramas (shard 2, 28.8 GB) se lee del NVMe una fila por token
no-host = 1                  # por defecto nada fijo...
override-tensor = blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU
                             # ...y se fijan solo las capas 0-43 (29.4 GiB de GTT). REQUIERE EL PARCHE (patches/)
spec-type = draft-mtp
spec-draft-model = ~/models-nvme/qwen3.8-flash-next/mtp-Qwen3.8-Flash-Next-Q4_0-d2t65k.gguf
                             # borrador con la salida recortada a los 65536 tokens más frecuentes (parche 8,
                             # tools/gguf_mtp_d2t.py): +2 %, -0.24 GiB de VRAM, misma aceptación
spec-draft-n-max = 2         # borradores de 2 tokens: +30% de generación (3 y 4 rinden menos)
parallel = 1                 # IMPRESCINDIBLE: con 4 slots (default del router) rinde la mitad y el GTT llega a 30 GiB
ubatch-size = 2048           # entra gracias a la VRAM que libera hc en Q8_0 (con el original deja la VRAM en 7.8/8)
batch-size = 2048
```

Origen: https://huggingface.co/SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF (modelo en 2 shards y borradores MTP).

El GGUF `-hcq8` es el de SC117 con las hyper-connections (`hc_(attn|ffn)_(up|down)`, 1.2 GiB en BF16) pasadas a Q8_0
con `llama-quantize COPY --tensor-type` (parche 3). Calidad medida con `llama-perplexity --kl-divergence` (12 × 512 tokens):
perplejidad 0.9988× ± 0.003 (igual), KLD media 0.0077, 97.5 % mismo token más probable. VRAM −0.55 GB, velocidad +2–3 %.

Camino de mejoras (prompt largo / generación, por el router): por defecto 62 / 12.8 → tandas grandes 104 / 12.7 →
44 capas fijas (parche 1) 120 / 12.4 → + MTP 120 / 16.7 → margen de VRAM 105 / 15 → indexer en GPU (parche 2) +4 % gen →
hc en Q8_0 + tandas de 2048: **132 / 15–15.4**.

### Dónde se va el tiempo de un token de Flash-Next (2026-11-15)

Con `tools/bench/vk_timings.py` (medidor de Vulkan) y `cpu_profile.sh` (perf), ~83 ms por token con el medidor:

| Parte | ms por token |
|---|---|
| GPU, suma de operaciones | 47 (57 %) |
| · matmul (de eso, pesos BF16 de las hyper-connections: 10.9 antes de Q8_0) | 29 |
| · operaciones chicas (ADD, MUL, CPY, GET_ROWS…): ~2980 lanzamientos por token, 15.7 µs de promedio | 8 |
| · DeltaNet 2.4 · lightning indexer 0.27 | |
| Expertos en CPU + sincronizaciones + copias | 35.5 (43 %) |

- GPU y CPU **se turnan y nunca trabajan a la vez** (98 tramos por token: 49 GPU + 49 CPU).
- Perf del CPU: 65 % de los hilos **girando en espera** (libgomp), 27 % calculando expertos (`ggml_vec_dot_q2_0_q8_0`).
  Esa espera activa es lo mejor: `OMP_WAIT_POLICY=PASSIVE` 12.2, `GOMP_SPINCOUNT=10000` 12.4, `--prio 2` 12.4,
  contra 13.2 tok/s por defecto.
- Margen que queda: solapar CPU y GPU (cambio grande en el scheduler). Las operaciones chicas de Vulkan cuestan de verdad
  ~1.7 ms/token (el medidor exagera): fusionarlas no vale la pena (`analisis-rendimiento.md` 5.7).
- **Operaciones chicas, primer paso hecho (parche 4):** las fusiones de las hyper-connections (`DSV4_HC_PRE/POST`) ya
  estaban activas; las ~430 sumas sin nombre por token eran la combinación de los 10 expertos (9 sumas por capa).
  La fusión `multi_add` estaba apagada en Polaris por exigir RTE fp16; habilitada: 2980 → 2591 operaciones por token,
  Flash-Next 13.4 → 13.8 tok/s sin MTP (15.0 → 15.7 por el router), Qwen3.6 27.8 → 28.7.
- **Reloj de la memoria de la GPU:** con los MoE la GPU trabaja a ráfagas y amdgpu baja la VRAM a 1000 MHz en las pausas.
  `scripts/install-gpu-mclk.sh` (una vez, con sudo) instala un ayudante root; los units del router y de KoboldCpp fijan
  la memoria en 2000 MHz al arrancar y la devuelven a `auto` al parar. Flash-Next con MTP +2–3 %. (Instalado el 2026-11-15.)
- **RAM en XMP perfil 1 (DDR4-3200)** desde el 2026-11-15: 43.5 GB/s. **Sin overclock de VRAM**: el SMU de Polaris falla con la tabla modificada (cuelgue, memoria clavada en 300 MHz; bitácora 5.13).
- **`GGML_VK_MAX_NODES_PER_SUBMIT=16`** en el unit del router: la GPU arranca antes en cada tramo. +1.4 % con MTP.
- **`GGML_VK_FIRST_SUBMIT_NODES=2`** (parche 7) en el unit del router: el primer lote de cada tramo sale a los 2 nodos.
  Sin MTP +2.9 %, con MTP +0.8–1.4 %, salida idéntica (bitácora 5.21).
- **Atención dispersa desde 4× (parche 6):** con KV Q8_0 la ruta dispersa ahora se usa desde ~8K de contexto (antes
  desde ~32K). Con MTP +6–7 % entre 20K y 30K; el texto a temp 0 cambia respecto del motor anterior (bitácora 5.20).
- **Expertos en el CPU (parche 5):** el kernel Q2_0 viejo era el límite (3.8 GB/s por hilo, la RAM da 40). Con el nuevo
  Flash-Next pasa a 15.2 tok/s sin MTP y **17.6 por el router**. Detalle en `analisis-rendimiento.md` 5.8.
- El lightning indexer crece con el contexto (0.27 ms/token a 136 tokens, 0.74 a 19K) pero sigue siendo <1 % del total;
  la atención completa explica la mayor parte del costo extra de los contextos largos (+4.6 ms/token de 136 a 19K).

### Lo que no funcionó (para no repetirlo)

- MTP con borradores de 3 o 4 tokens (acierta igual que Strata, 3.1 tokens por pasada, pero verificar más tokens obliga
  a leer más expertos en el CPU): 16.2 contra 16.9 tok/s de borradores de 2. `--spec-draft-p-min` 0.7/0.85 tampoco.
- Combinar MTP con prompt lookup (`--spec-type draft-mtp,ngram-simple` / `ngram-mod`): igual en texto,
  **−29 % / −4 % al editar código** (18.8 tok/s con MTP solo).
- Expert cache en Flash-Next (no hay VRAM: 3.2 GB para 37 GB de expertos): siempre peor.
- Una variante del lightning indexer "de a 1 token": en la GPU tarda 0.27 ms por token, no hay qué ganar.
- Medir con MTP y temperatura 0.7 para comparar cambios chicos: el ruido del borrador tapa diferencias de ±5 %.
  Para A/B usar `TEMP=0`, sin MTP, y alternar las versiones.

## Descubrimientos

- **El expert cache no siempre ayuda.** Con mmap (default) los expertos no están fijos y el cache empeora todo.
  Con 8 GB de VRAM solo sirve si queda lugar: en Flash-Next la parte densa ocupa 4.7 GB y el cache empeora siempre.
- **Fijar la memoria acelera el prompt, no la generación.** En la generación el CPU calcula los expertos desde la RAM,
  fija o común da igual. Fijar sirve para que la GPU copie expertos rápido (prompt y cache).
- **El cuello de botella de la generación es la parte densa + los turnos GPU↔CPU por capa**, no la RAM:
  Qwen3.6 y Flash-Next leen casi lo mismo de expertos por token (582 vs 632 MiB), pero la parte densa de Flash-Next
  es 3.8 GiB contra 1.6 GiB y tiene 48 capas contra 40. Cambiar `-t` (6 a 16 hilos) no cambia nada; la GPU está ~55 % ocupada.
  Por eso **MTP** rinde (verifica varios tokens por pasada), aunque la comunidad diga que no ayuda en los A3B.
- `--lazy-mode` funciona aunque `--load-mode` no sea mmap (llama.cpp abre su propio mapeo para esos tensores).
- `-ot` y `--cpu-moe` en un preset: el router **no respeta el orden** de las opciones, y en `-ot` gana la primera regla
  que coincide. Poner todas las reglas en un solo `override-tensor` separadas por coma.
- **Trampa:** el `n-gpu-layers = 99` de los ajustes globales apaga el `--fit` de llama.cpp. Un MoE que no entra en la VRAM
  sin `cpu-moe` / `override-tensor` queda a ~2 tok/s (la GPU lee pesos por PCIe). Recomendado sacarlo de los globales.
- El router descarga de forma asíncrona: cargar otro modelo enseguida da `model limit reached`. El panel descarga los
  otros y reintenta (`actions._load_with_retry`).
- `mtp-q2_0.gguf` del repo de SC117 es formato de Strata (`qwen4exp-mtp`), llama.cpp no lo carga. Usar el `mtp-*-Q4_0.gguf`.
- **Strata** (github.com/Niko1221/Strata, MIT) no tiene backend Vulkan para la RX 580. Tiene rutas CUDA/HIP/SYCL/Metal,
  pero eso no habilita Polaris. No descartar sus ideas por el backend: revisión de fuentes en bitácora 5.19.
  De esa revisión salió el parche 6 (umbral de atención dispersa con KV Q8, 5.20). El kernel Q2_0 multi-token quedó
  descartado (los tokens de un paso no comparten expertos) y solapar ramas tiene techo bajo (5.21).
- La RX 580 **no tiene `shaderFloat16`** (sí lectura/escritura de 16 bits). Operaciones de Vulkan que exigen fp16 caen al CPU:
  revisar con `tools/bench/sched_profile.py` qué corre en el CPU además de los expertos.
- `GGML_VK_PERF_LOGGER` aborta (`GGML_ASSERT(ctx->compute_ctx.expired())`) en modelos que alternan CPU/GPU; para medir se
  usó una copia de `libggml-vulkan` con ese assert cambiado por un `synchronize` (no está en el fork).

## llama.cpp: fork y parches

- Fuente: fork público `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers` = oficial + 10 parches + notas en el README.
- Clon superficial en `~/llama.cpp`; `origin` = fork, `upstream` = oficial (push deshabilitado).
- Parches (ver `patches/README.md`): `-ot` con `Vulkan_Host`; lightning indexer fp32 en Vulkan; `llama-quantize COPY --tensor-type`;
  `multi_add` en AMD; kernel Q2_0 AVX2 del CPU (expertos de Flash-Next, +12 % en generación); atención dispersa desde 4× en GCN;
  `GGML_VK_FIRST_SUBMIT_NODES`; vocabulario de salida reducido (`d2t`) para el borrador MTP.
- Actualizar: skill `llama-cpp-update` / `scripts/update-llama.sh` (con `--check` solo informa). La versión queda fija hasta correrlo.
- Al 2026-11-14: base `9183a55`; había 33 commits nuevos en el oficial y ninguno mejoraba algo en esta máquina.

### Si se propone upstream

- Reglas de llama.cpp (`CONTRIBUTING.md`, `AGENTS.md`): abrir primero un issue, declarar el uso de IA, y el texto
  del issue/PR y los mensajes de commit los escribe una persona. El que manda el PR tiene que poder explicar cada línea.
- Antecedente: el PR #29213 (evitar el crash de GTT dentro de llama.cpp) fue rechazado por el maintainer de Vulkan:
  "es un bug de RADV". El parche propio es distinto (control del usuario, sirve en cualquier backend), pero solo se probó en Vulkan.
- Datos para el issue: RX 580 + 64 GB, Flash-Next Q2_0, 44/48 capas fijas: prompt largo 104 → 120–125 tok/s, generación igual.

## Herramientas de medición (`tools/bench/`)

- `moebench.py`: benchmark de configuraciones con un llama-server aparte (puerto 18081).
- `utilization.py`: cuánto trabajan CPU y GPU durante una generación (para encontrar el cuello de botella).
- `gguf_sizes.py`: tamaño de los expertos por capa (para calcular cuántas capas entran en el GTT).
- `smoke_models.py`: carga cada MoE por el panel y compara contra `baselines.json`.
- `ctx_stability.py`: por el router, 62K de entrada + 2048 de salida, tres cargas, memoria por proceso y logs.
  `--ai-mode` cierra KDE y lo restaura al terminar; salida en una carpeta nueva fuera de `/tmp`.
- `ctx_quality.py`: datos verificables del proyecto y marcas nuevas en ~58K tokens, conversación, cache del prompt
  y límite de contexto. Conserva el estado inicial del modo IA. No mide calidad general ni pérdida frente a F16.
- `parallel_test.py`: compara uno/dos slots y tiempo total de dos trabajos; `--followup` agrega continuaciones.
  Resultado (5.18): reservar dos slots no baja la velocidad de un único pedido, pero dos trabajos nuevos tardan
  ~5 % más en paralelo. El piloto cacheado ahorró ~14 % en continuaciones pequeñas, sólo ~2 % al incluir el primer
  turno. 64K totales se dividen en dos slots de 32K; queda `parallel = 1`. Un slot también puede recuperar varias
  conversaciones de la caché en RAM. No se habilitaron subagentes de Cline.
- `sched_profile.py`: tramos (splits) por token y qué operaciones que no son expertos caen en el CPU.
- `vk_timings.py`: tiempo de GPU por operación y cuánto queda para CPU + esperas (necesita una libggml-vulkan sin el
  assert del medidor, ver Descubrimientos).
- `cpu_profile.sh`: perfil del CPU con `perf` (requiere `sudo sysctl kernel.perf_event_paranoid=1`, vuelve a 4 al reiniciar).
- `moebench.py` acepta `TEMP=0` (comparar sin ruido), `PROMPT_FILE` y `LD_LIBRARY_PATH` (comparar dos `libggml-vulkan`
  sin recompilar).

Antes de medir: apagar texto e imágenes en el panel. Para probar `--load-mode none` con modelos grandes, envolver
el server en un límite de memoria (`WRAP="systemd-run --user --scope -p MemoryMax=56G ..."`).

## Compañero (pestaña «Compañero», `buddy.py`)

- Agente propio sobre el modelo de texto cargado (o el elegido en sus ajustes), con personalidad editable, memoria
  (`~/.config/llama-panel/buddy/memoria.md`, herramienta `recordar`) y herramientas con nivel de riesgo
  (`buddy_tools.py`; registro en `buddy/herramientas.log`). La seguridad está en las herramientas, no en el modelo:
  el modelo solo propone llamadas y lo que transcribe Whisper puede traer instrucciones de cualquiera que hable cerca.
- Voz en el CPU (la GPU es del modelo): whisper.cpp 1.9.5 en `~/whisper.cpp`, `ggml-small-q8_0` y la voz
  `es_AR-daniela-high` de Piper en `~/models-nvme/{whisper,piper}` (rutas en paths.env).
- Mediciones (Flash-Next `-hcq8`, modo IA, 2026-11-17):
  - Whisper con 4 s de audio: small 1.7 s con la ventana entera de 30 s, **0.6 s** ajustando `-ac` al largo
    (`voice.audio_ctx`); large-v3-turbo q5_0 11 s y 2.1 s, pero con la ventana chica repite palabras. 16 hilos no
    mejora a 8. Con el audio en webm del navegador (ffmpeg incluido): 0.74 s.
  - Primer token: 1.4 s sin herramientas, 3.8 s con una ronda de herramienta. Por voz, ~2.2 s desde que se suelta
    el botón (sin contar Piper).
  - **Trampa: el prompt tiene que quedar fijo.** Personalidad y herramientas son ~1200 tokens: si cambian, Flash-Next
    los reprocesa a ~58 tok/s (~20 s). Por eso la memoria se lee al empezar cada charla (no en cada mensaje), el
    prompt no lleva la hora y el historial se recorta de a mitades.
- El micrófono del navegador solo anda en https o `localhost`: desde la laptop, túnel SSH
  (`ssh -L 8090:localhost:8090 …`) y abrir `http://localhost:8090`.

## Modo IA

Botón «Modo IA» en el panel: apaga SDDM/KDE y Sunshine (~350–400 MiB de VRAM: kwin 95, plasmashell 79, Xorg, Sunshine)
y muestra un dashboard estilo btop en tty1 (`llama_panel/kiosk.py`, curses, solo caracteres de la consola de Linux:
bloques y bordes, no braille). «Salir del modo IA» vuelve a levantar SDDM, que tiene inicio de sesión automático.
Se instala una vez con `sudo ~/llama-panel/scripts/install-modo-ia.sh`: ayudante root `/usr/local/sbin/llama-modo-ia`
(solo systemctl/chvt con argumentos fijos), servicio `llama-kiosk.service` (corre como el usuario) y una regla de sudoers
limitada a ese ayudante. Probar el cuadro por SSH: `python3 -m llama_panel.kiosk --once --size 150x44`.

Lecciones de la primera prueba (2026-11-15):
- **Libera 415 MiB**: la VRAM en reposo pasa de 429 a 14 MiB.
- **Sin escritorio se pierde la GPU** si el usuario no está en los grupos `render`/`video`: el permiso sobre `/dev/dri`
  lo da logind (ACL) solo a la sesión activa del asiento. Sin él, Vulkan cae a `llvmpipe` (CPU): 1.9 tok/s.
  El instalador agrega los grupos (hace falta reiniciar) y el panel no deja activar el modo si su proceso no los tiene.
- **La consola queda en 640×480**: hay algo en HDMI-A-1 que reporta ese modo (el monitor real es HDMI-A-2, 1080p) y
  la consola clona las salidas. El dashboard elige la fuente sola (8×16 → 80×30) y tiene un layout compacto.
  Arreglo de fondo: `video=HDMI-A-1:d` en la línea del kernel (GRUB), si eso que está en HDMI-A-1 no hace falta sin escritorio.
- El dashboard se recarga solo cuando cambia `kiosk.py`.

Dashboard (2026-11-17): las barras de prompt y contexto ya no saltan de a tanda. llama-server informa el avance recién
al terminar cada tanda (2048 tokens ≈ 15 s con Flash-Next) y `n_prompt_tokens` de `/slots` incluye la tanda en curso
entera: el contexto marcaba 2048 al arrancar y el progreso iba 0 → 53 → 100 %. Ahora se estima dentro de la tanda con
la velocidad media del pedido (en la primera, la de la última tanda grande), sin pasar de la tanda ni volver atrás
(`stats.estimate_prompt`). El porcentaje queda en «--» hasta que termina la primera tanda (antes no se conoce el total).

Dashboard (2026-11-16): la velocidad de prompt ya no se borra con cada pedido. Un pedido con caché (pocos tokens
nuevos) no emite «prompt processing»: su velocidad sólo llega con el `prompt eval time` final, y el pedido siguiente
la limpiaba antes de la próxima lectura. Ahora el panel guarda la del pedido anterior del mismo modelo
(`prompt_tps_prev`) y el dashboard la muestra como «(pedido anterior)» hasta tener la propia; el gráfico de prompt
toma cada dato una vez, en cualquier fase. El tiempo de prompt corre desde el inicio del pedido sin esperar un log,
y si `/slots` confirma que el pedido sigue en curso no se marca como atrasado (las tandas de 2048 tokens tardan
~15 s, más que el umbral de 8 s). Replay de los logs reales leyendo cada 2 s: lecturas con pedido activo y sin
velocidad de prompt, 1065 de 3495 → 53 (sólo el primer pedido de cada modelo).
Intervalos: «en vivo» se lee cada 1 s (~14 ms por lectura: journal de 150 líneas + `/slots`) y el resto (estado,
CPU, historial) cada 3 s. Los tokens de salida avanzan con `/slots`; la velocidad sigue llegando del log cada ~3 s.

Dashboard (2026-11-15): «en vivo» conserva las barras de prompt/contexto entre tandas y al terminar el pedido.
El estado se acumula entre lecturas del journal: ocho segundos sin logs señalan un dato atrasado, no el fin de la
tarea (una tanda larga puede tardar más de veinte). Al iniciar otro pedido se limpian sus métricas; al descargar
el modelo se limpia el contexto. Se muestran media de prompt, media de generación y últimos 3 s por separado,
gráficos de ambas velocidades, tokens, tiempos, estimación restante del prompt y ms/token. MTP se informa al
terminar. CPU muestra temperatura del paquete y del núcleo más caliente (`coretemp`); VRAM muestra usado/libre
con dos decimales. Validado con replay de los logs reales de 62K y layouts 160×44, 140×44 y 120×36; sin activar
modo IA para probar el dibujo.
La conversación posterior (bitácora 5.17) agregó lectura de `/slots`: el contexto residente incluye el prefijo
reutilizado, aunque el log cuente sólo tokens nuevos. Se muestra el reuso por separado y la ETA lo descuenta.
El total del prompt durante la lectura es aproximado; `/slots.n_prompt_tokens` describe la secuencia residente,
no el pedido completo. Recuperación: 23/23 campos en ~58K y conversación hasta 65.300 tokens de entrada, con una
imprecisión porcentual en la explicación libre. No es una evaluación general de calidad ni un control contra F16.

Después del reinicio con los grupos y `video=HDMI-A-1:1920x1080@60e` (el dummy de Sunshine sin EDID quedaba en 640×480;
`vdisplay` le agrega el 1440p por xrandr, así que darle un modo en el kernel no la afecta): consola en 160×45, la GPU se
ve sin escritorio (8164 MiB libres) y la VRAM en reposo queda en 14 MiB.

**Qué ganan los modelos con el modo IA** (medido 2026-11-15):
- Velocidad: casi nada. Flash-Next igual; Qwen3.6 con cache 5600 MiB +2 % (no vale una configuración aparte);
  tandas de 4096 en Flash-Next van mucho peor (prompt 45 tok/s).
- **Contexto: Flash-Next pasa de 8K a 32K** (VRAM 7.56 GB en reposo, 7.72 con un prompt de 16K; con escritorio no entra).
  Superado: con la cache KV en Q8_0 y el borrador MTP en tandas de 512 entran **64K** (opciones de modo IA; bitácora
  5.14–5.15). Ojo: la VRAM «usada» engaña cuando no entra (el kernel manda el resto a GTT): mirar `tools/bench/gpu_mem_watch.sh`.
  La cifra vieja de 7.97 era antes de reducir las tandas del borrador. Consolidación (bitácora 5.16): **62.000 de
  entrada + 2048 de salida por el router**, sin truncamiento; prompt 93.76, gen con MTP 16.86 tok/s. Pico **7.761 GiB**
  de VRAM total y **29.422 GiB** de GTT del proceso. Tres cargas sin acumulación visible de memoria. Una corrida,
  no una prueba de horas ni de calidad; el margen cerca de 64K es menor que el medido con 30K. No subir a 80K.
  Vuelto a medir el 2026-11-16 con el borrador recortado (bitácora 5.30): 64K con 60.7K de entrada, pico **7.49 GiB**,
  prompt 92, gen 18.0–18.2 tok/s. **80K no entra**: la VRAM llega a 7.99 GiB y el prompt se desploma desde ~26K.
- Para eso cada modelo tiene **«opciones extra en modo IA»** (`extra_ai` en settings.json): se suman y pisan a las normales
  solo con el modo IA activo. Al entrar o salir del modo IA el panel regenera el preset y recarga el modelo cargado.

## Pendientes / ideas

- Con el modo IA activo, medir cuánta VRAM queda y si entran tandas o cache más grandes.
- Borrar el Flash-Next original (shard 1, 38 GB) si el `-hcq8` anda bien un tiempo.
- ~~Fusionar operaciones chicas en Vulkan~~: medido, techo +2.4 % (`analisis-rendimiento.md` 5.7).
- Mover Qwen3.6 al NVMe (el usuario prefiere dejarlo en el HDD por ahora: tarda ~2 min en cargar si no está en caché).
- ~~Sacar `n-gpu-layers = 99` de los ajustes globales~~: hecho (2026-11-17). Los MoE tienen su propio 99 en el
  control «Capas en GPU»; los densos (Qwen3-8B ×2, Qwen3.5-9B) rinden igual sin el valor global (A/B alternado:
  30.7–31.2 / 24.2–24.5 tok/s en los dos brazos, misma VRAM; `~/llama-panel-results/2026-11-17-ngl-global.json`).
- Campos propios para MoE en el panel: hecho en parte (controles de capas en GPU y expertos en RAM, 2026-11-17).
  Falta la caché de expertos (`moe-cache-mib` / `cpu-share`, hoy en opciones extra) y un botón de tuning.
- Botón "Actualizar desde GitHub" en el panel.
- ~~Descarga automática por inactividad~~ y encendido a demanda: hecho (2026-11-17, bitácora 5.35).
- Subir el límite de GTT (permitiría fijar las 48 capas de Flash-Next; ganancia estimada: algo más de prompt).
