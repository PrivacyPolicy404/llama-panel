# Parches locales de llama.cpp

El llama.cpp de esta máquina sale del fork **github.com/PrivacyPolicy404/llama.cpp**, rama `ot-host-buffers`:
el `master` oficial más los 4 parches de abajo y notas en el README del fork. En `~/llama.cpp`:

- `origin` = el fork (ahí se sube la rama)
- `upstream` = ggml-org/llama.cpp, solo lectura (el push está deshabilitado)

Para actualizar sin perder los parches:

```bash
~/llama-panel/scripts/update-llama.sh --check   # cuántos commits nuevos hay
~/llama-panel/scripts/update-llama.sh           # rebase + compilar + verificar + subir al fork + reiniciar el router
```

Si algún parche deja de aplicar limpio, el script no cambia nada y avisa.
Los archivos `.patch` son copias de los commits (sin las notas del README), por si hiciera falta reaplicarlos a mano con `git am`.

## 1. `llama.cpp-ot-host-buffers.patch`

`-ot` / `--override-tensor` acepta los buffers de memoria fija (`Vulkan_Host`, `CUDA_Host`, ...), para fijar solo una
parte de los tensores que viven en RAM. En amdgpu la memoria fija (GTT) está limitada a la mitad de la RAM (31 GiB acá)
y los expertos de Flash-Next ocupan 31.6 GiB: fijarlos todos tira el driver.

Uso: `-lm none --lazy-mode on --no-host -ot "blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU"`
(44 de 48 capas fijas). Prompt largo 104 -> 125 tok/s. **Sin este parche la configuración de Flash-Next no carga.**

## 2. `llama.cpp-vulkan-lightning-indexer-fp32.patch`

La RX 580 no tiene `shaderFloat16` y el lightning indexer de Vulkan lo exigía, así que corría en el CPU: dos turnos
GPU↔CPU extra en cada una de las 12 capas de atención dispersa de Flash-Next. El parche agrega una variante del shader
que lee K y la máscara en 16 bits pero calcula en 32. `test-backend-ops`: 210/210 OK.
Tramos por token 122 -> 98; generación +4–5 % (temperatura 0, sin MTP). En la GPU tarda 0.27 ms por token.

## 3. `llama.cpp-quantize-copy-tensor-type.patch`

`llama-quantize ENTRADA SALIDA COPY --tensor-type 'patron=tipo'` recuantiza solo los tensores nombrados y copia el
resto tal cual (antes `COPY` ignoraba `--tensor-type`). Se usó para generar Flash-Next con las hyper-connections en Q8_0:

```bash
llama-quantize --keep-split --tensor-type 'hc_(attn|ffn)_(up|down)=q8_0' \
    ORIGINAL-00001-of-00002.gguf SALIDA-hcq8.gguf COPY 8
```

## 4. `llama.cpp-vulkan-multi-add-amd.patch`

La fusión `multi_add` de Vulkan (cadenas de sumas f32 del mismo tamaño en un solo shader) estaba atada a
`shaderRoundingModeRTEFloat16`, que la RX 580 no tiene. Ese chequeo deja afuera los drivers donde falla (Intel ANV,
ver PR #15252), pero también a Polaris. El shader es solo f32: el parche la habilita en los drivers de AMD.
Sirve para las 9 sumas que combinan los 10 expertos de cada capa: 2980 -> 2591 operaciones por token.
`test-backend-ops -o ADD`: 116/116 OK. Flash-Next +2.5 %, Qwen3.6 +3 % (por el router).
Para apagarla: `GGML_VK_DISABLE_MULTI_ADD=1`.

## Si se propone upstream

Las reglas de llama.cpp (`CONTRIBUTING.md`, `AGENTS.md`) piden abrir primero un issue, declarar el uso de IA,
y que la descripción del PR y los mensajes de commit los escriba una persona. Los commits del fork tienen mensajes
escritos con IA: para un PR hay que rehacerlos con mensajes propios, uno por PR, sin las notas del README del fork.
