#!/usr/bin/env bash
# Instala el "modo IA": apagar el escritorio (SDDM/KDE) y mostrar un dashboard de texto en tty1,
# para dejarle toda la VRAM a los modelos. Correr UNA vez con sudo:
#
#   sudo ~/llama-panel/scripts/install-modo-ia.sh             # instalar
#   sudo ~/llama-panel/scripts/install-modo-ia.sh --uninstall # desinstalar
#
# Instala:
#   /usr/local/sbin/llama-modo-ia            ayudante root: solo para/arranca SDDM y el dashboard (on|off|status)
#   /etc/systemd/system/llama-kiosk.service  el dashboard en tty1, corre como tu usuario (no como root)
#   /etc/sudoers.d/llama-modo-ia             permite a tu usuario correr SOLO ese ayudante sin contraseña
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Correr con sudo"; exit 1; }
USER_NAME="${SUDO_USER:-}"
[ -n "$USER_NAME" ] && [ "$USER_NAME" != root ] || { echo "Correr con sudo desde tu usuario (no como root directo)"; exit 1; }
USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

HELPER=/usr/local/sbin/llama-modo-ia
UNIT=/etc/systemd/system/llama-kiosk.service
SUDOERS=/etc/sudoers.d/llama-modo-ia

if [ "${1:-}" = "--uninstall" ]; then
    systemctl stop llama-kiosk.service 2>/dev/null || true
    rm -f "$HELPER" "$UNIT" "$SUDOERS"
    systemctl daemon-reload
    systemctl start sddm.service || true
    echo "Modo IA desinstalado."
    exit 0
fi

# 1) Ayudante root. No ejecuta nada del repo: solo systemctl y chvt con argumentos fijos.
cat > "$HELPER" <<'EOF'
#!/bin/sh
# Modo IA del panel: apaga el escritorio y muestra el dashboard en tty1, o vuelve al escritorio.
set -e
case "$1" in
    on)
        systemctl stop sddm.service
        systemctl start llama-kiosk.service
        chvt 1
        ;;
    off)
        systemctl stop llama-kiosk.service
        systemctl start sddm.service
        ;;
    status)
        systemctl is-active llama-kiosk.service || true
        ;;
    *)
        echo "uso: llama-modo-ia on|off|status" >&2
        exit 2
        ;;
esac
EOF
chown root:root "$HELPER"
chmod 755 "$HELPER"

# 2) Servicio del dashboard: corre como el usuario, en tty1, con letra grande.
cat > "$UNIT" <<EOF
[Unit]
Description=Panel IA - modo IA (dashboard de texto en tty1)
Conflicts=getty@tty1.service
After=getty@tty1.service

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
Environment=TERM=linux PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8
ExecStartPre=+/usr/bin/setfont -C /dev/tty1 Uni2-TerminusBold24x12
ExecStart=/usr/bin/python3 -m llama_panel.kiosk
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
Restart=on-failure
RestartSec=3
EOF

# 3) sudoers: solo el ayudante, con sus tres argumentos posibles. Se valida antes de activarlo.
TMP=$(mktemp)
cat > "$TMP" <<EOF
# Modo IA de llama-panel: el panel puede apagar/prender el escritorio sin pedir contraseña
$USER_NAME ALL=(root) NOPASSWD: $HELPER on, $HELPER off, $HELPER status
EOF
if visudo -cf "$TMP" >/dev/null; then
    install -m 440 -o root -g root "$TMP" "$SUDOERS"
    rm -f "$TMP"
else
    rm -f "$TMP"
    echo "Error: la regla de sudoers no valida; no se instaló nada en /etc/sudoers.d"
    exit 1
fi

systemctl daemon-reload
echo "Modo IA instalado para $USER_NAME."
echo "  Probar: sudo -n $HELPER status   (no debería pedir contraseña)"
echo "  Usar:   botón «Modo IA» en el panel"
