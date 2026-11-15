# Notas: hardware, modelos y lo que aprendimos

Contexto acumulado para no tener que redescubrirlo. Primera versión: 2026-11-14.
La bitácora detallada de cada experimento (método y números) está en `analisis-rendimiento.md`.

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
spec-draft-model = ~/models-nvme/qwen3.8-flash-next/mtp-Qwen3.8-Flash-Next-Q4_0.gguf
spec-draft-n-max = 2         # borradores de 2 tokens: +30% de generación (3 y 4 rinden menos)
parallel = 1                 # IMPRESCINDIBLE: con 4 slots (default del router) rinde la mitad y el GTT llega a 30 GiB
ubatch-size = 2048           # entra gracias a la VRAM que libera hc en Q8_0 (con el original deja la VRAM en 7.8/8)
batch-size = 2048
```

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
- Margen que queda: fusionar operaciones chicas en Vulkan (hasta ~8 ms), solapar CPU y GPU (cambio grande en el scheduler).
- **Operaciones chicas, primer paso hecho (parche 4):** las fusiones de las hyper-connections (`DSV4_HC_PRE/POST`) ya
  estaban activas; las ~430 sumas sin nombre por token eran la combinación de los 10 expertos (9 sumas por capa).
  La fusión `multi_add` estaba apagada en Polaris por exigir RTE fp16; habilitada: 2980 → 2591 operaciones por token,
  Flash-Next 13.4 → 13.8 tok/s sin MTP (15.0 → 15.7 por el router), Qwen3.6 27.8 → 28.7.
  Quedan por token, en la GPU: SCALE 448, MUL 372, RMS_NORM 256, SIGMOID 230, CPY 150, GET_ROWS 148, SILU 134.
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
- **Strata** (github.com/Niko1221/Strata, MIT) no corre en la RX 580: necesita CUDA (RTX 20+) o HIP/ROCm (RDNA2+);
  Polaris (gfx803) quedó fuera de ROCm. Sus ideas ya están casi todas en llama.cpp; sus kernels propios son CUDA/HIP.
- La RX 580 **no tiene `shaderFloat16`** (sí lectura/escritura de 16 bits). Operaciones de Vulkan que exigen fp16 caen al CPU:
  revisar con `tools/bench/sched_profile.py` qué corre en el CPU además de los expertos.
- `GGML_VK_PERF_LOGGER` aborta (`GGML_ASSERT(ctx->compute_ctx.expired())`) en modelos que alternan CPU/GPU; para medir se
  usó una copia de `libggml-vulkan` con ese assert cambiado por un `synchronize` (no está en el fork).

## llama.cpp: fork y parches

- Fuente: fork público `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers` = oficial + 4 parches + notas en el README.
- Clon superficial en `~/llama.cpp`; `origin` = fork, `upstream` = oficial (push deshabilitado).
- Parches (ver `patches/README.md`): `-ot` con `Vulkan_Host`; lightning indexer fp32 en Vulkan; `llama-quantize COPY --tensor-type`.
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
- `sched_profile.py`: tramos (splits) por token y qué operaciones que no son expertos caen en el CPU.
- `vk_timings.py`: tiempo de GPU por operación y cuánto queda para CPU + esperas (necesita una libggml-vulkan sin el
  assert del medidor, ver Descubrimientos).
- `cpu_profile.sh`: perfil del CPU con `perf` (requiere `sudo sysctl kernel.perf_event_paranoid=1`, vuelve a 4 al reiniciar).
- `moebench.py` acepta `TEMP=0` (comparar sin ruido), `PROMPT_FILE` y `LD_LIBRARY_PATH` (comparar dos `libggml-vulkan`
  sin recompilar).

Antes de medir: apagar texto e imágenes en el panel. Para probar `--load-mode none` con modelos grandes, envolver
el server en un límite de memoria (`WRAP="systemd-run --user --scope -p MemoryMax=56G ..."`).

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

Después del reinicio con los grupos y `video=HDMI-A-1:1920x1080@60e` (el dummy de Sunshine sin EDID quedaba en 640×480;
`vdisplay` le agrega el 1440p por xrandr, así que darle un modo en el kernel no la afecta): consola en 160×45, la GPU se
ve sin escritorio (8164 MiB libres) y la VRAM en reposo queda en 14 MiB.

**Qué ganan los modelos con el modo IA** (medido 2026-11-15):
- Velocidad: casi nada. Flash-Next igual; Qwen3.6 con cache 5600 MiB +2 % (no vale una configuración aparte);
  tandas de 4096 en Flash-Next van mucho peor (prompt 45 tok/s).
- **Contexto: Flash-Next pasa de 8K a 32K** (VRAM 7.56 GB en reposo, 7.72 con un prompt de 16K; con escritorio no entra).
  Prompt de 16 224 tokens a 124 tok/s, generación 16.9 tok/s. 64K deja la VRAM en 7.97: demasiado justo.
- Para eso cada modelo tiene **«opciones extra en modo IA»** (`extra_ai` en settings.json): se suman y pisan a las normales
  solo con el modo IA activo. Al entrar o salir del modo IA el panel regenera el preset y recarga el modelo cargado.

## Pendientes / ideas

- Con el modo IA activo, medir cuánta VRAM queda y si entran tandas o cache más grandes.
- Borrar el Flash-Next original (shard 1, 38 GB) si el `-hcq8` anda bien un tiempo.
- Fusionar operaciones chicas en Vulkan (cadenas de ADD de las hyper-connections): hasta ~8 ms por token.
- Mover Qwen3.6 al NVMe (el usuario prefiere dejarlo en el HDD por ahora: tarda ~2 min en cargar si no está en caché).
- Sacar `n-gpu-layers = 99` de los ajustes globales.
- Campos propios para MoE en el panel (expertos en RAM, cache, reparto con el CPU) y un botón de tuning.
- Botón "Actualizar desde GitHub" en el panel; descarga automática por inactividad.
- Subir el límite de GTT (permitiría fijar las 48 capas de Flash-Next; ganancia estimada: algo más de prompt).
