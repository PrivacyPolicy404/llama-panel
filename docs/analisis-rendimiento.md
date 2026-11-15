# Bitácora de análisis de rendimiento (RX 580 + MoE grandes)

Registro de todo lo que se midió entre el 2026-11-14 y el 2026-11-15 para correr modelos MoE más grandes que la VRAM:
qué se probó, cómo, qué dio y qué se concluyó. Sirve para no repetir pruebas y para saber de dónde sale cada
decisión de configuración. El estado vigente y las trampas resumidas están en `notas.md`; los parches, en `../patches/`.

Convención de las tablas: **gen** = tokens/s de generación, **prompt** = tokens/s procesando el prompt, VRAM en GiB.
"Directo" = un `llama-server` aparte lanzado por `tools/bench/moebench.py`; "router" = a través del panel, igual que el uso real.

---

## 1. Máquina y límites que condicionan todo

| | |
|---|---|
| GPU | AMD RX 580 8 GB, Polaris (GCN4), solo Vulkan (RADV, Mesa 25.2). PCIe 3.0 x16 |
| Capacidades que **faltan** | `shaderFloat16` (sí tiene lectura/escritura de 16 bits), `shaderRoundingModeRTEFloat16`, cooperative matrix |
| CPU | i7-10700, 8 núcleos / 16 hilos, AVX2 |
| RAM | 64 GB DDR4 (62.7 GiB) |
| Discos | NVMe 970 EVO Plus (~1.9 GB/s medidos al cargar) · HDD WD `/mnt/hdd1` (~185 MB/s medidos) |
| GTT (memoria fija que la GPU puede usar) | **31 GiB** (mitad de la RAM, default de amdgpu). Pasarse = `device lost` |
| memlock | 8 GB (`--load-mode mlock` no sirve para modelos grandes) |

Velocidades de transferencia medidas por `moe-cache-tune.py`: subida por PCIe **9.2 GiB/s**, lectura de expertos por el CPU **68 GiB/s**.

---

## 2. Cómo medir sin engañarse (metodología)

Herramientas (todas en `tools/bench/`, con instrucciones en su encabezado):

| Herramienta | Para qué |
|---|---|
| `moebench.py` | Benchmark directo de configuraciones. Variables: `TEMP`, `PROMPT_MULT`, `N_PREDICT`, `PROMPT_FILE`, `CTX`, `WRAP`, `LD_LIBRARY_PATH` |
| `smoke_models.py` | Carga cada MoE por el panel/router y compara contra `baselines.json` (avisa si algo baja más de 15 %) |
| `sched_profile.py` | Cuántos tramos (splits) GPU↔CPU arma el scheduler por token y qué operaciones caen en el CPU |
| `vk_timings.py` | Tiempo de GPU por operación y cuánto queda para CPU + esperas |
| `cpu_profile.sh` | Perfil del CPU con `perf` |
| `utilization.py` | % de GPU ocupada y núcleos de CPU durante una generación |
| `gguf_sizes.py` | Tamaño de los expertos por capa (para calcular cuántas capas entran en el GTT) |

Reglas que salieron de errores reales:

1. **Para comparar cambios chicos (±5 %): `TEMP=0`, sin MTP, y alternar las versiones** (A, B, A, B). Con MTP y
   temperatura 0.7, lo que acierta el borrador varía entre corridas y tapa diferencias de varios puntos
   (pasó con el parche del lightning indexer: con ruido parecía neutro, controlado dio +4–5 %).
2. **Medir por el router antes de dar algo por bueno.** El router usa `parallel = 4` por defecto y los ajustes globales
   del panel (`n-gpu-layers = 99`); dos veces el benchmark directo dio el doble que el router por eso.
3. **Probar `--load-mode none` con modelos grandes dentro de un límite de memoria**:
   `WRAP="systemd-run --user --scope --quiet -p MemoryMax=58G -p MemorySwapMax=0"`. Si algo se carga entero en RAM,
   muere el server y no el escritorio.
4. **GPU libre**: texto e imágenes apagados en el panel. Cargar texto apaga el generador de imágenes.
5. Para comparar dos `libggml-vulkan` sin recompilar: copiar cada versión a una carpeta y usar `LD_LIBRARY_PATH`
   (el resto de las bibliotecas no cambia).
6. `GGML_VK_PERF_LOGGER` **aborta** en modelos que alternan CPU/GPU (`GGML_ASSERT(ctx->compute_ctx.expired())`).
   Para medir se compila una copia aparte de `libggml-vulkan` cambiando ese assert por
   `if (!ctx->compute_ctx.expired()) ggml_backend_vk_synchronize(backend);` y se restaura el árbol. No está en el fork.
7. `perf` necesita `sudo sysctl kernel.perf_event_paranoid=1` (vuelve a 4 al reiniciar).
8. **Una mejora de una operación no es una mejora del modelo**: dos veces una matmul bajó 25–60 % en `test-backend-ops`
   y el total quedó igual. Siempre cerrar con un A/B de punta a punta, y con MTP si es el uso real.
