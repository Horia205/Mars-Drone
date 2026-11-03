#!/usr/bin/env python3
"""Find the Pixhawk: listens on every likely serial port and baud rate for a
MAVLink heartbeat, then says which firmware the Pixhawk runs. If there is no
heartbeat it reports whether *any* bytes arrived, which tells a wiring/power
problem apart from a wrong port setting on the Pixhawk.

Stop the bridge first, it holds the serial port:
    sudo systemctl stop mars-drone-bridge
    /opt/mars-drone/venv/bin/python pi/check_pixhawk.py
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("MAVLINK20", "1")

import serial  # noqa: E402
from pymavlink import mavutil  # noqa: E402

PORTS = ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0", "/dev/ttyACM0", "/dev/ttyUSB0"]
BAUDS = [57600, 115200, 921600, 500000, 230400]
LISTEN_SECONDS = 2.5  # a Pixhawk sends a heartbeat every second
FIRMWARE = {
    mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA: "ArduPilot",
    mavutil.mavlink.MAV_AUTOPILOT_PX4: "PX4",
}


def bridge_running():
    result = subprocess.run(["systemctl", "is-active", "--quiet", "mars-drone-bridge"])
    return result.returncode == 0


def print_board_info():
    try:
        with open("/proc/device-tree/model") as f:
            print(f"Board        : {f.read().strip(chr(0))}")
    except OSError:
        pass
    if os.path.islink("/dev/serial0"):
        print(f"/dev/serial0 : -> {os.path.realpath('/dev/serial0')}")
    print()


def listen(port, baud):
    """Read raw bytes for a moment. Returns (bytes received, first heartbeat or None)."""
    data = bytearray()
    try:
        with serial.Serial(port, baud, timeout=0.2) as ser:
            deadline = time.time() + LISTEN_SECONDS
            while time.time() < deadline:
                data += ser.read(1024)
    except (serial.SerialException, OSError) as exc:
        print(f"    cannot open: {exc}")
        return 0, None
    parser = mavutil.mavlink.MAVLink(None)
    parser.robust_parsing = True  # skip garbage instead of raising
    for msg in parser.parse_buffer(bytes(data)) or []:
        if msg.get_type() == "HEARTBEAT":
            return len(data), msg
    return len(data), None


def report_found(port, baud, hb):
    firmware = FIRMWARE.get(hb.autopilot, f"unknown autopilot #{hb.autopilot}")
    vehicle = mavutil.mavlink.enums["MAV_TYPE"][hb.type].name.replace("MAV_TYPE_", "")
    print()
    print(f"FOUND the Pixhawk on {port} at {baud} baud")
    print(f"  firmware : {firmware}")
    print(f"  vehicle  : {vehicle}")
    print(f"  mode     : {mavutil.mode_string_v10(hb)}")
    print()
    if port.startswith(("/dev/ttyACM", "/dev/ttyUSB")):
        print("This is the Pixhawk's USB cable - that works fine, you can keep using it.")
    print("Put these lines in /etc/default/mars-drone:")
    print(f"  SERIAL_PORT={port}")
    print(f"  BAUD={baud}")
    print("then: sudo systemctl start mars-drone-bridge")


def report_not_found(noisy):
    print()
    if noisy:
        print("No heartbeat, but data IS arriving here:")
        for port, baud, count in noisy:
            print(f"  {port} at {baud} baud: {count} bytes")
        print("So the wires work. The Pixhawk port is probably not set to MAVLink, or uses")
        print("another baud rate. Plug the Pixhawk's USB cable into the Pi and run this")
        print("script again - it will find it over USB, and we can fix the port settings.")
    else:
        print("Not a single byte arrived on any port. Check:")
        print("  - the Pixhawk is powered (LEDs on)")
        print("  - the cable is in the Pixhawk's TELEM port (TELEM2), not GPS/I2C/etc.")
        print("  - wires: Pixhawk TX -> Pi pin 10 (RX), Pixhawk RX -> Pi pin 8 (TX), GND -> pin 6")
        print("  - TX and RX are crossed, not straight through")
        print("Or plug the Pixhawk's USB cable into the Pi and run this script again.")


def main():
    if bridge_running():
        print("The bridge service is using the serial port. Stop it first:")
        print("    sudo systemctl stop mars-drone-bridge")
        sys.exit(1)

    print_board_info()
    ports = [p for p in PORTS if os.path.exists(p)]
    if not ports:
        print("No serial ports found. Did you run setup_pi.sh and reboot?")
        sys.exit(1)

    noisy = []  # (port, baud, bytes) where something arrived but no heartbeat
    for port in ports:
        for baud in BAUDS:
            print(f"Listening on {port} at {baud} baud ...", end=" ", flush=True)
            count, hb = listen(port, baud)
            if hb is not None:
                print("heartbeat!")
                report_found(port, baud, hb)
                return
            print(f"{count} bytes, no heartbeat" if count else "nothing")
            if count:
                noisy.append((port, baud, count))
    report_not_found(noisy)
    sys.exit(1)


if __name__ == "__main__":
    main()
