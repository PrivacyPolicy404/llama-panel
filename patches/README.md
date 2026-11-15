# Parches locales de llama.cpp

## llama.cpp-ot-host-buffers.patch

Permite usar los buffers de memoria fija (`Vulkan_Host`, `CUDA_Host`, ...) en `-ot` / `--override-tensor`,
para fijar solo una parte de los tensores que viven en RAM. Sirve cuando la memoria fija tiene un límite
(en amdgpu, el GTT es por defecto la mitad de la RAM: 31 GiB en esta máquina) y los expertos de un MoE no entran enteros.

- Base: llama.cpp `9183a55` (rama local `ot-host-buffers` en `~/llama.cpp`).
- Lo usa la configuración de Qwen3.8-Flash-Next en el panel. **Sin el parche ese modelo no carga** (`unknown buffer type`).

Al actualizar llama.cpp:

```bash
cd ~/llama.cpp
git stash            # o commitear la rama
git checkout master && git pull
git checkout -b ot-host-buffers-new
git apply ~/llama-panel/patches/llama.cpp-ot-host-buffers.patch
cmake --build build --target llama-server llama-bench -j 14
```

Uso (ejemplo): `-lm none --lazy-mode on --no-host -ot "blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU"`.