9. **Los techos de un microbenchmark sin afinar son pesimistas** (5.8: se predijo +3 % y dio +12.5 % con prefetch).
10. **El medidor de Vulkan serializa todo**: la suma por operación exagera el costo de las operaciones chicas
   (decía 6–8 ms, eran ~1.7). Para saber cuánto vale de verdad sacar operaciones, despacharlas dos veces (5.7);
   saltearlas no sirve en un MoE (el router colapsa y el CPU trabaja menos).
11. Trampas del entorno de trabajo: el scratchpad vive en `/tmp` y **se borra al reiniciar**; `pkill -f` con un patrón
   que aparece en el propio comando mata la shell; en una corrida en segundo plano, las líneas
   `VAR=valor comando` de un script se saltearon sin error (correr esas variantes en primer plano o con `export`).

---

## 3. Qwen3.6-35B-A3B y el expert cache (2026-11-14)

Modelo: `Huihui-Qwen3.6-35B-A3B-abliterated Q4_K` (21.7 GB, en el HDD). 36B totales, 3B activos, 256 expertos (8 por token).
Expert cache de llama.cpp 1.0 (`--moe-cache-mib`, `--moe-cache-cpu-share`): guarda en VRAM los expertos más usados
de los que viven en RAM; con `cpu-share` el CPU calcula una parte de los que faltan en vez de subirlos.

Directo, prompt ~335 tokens, 256 generados:

| Configuración | gen | prompt | VRAM |
|---|---|---|---|
| Por defecto (fit) | 26.1 | 104 | 7.0 |
| Expertos en RAM (`-cmoe`) | 23.0 | 86 | 2.4 |
| `-cmoe` + cache 3000 MiB (mmap, default) | **14.6** | 88 | 5.3 |
| Fijos (`-lm none`): por defecto | 26.2 | **156** | 7.0 |
| Fijos: `-cmoe` | 23.1 | 139 | 2.4 |
| Fijos: `-cmoe` + cache 3000 | 23.6 | 145 | 5.3 |
| Fijos: `-cmoe` + cache 3000 + cpu-share 0.9 | 27.1 | 150 | 5.3 |
| Fijos: fit + cache 1500 | 24.0 | 152 | 7.0 |
| Fijos: `-cmoe` + cache 5184 + cpu 0.8 (recomendado por `moe-cache-tune.py`, predijo 28.4) | **28.5** | 160 | 7.47 |
| Fijos: `-cmoe` + cache 4608 + cpu 0.8 (**elegida**, deja margen) | 27.9 | 158 | 6.91 |

Conclusiones:
- **Con mmap el cache empeora** (14.6): los expertos no quedan fijos y subirlos es lento. `load-mode = none` es obligatorio.
- Fijar la memoria acelera el **prompt** (+50 %), no la generación.
- El cache solo rinde con **reparto al CPU** (PCIe 3.0 lento vs CPU leyendo de RAM rápido). Ganancia total chica (+7–9 %).
- `moe-cache-tune.py` predice muy bien (28.4 predicho, 28.5 medido): 90 % de aciertos, "resto" 31 ms por token.
- Router: 27.1 tok/s; hoy 28.7 (con el parche 4). Prompt largo 225–228 tok/s.

---

## 4. Elección de modelos (2026-11-14)

Se buscó en la **API de Hugging Face** (no en blogs) el MoE más nuevo de cada familia que entrara en 64 GB:
- Qwen3.8 solo salió como 27B denso, 2.4T-A95B y Flash-Next (180B). No existe un 35B-A3B oficial.
- Candidatos: Qwen3.6-35B-A3B (el mejor en benchmarks), Gemma 4 26B-A4B, Nemotron 3.5 Lightning 30B-A3B, Nemotron 3.5 Bolt 74B-A9B.
- Elegidos: Qwen3.6 (versión huihui-ai, la más descargada) y **Qwen3.8-Flash-Next** en la cuantización GSQ-RCO de ISTA
  abliterada por SC117 (Q2_0, 66.8 GB en 2 shards).

Flash-Next: 125B de MoE con **6B activos** (512 expertos, 10 por token, 48 capas) + **51B de tabla de n-gramas**
(28.8 GB, shard 2) + 4B de MTP. La tabla se lee una fila por token: puede quedar en disco con `--lazy-mode on`, y
**tiene que estar en el NVMe** (el HDD es lento para lecturas aleatorias). Strata (el motor para el que se hizo esa
cuantización) no corre en la RX 580 (CUDA RTX 20+ / ROCm RDNA2+).

---

## 5. Flash-Next: de 12.8 a 18.2 tok/s

### 5.1 Punto de partida y lo que no funcionó (directo, temp 0.7)

| Prueba | gen | prompt | Nota |
|---|---|---|---|
| Por defecto | 12.8 | 45 (335 tok) | |
| `-cmoe` | 12.8 | 43 | |
| `-cmoe` + cache 4000 + cpu 0.8 (mmap) | 10.3 | 46 | cache empeora |
| `-cmoe` con 6 / 12 / 16 hilos | 12.5 / 12.6 / 12.1 | | **los hilos no cambian nada** |
| `moe-cache-tune.py` | 9.0 (cache 3200, share 0) | | se detuvo: no hay VRAM para un segundo tamaño |
| Fijar todos los expertos (`-lm none`) | — | | **device lost**: 31.6 GiB de expertos > 31 GiB de GTT |
| `-lm none --lazy-mode on --no-host` (RAM común) | 12.4 | 104.8 | igual que mmap (12.7 / 104.5) |

