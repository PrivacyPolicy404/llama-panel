#!/bin/bash
# Cada 2 s: VRAM total del sistema y VRAM/GTT/visible del llama-server según el kernel (fdinfo de DRM).
# Uso: tools/bench/gpu_mem_watch.sh SALIDA &   (cortarlo con kill). mem_info_vram_used solo dice cuánta VRAM está
# ocupada; esto dice además cuánto de lo que pidió llama.cpp terminó en GTT (memoria del sistema) porque no entraba.
VR=$(ls /sys/class/drm/card*/device/mem_info_vram_used | head -1)
D=$(dirname $VR)
while sleep 2; do
    pid=$(pgrep -x llama-server | head -1)
    line="$(awk '{printf "total %.2f", $1/2^30}' $VR) vis $(awk '{printf "%.2f", $1/2^30}' $D/mem_info_vis_vram_used)"
    if [ -n "$pid" ]; then
        line="$line | llama $(cat /proc/$pid/fdinfo/* 2>/dev/null | awk '
            /^drm-client-id/ {id=$2}
            /^drm-memory-vram/ {v[id]=$2}
            /^drm-memory-gtt/ {g[id]=$2}
            /^amd-memory-visible-vram/ {s[id]=$2}
            END {for (i in v) {V+=v[i]; G+=g[i]; S+=s[i]}; printf "vram %.2f gtt %.2f visible %.2f GiB", V/2^20, G/2^20, S/2^20}')"
    fi
    echo "$line"
done > "$1"
