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
| `gpu_mem_watch.sh` | VRAM y GTT del `llama-server` según el kernel (fdinfo): muestra lo que pidió como VRAM y terminó en GTT |
| `ctx_stability.py` | Ventana de 64K por el router: entrada de 62K, salida de 2048, tres cargas y muestras DRM con fecha/fase; restaura escritorio y límites |
| `ctx_quality.py` | Recuperación en ~58K tokens del proyecto, conversación con caché, cercanía al límite y rechazo del exceso; guarda rúbrica y respuestas |
| `parallel_test.py` | Uno/dos slots: pedido individual, dos trabajos en serie/concurrentes y continuación de conversaciones con caché |

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
12. **«VRAM usada» no dice si entra** (5.15): cuando lo que pide llama.cpp no cabe, el kernel manda el sobrante a GTT
   sin avisar y `mem_info_vram_used` queda por debajo de 8 GB como si sobrara. Ver lo del proceso con
   `gpu_mem_watch.sh`: si su GTT supera lo fijado a propósito (29.4 GiB de expertos en Flash-Next), no entró.
   Y lo contrario: VRAM al 100 % (7.97 de 8) tampoco sirve, el driver necesita margen y los prompts largos se frenan.

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

### 5.14 64K de contexto con la cache KV en Q8_0 (2026-11-15)

- Flash-Next es híbrido: solo una de cada cuatro capas tiene atención completa (el resto es recurrente, estado fijo de
  338 MiB), así que la cache KV es chica: **1.9 GiB a 64K en F16** (1536 + 384 del borrador MTP). En Q8_0: **1.0 GiB**.
- Calidad (`llama-perplexity`, 6 tramos de 4096, F16 contra Q8_0): PPL 0.99987×, **KLD media 0.0035**, 99 % 0.041,
  **mismo token 98.05 %**. Despreciable.
- VRAM con escritorio, 64K: F16 **7.99 GiB** (se pasa, gen 15.9) / Q8_0 **7.26 GiB** (gen 21.5, temp 0). Con un
  prompt de **29 527 tokens**: pico 7.71 GiB, prompt 105.9 tok/s, gen 16.5 con todo ese contexto (MTP acepta 90 %).
  En modo IA la misma prueba dio prompt 63 tok/s y VRAM 7.98. **Esa lectura de 7.71 engañaba: ver 5.15.**
- Costo con prompt corto (temp 0, MTP, alternado ×2, prompt de 3287): 8K F16 20.64 / 20.45 gen, 134 prompt;
  64K Q8_0 20.09 / 20.02 gen, 129 prompt → **−2.4 % gen, −3.5 % prompt**. Router: 17.4 / 131 (referencia 18.2 / 135).
- Primero quedó como configuración normal; a pedido del usuario pasó a las **opciones de modo IA** (sin modo IA,
  8K en F16, que es ~3 % más rápido). Reproducir: `tools/bench/ctx_kv_test.sh`.

### 5.15 Lo que pide de verdad el contexto largo, y el borrador en tandas de 512 (2026-11-15)

- La pista: en modo IA (más VRAM libre) el mismo 64K Q8_0 andaba **peor** que con escritorio: prompt de 29.5K a
  64 tok/s contra 106, y la VRAM clavada en 7.98 desde la carga (con escritorio arranca en 7.30 y sube despacio).
- Medido por proceso (fdinfo de DRM, `tools/bench/gpu_mem_watch.sh`), prompt de 6567, temp 0:

| 64K Q8_0 | llama en VRAM | llama en GTT | prompt |
|---|---|---|---|
| con escritorio | 7.23 | 30.37 | 126 |
| modo IA | **7.97** | 29.6 | 119 |

  Lo pedido es igual (~37.6 GiB en total), pero **llama.cpp pide ~8 GiB de VRAM**. Con escritorio el kernel deja
  ~0.75 GiB en GTT desde la carga (las 44 capas fijas son 29.4 GiB; el resto es lo que no entró), y la lectura total
  (7.71) parecía tener margen. En modo IA entra todo justo y la placa queda llena.
- Quién lo usa: los buffers de cálculo, **902 MiB del modelo y 867 MiB del borrador MTP**, los dos dimensionados para
  tandas de 2048. El borrador tiene su propia opción: `--spec-draft-n-ubatch`.

| 64K Q8_0, con escritorio | llama en VRAM | GTT | gen | prompt |
|---|---|---|---|---|
| borrador en tandas de 2048 | 7.23 | 30.37 (0.9 de más) | 19.15 | 127.4 |
| 1024 | 7.60 | 29.45 | 19.38 | 132.2 |
| **512** | **7.41** | **29.41** (nada de más) | 19.21 | **132.0** |

- Con 8K F16 (alternado ×2, prompt de 3287): 2048 → 20.65 / 20.57 gen, 134.1 / 134.0 prompt, VRAM 7.09 / 7.23;
  512 → 20.43 / 20.76, 133.5 / 133.6, VRAM **6.67 / 6.81**. Misma velocidad, **0.42 GiB menos**.
- Pero con el prompt de 29.5K y escritorio, 64K con tandas de 512 bajó a **68 tok/s** (todo en VRAM: llama 7.53,
  total 7.93). La causa no es el borrador sino **la placa casi llena**:

| Prompt de 29 527 tokens, KV Q8_0 | VRAM total | prompt | gen |
|---|---|---|---|
| 64K, borrador 2048, escritorio (0.9 GiB en GTT) | 7.71 | 106 | 16.5 |
| 64K, borrador 2048, modo IA | 7.98 | 64 | 16.9 |
| 64K, borrador 512, escritorio | 7.93 | 68 | 16.6 |
| 48K, borrador 512, escritorio | 7.57 | **109** | 14.9 |
| **64K, borrador 512, modo IA** | **7.56** | **108.8** | 16.3 |

- **Regla: con la VRAM total en ~7.9 GB o más, los prompts largos pierden ~40 %** (con 6.5K todavía no se nota).
  Dejar ~0.3–0.4 GB libres. Con prompts cortos el umbral no aparece, así que hay que medir siempre con uno largo.