### 5.2 Lo que sí sumó (prompt largo = 2631 tokens)

| Paso | gen | prompt largo | VRAM |
|---|---|---|---|
| Tandas de 512 / 1024 / 2048 (`-ub`, `-b`) | 12.8 / 12.7 / 12.5 | 62 / 90 / **106** | |
| **Parche 1**: fijar solo 40 capas (`-ot ...=Vulkan_Host`), GTT pico 26.87 GiB | 12.4 | **125.6** | |
| + MTP borrador de 2 tokens (`mtp-...-Q4_0.gguf`) | **16.6** | 99.5 | 7.75 |
| MTP de 3 / 4 tokens | 15.9 / 14.6 | | |
| 44 capas fijas (GTT pico 29.51 GiB) + MTP 2, tandas 2048 | 16.7 | 120.4 | 7.81 |
| Ídem con tandas 1024 (margen de VRAM) | 16.8 | 105.4 | 7.16 |

MTP: aciertos 73–83 % con 2 tokens (2.45–2.66 tokens por pasada), 71–74 % con 3 (3.1–3.2, igual que Strata), 61 % con 4.
Verificar más tokens obliga a leer más expertos distintos en el CPU: en esta máquina el óptimo es 2.

### 5.3 Pasar por el router (el uso real)

| Situación | gen | prompt largo |
|---|---|---|
| `n-gpu-layers = 99` global sin `cpu-moe` / `override-tensor` | **1.9** | | (`--fit` se apaga y la GPU lee pesos por PCIe)
| Configuración completa, `parallel` por defecto (4) | 8–9 | 49 | (GTT 30 GiB)
| + `parallel = 1` | 14.3–15.7 | 106 |

### 5.4 Estrategias de borradores de Strata (config final, temp por defecto)

| Prueba | Texto | Editar código |
|---|---|---|
| MTP 2 (actual) | 16.9 | **18.8** |
| MTP 3 con `--spec-draft-p-min` 0.7 / 0.85 | 16.2 / 16.1 | |
| MTP 2 + `ngram-simple` | 17.0 | **13.4 (−29 %)** |
| MTP 2 + `ngram-mod` | | 18.1 |

Ninguna mejora a MTP de 2 solo.

### 5.5 Dónde se va el tiempo (después del parche 2, modelo original)

- `utilization.py`: GPU ocupada 54–56 %, CPU 7.6 núcleos "ocupados" (girando). Qwen3.6: GPU 47 %.
- **Corrección importante**: los dos MoE leen casi lo mismo de expertos por token (Qwen3.6 582 MiB, Flash-Next 632 MiB).
  Flash-Next es más lento por su **parte densa** (3.8 GiB contra 1.6 GiB) y sus 48 turnos GPU↔CPU por token, no por la RAM.
- `cpu_profile.sh` (perf): **64.8 % de los hilos girando en espera** (libgomp), 27.4 % calculando expertos
  (`ggml_vec_dot_q2_0_q8_0`), 1.9 % esperando a la GPU.
- `vk_timings.py`, ~83 ms por token con el medidor:

| Parte | ms/token |
|---|---|
| GPU, suma de operaciones | 47.1 (57 %) |
| · multiplicaciones de matrices | 29.3 (de eso, pesos BF16 de las hyper-connections: 10.9) |
| · operaciones chicas (ADD 565/token, MUL, CPY, GET_ROWS…) | 8.1 |
| · DeltaNet 2.4 · lightning indexer 0.27 | |
| CPU (expertos) + sincronizaciones + copias | 35.5 (43 %) |

GPU y CPU **se turnan y nunca trabajan a la vez**: 98 tramos por token (49 GPU + 49 CPU). ~2980 operaciones lanzadas
por token, 15.7 µs de promedio.

### 5.6 Cómo esperan los hilos (temp 0, sin MTP)

| Variante | gen |
|---|---|
| **Por defecto (giran)** | **13.16 / 13.19** |
| `OMP_WAIT_POLICY=PASSIVE` | 12.23 |
| `GOMP_SPINCOUNT=10000` | 12.40 |
| `--prio 2` | 12.43 |

La espera activa es lo mejor: los hilos arrancan apenas la GPU les pasa el turno (49 veces por token).

### 5.7 ¿Cuánto valen las operaciones chicas de la GPU? (2026-11-15, `-hcq8`, temp 0, sin MTP)

**Respuesta: ~1.7 ms de ~73 ms por token. Fusionarlas todas daría como mucho +2.4 %. No vale la pena.**

1. **Medidor** (`vk_timings.py`, 43.4 ms de GPU por token): matmul 27.0, RMS_NORM_MUL 2.0, DeltaNet 2.4,
   GET_ROWS 1.6 + CPY 1.5 (estados de DeltaNet, `build_rs`), MUL 0.9, SCALE 0.79 (188/token: el resto ya está fusionado),
   ADD 0.56, sigmoides/silu/concat/etc. ~3. Las escalas por 1/hc no justifican hornearlas en los pesos.
