# Parches locales de llama.cpp

El llama.cpp de esta máquina sale del fork **github.com/PrivacyPolicy404/llama.cpp**, rama `ot-host-buffers`:
el `master` oficial más el parche de abajo. En `~/llama.cpp`:

- `origin` = el fork (ahí se sube la rama)
- `upstream` = ggml-org/llama.cpp, solo lectura (el push está deshabilitado)

Para actualizar sin perder el parche:

```bash
~/llama-panel/scripts/update-llama.sh --check   # cuántos commits nuevos hay
~/llama-panel/scripts/update-llama.sh           # rebase + compilar + verificar + subir al fork + reiniciar el router
```

Si el parche deja de aplicar limpio, el script no cambia nada y avisa.

La rama tiene dos commits sobre el oficial: el parche y una nota en el README del fork que explica qué cambia y por qué.

## llama.cpp-ot-host-buffers.patch

Copia del commit del parche (sin la nota del README), por si hiciera falta reaplicarlo a mano (`git apply`).

Permite usar los buffers de memoria fija (`Vulkan_Host`, `CUDA_Host`, ...) en `-ot` / `--override-tensor`,
para fijar solo una parte de los tensores que viven en RAM. Sirve cuando la memoria fija tiene un límite
(en amdgpu, el GTT es por defecto la mitad de la RAM: 31 GiB en esta máquina) y los expertos de un MoE no entran enteros.

Lo usa la configuración de Qwen3.8-Flash-Next en el panel. **Sin el parche ese modelo no carga** (`unknown buffer type`).

Uso (ejemplo): `-lm none --lazy-mode on --no-host -ot "blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU"`.

## Si se manda upstream

Las reglas de llama.cpp (`CONTRIBUTING.md`, `AGENTS.md`) piden abrir primero un issue, declarar el uso de IA,
y que la descripción del PR y los mensajes de commit los escriba una persona. El commit de la rama del fork
tiene un mensaje escrito con IA: para un PR hay que rehacerlo con un mensaje propio, y llevar solo el commit
del parche (la nota del README es propia del fork).
