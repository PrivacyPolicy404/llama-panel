#!/usr/bin/env bash
# Memoria de la GPU al máximo mientras haya un servidor de modelos encendido. Correr UNA vez con sudo:
#
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh             # instalar
#   sudo ~/llama-panel/scripts/install-gpu-mclk.sh --uninstall # desinstalar
#
# Por qué: con los MoE la GPU trabaja a ráfagas (espera al CPU en cada capa) y amdgpu baja el reloj de la VRAM
# en las pausas (2000 -> 1000 MHz el 16 % del tiempo): las matmul, que dependen de la memoria, arrancan a media
# velocidad. Fijarlo da Flash-Next +2-3 % con MTP (docs/analisis-rendimiento.md 5.9). En reposo gasta más, por
# eso los units del router y de KoboldCpp lo piden al arrancar (high) y lo devuelven al parar (auto).
#
# Instala:
#   /usr/local/sbin/llama-gpu-mclk        ayudante root: high|auto|status sobre la placa amdgpu (no recibe rutas)
#   /etc/sudoers.d/llama-gpu-mclk         permite a tu usuario correr SOLO ese ayudante sin contraseña
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Correr con sudo"; exit 1; }
USER_NAME="${SUDO_USER:-}"
[ -n "$USER_NAME" ] && [ "$USER_NAME" != root ] || { echo "Correr con sudo desde tu usuario (no como root directo)"; exit 1; }

HELPER=/usr/local/sbin/llama-gpu-mclk
SUDOERS=/etc/sudoers.d/llama-gpu-mclk

if [ "${1:-}" = "--uninstall" ]; then
    [ -x "$HELPER" ] && "$HELPER" auto || true
    rm -f "$HELPER" "$SUDOERS"
    echo "llama-gpu-mclk desinstalado (memoria de la GPU en automático)."
    exit 0
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