2. **Hallazgo puntual**: `hc_*_inject` (4 filas × 10240, BF16) lanza 2 grupos de trabajo para 36 CUs: 14.5 µs, 5.6 GB/s,
   1.38 ms/token en el medidor. llama.cpp ya sabe unirla con `hc_*_down` si el GGUF trae `hc_*_down_inject`
   (`build_hc_mix`). Se generó ese GGUF con `tools/gguf_merge_hc.py` (37 s, inject a Q8_0) y hubo que cambiar el
   grafo (escalar después de partir; si no, se rompe la fusión `HC_POST_GATE`). Medidor: GPU 43.4 → 41.6 ms.
   **A/B real (4 rondas alternadas): 13.86 → 13.97 (+0.8 %)**. Descartado: no paga un parche en el grafo de `qwen4exp`
   ni validar calidad de nuevo. Se revirtió y se borró el modelo.
3. **Por qué el medidor exagera**: escribe un timestamp y una barrera entre cada operación, así que todo corre en serie.
   Sin medidor, operaciones independientes se solapan (la matmul de 4 filas corría en paralelo con la de 320).
   **La suma del medidor sirve para ver proporciones de las operaciones grandes, no el costo de las chicas.**
4. **Ablación salteando operaciones: no sirve en un MoE.** Saltear las elementales dio gen 14.0 → 16.4 y prompt
   62 → 168: con el estado basura el router elige expertos degenerados y el CPU trabaja mucho menos.
5. **Método que sí sirve: despachar dos veces** cada operación chica sin fusionar (lib temporal con
   `GGML_VK_DOUBLE_OPS=SCALE,MUL,...`; en `ggml_backend_vk_graph_compute`, una llamada extra a `ggml_vk_build_graph`
   antes de la normal, solo si no hay fusión, `view_src == nullptr` y la salida no pisa una entrada). La salida queda
   **idéntica** (verificado a temp 0), así que ruteo y CPU no cambian; lo que sube es el costo real de esas operaciones:

| Variante (2 rondas alternadas) | gen | ms/token |
|---|---|---|
| normal | 13.78 / 13.77 | 72.6 |
| elementales ×2 (SCALE, MUL, ADD, SIGMOID, SILU, GLU, CONT, CONCAT, FILL…) | 13.52 / 13.55 | 73.9 (+1.3) |
| todas las chicas ×2 (+ GET_ROWS, CPY, SET_ROWS, RMS_NORM, ROPE, SOFT_MAX, TOP_K…) | 13.54 / 13.39 | 74.3 (+1.7) |

Conclusión: de los ~73 ms por token, la GPU real son ~35–40 y las operaciones chicas casi no pesan.
Lo grande es el CPU (expertos) + los 49 cambios de turno GPU↔CPU: ~35 ms.

### 5.8 Dónde se van los ~35 ms del CPU, y el kernel Q2_0 nuevo (2026-11-15, temp 0, sin MTP)

1. **Reparto por token** (lib temporal de `libggml-base` con marcas de tiempo por tramo en
   `ggml_backend_sched_compute_splits`, `GGML_SCHED_TIMING=1`): de ~72.5 ms, el CPU **espera a la GPU 37.9**, **calcula
   expertos 24**, graba/envía comandos de Vulkan 5.7, copia entradas a la GPU 0.9, y 3.9 se van fuera del scheduler
   (armar el grafo, muestreo, lecturas lazy). GPU y CPU se turnan: nunca trabajan a la vez.
2. **Hilos** (tiempo de expertos): 4 → 35 ms, 8 → 24, 12 → 23.4, 16 → 23 (y empeora lo de Vulkan). Sin memoria fija
   los expertos tardan ~1.5 ms menos (22.6 contra 24), pero la memoria fija es lo que hace rápido el prompt.
3. **Por operación** (lib temporal de `libggml-cpu`, `GGML_CPU_TIMING=1`): 144 `MUL_MAT_ID` por token (gate, up, down)
   son el 98 %; los hilos trabajan 21.1 de 21.6 ms (casi nada de barreras ni desbalance). Cada hilo procesa
   **3.8 GB/s**, cuando un hilo solo puede leer 23.6 GB/s de la RAM (medidor propio en C: 40.8 GB/s con 4 hilos).
   **El límite era el cálculo del kernel `ggml_vec_dot_q2_0_q8_0`, no la RAM.**
4. **Kernel nuevo** (parche 5): las activaciones Q8_0 se reacomodan una vez por columna para coincidir con el empaquetado
   de Q2_0 (el byte b tiene los pesos 4b..4b+3; `(qs >> 2k) & 3` da el plano k). Desempaquetar 32 pesos pasa de 6
   instrucciones a 2; `sum(y)` y las escalas se calculan una vez por columna. Microbenchmark (`q2bench.c`, exacto contra la
   referencia escalar): 1 hilo 5.1 → 11.2 GB/s; 8 hilos desde la RAM 33 → 34 (techo del patrón de acceso).
5. **Prefetch por software**: el prefetcher del hardware no se adelanta lo suficiente con filas cortas (720 / 180 bytes) y
   expertos en lugares distintos. Prefetch 1 KiB adelante: 8 hilos 34 → **40 GB/s**. Páginas grandes (`MADV_HUGEPAGE`): nada.

