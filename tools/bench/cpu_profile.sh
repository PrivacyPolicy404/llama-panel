#!/usr/bin/env bash
# Perfil del CPU durante la generacion: en que funciones se va el tiempo (calculo de expertos, esperas entre hilos,
# espera a la GPU, copias...). Requiere: sudo sysctl kernel.perf_event_paranoid=1 (vuelve a 4 al reiniciar).
#
# Uso: tools/bench/cpu_profile.sh MODELO SALIDA_DIR [args extra de llama-server...]
#   SECS=15 (segundos de muestreo)  N_PREDICT=600  LD_LIBRARY_PATH=... (otra libggml-vulkan)
set -euo pipefail
MODEL=$1; OUT=$2; shift 2
SECS=${SECS:-15}; N_PREDICT=${N_PREDICT:-600}; PORT=18085
mkdir -p "$OUT"
GGML_VK_VISIBLE_DEVICES=0 "$HOME/llama.cpp/build/bin/llama-server" -m "$MODEL" -c 8192 -np 1 -t 8 \
    --host 127.0.0.1 --port $PORT --no-webui "$@" > "$OUT/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
until curl -sf "localhost:$PORT/health" >/dev/null; do
    kill -0 $SRV 2>/dev/null || { echo "el server termino, ver $OUT/server.log"; exit 1; }
    sleep 1
done
curl -s "localhost:$PORT/completion" -H 'Content-Type: application/json' \
    -d "{\"prompt\":\"El faro de Alejandria\",\"n_predict\":$N_PREDICT,\"temperature\":0,\"ignore_eos\":true}" > "$OUT/resp.json" &
REQ=$!
sleep 5   # que arranque la generacion
perf record -F 997 -g -p $SRV -o "$OUT/perf.data" -- sleep "$SECS" 2> "$OUT/perf-record.log"
wait $REQ || true
python3 -c "import json; t=json.load(open('$OUT/resp.json'))['timings']; print(f\"generacion: {t['predicted_per_second']:.1f} tok/s\")"
perf report -i "$OUT/perf.data" --stdio --no-children --sort dso,symbol --percent-limit 0.7 2>/dev/null \
    | grep -v '^$' | grep -E '^ +[0-9]' | head -40 | tee "$OUT/top.txt"
echo "--- por hilo"
perf report -i "$OUT/perf.data" --stdio --no-children --sort comm --percent-limit 1 2>/dev/null \
    | grep -E '^ +[0-9]' | head -12 | tee "$OUT/threads.txt"
