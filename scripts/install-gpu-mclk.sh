#!/usr/bin/env bash
# Memoria de la GPU al máximo mientras haya un servidor de modelos encendido. Correr UNA vez con sudo:
#
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh             # instalar (sin overclock; saca uno anterior)
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh --oc 2250   # instalar + overclock de VRAM al arrancar
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh --oc 2250 --oc-mv 1000 --power 200
#        overclock + piso de voltaje de ese nivel (950-1100 mV) + tope de potencia en W, todo al arrancar.
#        Con overclock la memoria queda FIJA en su nivel más alto desde el arranque (sin transiciones: las dos fallas
#        del SMU fueron al cambiar de nivel) y el ayudante ya no la mueve.
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh --uninstall # desinstalar todo
#
# Por qué: con los MoE la GPU trabaja a ráfagas (espera al CPU en cada capa) y amdgpu baja el reloj de la VRAM
# en las pausas (2000 -> 1000 MHz el 16 % del tiempo): las matmul, que dependen de la memoria, arrancan a media
# velocidad. Fijarlo da Flash-Next +2-3 % con MTP (docs/analisis-rendimiento.md 5.9). En reposo gasta más, por
# eso los units del router y de KoboldCpp lo piden al arrancar (high) y lo devuelven al parar (auto).
#
# Instala:
#   /usr/local/sbin/llama-gpu-mclk        ayudante root: high|auto|status sobre la placa amdgpu (no recibe rutas)
#   /etc/sudoers.d/llama-gpu-mclk         permite a tu usuario correr SOLO ese ayudante sin contraseña
#
#   /etc/systemd/system/llama-gpu-oc.service   (solo con --oc) overclock de VRAM, UNA vez por arranque y antes del escritorio
#   /usr/local/sbin/llama-gpu-oc               (solo con --oc) el script que lo aplica
#
# Overclock: la placa calcula bien hasta 2250 MHz (test-backend-ops y texto idéntico), pero reescribir la tabla de relojes
# en caliente (2250 -> 2000, con el escritorio activo) colgó el SMU y la máquina entera (bitácora 5.13). Por eso el
# ayudante nunca la toca: se escribe una sola vez al arrancar, antes de SDDM, con el mismo voltaje de fábrica.
# Necesita amdgpu.ppfeaturemask con el bit de overdrive (0x4000) en la línea del kernel.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Correr con sudo"; exit 1; }
USER_NAME="${SUDO_USER:-}"
[ -n "$USER_NAME" ] && [ "$USER_NAME" != root ] || { echo "Correr con sudo desde tu usuario (no como root directo)"; exit 1; }

HELPER=/usr/local/sbin/llama-gpu-mclk
SUDOERS=/etc/sudoers.d/llama-gpu-mclk
OC_UNIT=/etc/systemd/system/llama-gpu-oc.service
OC_SCRIPT=/usr/local/sbin/llama-gpu-oc

if [ "${1:-}" = "--uninstall" ]; then
    [ -x "$HELPER" ] && "$HELPER" auto || true
    systemctl disable llama-gpu-oc.service 2>/dev/null || true
    rm -f "$HELPER" "$SUDOERS" "$OC_UNIT" "$OC_SCRIPT" /etc/llama-gpu-oc.mhz /etc/llama-gpu-oc.power /etc/llama-gpu-oc.mv
    systemctl daemon-reload
    echo "Desinstalado: memoria de la GPU en automático; el overclock deja de aplicarse desde el próximo arranque."
    exit 0
fi

OC_MHZ=""
OC_MV=""
POWER_W=""
while [ $# -gt 0 ]; do
    case "$1" in
        --oc) OC_MHZ="${2:-}"; shift 2 ;;
        --oc-mv) OC_MV="${2:-}"; shift 2 ;;
        --power) POWER_W="${2:-}"; shift 2 ;;
        *) echo "opción desconocida: $1"; exit 2 ;;
    esac
done
if [ -n "$OC_MHZ" ]; then
    case "$OC_MHZ" in 2050|2100|2150|2200|2250) ;; *) echo "uso: --oc 2050|2100|2150|2200|2250"; exit 2 ;; esac
    grep -q "amdgpu.ppfeaturemask" /proc/cmdline || { echo "Falta amdgpu.ppfeaturemask (bit 0x4000) en la línea del kernel"; exit 1; }