| A/B (alternado) | gen sin MTP | prompt 2631 tok |
|---|---|---|
| kernel viejo | 13.75 / 13.54 / 13.68 / 13.09 | 132.4 |
| nuevo | 14.38 / 14.63 / 14.28 / 14.27 | 132.9 |
| nuevo + prefetch 512 / **1024** / 2048 | 15.12 / **15.21** / 15.15 | 63 (prompt corto) |
| **router con MTP** | 15.7 → **17.6** | 132.7 |

- El prompt no cambia: con tandas grandes los expertos de la memoria fija se suben y se calculan en la GPU.
- Qwen3.6 no cambia (sus expertos son Q4_K).
- Verificación: `test-backend-ops -o MUL_MAT_ID -b Vulkan0` (el CPU es la referencia) → todos los `q2_0` OK.

### 5.9 La GPU con MTP, y el reloj de la memoria (2026-11-15, temp 0)

1. **Costo real por tipo de peso** (despachar dos veces las `MUL_MAT` de un tipo, sin MTP): Q8_0 +9.8 ms/token
   (1482 MiB, 158 GB/s), Q3_K +6.8 (457 MiB, **70 GB/s**), IQ4_XS +2.9 (282 MiB, 101 GB/s). Por peso, Q3_K cuesta lo
   mismo que Q8_0: **recuantizar no ayuda** (lo que se gana en velocidad se pierde en bytes, y suma VRAM).
2. En Polaris los shaders de menos de 4 bits están limitados por el cálculo (~330–430 Gpesos/s, cualquiera sea el
   tamaño); Q3_K es el peor (252). MMVQ (producto punto entero) no se usa: llama.cpp lo exige acelerado y Polaris no lo tiene.
3. **Con MTP** (cada verificación es n=3; acepta 83 %, 2.67 tokens por paso), por paso: GPU 63 ms, expertos en el CPU
   43 ms (hasta 30 expertos distintos por capa, ~1.8 GB a ~43 GB/s: **en el techo de la RAM**), grabar comandos 6.4,
   borradores + muestreo 17. Por token: 23.6 / 16 / 2.4 / 6.4.
4. Probado y descartado (mejora clara en la operación, **nada de punta a punta**):
   - IQ4_XS con n≥3: el shader dedicado se desploma (n=3 2.4×, n=8 7.7× más lento que el genérico); usar el genérico
     desde 3 columnas lo arregla en el test (diff guardado), pero el modelo da 20.31 → 20.37.
   - Q8_0 con 4 filas por grupo desde 2 columnas: n=3 2560×6144 122 → 93 µs; modelo 20.43 → 20.40 (y 20.96 → 20.95
     con la memoria fija). El medidor muestra que esa matmul baja 0.6 ms y otras del mismo tramo suben lo mismo.
   - `rm_kq = 2`: mejor con n=1, mucho peor con n=2–4. `--backend-sampling`: 20.65 → 20.72 (ruido).
   - Cantidad de borradores (con el kernel nuevo): 1 → 19.4, **2 → 20.45**, 3 → 19.15, 4 → 18.8. Cada borrador extra
     agranda la unión de expertos que el CPU tiene que leer, y eso ya está en el techo de la RAM.
5. **Reloj de la memoria de la GPU**: muestreando cada 10 ms durante una generación con MTP, el núcleo está siempre en
   1366 MHz pero **la memoria está en 1000 MHz (la mitad) el 16 % del tiempo**: amdgpu la baja en las pausas en que la
   GPU espera al CPU. Fijada en 2000 MHz (`manual` + `pp_dpm_mclk = 2`): con MTP 20.4–20.65 → **20.80 / 21.06 / 21.02**
   (+2–3 %), prompt 60 → 61; sin MTP igual (15.2). Se automatizó: `scripts/install-gpu-mclk.sh` (ayudante root +
   sudoers) y los units del router y de KoboldCpp lo piden al arrancar y lo liberan al parar.

### 5.10 Cada cuánto se le manda trabajo a la GPU (2026-11-15, MTP, temp 0, memoria fija)

- Cada tramo de GPU tiene ~53 nodos y poco cálculo, así que la regla por FLOPs de Vulkan no corre
  (`flops_per_submit = 0`): se envía cada 100 nodos o al llegar al 80 % del tramo (`almost_ready`). La GPU no arranca
  hasta que el CPU grabó ~43 de los 53 comandos (grabar cuesta ~6.4 ms por paso).
- `GGML_VK_MAX_NODES_PER_SUBMIT` (variable de entorno, sin parche): 100 → 20.90, 32 → 21.08, **16 → 21.21**, 8 → 21.07,
  4 → 20.93. Confirmación 16 contra 100 en 5 rondas: **21.15 contra 20.86 (+1.4 %)**, gana 4 de 5; prompt largo igual.
  Va en el unit del router (`llama_panel/install.py`).
- Uso real por el router en modo IA (32K de contexto, muestreo por defecto): el MTP acierta 59–69 % (83 % a temp 0);
  una respuesta de 900 tokens dio 15.6 tok/s y el test de humo 16.8. Con muestreo hay ±5 % de ruido entre corridas.

### 5.11 Muestreo de los borradores del MTP (2026-11-15, temp 0.8)

- llama.cpp trae `--spec-draft-sampling probabilistic`: el borrador muestrea a la misma temperatura y el modelo lo
  verifica por rechazo (exacto: la distribución del texto es la de sin MTP). El MTP lo soporta (`pending_h`).
