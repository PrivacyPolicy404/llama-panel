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
8. Trampas del entorno de trabajo: el scratchpad vive en `/tmp` y **se borra al reiniciar**; `pkill -f` con un patrón
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

## 5. Flash-Next: de 12.8 a 15.7 tok/s

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

---

## 6. Los cuatro parches (fork `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers`)

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

---

## 9. Configuración vigente (resumen; detalle en `notas.md`)

| Modelo | Router | Claves |
|---|---|---|
| Qwen3.6-35B-A3B | 28.7 gen, 228 prompt largo, VRAM 7.1 | `cpu-moe`, `load-mode = none`, cache 4608 + cpu 0.8 |
| Flash-Next `-hcq8` | 15.7 gen, 133 prompt largo, VRAM 7.3 | parche 1 (44 capas fijas), `lazy-mode`, MTP 2, `parallel = 1`, tandas 2048; modo IA: 32K de contexto |

---

## 10. Próximos pasos posibles (con lo que se espera ganar)

1. **Más fusiones de operaciones chicas** (quedan por token en la GPU: SCALE 448, MUL 372, RMS_NORM 256, SIGMOID 230,
   CPY 150, GET_ROWS 148, SILU 134). Candidato claro: escalas por constante (1/4) justo después de una matmul
   (`hc_inject`, etc.), que se podrían hornear en los pesos y sacar del grafo. Medir primero cuánto suman en ms.
2. Solapar CPU y GPU (hoy se turnan): cambio grande en el scheduler; techo teórico alto pero costoso.
3. Atención con contexto largo (lo que más crece con los 32K del modo IA).
4. Subir el GTT por GRUB para fijar las 48 capas: ganancia estimada chica (solo prompt).

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
