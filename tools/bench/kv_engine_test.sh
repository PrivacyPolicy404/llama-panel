#!/bin/bash
# Cache KV mixta para Flash-Next (idea de Basalt, 2026-11-18):
#   Cache KV mixta: claves en Q8_0 y valores en Q4_0 (Basalt usa «k8v4»). KLD contra F16, VRAM y velocidad.
#
# Uso: tools/bench/kv_engine_test.sh
# Requiere la GPU libre (texto e imagenes apagados). Escribe logs en una carpeta temporal.
set -u
W=$(mktemp -d)
cd "$W"
M=$(ls ~/models-nvme/qwen3.8-flash-next-hcq8/*00001-of-*.gguf)
BENCH=~/llama-panel/tools/bench/moebench.py
PPL=~/llama.cpp/build/bin/llama-perplexity
OT='blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU'
BASE="-lm none --lazy-mode on --no-host -ot $OT -ub 2048 -b 2048 -np 1"
export WRAP="systemd-run --user --scope --quiet -p MemoryMax=58G -p MemorySwapMax=0" GGML_VK_MAX_NODES_PER_SUBMIT=16
J='import json,sys; d=json.loads(sys.stdin.read()); print("  gen", d.get("gen_tps"), "| prompt", d.get("prompt_tps"), "tok/s | VRAM", d.get("vram_gib"), "GiB", d.get("error", ""))'
sudo -n /usr/local/sbin/llama-gpu-mclk high 2>/dev/null

# El mismo texto y los mismos tramos que la prueba de la cache Q8_0 (bitácora 5.14)
cat ~/llama.cpp/docs/*.md ~/llama.cpp/docs/*/*.md ~/llama.cpp/tools/*/README.md 2>/dev/null | head -c 400000 > texto.txt
P="-c 4096 --chunks 6 -ub 2048 -b 2048"
show() { grep -E "Mean PPL|Mean    KLD|99.0%   KLD|Maximum KLD|^Same top p" "$1" | sed "s|^|  $2: |"; }

echo "== 1. calidad de la cache (KLD contra F16, contexto 4096, 6 tramos)"
$WRAP $PPL -m "$M" -f texto.txt -lm none --lazy-mode on --no-host -ot "$OT" $P --kl-divergence-base gpu_f16.kld > gpu_f16.log 2>&1
grep -E "Final estimate" gpu_f16.log | sed 's/^/  F16: /'
for kv in "q8_0 q8_0" "q8_0 q4_0" "q4_0 q4_0"; do
    set -- $kv
    $WRAP $PPL -m "$M" -f texto.txt -lm none --lazy-mode on --no-host -ot "$OT" $P -ctk $1 -ctv $2 \
        --kl-divergence-base gpu_f16.kld --kl-divergence > "kv_$1_$2.log" 2>&1
    show "kv_$1_$2.log" "K $1 / V $2"
done

echo "== 1b. VRAM y velocidad a 64K (TEMP=0, sin MTP, alternadas)"
for round in 1 2; do
    for kv in "q8_0 q8_0" "q8_0 q4_0"; do
        set -- $kv
        echo "-- vuelta $round, K $1 / V $2"
        TEMP=0 N_PREDICT=256 CTX=65536 PROMPT_MULT=8 python3 $BENCH "$M" "x|$BASE -ctk $1 -ctv $2" 2>&1 | tail -1 | python3 -c "$J"
    done
done

# El error del motor (la GPU contra el CPU con los mismos pesos) no se mide acá: el modelo pesa 61.7 GiB y entero en
# el CPU no entra en 62 de RAM. Se mide por operación con test-backend-ops (ver bitácora).

sudo -n /usr/local/sbin/llama-gpu-mclk auto 2>/dev/null
echo "logs en $W"