- A/B directo, 3 rondas: greedy 19.34 (acepta ~72 %), probabilistic 19.80 (~74 %), **probabilistic + `p-min 0`
  19.78 (~75 %)**: +2.3 %. A temp 0 no cambia nada (p y q son puntuales). Va en las opciones extra de Flash-Next.
- **Router con todo lo de 5.9–5.11** (memoria fija, envíos cada 16 nodos, borradores probabilísticos), 3 corridas del
  test de humo: generación **18.3 / 17.9 / 18.4** (antes 17.6), prompt largo 135.3 (antes 132.6). Nueva referencia.

### 5.12 Cuánto cuestan los borradores, y recortarles el vocabulario (2026-11-15)

- Lib temporal con tiempos por grafo: por paso, verificación ~114 ms, **3 grafos de borrador de 3.3 ms** (~10 ms, 8 %),
  fuera de todo grafo ~3.3 ms. La mitad de cada borrador es su salida (248320 × 2560 Q4_0, 341 MiB).
- Borrador sobre los primeros K ids (resto en -inf; el modelo sigue verificando con el vocabulario entero):
  temp 0, K=65536: misma aceptación, +3 %. **Temp 0.8: aceptación 75 → 63 % (K=65536) / 68 % (K=98304), -5 a -7 %.**
  Con muestreo importa toda la distribución, y en español mucha probabilidad cae en ids altos. Descartado
  (diff en `patches/experimentos/`).
- La espera por la GPU ya está bien resuelta en llama.cpp: duerme hasta el 80 % del tramo y después espera activa.

### 5.13 Hardware: RAM a 3200 y overclock de VRAM (2026-11-15)

- **RAM, XMP perfil 1 (DDR4-3200, antes perfil 2 a 3000)**: `membw` 40.8 → 43.5 GB/s. Texto a temp 0 idéntico a la
  referencia (`tools/bench/gen_ref.py`). Sin MTP 15.15 → 15.24 (+0.6 %: con 1 token el tramo de expertos es tan corto
  que no llega al techo); con MTP a temp 0.8 ~19.75 → 20.15 (+2 %). **Queda.**
- **VRAM**: `amdgpu.ppfeaturemask=0xfff7ffff` (bit de overdrive) habilita `pp_od_clk_voltage` (MCLK hasta 2250 MHz).
  Escalera 2000 → 2250 con verificación en cada paso: `test-backend-ops` 633/633, texto idéntico, sin errores del
  kernel; Q8_0 4096×14336 285 → 255 µs. **Pero al volver de 2250 a 2000 en caliente (escritorio activo) el SMU dejó de
  responder ("last message was failed"), se colgó el anillo gfx (kscreenlocker) y el reset de la GPU no terminó:
  la máquina quedó colgada.** Descartado: reescribir la tabla de relojes con displays activos no es seguro en Polaris.
  El ayudante `llama-gpu-mclk` volvió a solo elegir niveles de fábrica (`high`/`auto`).
- **Segundo intento: aplicarlo una sola vez al arrancar**, antes del escritorio (`install-gpu-mclk.sh --oc 2250`:
  `llama-gpu-oc.service`, `Before=display-manager.service`). Nadie usa la placa en ese momento y después solo cambia
  de nivel el driver, como siempre. Arrancó bien (2250 MHz, 950 mV, sin errores del kernel), texto idéntico.
  Contra 2000 MHz (RAM a 3200 en los dos): sin MTP 15.24 → 15.40 (+1 %), MTP a temp 0.8 20.15 → 20.64 (+2 %),
  prompt largo igual (~135). **La placa llega al tope de potencia (153–156 W de 155) y baja el núcleo a ~1210–1240
  MHz**: por eso la ganancia es chica y el prompt (más núcleo) no mejora.
- **Descartado del todo**: en ese mismo arranque, al entrar al modo IA (se apaga el escritorio y se reconfiguran las
  salidas de video) y pedir la memoria al máximo, el SMU rechazó el cambio ("last message was failed ret is 255") y la
  memoria quedó clavada en 300 MHz en `manual`. Con la tabla de fábrica eso nunca pasó (modo IA + memoria fija se
  usaron muchas veces). Con la tabla modificada, Polaris falla los cambios de nivel de memoria cuando cambia la
  configuración de pantallas. +1–2 % no paga cuelgues. (Unas rayas en pantalla en ese momento eran el cable HDMI.)
  `install-gpu-mclk.sh` sin `--oc` ahora saca un overclock anterior.
- **Último intento** (a pedido, "si no anda volvemos"): piso de voltaje 1000 mV para ese nivel, tope de 200 W y la
  memoria fija desde el arranque (sin transiciones después). Falló en el arranque mismo, antes del escritorio: el SMU
  rechazó pasar la memoria al nivel 2 apenas aplicada la tabla ("last message was failed ret is 255", queda en 300
  MHz). **Conclusión final: este SMU no acepta cambios de nivel de memoria con la tabla modificada, con o sin
  pantallas de por medio, con o sin más voltaje. Placa de fábrica.**

---

## 6. Los cinco parches (fork `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers`)

