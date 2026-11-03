#!/usr/bin/env bash
# One-time setup of the Raspberry Pi that rides on the drone.
#
#   sudo bash pi/setup_pi.sh
#
# What it does:
#   1. installs Python + MAVProxy into /opt/mars-drone/venv
#   2. enables the GPIO serial port the Pixhawk is wired to (and frees it
#      from Bluetooth and the serial login console)
#   3. installs a service that starts at boot and forwards MAVLink from the
#      Pixhawk to the network on TCP ports 5760 and 5761
# Reboot afterwards so the serial port changes take effect.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run me with sudo:  sudo bash $0"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-pi}"
INSTALL_DIR=/opt/mars-drone
CONFIG=/etc/default/mars-drone
SERVICE=/etc/systemd/system/mars-drone-bridge.service

echo "==> Installing system packages"
apt-get update
apt-get install -y python3-venv python3-pip python3-dev git
# ModemManager probes USB serial devices and can grab the Pixhawk's port.
apt-get purge -y modemmanager || true

echo "==> Creating Python environment in $INSTALL_DIR/venv"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install MAVProxy pymavlink pyserial

echo "==> Enabling the GPIO serial port"
# Turn off the login console on the serial port, turn the port itself on.
raspi-config nonint do_serial_cons 1 && raspi-config nonint do_serial_hw 0 \
    || raspi-config nonint do_serial 2   # older raspi-config versions
BOOT_CONFIG=/boot/firmware/config.txt
[[ -f $BOOT_CONFIG ]] || BOOT_CONFIG=/boot/config.txt
if ! grep -q '^dtoverlay=disable-bt' "$BOOT_CONFIG"; then
    # On a Pi 4 Bluetooth normally owns the good UART; give it to the Pixhawk instead.
    printf '\n[all]\n# MARS drone: Pixhawk on GPIO 14/15\nenable_uart=1\ndtoverlay=disable-bt\n' >> "$BOOT_CONFIG"
fi
systemctl disable --now hciuart 2>/dev/null || true
usermod -aG dialout "$RUN_USER"

echo "==> Writing settings to $CONFIG"
if [[ ! -f $CONFIG ]]; then
    # A Pixhawk plugged in by USB shows up under /dev/serial/by-id (a name that
    # survives re-plugging); otherwise assume the GPIO serial port.
    PORT=/dev/serial0
    for dev in /dev/serial/by-id/*; do
        [[ -e $dev ]] && PORT=$dev && break
    done
    echo "    using SERIAL_PORT=$PORT"
    cat > "$CONFIG" <<EOF
# Settings for the MARS drone MAVLink bridge.
# After changing them:  sudo systemctl restart mars-drone-bridge
# Run pi/check_pixhawk.py to find the right values.
SERIAL_PORT=$PORT
BAUD=57600
EOF
else
    echo "    (already exists, leaving it alone)"
fi

echo "==> Installing the mars-drone-bridge service"
cat > "$SERVICE" <<EOF
[Unit]
Description=MARS drone MAVLink bridge (Pixhawk serial -> TCP 5760/5761)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
SupplementaryGroups=dialout
EnvironmentFile=$CONFIG
StateDirectory=mars-drone
WorkingDirectory=/var/lib/mars-drone
# "set heartbeat 0": the bridge must not pretend to be a ground station, or the
# Pixhawk's GCS failsafe would never notice the laptop's link dropping.
ExecStart=$INSTALL_DIR/venv/bin/mavproxy.py --master=\${SERIAL_PORT} --baudrate=\${BAUD} \\
    --out=tcpin:0.0.0.0:5760 --out=tcpin:0.0.0.0:5761 \\
    --daemon --non-interactive --state-basedir=/var/lib/mars-drone \\
    "--cmd=set heartbeat 0"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable mars-drone-bridge

echo
echo "Done. Now:"
echo "  1. sudo reboot"
echo "  2. sudo systemctl stop mars-drone-bridge"
echo "     $INSTALL_DIR/venv/bin/python $REPO_DIR/pi/check_pixhawk.py"
echo "  3. put the port/baud it finds into $CONFIG, then"
echo "     sudo systemctl start mars-drone-bridge"