fi
if [ -n "$OC_MV" ]; then
    [ -n "$OC_MHZ" ] || { echo "--oc-mv va junto con --oc"; exit 2; }
    case "$OC_MV" in ''|*[!0-9]*) echo "--oc-mv: milivolts enteros"; exit 2 ;; esac
    [ "$OC_MV" -ge 950 ] && [ "$OC_MV" -le 1100 ] || { echo "--oc-mv entre 950 y 1100"; exit 2; }
fi
if [ -n "$POWER_W" ]; then
    [ -n "$OC_MHZ" ] || { echo "--power va junto con --oc (los dos se aplican al arrancar)"; exit 2; }
    case "$POWER_W" in ''|*[!0-9]*) echo "--power: watts enteros"; exit 2 ;; esac
    MAXW=$(( $(cat /sys/class/drm/card*/device/hwmon/hwmon*/power1_cap_max 2>/dev/null | head -1) / 1000000 ))
    [ "$POWER_W" -ge 100 ] && [ "$POWER_W" -le "$MAXW" ] || { echo "--power entre 100 y $MAXW W"; exit 2; }
fi

cat > "$HELPER" <<'EOF'
#!/bin/sh
# Reloj de la memoria de la GPU AMD: high = nivel más alto fijo (el núcleo sigue en automático), auto = el driver decide.
set -e
DEV=""
for d in /sys/class/drm/card*/device; do
    [ -f "$d/pp_dpm_mclk" ] && [ -f "$d/power_dpm_force_performance_level" ] && { DEV="$d"; break; }
done
[ -n "$DEV" ] || { echo "no hay una GPU amdgpu con pp_dpm_mclk" >&2; exit 1; }
# con overclock (llama-gpu-oc.service) la memoria queda fija desde el arranque: no cambiarla de nivel nunca
OC_ACTIVE=0
[ -f /etc/llama-gpu-oc.mhz ] && OC_ACTIVE=1
case "$1" in
    high)
        [ "$OC_ACTIVE" = 1 ] && exit 0
        TOP=$(grep -c . "$DEV/pp_dpm_mclk")
        echo manual > "$DEV/power_dpm_force_performance_level"
        echo $((TOP - 1)) > "$DEV/pp_dpm_mclk"
        ;;
    auto)
        [ "$OC_ACTIVE" = 1 ] && exit 0
        echo auto > "$DEV/power_dpm_force_performance_level"
        ;;
    status)
        echo "$(cat "$DEV/power_dpm_force_performance_level") $(grep '\*' "$DEV/pp_dpm_mclk")"
        ;;
    *)
        echo "uso: llama-gpu-mclk high|auto|status" >&2
        exit 2
        ;;
esac
EOF
chown root:root "$HELPER"
chmod 755 "$HELPER"
rm -f /etc/llama-gpu-mclk.conf  # de la versión que cambiaba el overclock en caliente (descartada)

if [ -z "$OC_MHZ" ] && [ -f "$OC_UNIT" ]; then
    # sin --oc: sacar el overclock de una instalación anterior (vuelve a fábrica en el próximo arranque)
    systemctl disable llama-gpu-oc.service 2>/dev/null || true
    rm -f "$OC_UNIT" "$OC_SCRIPT" /etc/llama-gpu-oc.mhz /etc/llama-gpu-oc.power /etc/llama-gpu-oc.mv
    systemctl daemon-reload
    echo "Overclock de VRAM desactivado: la placa vuelve a fábrica en el próximo arranque."
fi

if [ -n "$OC_MHZ" ]; then
    cat > "$OC_SCRIPT" <<'OCEOF'
#!/bin/sh
# Overclock de la VRAM: el nivel más alto de la memoria a la frecuencia de /etc/llama-gpu-oc.mhz, con su voltaje de
# fábrica. Corre UNA vez por arranque, antes del escritorio (llama-gpu-oc.service). No reescribir la tabla en caliente:
# colgó la máquina (bitácora 5.13 de llama-panel).
set -e
MHZ=$(cat /etc/llama-gpu-oc.mhz)
DEV=""
for i in $(seq 1 60); do
    for d in /sys/class/drm/card*/device; do
        [ -w "$d/pp_od_clk_voltage" ] && DEV="$d"
    done
    [ -n "$DEV" ] && break
    sleep 0.5