- **Queda**: `spec-draft-n-ubatch = 512` en la configuración normal (8K F16: misma velocidad, 0.42 GiB menos) y, en las
  opciones de modo IA, 64K con KV Q8_0 (7.56 GB, prompt largo 109, gen 16.3 con 30K de contexto). Sin modo IA, el
  máximo con margen es ~48K. No se subió el modo IA a 80K: quedaría en ~7.8, al borde del umbral.

---

### 5.16 Consolidación: cerca de 64K ocupados por el router (2026-11-15)

- Objetivo: cerrar lo que no probaba el prompt de 29.5K de 5.15: llenar casi toda la ventana, sostener generación
  y verificar que descargar/recargar no acumula recursos. Sin cambiar modelos, binarios ni settings.json.
- Camino real: API del panel -> router :8080 -> Flash-Next `-hcq8`. Modo IA, contexto 65536, KV Q8_0 en modelo
  y borrador, tandas 2048/512, MTP 2 probabilístico, un slot. Router limitado temporalmente a 58 GiB, sin swap.
- Entrada: **62.000 tokens exactos** de documentación variada de llama.cpp, guardados como IDs. `cache_prompt = false`:
  **ningún token reutilizado**. Salida forzada con `ignore_eos`, temperatura 0.8 y semilla 1234.

| Medida | Resultado |
|---|---|
| Prompt | **62.000 tokens**, 661,29 s, **93,76 tok/s** |
| Generación con MTP | **2.048 tokens**, 121,40 s, **16,86 tok/s** |
| Entrada + salida | **64.048 posiciones**, 97,73 % de la ventana; sin truncamiento ni desplazamiento de contexto registrado |
| Aceptación MTP | 1228/1638, **74,97 %** |
| Pico de VRAM total | **7,761 GiB** (proceso: 7,747 GiB) |
| Pico de GTT del proceso | **29,422 GiB**, se mantiene cerca de los expertos fijados |
| Tres cargas, prompt corto idéntico, temp 0 | **19,454 / 19,463 / 19,467 tok/s**, texto idéntico en las tres |
| VRAM tras descargar, antes de recargar | **0,014 GiB**; sin acumulación visible entre ciclos |
| Registros del kernel/router | Sin errores Vulkan, reset de GPU, fallos de anillo ni OOM detectados |

- **Conclusión acotada:** pasó una carga casi completa y dos recargas posteriores con generación. Los 7,56 GiB de
  5.15 eran con 30K ocupados: cerca del límite el pico sube a 7,761 GiB y el margen físico baja a ~0,24 GiB.
  No habilitar 80K a partir de este resultado. No es una prueba de horas ni valida calidad semántica o recuperación
  de información a 64K. Tampoco es un A/B de velocidad con 5.15: el texto y la longitud son distintos.
- Escritorio restaurado, router apagado y límites devueltos a los valores iniciales; hash de settings.json sin cambios.
  Al restaurar el escritorio aparecen los mensajes conocidos `No EDID found` del dummy HDMI-A-1; no son fallos
  de cómputo durante la carga.
- Humo posterior con escritorio, por el router, **ambos modelos OK**, sin reescribir baselines:
  Flash-Next **17,8 gen / 134,2 prompt**, VRAM **6,67 GiB**; Qwen3.6 **28,9 / 231,9**, VRAM 6,92 GiB.
  Flash-Next quedó ~2 % por debajo de la referencia 18,2, dentro del ruido conocido con MTP y muestreo;
  una sola corrida no prueba un cambio de velocidad. Qwen3.6 cargó en frío desde el HDD en 119 s.
  Ver `smoke-normal.log` en los artefactos. Los 33 tests del panel también pasaron.
- Reproducir: `python3 tools/bench/ctx_stability.py --ai-mode --output ~/llama-panel-results/64k-corrida-2`.
  **Cierra KDE**; requiere servidores y modo IA apagados. Artefactos de esta corrida:
  `/home/gen/llama-panel-results/2026-11-15-64k-stability/` (respuestas, tokens, muestras, presets, versiones y logs).
  Panel base `016758b`, fork `9d77a39`; no se recompiló ni actualizó llama.cpp.

---

### 5.17 Recuperación y conversación con el proyecto como contexto (2026-11-15)

- Flash-Next `-hcq8` por el router, configuración de modo IA vigente: 64K Q8_0, MTP 2, tandas 2048/512.
  Temperatura 0, semilla 1234, pensamiento desactivado y respuesta JSON. No se cambiaron versiones ni settings.
- Corpus de **57.999 tokens**: archivos del proyecto, preservando completos los documentos y fuentes necesarios
  para responder. Tres marcas aleatorias nuevas en posiciones **68 / 39.783 / 57.769**. La marca interior está
  aproximadamente al 69 %, no exactamente a la mitad. Rúbrica guardada antes de inferir; no se suministra al modelo.
- **23/23 campos cerrados correctos**: tres marcas y diez datos del proyecto (capas, tandas, ventanas, reintento de
  carga, prefetch Q2_0, dashboard y sensor), seis campos de razonamiento sobre memoria y comparación de velocidades,
  tres campos de recuerdo entre turnos y un campo en la conversación cercana al límite.

| Turno | Entrada total | Reutilizados | Tokens nuevos procesados | Tiempo de prompt | Salida / gen |
|---|---:|---:|---:|---:|---:|
| Recuperación de datos | 57.999 | 0 | 57.999 | 623,07 s | 262 / 19,83 tok/s |
| Razonamiento | 58.401 | 58.260 | 141 | 5,36 s | 328 / 18,03 tok/s |
| Recuerdo entre turnos | 58.776 | 58.729 | 47 | 3,16 s | 60 / 19,59 tok/s |
| Conversación cercana al límite | **65.300** | 58.836 | 6.464 | 114,79 s | 26 / 18,23 tok/s |