### Parche 1 — `-ot` con buffers de memoria fija (`Vulkan_Host`)
Permite fijar solo parte de los expertos. Ver 5.2. Detalle: con mmap, un override a host buffer se ignora; el parche avisa.

### Parche 2 — lightning indexer fp32 en Vulkan
- Hallazgo: `sched_profile.py` mostró **122 tramos por token** y 12 `LIGHTNING_INDEXER` en el CPU. La RX 580 no tiene
  `shaderFloat16` y el shader lo exigía (tiles y productos punto en f16).
- Parche: variante del shader que lee K/máscara en 16 bits pero calcula en 32. `test-backend-ops`: 210/210 OK.
- Resultado: 122 → **98 tramos**; A/B controlado (temp 0, sin MTP): corto 12.51/12.67 → **13.13/13.17** (+4–5 %),
  ~7K de contexto 12.34 → 12.60 (+2 %).
- Indexer en la GPU según el contexto: 0.27 ms/token (136 tok), 0.29 (5.4K), 0.51 (10.8K), 0.74 (19.4K).
  Una variante "de a 1 token" ganaría como mucho 0.3–0.7 ms/token (<1 %): **no se hizo**. El costo extra de los
  contextos largos (+4.6 ms/token de 136 a 19K) viene sobre todo de la atención completa de las 12 capas.

### Parche 3 — `llama-quantize COPY --tensor-type`
- Hallazgo: 10.9 ms/token en matmul de pesos BF16 (las hyper-connections `hc_(attn|ffn)_(up|down)`, 1.2 GiB).
- Parche: `COPY` aplica `--tensor-type` y copia el resto (antes los ignoraba). Se generó `-hcq8` (192 tensores a Q8_0).
- Calidad (`llama-perplexity --kl-divergence`, 12 × 512 tokens): perplejidad 0.9988× ± 0.003, KLD media 0.0077,
  KLD 99 % 0.105, mismo token más probable 97.45 %. **Sin pérdida medible.**
- Resultado directo: gen +2 % (13.36/13.17 → 13.63/13.46), VRAM −0.55 GB (4.86 → 4.31).
  Por el router, con la VRAM liberada usada para tandas de 2048: 14.7/14.8 → **15.0/15.4**, prompt 106.7 → **132**.

