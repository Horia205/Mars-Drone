#!/usr/bin/env bash
# Run a simulated ArduCopter drone on this computer (Linux / WSL only).
# The first run downloads the official ArduPilot simulator (~7 MB).
#
#   bash sim/run_sim.sh
#
# Then, in a second terminal:
#   python drone_console.py tcp:127.0.0.1:5760
set -euo pipefail

SIM_DIR="$(cd "$(dirname "$0")" && pwd)/bin"
BINARY_URL=https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter
PARAMS_URL=https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/autotest/default_params/copter.parm
# Home location for the simulator: lat,lon,altitude(m),heading
HOME_LOCATION="${HOME_LOCATION:--35.363261,149.165230,584,353}"

mkdir -p "$SIM_DIR"
if [[ ! -x $SIM_DIR/arducopter ]]; then
    echo "Downloading ArduCopter simulator ..."
    curl -fsSL "$BINARY_URL" -o "$SIM_DIR/arducopter"
    curl -fsSL "$PARAMS_URL" -o "$SIM_DIR/copter.parm"
    chmod +x "$SIM_DIR/arducopter"
fi

cd "$SIM_DIR"
echo "Simulated drone starting - it listens on tcp:127.0.0.1:5760. Ctrl+C to stop."
exec ./arducopter --model + --speedup 1 --defaults copter.parm --home "$HOME_LOCATION" "$@"