- El último turno terminó con **65.326 tokens totales (~99,7 % de la ventana)**, sin truncamiento registrado.
  La entrada de **65.537** fue rechazada con **HTTP 400, `exceed_context_size_error`**; no se recortó silenciosamente.
  Control adicional en otro arranque: después del mismo exceso, **el mismo proceso** respondió un pedido válido,
  conservando **10.524 tokens de caché**, con sólo 25 nuevos por procesar. No se recargó entre error y recuperación.
- Pico de VRAM total muestreado en la prueba principal: **7,75 GiB**. Sin fallos Vulkan, resets ni OOM detectados.
- **Revisión libre, fuera de los campos cerrados:** la explicación mezcla el porcentaje de ocupación: 97,73 %
  corresponde a 62.000 + 2048 tokens de 5.16, no a los 62.000 de entrada solos. Por eso el resultado no se presenta
  como «razonamiento perfecto». La rúbrica admite tanto el fragmento `limit reached` del código como el error
  completo `model limit reached`; esa aclaración quedó guardada antes de recibir la primera respuesta.
- La marca de inicio también aparece en una respuesta previa: el turno de 65.300 mide continuidad de conversación,
  no una segunda prueba independiente de búsqueda a distancia completa. No hay control F16 ni comparación con otro
  modelo: esta prueba no cuantifica pérdida por Q8_0 ni calidad general. La generación corta a temp 0 con JSON
  tampoco reemplaza la referencia de velocidad normal con muestreo.
- **Dashboard real:** 387 muestras activas, todas conservando contexto 65.536, incluidas pausas sin logs. La
  conversación reveló otro caso: `n_tokens` del log cuenta sólo tokens nuevos, mientras el progreso incluye el prefijo
  reutilizado. Se agregó `/slots` para mostrar **caché y secuencia residente**, sin confundirlos con nuevos tokens.
  `n_prompt_tokens` de `/slots` es la secuencia residente (incluye la tanda en curso), **no** el total del pedido;
  durante el prompt ese total sigue siendo estimado a partir del progreso redondeado. La ETA descuenta la caché.
  Comprobación adicional: 39 muestras con prefijo reutilizado y contexto residente nunca menor que la caché.
  Pasan **46 tests**, incluido el caso real 58.836 reutilizados + 4416 nuevos y el cálculo de ETA.
- Artefactos: `/home/gen/llama-panel-results/2026-11-15-64k-quality/`, con rúbrica, corpus, pedidos, respuestas,
  telemetría, resumen y registros; `cache-recovery/` conserva el control adicional. Base del panel `e224aa5`, fork
  `9d77a39`; el dashboard se corrigió durante la validación adicional. Servidores apagados, límites restaurados,
  settings intactos y modo IA conservado activo, como estaba al empezar.
- Reproducir: `python3 tools/bench/ctx_quality.py --ai-mode --output ~/llama-panel-results/64k-calidad-2`.
  El script actual incluye la comprobación de recuperación en el mismo proceso y excluye su propio código del
  corpus para no filtrar la rúbrica. El corpus queda guardado: futuras versiones del proyecto cambian la entrada.

---

### 5.18 Dos slots para subagentes: velocidad y contexto (2026-11-15)

- Objetivo: medir `parallel = 2` para Cline, distinguiendo costo de reservar otro slot, velocidad por respuesta
  y tiempo total de dos trabajos. Configuración actual de modo IA copiada del preset, MTP 2, tandas 2048/512,
  KV Q8_0, 44 capas fijas, reloj de memoria 2000 MHz y envíos cada 16 nodos. Sin cambiar binarios ni settings.
- Router aislado en :18081, mismos presets salvo `parallel`, límite de 58 GiB sin swap. 64K totales:
  **un slot de 65536, o dos de 32768**. Dos fragmentos distintos de código (1587 y 1421 tokens de entrada),
  salida forzada de 256 tokens por trabajo, temperatura 0, semilla 1234. Sin reuso en las rondas iniciales.

| Medida | `parallel = 1` | `parallel = 2` |
|---|---:|---:|
| Un pedido activo, gen media | **18,30 tok/s** | **18,57 tok/s** |
| Dos trabajos nuevos, tiempo total | **52,32 s** | **55,09 s** |
| Generación A/B mientras ambos están activos | 18,4–18,6 / 20,1–20,3 tok/s (serie) | **10,3 / 11,3 tok/s** |
| Dos continuaciones con caché, tiempo total | **29,90 s** | **25,59 s** |
| Contexto por slot | **65536** | **32768** |
| Pico de VRAM total, prompts cortos | **7,393 GiB** | **7,613 GiB** |
| Pico de GTT del proceso | **29,447 GiB** | **29,447 GiB** |

- Reservar dos slots sin competencia **no golpeó la velocidad individual**. Con ambos activos, la velocidad por
  respuesta cae casi a la mitad. Dos trabajos nuevos tardaron **5,28 % más** en paralelo: prefill incluido, no ganó
  el conjunto. Repeticiones válidas: p1 52,324 / 52,406 / 52,235 s; p2 55,071 / 55,099 s.
- Piloto con continuación: **14,42 % menos tiempo** en paralelo (dos respuestas de 256 tokens, sólo 16 tokens
  nuevos de prompt cada una). Pero **un solo slot también reutilizó ambas conversaciones**: cache_n 1842/1676,
  igual que con dos slots. El default `cache-ram = 8192 MiB` / `cache-idle-slots` permite conservar estados en RAM;
  no hay que asumir que un único slot pierde siempre la conversación anterior al alternar tareas.
- La secuencia completa de dos turnos (dos trabajos nuevos + dos continuaciones) quedó casi empatada:
  **82,14 s en serie frente a 80,69 s en paralelo**, ahorro de **1,76 %** en ese piloto. La mejora con caché tiene
  una sola observación por variante y las continuaciones incluyen la respuesta anterior de cada variante; esos
  textos difieren entre ejecución individual y batched aun a temp 0. No es un A/B con historiales idénticos ni una
  medición de tareas completas de Cline. No atribuir todo el 14 % a reservar slots ni extrapolarlo a herramientas
  que agreguen mucho texto nuevo. Los slots se fijaron explícitamente con `id_slot`; Cline deberá asignar/reutilizar
  sus conversaciones correctamente para reproducir ese caso.
