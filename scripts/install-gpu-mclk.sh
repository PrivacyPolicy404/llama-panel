#!/usr/bin/env bash
# Memoria de la GPU al máximo mientras haya un servidor de modelos encendido. Correr UNA vez con sudo:
#
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh             # instalar (sin overclock)
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh --oc 2250   # instalar + overclock de VRAM al arrancar
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
    rm -f "$HELPER" "$SUDOERS" "$OC_UNIT" "$OC_SCRIPT" /etc/llama-gpu-oc.mhz
    systemctl daemon-reload
    echo "Desinstalado: memoria de la GPU en automático; el overclock deja de aplicarse desde el próximo arranque."
    exit 0
fi

OC_MHZ=""
if [ "${1:-}" = "--oc" ]; then
    OC_MHZ="${2:-}"
    case "$OC_MHZ" in 2050|2100|2150|2200|2250) ;; *) echo "uso: --oc 2050|2100|2150|2200|2250"; exit 2 ;; esac
    grep -q "amdgpu.ppfeaturemask" /proc/cmdline || { echo "Falta amdgpu.ppfeaturemask (bit 0x4000) en la línea del kernel"; exit 1; }
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
case "$1" in
    high)
        TOP=$(grep -c . "$DEV/pp_dpm_mclk")
        echo manual > "$DEV/power_dpm_force_performance_level"
        echo $((TOP - 1)) > "$DEV/pp_dpm_mclk"
        ;;
    auto)
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
echo "m $LVL $MHZ $MV" > "$OD"
echo c > "$OD"
echo "VRAM: nivel $LVL a $MHZ MHz ($MV mV)"
OCEOF
    chown root:root "$OC_SCRIPT"
    chmod 755 "$OC_SCRIPT"
    echo "$OC_MHZ" > /etc/llama-gpu-oc.mhz
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
    echo "Overclock de VRAM a $OC_MHZ MHz: se aplica desde el PRÓXIMO arranque (la tabla no se toca ahora, en caliente)."
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
