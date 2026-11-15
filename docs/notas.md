# Notas: hardware, modelos y lo que aprendimos

Contexto acumulado para no tener que redescubrirlo. Primera versión: 2026-11-14.

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
| Qwen3.8-Flash-Next GSQ-RCO abliterated Q2_0 (SC117, 66.8 GB en 2 shards) | `~/models-nvme/qwen3.8-flash-next` | 15.0 tok/s | 105 tok/s | 7.2 GB |

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
ubatch-size = 1024           # 2048 da algo más de prompt pero deja la VRAM en 7.8/8
batch-size = 1024
```

Camino de mejoras (prompt largo / generación): por defecto 62 / 12.8 → tandas grandes 104 / 12.7 →
44 capas fijas (parche) 120 / 12.4 → + MTP 120 / 16.7 → final con margen de VRAM 105 / 15.

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
- Strata (motor de terceros) promete más con caching por experto, pero no se verificó si soporta Vulkan/AMD.

## llama.cpp: fork y parche

- Fuente: fork público `PrivacyPolicy404/llama.cpp`, rama `ot-host-buffers` = oficial + parche + nota en el README.
- Clon superficial en `~/llama.cpp`; `origin` = fork, `upstream` = oficial (push deshabilitado).
- Parche: `-ot` acepta `Vulkan_Host` / `CUDA_Host` para fijar solo parte de los tensores en RAM. Ver `patches/README.md`.
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

Antes de medir: apagar texto e imágenes en el panel. Para probar `--load-mode none` con modelos grandes, envolver
el server en un límite de memoria (`WRAP="systemd-run --user --scope -p MemoryMax=56G ..."`).

## Pendientes / ideas

- Mover Qwen3.6 al NVMe para que cargue en segundos y no en ~2 minutos.
- Sacar `n-gpu-layers = 99` de los ajustes globales.
- Campos propios para MoE en el panel (expertos en RAM, cache, reparto con el CPU) y un botón de tuning.
- Botón "Actualizar desde GitHub" en el panel; descarga automática por inactividad.
- Subir el límite de GTT (permitiría fijar las 48 capas de Flash-Next; ganancia estimada: algo más de prompt).