### Parche 4 — fusión `multi_add` de Vulkan en AMD
- Hallazgo: las fusiones de las hyper-connections (`DSV4_HC_PRE/POST`) ya estaban activas; las ~430 sumas sin nombre
  por token eran la **combinación de los 10 expertos** (9 sumas por capa). La fusión `multi_add` exigía
  `shaderRoundingModeRTEFloat16` (proxy para excluir el driver Intel ANV, PR #15252), que la RX 580 no tiene.
- Parche: habilitarla también en drivers AMD. `test-backend-ops -o ADD`: 116/116 OK.
- Resultado: 2980 → **2591 operaciones por token**; A/B controlado 13.49/13.37 → **13.83/13.71** (+2.5 %);
  router: Flash-Next 15.0 → **15.7**, Qwen3.6 27.8 → **28.7**.


### Parche 5 — kernel Q2_0 × Q8_0 para AVX2 en el CPU
- Hallazgo y método en 5.8: los expertos tardaban 22–24 ms/token limitados por el cálculo (3.8 GB/s por hilo).
- Parche: activaciones reacomodadas una vez por columna + prefetch 1 KiB. Solo cambia `MUL_MAT_ID` con pesos Q2_0 en x86 AVX2.
- Resultado: sin MTP 13.5 → **15.2** (+12.5 %); router 15.7 → **17.6**. Prompt igual.

---

## 7. Modo IA (escritorio apagado) — 2026-11-15

- Libera **415 MiB** de VRAM (en reposo: 429 → 14 MiB; kwin 95, plasmashell 79, más Xorg y Sunshine).
- Primera prueba fallida: sin sesión de escritorio, logind saca el permiso sobre `/dev/dri` (ACL de la sesión activa) y
  Vulkan cae a `llvmpipe` → Qwen3.6 a **1.9 tok/s**. Arreglo: usuario en los grupos `render` y `video` + reinicio; el
  panel no deja activar el modo IA si su proceso no tiene el grupo `render`.
- Consola en 640×480: el dummy HDMI de Sunshine (HDMI-A-1) no manda EDID y la consola clona las salidas.
  Arreglo: `video=HDMI-A-1:1920x1080@60e` en GRUB (antes `:e`); `vdisplay` sigue agregando el 1440p por xrandr.
  Consola: 53×20 → 160×45.
- Qué se gana con la VRAM liberada:

| Prueba (en modo IA) | gen | prompt | VRAM |
|---|---|---|---|
| Flash-Next, tandas 2048 | 17.3 (MTP, temp 0) | 125 | 6.84 |
| Flash-Next, tandas 4096 | 9.0 | **45** | 6.38 |
| Qwen3.6, cache 4608 / 5184 / 5600 | 27.7 / 28.2 / 28.4 | 160–165 | 6.50 / 7.06 / 7.47 |
| Flash-Next, contexto 8K / **32K** / 64K | ~17.9 | | 6.77 / **7.56** / 7.97 |

- Conclusión: velocidad casi igual; lo que gana es **contexto**: Flash-Next a 32K (prompt de 16 224 tokens a 124 tok/s,
  gen 16.9 con MTP, VRAM 7.72). Se implementaron las «opciones extra en modo IA» por modelo y el cambio automático.

---

## 8. Lo que no funcionó (para no repetirlo)

| Idea | Resultado |
|---|---|
| Expert cache con mmap | Peor (Qwen3.6 14.6 vs 23 sin cache) |
| Expert cache en Flash-Next | Siempre peor: solo hay ~3.2 GB de VRAM para 37 GB de expertos |
| Fijar los 48 capas de Flash-Next | device lost (GTT 31 GiB). Se podría subir el GTT por GRUB: no se hizo |
| Más hilos / menos hilos | Sin cambio (el CPU espera a la GPU) |
| OpenMP pasivo, giro corto, prioridad alta | Peor que el default |
| MTP de 3–4 tokens, `p-min` | Peor que 2 |
| MTP + prompt lookup (`ngram-*`) | Igual en texto, hasta −29 % en código |
| `mtp-q2_0.gguf` de SC117 | Formato Strata (`qwen4exp-mtp`), llama.cpp no lo carga |
| Tandas de 4096 | Prompt 45 tok/s (llama.cpp cambia a un camino peor) |
| Lightning indexer "de a 1 token" | Techo <1 %: no se implementó |
| Unir `hc_down` + `hc_inject` (`-hcq8m`) | +0.8 %: no paga el parche en el grafo (5.7) |
| Fusionar / hornear las operaciones chicas | Techo medido +2.4 % (todas juntas cuestan ~1.7 ms/token, 5.7) |
| Ablación salteando operaciones | Resultado falso: el router de expertos colapsa (5.7) |
| Recuantizar Q3_K (a Q4_K, IQ3_S, Q8_0) | Por peso cuesta igual; suma VRAM o arriesga calidad (5.9) |
| Afinar filas por grupo / shader genérico de IQ4_XS | Mejora la operación, nada de punta a punta (5.9) |
| 3–4 borradores MTP, `--backend-sampling` | Peor / ruido (5.9) |
| Enviar a la GPU cada 4–8 nodos | Peor que 16 (el costo de cada envío empieza a pesar, 5.10) |
| Vocabulario recortado para el borrador MTP | +3 % a temp 0, -6 % a temp 0.8 (5.12) |
| Overclock de VRAM (pp_od_clk_voltage) | +1–2 % (tope de potencia); el SMU falla con la tabla modificada: cuelgue y memoria clavada (5.13) |

---

## 9. Configuración vigente (resumen; detalle en `notas.md`)

| Modelo | Router | Claves |
|---|---|---|
| Qwen3.6-35B-A3B | 28.8 gen, 228 prompt largo, VRAM 7.1 | `cpu-moe`, `load-mode = none`, cache 4608 + cpu 0.8 |
| Flash-Next `-hcq8` | 18.2 gen, 135 prompt largo, VRAM 7.2 | parche 1 (44 capas fijas), `lazy-mode`, MTP 2 probabilístico, `parallel = 1`, tandas 2048, memoria de la GPU fija, envíos cada 16; modo IA: 32K de contexto |

---

## 10. Próximos pasos posibles (con lo que se espera ganar)

1. ~~Más fusiones de operaciones chicas~~: medido en 5.7, techo +2.4 %. Descartado.
2. ~~Expertos en el CPU~~: hecho (5.8, parche 5). Ahora están en el techo de la RAM (~40 GB/s).
3. ~~La GPU, operación por operación~~: medido en 5.9. Las mejoras de shaders sueltos no llegan al total; la
   memoria fija sí (+2–3 %). Queda entender por qué el tramo de GPU no baja cuando baja una operación.
4. Grabar comandos de Vulkan (5.7 ms/token, en el CPU) y lo de fuera del scheduler (3.9 ms).
5. Solapar CPU y GPU (hoy se turnan): cambio grande en el scheduler.
6. Atención con contexto largo (lo que más crece con los 32K del modo IA).
7. Subir el GTT por GRUB para fijar las 48 capas: ganancia estimada chica (solo prompt).

## 11. Reproducir las mediciones clave

```bash
# A/B controlado de dos variantes (alternar, temp 0, sin MTP)
WRAP="systemd-run --user --scope --quiet -p MemoryMax=58G -p MemorySwapMax=0" TEMP=0 PROMPT_MULT=8 N_PREDICT=256 \
  python3 tools/bench/moebench.py MODELO "A|args..." "B|args..."

# Tramos por token y operaciones en el CPU
python3 tools/bench/sched_profile.py MODELO salida.log ARGS...

# Calidad de una recuantización
llama-perplexity -m ORIGINAL -f texto.txt -c 512 --chunks 12 --kl-divergence-base base.bin ARGS...
llama-perplexity -m NUEVO    -f texto.txt -c 512 --chunks 12 --kl-divergence-base base.bin --kl-divergence ARGS...

# Correctitud de un shader de Vulkan
GGML_VK_VISIBLE_DEVICES=0 ~/llama.cpp/build/bin/test-backend-ops test -o OPERACION -b Vulkan0

# Verificación rápida de todo por el router
python3 tools/bench/smoke_models.py
```