- **129 muestras con dos slots ocupados simultáneamente**, sin errores de GPU/OOM ni disparos del límite preventivo
  de GTT (30,5 GiB). La medición no llena los dos slots de 32K: el pico con dos contextos largos sigue pendiente.
  No se probó 128K totales ni KV unificada. No son dos ventanas independientes de 64K.
- Dos incidentes del lanzador no cuentan como mediciones: se corrigió un nombre local que tapaba el módulo de
  concurrencia; después, el monitor consultó `/slots` durante una carga y disparó una autocarga que compitió con
  `/models/load`. El monitor ahora consulta sólo tras la carga. Se conservaron las rondas válidas y se completó la
  repetición de dos slots en el piloto de conversación. No hubo device lost ni fallo del driver.
- **Decisión: conservar `parallel = 1` y los 64K**. Hay una mejora acotada posible en continuaciones pequeñas y
  cacheadas, no una aceleración grande de dos tareas nuevas. Antes de habilitar subagentes concurrentes en Cline,
  comparar tareas completas del cliente y aceptar/configurar el límite de 32K por slot. La sesión de Cline
  inspeccionada tenía `enable_spawn = false`; no se cambió esa configuración.
- Artefactos: `/home/gen/llama-panel-results/2026-11-15-parallel2-r1/` y
  `/home/gen/llama-panel-results/2026-11-15-parallel2-conversation/` (resumen en `summary.json`). El intento preliminar
  descartado está en `2026-11-15-parallel2/`. Settings intactos, modo IA conservado y modelo original recargado con
  `parallel = 1`; como se avisó, se perdió la caché anterior de Cline. Base del panel `ce3432f`, fork `9d77a39`.

---

### 5.19 Próximos candidatos: revisión de Strata y del fork (2026-11-15)

