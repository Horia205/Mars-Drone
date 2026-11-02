#!/usr/bin/env python3
"""Find the Pixhawk: tries every likely serial port and baud rate until it
hears a MAVLink heartbeat, then says which firmware the Pixhawk runs.

Stop the bridge first, it holds the serial port:
    sudo systemctl stop mars-drone-bridge
    /opt/mars-drone/venv/bin/python pi/check_pixhawk.py
"""

import os
import subprocess

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil  # noqa: E402

PORTS = ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0", "/dev/ttyACM0", "/dev/ttyUSB0"]
BAUDS = [57600, 115200, 921600, 500000, 230400]
FIRMWARE = {
    mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA: "ArduPilot",
    mavutil.mavlink.MAV_AUTOPILOT_PX4: "PX4",
}


def bridge_running():
    result = subprocess.run(["systemctl", "is-active", "--quiet", "mars-drone-bridge"])
    return result.returncode == 0


def try_port(port, baud):
    try:
        conn = mavutil.mavlink_connection(port, baud=baud)
    except Exception as exc:
        print(f"  {port} @ {baud}: cannot open ({exc})")
        return None
    try:
        hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=3)
        return hb
    finally:
        conn.close()


def main():
    if bridge_running():
        print("The bridge service is using the serial port. Stop it first:")
        print("    sudo systemctl stop mars-drone-bridge")
        raise SystemExit(1)

    ports = [p for p in PORTS if os.path.exists(p)]
    if not ports:
        print("No serial ports found. Did you run setup_pi.sh and reboot?")
        raise SystemExit(1)

    for port in ports:
        for baud in BAUDS:
            print(f"Trying {port} at {baud} baud ...")
            hb = try_port(port, baud)
            if hb is None:
                continue
            firmware = FIRMWARE.get(hb.autopilot, f"unknown autopilot #{hb.autopilot}")
            vehicle = mavutil.mavlink.enums["MAV_TYPE"][hb.type].name.replace("MAV_TYPE_", "")
            print()
            print(f"FOUND the Pixhawk on {port} at {baud} baud")
            print(f"  firmware : {firmware}")
            print(f"  vehicle  : {vehicle}")
            print(f"  mode     : {mavutil.mode_string_v10(hb)}")
            print()
            print("Put these lines in /etc/default/mars-drone:")
            print(f"  SERIAL_PORT={port}")
            print(f"  BAUD={baud}")
            print("then: sudo systemctl start mars-drone-bridge")
            return

    print()
    print("No heartbeat on any port. Check:")
    print("  - Pixhawk is powered (LEDs on)")
    print("  - wires: Pixhawk TX -> Pi pin 10 (RX), Pixhawk RX -> Pi pin 8 (TX), GND -> pin 6")
    print("  - TX and RX are crossed, not straight through")
    print("  - the Pixhawk port is set to MAVLink (ArduPilot: SERIAL2_PROTOCOL = 2)")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
