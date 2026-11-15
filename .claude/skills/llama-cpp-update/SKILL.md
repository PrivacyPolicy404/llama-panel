---
name: llama-cpp-update
description: Revisar las novedades de llama.cpp y, si conviene, actualizar el llama.cpp de esta máquina (fork PrivacyPolicy404/llama.cpp, rama ot-host-buffers con parches locales) sin romper el panel ni los modelos MoE. Usar cuando el usuario pregunte si hay algo nuevo en llama.cpp, si conviene actualizar, pida actualizarlo, o haya que verificar que los modelos siguen rindiendo después de un cambio.
---

# Revisar y actualizar llama.cpp

Antes de empezar, leer `docs/notas.md` (hardware, mediciones, por qué cada configuración) y `patches/README.md` (el fork y los parches).

**Regla:** la versión de llama.cpp está fijada a propósito. Revisar e informar está bien; **actualizar solo si el usuario lo pide o lo aprueba** después de ver el resumen.

## 1. Ver cuántos commits nuevos hay

```bash
~/llama-panel/scripts/update-llama.sh --check
git -C ~/llama.cpp log --format='%h %ad %s' --date=short HEAD..upstream/master
```

## 2. Clasificar cuáles importan en ESTA máquina

Hardware: AMD RX 580 8 GB (Polaris / GCN4, solo Vulkan con RADV, PCIe 3.0), i7-10700 (AVX2, 8 núcleos), 64 GB DDR4.
Los expertos de los MoE se calculan en el CPU; la parte densa en la GPU.

Pueden importar (leer el diff y el PR antes de opinar: `git -C ~/llama.cpp show <hash>`, `gh pr view <N> --repo ggml-org/llama.cpp`):
- **Vulkan** genérico. Ojo con las condiciones: lo que dice coopmat / coopmat2 / cooperative matrix, RDNA3, RDNA4, Intel o UMA **no aplica** a Polaris.
- **CPU backend**: matmul de Q2_0, Q4_K, IQ*, multihilo, AVX2 (ahí se calculan los expertos en RAM).
- **MoE / expert cache** (`llama-moe-cache`), `--cpu-moe`, `-ot` / `--override-tensor`, el scheduler de `ggml-backend`.
- **Carga de modelos**: `llama-model-loader`, `--load-mode`, `--lazy-mode`, mmap, shards, host buffers (puede chocar con el parche).
- **Server / router**: `--models-preset`, `/models`, carga y descarga de modelos, `parallel`, presets.
- **Speculative / MTP** (`draft-mtp`, `-md`): Flash-Next depende de eso.
- **Arquitecturas** en uso: `qwen35moe` (Qwen3.6), `qwen4exp` (Qwen3.8-Flash-Next), `qwen3` (los densos 8–9B).

No importan: CUDA, Metal, SYCL, OpenCL, Hexagon, CANN, s390x, Windows, macOS, `convert_*.py`, mtmd (visión/audio), modelos que no se usan.

Revisar también las **condiciones** de cada arreglo (ejemplo real: #31775 arreglaba el expert cache, pero solo con `-nkvo`, que no se usa).

## 3. Informar al usuario

Tabla corta: commit, qué hace, si le sirve y por qué. Terminar con una recomendación (actualizar ahora / esperar) y la pregunta. Si nada importa, decirlo sin vueltas.

## 4. Actualizar (solo con su ok)

1. Confirmar que no esté usando los modelos (el script reinicia el router si está encendido).
2. `~/llama-panel/scripts/update-llama.sh`
   - Rebase de la rama `ot-host-buffers` (3 parches + 2 commits de notas del README del fork) sobre `upstream/master`, compila, verifica, sube al fork.
   - Si algún parche no aplica limpio, el script no cambia nada. Adaptarlo a mano (detalle en `patches/README.md`):
     1. `-ot` con host buffers: `common/arg.cpp` y `tools/llama-bench/llama-bench.cpp` (agregar `ggml_backend_dev_host_buffer_type` a la lista), `src/llama-model-loader.cpp` (aviso cuando un override explícito a host buffer se ignora por mmap).
     2. Lightning indexer fp32: `vulkan-shaders/lightning_indexer.comp` (`LI_VEC4` según `FLOAT16`), `vulkan-shaders-gen.cpp` (variante `_fp32`), `ggml-vulkan.cpp` (elegir la variante si `!device->fp16` y no exigir fp16 en `supports_op`).
     3. `llama-quantize COPY --tensor-type`: `src/llama-quant.cpp`, donde se arma `metadata[i].target_type`.
   - Si choca una nota del README del fork, rehacerla arriba de todo del README nuevo.
3. Volver a exportar los parches al repo del panel y commitear (buscar cada hash con `git log --oneline`):
   `git -C ~/llama.cpp format-patch -1 --stdout <hash> > ~/llama-panel/patches/<nombre>.patch`
4. Si el oficial ya incluye alguno de los parches (por ejemplo, si soportan fp16 opcional en el indexer), sacarlo de la rama.

## 5. Verificar

```bash
cd ~/llama-panel && python3 -m unittest            # tests del panel
python3 tools/bench/smoke_models.py                # carga cada MoE por el panel y compara con tools/bench/baselines.json
```

- `smoke_models.py` apaga el generador de imágenes (pregunta antes) y avisa si algo rinde menos del 85 % de la referencia.
- Qwen3.6 vive en el disco mecánico: si no está en la caché de RAM tarda ~2 min en cargar. No es una regresión.
- Si hay regresión: el script de actualización imprime el comando para volver a la versión anterior. Antes de volver, mirar los logs del router (`journalctl --user -u llama-router -n 200`).
- Si una mejora cambia los números para bien, actualizar las referencias con `--save-baseline` y anotarlo en `docs/notas.md`.

## Convenciones

- Commits: el usuario como único autor, **sin** `Co-Authored-By`; terminar el mensaje con `Written with AI assistance (Claude).`. El asunto describe el cambio (no una orden).
- Nunca hacer push al repo oficial (`upstream` tiene el push deshabilitado). Issues/PRs a llama.cpp los escribe el usuario (reglas de `AGENTS.md` de llama.cpp).
- Responder en castellano rioplatense.