**Revisión estática, sin nuevas mediciones ni cambios de motor/configuración.** Fork `9d77a39`; Strata
[`baf1636`](https://github.com/Niko1221/Strata/tree/baf16363b4403ef0ed4f538f5d5ed11c43fb9535), clonado para lectura
en `/home/gen/strata-review`. Strata es MIT y no tiene backend Vulkan para Polaris. Sí se pueden adaptar ideas
CPU y de planificación; no corresponde descartar todo por depender de CUDA/HIP en su implementación actual.

**Primer experimento propuesto: calibrar la atención dispersa Q8 para Polaris.** En `ggml-vulkan.cpp:8596–8613`,
la ruta sparse compacta las posiciones finitas de la máscara y consulta sólo esas posiciones de K/V. Con KV
cuantizada exige `KV >= max(4096, 16*n_kv_max)`; el comentario justifica ese factor 16 con RDNA3/RDNA4. La RX 580
no es ninguna de ellas. `qwen4exp.cpp:995` ya transmite el presupuesto seleccionado como `n_kv_max`, y
`llama-graph.cpp:3064` lo incorpora a flash attention. Hay más condiciones de elegibilidad: no basta el umbral.
Strata también tiene atención directa sobre celdas seleccionadas, sin reunir primero una copia de K/V
([contrato del kernel](https://github.com/Niko1221/Strata/blob/baf16363b4403ef0ed4f538f5d5ed11c43fb9535/include/strata/kernels/qsa_decode_attn.hpp)).
No sería una función nueva: habría que registrar qué ruta usa realmente cada caso y comparar, con una variante
aislada, factores 16/8/4 en contextos cortos, intermedios y largos. Primero temp 0 sin MTP; luego verificación
de tres tokens y MTP real. Medir latencia completa, calidad y memoria, no sólo el shader. No hay ganancia
demostrada ni se confirmó todavía la ruta elegida en las solicitudes actuales.

**Segundo candidato acotado: Q2_0 CPU para varios tokens del mismo experto.** Strata carga/desempaqueta el
bloque de pesos fuera del bucle de tokens (`row_multi<NT>` y `row_bp_acc<NT>` en
[sus kernels AVX2](https://github.com/Niko1221/Strata/blob/baf16363b4403ef0ed4f538f5d5ed11c43fb9535/src/kernels/cpu/q2_avx2.cpp)).
Nuestro `ggml-cpu.c:1523–1553` prepara activaciones por columna, pero llama al producto punto por separado para
cada columna/fila. El camino tiled genérico no incluye Q2_0 (`tiled/tiled.cpp:615`). La adaptación debe conservar
nuestro prefetch y preparación, compartir el desempaquetado para 2–4 columnas y mantener el caso de una columna.
Contar primero cuántas columnas usa cada experto durante MTP y dos slots. **No asumir ahorro proporcional de
RAM:** los bloques de 16 filas actuales ya pueden reutilizar pesos desde caché del CPU. La ganancia potencial
es también de instrucciones/cargas; si los expertos apenas se repiten, será acotada. La variante bitplane de
Strata cambia el orden de suma flotante: no copiarla confundiendo esa diferencia con el agrupamiento de tokens.

**Cambio arquitectónico: solapar ramas independientes.** `build_layer_ffn` construye primero los expertos
ruteados y después el experto compartido, que usa la misma entrada. Estudiar si ese trabajo de GPU puede correr
mientras el CPU resuelve los ruteados. No se pueden solapar capas arbitrariamente: la siguiente necesita la
salida de la anterior. Además el scheduler reutiliza buffers entre splits; quitar esperas sin cambiar sus
vidas útiles es incorrecto (`ggml-backend.cpp:2220`). Strata usa otra stream para la rama compartida, pero sus
[fuentes](https://github.com/Niko1221/Strata/blob/baf16363b4403ef0ed4f538f5d5ed11c43fb9535/src/core/verify.cpp#L347)
documentan regresiones fuertes en algunas Radeon HIP. Es un candidato de mayor alcance, no una mejora portátil
garantizada. Perfilar la rama compartida antes de rediseñar el scheduler.

**Otros candidatos y límites:**
- Strata captura/reproduce grafos ([implementación](https://github.com/Niko1221/Strata/blob/baf16363b4403ef0ed4f538f5d5ed11c43fb9535/src/core/graph.cpp));
  Vulkan vuelve a grabar los comandos por evaluación. Reutilizar command buffers grabados exigiría manejar
  direcciones, formas, constantes e invalidación. El perfil histórico con MTP atribuía ~2.4 ms/token a grabación:
  es un techo orientativo de ese componente, no una promesa actual de rendimiento.
- Para Cline, medir por separado restaurar/guardar checkpoints y procesar tokens nuevos. Strata recicla y
  prepara buffers host (`STRATA_CKPT_POOL_READY`); su staging asíncrono en VRAM necesita margen que aquí escasea.
  Una continuación lenta no demuestra por sí sola que el culpable sea el checkpoint.
- Ya tenemos copia selectiva de expertos (`llama_context::sched_copy_experts`) y prefetch del scheduler;
  el routed streaming de Strata no se puede contar entero como una capacidad ausente. Los kernels basados en
  tensor cores/TF32 no se trasladan directamente a Polaris. Más borradores, expert cache y ngram básico ya se
  probaron: ver 5.4 y 5.9–5.12 antes de repetirlos.

---

### 5.20 Atención dispersa desde 4× en Polaris (parche 6, 2026-11-16)

**Hipótesis (5.19):** el umbral 16× de la ruta dispersa con KV cuantizada se midió en RDNA3/RDNA4; en la RX 580 la
ruta densa es más lenta y el empate debería estar más abajo. Con KV Q8_0 y ~2051 celdas seleccionadas, hasta ~32K de
contexto el modelo recorría toda la caché.

**Método:** `tools/bench/sparse_attention.py`. Biblioteca Vulkan experimental sola en `LD_LIBRARY_PATH` (servidor y
resto estables), router aparte en :18081, temp 0. El contexto se procesa una vez, se guarda el slot y cada medición
restaura **el mismo estado** (el servidor reevalúa los últimos 4 tokens); brazos alternados, 1 calentamiento + 2–3
medidas. Resultados crudos en `~/llama-panel-results/2026-11-15-tpge-sparse/`.

**Operación aislada** (`test-backend-ops perf`, µs, selección 2051, 3 consultas = paso con MTP 2):

| KV | densa | dispersa |
|---|---|---|
| 8K | 636 | — |
| 16K | 1155 | 439 |
| 32K | 2081 | 458 |
| 64K | — | 497 |

Con 1 consulta: 16K densa 445, dispersa 196. Corrección contra el CPU: 9/9 con ratios 16, 8 y 4.

**De punta a punta, sin MTP** (tok/s; actual = 16×):

| Contexto | 16× | 4× | 3× |
|---|---|---|---|
| 8K | 14.37–14.46 | 14.76 / 14.77 | 14.56 / 14.61 |
| 12K | 14.28–14.38 | 14.49 / 14.54 | 14.68 / 14.73 |
| 16K | 14.04–14.37 | 14.42 / 14.48 | 14.24 / 14.40 |
| 20K | 14.00–14.19 | 14.33 / 14.51 (8×: 14.38 / 14.57) | |
| 30K | 13.66–13.76 | 14.18 / 14.29 (8×: 14.23 / 14.32) | |
| 60K (control, misma ruta) | 13.78–13.91 | 13.96 / 13.97 | 13.67 / 13.88 |

**Con MTP 2** (192 tokens, dos brazos de cada uno): 12K 18.84–19.04 → 18.93–18.97 (igual); **20K 17.14 → 18.31
(+6.8 %)**; **30K 16.43 → 17.44 (+6.1 %)**. GTT máx 29.5 GiB, VRAM máx 7.57, sin eventos de kernel.

**Conclusiones:**
- La dispersa no pierde desde 8K. 3× y 4× no se distinguen (el control de 60K da ±0.1 tok/s entre cargas): queda **4×**.
- El texto a temp 0 cambia entre rutas (otro orden de suma), como ya pasaba al cruzar 32K; ambas salidas coherentes.
- La operación mejora 2.6–4.5× pero el total solo 3–7 %: sin MTP el contexto largo cuesta ~5.5 ms/token a 20K y la
  atención dispersa recupera ~2.1. Lo que frena sigue siendo expertos + cadena GPU↔CPU.
- Parche 6: en `AMD_GCN` con K/V Q8_0 el umbral es 4×; el resto de las GPUs no cambia. La biblioteca final dio los
  mismos hashes y velocidades que el ratio 4 experimental.
- Trampas del lanzador: en modo router los POST llevan `"model"` en el cuerpo (no en la query) y, tras restaurar un
  slot, `prompt_n` es 4, no 0.

### 5.21 Primer envío de cada tramo a los 2 nodos (parche 7, 2026-11-16)

**Hipótesis:** de los 5.7 ms/token de grabar/enviar comandos (5.8), parte ocurre con la GPU parada: al empezar cada uno
de los 49 tramos, la GPU espera a que se graben 16 nodos. Mandar un primer lote chico la hace arrancar antes.

**Método:** perilla `GGML_VK_FIRST_SUBMIT_NODES` (primer envío de cada grafo a los N nodos; después cada
`GGML_VK_MAX_NODES_PER_SUBMIT`). Mismo lanzador (`--arm-env`), 8K, temp 0, 3 medidas por brazo. **Texto idéntico
en todos los brazos** (no cambia ningún cálculo).

| Primer envío | tok/s sin MTP |
|---|---|
| 16 (actual) | 14.52–14.80 (4 brazos, media 14.67) |
| 8 | 14.85–14.89 |
| 4 | 14.85–14.94 |
| 3 | 14.96–14.99 |
| **2** | **14.97–15.19** (5 brazos, media 15.09: **+2.9 %**) |
| 1 | 14.83–15.07 |
| rampa 2→4→8→16 | 14.71–14.87 |
| rampa 4→8→16 | 14.69–14.91 |

Con MTP 2: 12K 18.88 → **19.14** (+1.4 %); 30K 17.41 → **17.54** (+0.8 %). Con MTP hay menos tramos por token y
la ganancia baja. Las rampas crecientes pierden: cada envío extra cuesta CPU.

**Queda:** `GGML_VK_FIRST_SUBMIT_NODES=2` en el unit del router (`install.py`).

**Validación del motor con los parches 6 y 7** (build estable, por el router, modo IA 64K): `smoke_models.py` OK,
Qwen3.6 29.2 tok/s / 230 prompt largo (referencia 28.8 / 227.6), Flash-Next 17.6 / 134.1 con MTP muestreado
(antes 17.4 / 131 en esa configuración).

**Estado de los candidatos de 5.19 tras revisar datos existentes:**
- *Q2_0 multi-columna:* **descartado sin implementar.** Con MTP se leen ~1.8 GB de expertos por paso = 3 × los
  ~600 MiB de un token: las columnas casi no comparten experto y esa lectura ya está en el techo de la RAM (5.9).
- *Solapar el experto compartido:* techo bajo. Sus pesos suman 143 MiB en 48 capas (parte densa: 3.8 GiB): como
  mucho ~1.5–2 ms/token, tocando el scheduler. No hay otra rama independiente: cada capa es una cadena.
- *Esperas a la GPU:* el backend ya espera girando al final de cada tramo; no hay latencia evitable ahí.
- *Pendiente con techo real:* pregrabar los comandos del tramo siguiente mientras el CPU espera a la GPU o calcula
  expertos (dependen de formas y buffers, no de datos). Techo ~3 ms/token; refactorización del backend Vulkan.
- *Revisar MTP 3 en contexto largo:* la consulta extra ahora es casi gratis en la atención; el costo de CPU no cambió.

### 5.22 MTP con los parches 6 y 7: confianza mínima y cantidad de borradores (2026-11-16)

Mismo lanzador (`--arm-preset CLAVE`, un preset por brazo), temp 0, 192 tokens, estados guardados de 12K y 30K,
motor estable con parches 6 y 7. Crudos en `~/llama-panel-results/2026-11-16-tpge-mtp/`.

- **Contadores del borrador** (`verbosity = 5`, línea `statistics draft-mtp`): 75 pasos, generar los 2 borradores
  cuesta **9.0 ms por paso** (6.7 % de ~135 ms). Aceptación por posición: **0.88 y 0.67** a 12K. La aceptación total
  baja con el contexto: 77 % a 12K, 70 % a 30K.
- **`spec-draft-p-min`** con 2 borradores (tok/s):

| p-min | 12K | 30K |
|---|---|---|
| **0 (queda)** | 18.80–18.95 | 17.33–17.45 |
| 0.3 | 18.74–18.87 | 17.33–17.37 |
| 0.5 | 18.49–18.83 | 17.65–17.82 |
| 0.7 | 17.88–18.25 | 17.52–17.86 |

  0.5 gana ~2 % a 30K y pierde ~1.3 % a 12K (un solo prompt por contexto): no es una mejora robusta.
- **`spec-draft-n-max`** (p-min 0): 2 → **18.87–19.20 / 17.44–17.62**; 3 → 18.49–18.61 / 17.31–17.36;
  4 → 18.40–18.55 / 15.03–15.17. **Sigue 2**, pero la brecha con 3 bajó de −6.4 % (5.9) a −2.5 % / −1.3 %.
- **La cuenta:** paso sin MTP ~66 ms; con 2 borradores ~133 ms; con 3, ~170 ms (3.15 tokens por paso). Cada posición
  extra cuesta ~33–37 ms (CPU ~14, GPU ~13–18, generar el borrador ~4.5), sin anomalía entre la 3.ª y la 4.ª.
  Costo por token ganado: 1.er borrador 37 ms, 2.º 49 ms, 3.º ~70 ms contra un promedio de 53. Para que 3 rinda
  hay que abaratar la posición extra o subir la aceptación.
- **Sin abrir:** la «puesta al día» del contexto del borrador (`process()`, una ejecución del bloque MTP por paso
  además de las 2 de borradores): ~8 ms por paso según 5.9 (17 de borradores + muestreo − 9 de generación).

### 5.23 Borrador MTP con la salida recortada a los tokens más frecuentes (parche 8, 2026-11-16)

**Hipótesis:** en 5.12 recortar la salida del borrador a los primeros K ids ganaba +3 % a temp 0 pero perdía
aceptación con muestreo, porque en castellano mucha probabilidad cae en ids altos. Si en vez de los primeros K ids se
toman los K **más frecuentes en el uso real**, la aceptación no debería bajar. (Después encontramos que la idea está
publicada: FR-Spec, ACL 2025, arXiv 2502.14856.)

**Orden por frecuencia** (`tools/mtp_vocab_order.py`): 13 M de tokens de repos y conversaciones locales,
documentación del sistema y oraciones de Leipzig (noticias y Wikipedia en castellano e inglés), con peso por corpus.
Cobertura sobre respuestas guardadas del modelo (no usadas para ordenar):

| K | más frecuentes | primeros K ids |
|---|---|---|
| 32768 | 97.7 % | — |
| 49152 | 98.9 % | — |
| 65536 | **99.67 %** | 92.9 % |

**Método:** `tools/bench/sparse_attention.py --mode decode --mtp`, estados guardados de 12K y 30K, 128 tokens,
brazos alternados. Crudos en `~/llama-panel-results/2026-11-16-tpge-mtp/` (`hot-temp0-c`, `hot-temp08`, `hot-trunc`,
`d2t-temp0`). Primero un prototipo (matriz reordenada + variable de entorno), después la versión definitiva.

- **Temp 0, prototipo, 192 tokens** (tok/s; generados/aceptados):

| K | 12K | 30K |
|---|---|---|
| 248320 (todo) | 19.15–19.28 (150/116) | 17.58–17.70 (158/111) |
| **65536** | **19.56–19.57** (150/116) | **17.92–17.94** (158/111) |
| 49152 | 19.23–19.35 (152/115) | 17.96 (158/111) |
| 32768 | 19.23–19.24 (154/114) | 17.97–18.05 (158/111) |

  Con 65536 no se pierde ni un borrador aceptado; achicar más no suma y empieza a perder alguno.
- **Temp 0.8, 5 semillas por brazo** (la prueba que había hundido a 5.12): a 12K el texto y la aceptación son
  idénticos al control en las 5 semillas, medias 17.60 / 17.81 → 17.98 / 17.99. A 30K, 3 semillas con texto idéntico
  ganan ~1.6 %; en las otras 2 el texto cambió y aceptó más (77 contra 73, 72 contra 71), medias 17.63 / 17.81 →
  18.37 / 18.45. **En ninguna semilla bajó la aceptación.**
- **Sin la cola en -inf** (logits de ancho K, sin rellenar hasta 248320): 12K 19.13 → 19.21, 30K igual. El relleno y
  la selección de candidatos sobre 248K no cuestan nada medible; no vale la pena una ruta especial.
- **Versión definitiva (parche 8):** el motor ya tenía la convención `d2t` para los borradores eagle3 y dflash
  (tensor con los ids de token de cada fila de la salida; los logits se reparten con `set_rows` sobre el vocabulario
  completo). El parche la habilita en el borrador MTP de qwen4exp. `tools/gguf_mtp_d2t.py` genera el GGUF: deja
  `output.weight` con las K filas elegidas y agrega `d2t`. Temp 0, original contra `-d2t65k`, mismo texto y misma
  aceptación (101/76 y 105/74):

| Borrador | 12K | 30K | VRAM máx |
|---|---|---|---|
| original | 18.76–18.88 | 17.52–17.61 | 7.34 GiB |
| **`-d2t65k`** | **19.18–19.25** | **17.84–18.11** | **7.10 GiB** |

  **+2.1 % a 12K, +2.2 % a 30K, −0.24 GiB de VRAM**, borrador 263 MB más chico.
- **Validación con el motor estable:** test de humo OK (Qwen3.6 29.1 / 230.6; Flash-Next 19.0 / 133.9, VRAM 7.16 GiB).
  Calidad a 64K con 58K ocupados: **24/24**, generación 18.9–20.7 tok/s (`~/llama-panel-results/2026-11-16-tpge-quality-d2t`).
- **Por qué no rinde más:** el borrador entero es ~9 ms de un paso de ~133 ms (5.22); recortar su salida saca ~2 ms
  por borrador. Lo que pesa es verificar (33–37 ms por posición).

**Lectura de afuera que queda como pista** (búsqueda del 2026-11-16, sin probar todavía):
- *Largo de borrador por umbral* (SpecDec++, arXiv 2405.19715): la política óptima es cortar cuando la probabilidad
  acumulada de rechazo pasa un umbral. Acá una posición extra rinde si su aceptación supera ~0.76. Primer paso:
  registrar (posición, probabilidad del borrador, aceptado) y calcular el umbral con datos.
- *Saltear expertos de peso bajo* (Lu et al., arXiv 2402.14800; MoDES; HOBBIT; en contra, BEAM arXiv 2605.14438): ataca
  los 17–24 ms de CPU por token. Es aproximado: primero el histograma de pesos del enrutador, después calidad.
- *Verificación dispersa* (arXiv 2512.21911): abarata atención y expertos solo en las posiciones verificadas.
- Descartadas por la cuenta: árboles de candidatos (cada posición cuesta 33–37 ms y la segunda alternativa acertaría
  ~6 %); precargar expertos predichos (20–30 MB por capa contra 16 MiB de L3).

### 5.24 Tres pistas medidas: umbral de confianza, expertos de peso bajo y el costo real del paso MTP (2026-11-16)

Motor experimental con tres instrumentos de diagnóstico (no van al fork): `LLAMA_MTP_CONF_LOG` (probabilidad de cada
borrador y cuántos se aceptaron), `LLAMA_MOE_W_LOG` (pesos del enrutador) y `TPGE_TIMINGS` (tiempos por etapa del
servidor). Crudos en `~/llama-panel-results/2026-11-16-tpge-mtp/` (`conf.log`, `pmin-temp-n2`, `pmin-temp-n4`,
`moe-w.log`, `timings`).

- **Umbral de confianza (SpecDec++): no rinde.** La probabilidad del borrador está bien calibrada (1163 pasos, temp
  0.8: p ≥ 0.99 → 95–99 % aceptado; p < 0.5 → 40–50 %; aceptación condicional por posición 0.775 / 0.751 / 0.721 /
  0.686). Pero medido con 5 semillas: 2 borradores sin umbral **18.31 / 18.17** (12K / 30K); con `p-min` 0.9,
  16.9 / 16.85; 4 borradores con 0.8–0.95, 17.3–17.7. Un borrador rinde aunque se acepte la mitad de las veces.
- **Saltear expertos de peso bajo: descartado.** Pesos normalizados medios por rango (11613 muestras, 49 capas):
  0.206 0.148 0.120 0.102 0.089 0.079 0.072 0.066 0.061 0.057. El último lleva 5.7 % y los tres últimos 18 %:
  no hay expertos despreciables. Con umbral 0.1 × el mayor se saltea 0.09 de 10.
- **Tiempos por etapa del paso MTP** (temp 0, 12K, ms por paso):

| Borradores | tok/s | generar borradores | modelo (lote) | puesta al día | resto | total |
|---|---|---|---|---|---|---|
| 1 | 18.96 | 4.4 | 91.9 (2) | 4.3 | 1.3 | 102.0 |
| 2 | 18.83 | 7.2 | 120.9 (3) | 4.8 | 1.6 | 134.4 |
| 3 | 18.58 | 10.0 | 155.5 (4) | 5.8 | 1.9 | 173.2 |

  El modelo es el 90 % del paso y **cada posición extra cuesta más que la anterior: +26, +29, +35 ms** sobre los
  ~66 de un token solo. Si la tercera costara 26, 3 borradores ganarían. Cada borrador son 2.8 ms; la puesta al día
  (`process()`: vuelve a pasar el lote verificado por el bloque MTP para llenar su caché) 4.3–5.8 ms por paso.
  A temp 0, 1 borrador rinde igual que 2.
- **Perfil de GPU por operación según el lote** (`perf-ops`, medidor de Vulkan activo, ms por paso): total
  63.5 / 81.9 / 107.1 con lotes de 2 / 3 / 4. El crecimiento está repartido entre todas las multiplicaciones de
  matrices (q8_0 10.7 → 14.0 → 18.8; iq4_xs 3.8 → 7.1 → 9.5; atención 4.2 → 6.1 → 7.6): es trabajo real por columna,
  no una anomalía. La única rareza es el salto de 3 a 4 columnas en **q3_K** (10240×2560: 177 → 225 → **382 µs**;
  8.5 → 10.7 → 17.9 ms por paso) y q5_K (3.6 → 4.2 → 6.5), que explica por qué la cuarta posición cuesta 35 ms.
  Arreglarlo solo acercaría 3 borradores a 2, sin superarlos.
- **Borrador en Q8_0 (para usar la VRAM libre): descartado.** `mtp-…-Q8_0.gguf` del repo de SC117 (sha256 verificado),
  con la salida recortada igual (`gguf_mtp_d2t.py` ahora acepta Q8_0). Temp 0.8, 5 semillas, alternado con el Q4_0:
  aceptación 0.700 → 0.729 a 12K y 0.727 → 0.724 a 30K (sin mejora real), y **18.4 → 14.6–15.4 / 18.2 → 13.8–14.0
  tok/s**: el bloque del borrador tiene 512 expertos que quedan en RAM, y en Q8_0 el CPU lee el doble. VRAM 7.10 →
  7.53 GiB. La cuantización del borrador no limita la aceptación.
- **Fusionar la puesta al día con el primer borrador: no rinde.** Prototipo (`LLAMA_MTP_DEFER`): la puesta al día
  se posterga y entra, solo con los tokens aceptados, en el mismo lote del primer borrador siguiente. Texto y
  aceptación idénticos a temp 0, pero 19.21 → 19.33 a 12K y 17.59 → 17.65 a 30K (+0.3–0.6 %, dentro del ruido).
  Para dejarlo firme habría que cuidar el estado guardado del slot; no lo vale. Diff con este prototipo y los
  instrumentos en `~/llama-panel-results/2026-11-16-tpge-mtp/worktree-instrumentos-y-defer.diff`.
- **Queda abierto:** el shader q3_K / q5_K con 4 columnas si alguna vez conviene MTP 3.

---

## 6. Los ocho parches (fork `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers`)

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

### Parche 6 — atención dispersa desde 4× en GCN con K/V Q8_0
- Hallazgo y método en 5.20. Cambia solo el umbral de la ruta dispersa en `AMD_GCN` con K/V Q8_0 (16× → 4×).
- Resultado: con MTP **+6.8 % a 20K y +6.1 % a 30K**; sin MTP +1.5–4 % desde 8K. Sobre 32K no cambia nada.

### Parche 7 — `GGML_VK_FIRST_SUBMIT_NODES`
- Hallazgo y método en 5.21. Perilla apagada por defecto; el panel la pone en 2 en el unit del router.
- Resultado: sin MTP **+2.9 %**, con MTP +0.8–1.4 %, salida idéntica.

### Parche 8 — vocabulario de salida reducido (`d2t`) para el borrador MTP
- Hallazgo y método en 5.23. Si el GGUF del borrador trae `d2t`, `output.weight` tiene solo esas filas y los logits
  se reparten sobre el vocabulario completo (convención de eagle3 / dflash). Sin `d2t` no cambia nada.
- Resultado: **+2 %** con los 65536 tokens más frecuentes, misma aceptación, −0.24 GiB de VRAM.

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
| Flash-Next `-hcq8` | 18.2 gen, 135 prompt largo, VRAM 7.2 (con 64K Q8_0: 17.4 / 131, VRAM 7.5) | parche 1 (44 capas fijas), `lazy-mode`, MTP 2 probabilístico, `parallel = 1`, tandas 2048, memoria de la GPU fija, envíos cada 16, primer envío a los 2 nodos, borrador MTP en tandas de 512 y con la salida recortada a 65536 tokens (`-d2t65k`); modo IA: **64K de contexto con KV en Q8_0** (~3 % más lento) |

---

## 10. Próximos pasos posibles (con lo que se espera ganar)

1. ~~Más fusiones de operaciones chicas~~: medido en 5.7, techo +2.4 %. Descartado.
2. ~~Expertos en el CPU~~: hecho (5.8, parche 5). Ahora están en el techo de la RAM (~40 GB/s).
3. ~~La GPU, operación por operación~~: medido en 5.9. Las mejoras de shaders sueltos no llegan al total; la
   memoria fija sí (+2–3 %). Queda entender por qué el tramo de GPU no baja cuando baja una operación.
4. Grabar comandos de Vulkan (5.7 ms/token, en el CPU) y lo de fuera del scheduler (3.9 ms). Primer paso hecho
   (5.21, parche 7); queda pregrabar el tramo siguiente durante las esperas (techo ~3 ms/token).
5. Solapar CPU y GPU (hoy se turnan): lo único independiente es el experto compartido, techo ~1.5–2 ms/token (5.21).
6. ~~Atención con contexto largo~~: hecho (5.20, parche 6). Con MTP a 30K: 16.4 → 17.4.
7. ~~Revisar MTP 3 contra MTP 2 en contexto largo~~: medido en 5.22, sigue 2 (la brecha bajó a −1.3 / −2.5 %).
8. ~0.4 GiB libres sin modo IA desde 5.15 (borrador en tandas de 512), sin uso todavía.
9. Subir el GTT por GRUB para fijar las 48 capas: ganancia estimada chica (solo prompt).
10. ~~Recortar la salida del borrador MTP~~: hecho (5.23, parche 8), +2 %.
11. Largo de borrador por umbral de confianza, saltear expertos de peso bajo y verificación dispersa: pistas de 5.23.
12. Fusionar la «puesta al día» del borrador (`process()`) con el primer borrador: una ejecución menos por paso (~1.5 %).

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
