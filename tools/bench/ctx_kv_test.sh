#!/bin/bash
# Contexto largo con la cache KV en Q8_0 para Flash-Next: VRAM, velocidad y calidad (KLD contra F16).
#
# Uso: tools/bench/ctx_kv_test.sh [--modo-ia]
#   --modo-ia  apaga el escritorio durante la prueba (libera ~0.4 GB de VRAM) y lo vuelve a prender al final.
# Requiere la GPU libre (texto e imagenes apagados). Escribe logs en una carpeta temporal.
set -u
W=$(mktemp -d)
cd "$W"
M=$(ls ~/models-nvme/qwen3.8-flash-next-hcq8/*00001-of-*.gguf)
MTP=$(ls ~/models-nvme/qwen3.8-flash-next/mtp-*Q4_0*.gguf)
BENCH=~/llama-panel/tools/bench/moebench.py
BASE="-lm none --lazy-mode on --no-host -ot blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU -ub 2048 -b 2048 -np 1 --spec-type draft-mtp --spec-draft-model $MTP --spec-draft-n-max 2 --spec-draft-sampling probabilistic --spec-draft-p-min 0"
Q8="-ctk q8_0 -ctv q8_0 -ctkd q8_0 -ctvd q8_0"
export WRAP="systemd-run --user --scope --quiet -p MemoryMax=58G -p MemorySwapMax=0" GGML_VK_MAX_NODES_PER_SUBMIT=16
J='import json,sys; d=json.loads(sys.stdin.read()); print("  gen", d.get("gen_tps"), "| prompt", d.get("prompt_tps"), "tok/s,", d.get("prompt_tokens"), "tokens | VRAM", d.get("vram_gib"), "GiB", d.get("error", ""))'
AI=0
[ "${1:-}" = "--modo-ia" ] && AI=1

if [ $AI = 1 ]; then
    echo "== modo IA"
    curl -s -X POST http://127.0.0.1:8090/api/action -H 'Content-Type: application/json' -d '{"do":"ai_mode_on"}'; echo
    sleep 10
fi
sudo -n /usr/local/sbin/llama-gpu-mclk high

for cfg in "32K F16|32768||256|8" "64K F16|65536||256|8" "64K Q8_0|65536|$Q8|256|8" "64K Q8_0, prompt ~30K|65536|$Q8|128|720"; do
    IFS='|' read -r name ctx kv npred mult <<< "$cfg"
    echo "== $name"
    TEMP=0.8 N_PREDICT=$npred CTX=$ctx PROMPT_MULT=$mult python3 $BENCH "$M" "x|$BASE $kv" 2>&1 | tail -1 | python3 -c "$J"
done

echo "== calidad: perplejidad y KLD, cache F16 contra Q8_0 (contexto 4096, 6 tramos)"
cat ~/llama.cpp/docs/*.md ~/llama.cpp/docs/*/*.md ~/llama.cpp/tools/*/README.md 2>/dev/null | head -c 400000 > texto.txt
P="-lm none --lazy-mode on --no-host -ot blk\.([0-9]|[1-3][0-9]|4[0-3])\.ffn_.*_exps=Vulkan_Host,ffn_.*_exps=CPU -c 4096 --chunks 6 -ub 2048 -b 2048"
$WRAP ~/llama.cpp/build/bin/llama-perplexity -m "$M" -f texto.txt $P --kl-divergence-base base.kld > ppl_f16.log 2>&1
grep -E "Final estimate" ppl_f16.log | sed 's/^/  F16: /'
$WRAP ~/llama.cpp/build/bin/llama-perplexity -m "$M" -f texto.txt $P -ctk q8_0 -ctv q8_0 --kl-divergence-base base.kld --kl-divergence > ppl_q8.log 2>&1
grep -E "Mean PPL|Mean    KLD|Maximum KLD|99.0%   KLD|Same top p" ppl_q8.log | sed 's/^/  Q8: /'

sudo -n /usr/local/sbin/llama-gpu-mclk auto
if [ $AI = 1 ]; then
    echo "== saliendo del modo IA"
    curl -s -X POST http://127.0.0.1:8090/api/action -H 'Content-Type: application/json' -d '{"do":"ai_mode_off"}'; echo
fi
echo "logs en $W"