done
[ -n "$DEV" ] || { echo "sin pp_od_clk_voltage (falta amdgpu.ppfeaturemask?)" >&2; exit 1; }
OD="$DEV/pp_od_clk_voltage"
LVL=$(awk '/^OD_MCLK:/{f=1;next} /^OD_/{f=0} f{n=$1} END{sub(":","",n); print n}' "$OD")
MV=$(awk '/^OD_MCLK:/{f=1;next} /^OD_/{f=0} f{v=$3} END{sub("mV","",v); print v}' "$OD")
[ -f /etc/llama-gpu-oc.mv ] && MV=$(cat /etc/llama-gpu-oc.mv)
echo "m $LVL $MHZ $MV" > "$OD"
echo c > "$OD"
echo "VRAM: nivel $LVL a $MHZ MHz ($MV mV)"
# tope de potencia (opcional): sin él la placa llega a 155 W con la VRAM subida y baja el reloj del núcleo
if [ -f /etc/llama-gpu-oc.power ]; then
    W=$(cat /etc/llama-gpu-oc.power)
    for h in "$DEV"/hwmon/hwmon*; do
        [ -w "$h/power1_cap" ] && echo $((W * 1000000)) > "$h/power1_cap" && echo "tope de potencia: $W W"
    done
fi
# memoria fija en el nivel más alto desde ahora (sin transiciones de nivel; el núcleo sigue en automático)
echo manual > "$DEV/power_dpm_force_performance_level"
echo "$LVL" > "$DEV/pp_dpm_mclk"
echo "memoria fija en el nivel $LVL: $(grep '\*' "$DEV/pp_dpm_mclk")"
OCEOF
    chown root:root "$OC_SCRIPT"
    chmod 755 "$OC_SCRIPT"
    echo "$OC_MHZ" > /etc/llama-gpu-oc.mhz
    if [ -n "$POWER_W" ]; then echo "$POWER_W" > /etc/llama-gpu-oc.power; else rm -f /etc/llama-gpu-oc.power; fi
    if [ -n "$OC_MV" ]; then echo "$OC_MV" > /etc/llama-gpu-oc.mv; else rm -f /etc/llama-gpu-oc.mv; fi
    cat > "$OC_UNIT" <<OCEOF
[Unit]
Description=llama-panel: overclock de la VRAM de la GPU, una vez al arrancar
After=systemd-udev-settle.service systemd-modules-load.service
Before=display-manager.service sddm.service llama-kiosk.service

[Service]
Type=oneshot
ExecStart=$OC_SCRIPT

[Install]
WantedBy=multi-user.target
OCEOF
    systemctl daemon-reload
    systemctl enable llama-gpu-oc.service
    echo "Overclock de VRAM a $OC_MHZ MHz${OC_MV:+ con $OC_MV mV}${POWER_W:+ y tope de $POWER_W W}, memoria fija: se aplica desde el PRÓXIMO arranque (nada se toca ahora, en caliente)."
fi

TMP=$(mktemp)
cat > "$TMP" <<EOF
# llama-panel: los servidores de modelos fijan/liberan el reloj de memoria de la GPU sin contraseña
$USER_NAME ALL=(root) NOPASSWD: $HELPER high, $HELPER auto, $HELPER status
EOF
if visudo -cf "$TMP" >/dev/null; then
    install -m 440 -o root -g root "$TMP" "$SUDOERS"
    rm -f "$TMP"
else
    rm -f "$TMP"
    echo "Error: la regla de sudoers no valida; no se instaló nada en /etc/sudoers.d"
    exit 1
fi

echo "llama-gpu-mclk instalado para $USER_NAME."
echo "  Probar: sudo -n $HELPER status   (no debería pedir contraseña)"
echo "  Después: python3 ~/llama-panel/panel.py --install   (para que los servidores lo usen)"
