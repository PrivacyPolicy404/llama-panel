# Archivo de modelos

Guardar en un HDD aparte los modelos abiertos que usamos (y los que vamos a poder usar con más hardware), con el
código y los papers para rehacerlos, antes de que sean difíciles de conseguir. La lista está en `manifiesto.txt` y se
baja **por etapas**: cada una se puede lanzar sola, cortar y retomar.

| Etapa | Qué | Aprox. | Internet |
|---|---|---|---|
| 1 | Lo que ya corre: Flash-Next Q2_0 (hcq8 y el de SC117 con sus MTP), Qwen3.6-35B-A3B Q4 | ~130 GB | no |
| 2 | Conocimiento: llama.cpp (fork y oficial), este repo, Heretic, papers de abliteración | ~1 GB | sí (poco) |
| 3 | Flash-Next como fuente: BF16 abliterado de SC117, para cuantizar Q3/Q4 de 512 expertos | ~285 GB | sí |
| 4 | Qwen3.8-27B (abliterado y original, Q4_K_M + Q6_K), Qwen3.6 Q6_K, Flash-Next Coder de ISTA | ~160 GB | sí |
| 5 | DeepSeek V4 Flash: Q2_K, UD-IQ3_XXS, Q3_K (para 96–128 GB de RAM) | ~315 GB | sí |
| 6 | GLM-4.5-Air Q4 y Q5, gpt-oss-120b, Gemma 4 26B-A4B | ~230 GB | sí |
| 7 | Opcional: Flash-Next original en BF16 | ~285 GB | sí |

Total sin la 7: ~1.1 TB. Con la 7: ~1.4 TB (entra en 2 TB).

## Antes de bajar: confirmar los repos

Varias entradas están marcadas `verificar`: el nombre salió de blogs y puede no existir o ser otro. `bajar` las
saltea. Con la PC conectada:

```bash
python3 tools/archivo/archivar.py tamanos 3 4 5 6 7 --detalle   # cuáles existen, cuánto pesan, qué archivos baja
python3 tools/archivo/archivar.py buscar "SC117"                # candidatos, por descargas
```

Corregir el nombre en `manifiesto.txt` si hace falta y cambiar `verificar` por `ok`. Mirar también la lista de
archivos: si un patrón trae de más (otra cuantización con un nombre parecido), agregar un `!patrón`.

## Bajar

El disco tiene que estar montado: el script no escribe en el disco del sistema (si el HDD no montó, la carpeta
queda en `/`). En la PC, por SSH y dentro de `tmux` para que siga si se corta la sesión:

```bash
export ARCHIVO_DIR=/mnt/archivo
python3 tools/archivo/archivar.py bajar 1
python3 tools/archivo/archivar.py bajar 2
python3 tools/archivo/archivar.py bajar 3
```

- Cortar con Ctrl+C cuando sea: el mismo comando sigue desde donde quedó (`.part`).
- Con la conexión compartida de la laptop: que no se suspenda mientras baja. Si se corta, reintenta solo hasta
  8 veces con esperas crecientes; si no vuelve, se corre de nuevo.
- `--solo TEXTO` baja una sola entrada de la etapa (ej. `bajar 4 --solo 27b-heretic`).
- Repos con acceso restringido: aceptar las condiciones en la web y `export HF_TOKEN=...`.
- `~/llama.cpp` tiene que ser un clon completo para el bundle (`git -C ~/llama.cpp fetch --unshallow` si no).

## Dentro de unos años

Cada carpeta tiene `ARCHIVO.md` (de dónde salió, revisión exacta, fecha) y `SHA256SUMS`. Para revisar el disco no
hace falta este script:

```bash
cd /mnt/archivo/modelos/qwen3.8-flash-next-hcq8 && sha256sum -c SHA256SUMS
```

o todo junto con `archivar.py verificar`. Conviene hacerlo una vez por año: los discos guardados en un cajón
también pierden datos. Los repositorios git se recuperan con `git clone archivo.bundle carpeta`. En la raíz quedan
`INDICE.md` y `_herramientas/` (este script y el manifiesto con que se armó).
