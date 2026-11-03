#!/usr/bin/env python3
"""MARS drone console.

Type commands in the terminal; they are sent to the Pixhawk as MAVLink
messages and the drone's answers are printed back.

    python drone_console.py 192.168.1.50          # Pi on the drone (option A)
    python drone_console.py tcp:127.0.0.1:5760    # simulator on this laptop
    python drone_console.py /dev/serial0          # running on the Pi itself (option B)

Type `help` at the prompt for the list of commands.
"""

import argparse
import math
import os
import shlex
import threading
import time

os.environ.setdefault("MAVLINK20", "1")  # must be set before pymavlink is imported

from pymavlink import mavutil  # noqa: E402

try:
    import readline  # noqa: F401  (gives arrow-key history at the prompt)
except ImportError:
    pass

mavlink = mavutil.mavlink

DEFAULT_PORT = 5760
LINK_TIMEOUT = 3.0  # seconds without a heartbeat before we warn about the link
EARTH_RADIUS = 6378137.0

# Messages we ask the Pixhawk to stream to us, and how often (Hz).
STREAMS = {
    mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 4,
    mavlink.MAVLINK_MSG_ID_SYS_STATUS: 1,
    mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 1,
    mavlink.MAVLINK_MSG_ID_VFR_HUD: 2,
}


def normalise_address(address):
    """Turn a bare IP like 192.168.1.50 into tcp:192.168.1.50:5760."""
    if ":" not in address and not address.startswith("/"):
        return f"tcp:{address}:{DEFAULT_PORT}"
    return address


def ask_yes(question):
    return input(f"{question} Type 'yes' to continue: ").strip().lower() == "yes"


class Drone:
    """Connection to one vehicle. A background thread keeps the latest
    telemetry in self.state so commands can read it at any time."""

    def __init__(self, address, baud=57600, source_component=190):
        self.address = address
        # 255/190 = the IDs a ground control station normally uses. Scripts that
        # run alongside the console pass another component id so their command
        # replies don't get mixed up.
        self.master = mavutil.mavlink_connection(
            address, baud=baud, source_system=255, source_component=source_component
        )
        self.source_component = source_component
        self.send_lock = threading.Lock()
        self.cond = threading.Condition()
        self.state = {}  # message type -> latest message
        self.acks = {}  # command id -> COMMAND_ACK
        self.params = {}  # param name -> value
        self.heartbeat = None
        self.last_heartbeat = 0.0
        self.link_ok = True
        self.running = True

    # ---- connection -------------------------------------------------

    def connect(self, timeout=20):
        """Wait for the autopilot's heartbeat, then start the helper threads."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.master.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
            # Ignore heartbeats from other ground stations, gimbals, etc.
            if msg and msg.type != mavlink.MAV_TYPE_GCS and msg.autopilot != mavlink.MAV_AUTOPILOT_INVALID:
                self.master.target_system = msg.get_srcSystem()
                self.master.target_component = msg.get_srcComponent()
                self._store_heartbeat(msg)
                break
        else:
            raise TimeoutError(f"no heartbeat from a drone on {self.address} after {timeout} s")

        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._gcs_heartbeat, daemon=True).start()
        for msg_id, hz in STREAMS.items():
            self.command(mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, msg_id, 1e6 / hz, wait=False)

    def close(self):
        self.running = False
        self.master.close()

    def _store_heartbeat(self, msg):
        self.heartbeat = msg
        self.last_heartbeat = time.time()

    def _reader(self):
        while self.running:
            try:
                msg = self.master.recv_match(blocking=True, timeout=0.5)
            except Exception as exc:  # socket closed, serial unplugged...
                if self.running:
                    print(f"\n[link] read error: {exc}")
                    time.sleep(1)
                continue
            self._check_link()
            if msg is None or msg.get_srcSystem() != self.master.target_system:
                continue
            kind = msg.get_type()
            self.state[kind] = msg
            if kind == "HEARTBEAT" and msg.get_srcComponent() == self.master.target_component:
                self._store_heartbeat(msg)
            elif kind == "COMMAND_ACK":
                if getattr(msg, "target_component", 0) not in (0, self.source_component):
                    continue  # reply to another ground station's command
                with self.cond:
                    self.acks[msg.command] = msg
                    self.cond.notify_all()
            elif kind == "PARAM_VALUE":
                with self.cond:
                    self.params[msg.param_id] = msg.param_value
                    self.cond.notify_all()
            elif kind == "STATUSTEXT":
                print(f"\n[drone] {msg.text}")

    def _check_link(self):
        silent_for = time.time() - self.last_heartbeat
        if self.link_ok and silent_for > LINK_TIMEOUT:
            self.link_ok = False
            print(f"\n[link] WARNING: no heartbeat for {silent_for:.0f} s - connection lost?")
        elif not self.link_ok and silent_for < LINK_TIMEOUT:
            self.link_ok = True
            print("\n[link] connection restored")

    def _gcs_heartbeat(self):
        """Tell the autopilot a ground station is connected (used by its GCS failsafe)."""
        while self.running:
            with self.send_lock:
                self.master.mav.heartbeat_send(
                    mavlink.MAV_TYPE_GCS, mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0
                )
            time.sleep(1)

    # ---- state helpers ----------------------------------------------

    @property
    def is_px4(self):
        return self.heartbeat.autopilot == mavlink.MAV_AUTOPILOT_PX4

    @property
    def firmware(self):
        names = {mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA: "ArduPilot", mavlink.MAV_AUTOPILOT_PX4: "PX4"}
        return names.get(self.heartbeat.autopilot, f"autopilot #{self.heartbeat.autopilot}")

    @property
    def mode(self):
        return mavutil.mode_string_v10(self.heartbeat)

    @property
    def armed(self):
        return bool(self.heartbeat.base_mode & mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

    def position(self):
        """(lat, lon, altitude above home in m, altitude above sea level in m) or None."""
        pos = self.state.get("GLOBAL_POSITION_INT")
        if pos is None:
            return None
        return pos.lat / 1e7, pos.lon / 1e7, pos.relative_alt / 1000, pos.alt / 1000

    def gps_fix(self):
        gps = self.state.get("GPS_RAW_INT")
        return gps.fix_type if gps else 0

    # ---- sending ----------------------------------------------------

    def command(self, cmd, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0, wait=True, timeout=3):
        """Send a COMMAND_LONG. Returns the MAV_RESULT, or None if no reply came."""
        with self.cond:
            self.acks.pop(cmd, None)
        with self.send_lock:
            self.master.mav.command_long_send(
                self.master.target_system, self.master.target_component,
                cmd, 0, p1, p2, p3, p4, p5, p6, p7,
            )
        if not wait:
            return None
        with self.cond:
            self.cond.wait_for(lambda: cmd in self.acks, timeout)
            ack = self.acks.get(cmd)
        return ack.result if ack else None

    def wait_until(self, condition, timeout=3):
        """Poll until condition() is true; telemetry only updates as messages arrive."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            time.sleep(0.1)
        return False

    def set_mode(self, name):
        name = name.upper()
        if name not in self.master.mode_mapping():
            raise KeyError(name)
        with self.send_lock:
            self.master.set_mode(name)
        return self.wait_until(lambda: self.mode == name)

    def set_armed(self, armed):
        result = self.command(mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1 if armed else 0)
        if result == mavlink.MAV_RESULT_ACCEPTED:
            self.wait_until(lambda: self.armed == armed)
        return result

    def get_param(self, name, timeout=3):
        name = name.upper()
        with self.cond:
            self.params.pop(name, None)
        with self.send_lock:
            self.master.mav.param_request_read_send(
                self.master.target_system, self.master.target_component, name.encode(), -1
            )
        with self.cond:
            self.cond.wait_for(lambda: name in self.params, timeout)
            return self.params.get(name)

    def set_param(self, name, value, timeout=3):
        name = name.upper()
        with self.cond:
            self.params.pop(name, None)
        with self.send_lock:
            self.master.param_set_send(name, value)
        with self.cond:
            self.cond.wait_for(lambda: name in self.params, timeout)
            return self.params.get(name)


def result_text(result):
    if result is None:
        return "no reply from the drone"
    return mavlink.enums["MAV_RESULT"][result].name.replace("MAV_RESULT_", "")


def report(action, result):
    if result == mavlink.MAV_RESULT_ACCEPTED:
        print(f"{action}: OK")
    else:
        print(f"{action}: {result_text(result)} (see any [drone] messages above for the reason)")


# ---- console commands ------------------------------------------------
# Each takes (drone, args). Raising ValueError/IndexError prints the usage line.

def cmd_status(drone, args):
    hb_age = time.time() - drone.last_heartbeat
    print(f"Firmware : {drone.firmware}")
    print(f"Mode     : {drone.mode}")
    print(f"Armed    : {'YES - propellers can spin' if drone.armed else 'no'}")
    bat = drone.state.get("SYS_STATUS")
    if bat:
        pct = f"{bat.battery_remaining} %" if bat.battery_remaining >= 0 else "unknown %"
        print(f"Battery  : {bat.voltage_battery / 1000:.2f} V, {pct}")
    gps = drone.state.get("GPS_RAW_INT")
    if gps:
        fixes = {0: "no GPS", 1: "no fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}
        print(f"GPS      : {fixes.get(gps.fix_type, gps.fix_type)}, {gps.satellites_visible} satellites")
    pos = drone.position()
    if pos:
        print(f"Altitude : {pos[2]:.1f} m above home")
        print(f"Position : {pos[0]:.7f}, {pos[1]:.7f}")
    hud = drone.state.get("VFR_HUD")
    if hud:
        print(f"Speed    : {hud.groundspeed:.1f} m/s, heading {hud.heading} deg")
    print(f"Link     : last heartbeat {hb_age:.1f} s ago")


def cmd_watch(drone, args):
    print("Live view - press Ctrl+C to stop watching (the drone is not affected).")
    while True:
        pos = drone.position()
        hud = drone.state.get("VFR_HUD")
        alt = f"{pos[2]:6.1f} m" if pos else "   ? m"
        spd = f"{hud.groundspeed:4.1f} m/s" if hud else "? m/s"
        print(f"  {drone.mode:<10} armed={'Y' if drone.armed else 'n'}  alt {alt}  speed {spd}")
        time.sleep(1)


def cmd_modes(drone, args):
    print(", ".join(sorted(drone.master.mode_mapping())))


def cmd_mode(drone, args):
    name = args[0]
    try:
        ok = drone.set_mode(name)
    except KeyError:
        print(f"Unknown mode '{name}'. Type 'modes' to list them.")
        return
    print(f"Mode is now {drone.mode}" if ok else f"Mode change refused - still {drone.mode}")


def cmd_arm(drone, args):
    if drone.armed:
        print("Already armed.")
        return
    if not ask_yes("Arming lets the propellers spin. Is everyone clear of the drone?"):
        print("Not armed.")
        return
    report("Arm", drone.set_armed(True))


def cmd_disarm(drone, args):
    pos = drone.position()
    if pos and pos[2] > 1.0 and not ask_yes(f"The drone looks {pos[2]:.1f} m in the air - disarming would drop it."):
        print("Not disarmed. Use 'land' instead.")
        return
    report("Disarm", drone.set_armed(False))


def cmd_takeoff(drone, args):
    alt = float(args[0])
    if not 0.5 <= alt <= 50:
        print("Pick an altitude between 0.5 and 50 m.")
        return
    if drone.gps_fix() < 3:
        print("No 3D GPS fix yet - takeoff needs GPS. Check 'status'.")
        return
    if not drone.armed:
        print("Arm first ('arm'). Note: ArduPilot disarms itself ~10 s after arming if you don't take off.")
        return
    if drone.is_px4:
        # PX4 wants the target as altitude above sea level.
        result = drone.command(mavlink.MAV_CMD_NAV_TAKEOFF, p5=math.nan, p6=math.nan, p7=drone.position()[3] + alt)
    else:
        if drone.mode != "GUIDED" and not drone.set_mode("GUIDED"):
            print("Could not switch to GUIDED mode, which takeoff needs.")
            return
        result = drone.command(mavlink.MAV_CMD_NAV_TAKEOFF, p7=alt)
    report("Takeoff", result)
    if result != mavlink.MAV_RESULT_ACCEPTED:
        return
    print("Climbing - Ctrl+C stops watching (the drone keeps climbing).")
    deadline = time.time() + 60
    while time.time() < deadline:
        current = drone.position()[2]
        print(f"  altitude {current:5.1f} m")
        if current >= alt * 0.95:
            print("Reached target altitude.")
            return
        time.sleep(1)
    print("Still not at target altitude after 60 s - check 'status'.")


def cmd_goto(drone, args):
    north, east = float(args[0]), float(args[1])
    up = float(args[2]) if len(args) > 2 else 0.0
    pos = drone.position()
    if pos is None or drone.gps_fix() < 3:
        print("No position yet - goto needs GPS.")
        return
    if not drone.armed or pos[2] < 0.5:
        print("Take off first.")
        return
    lat = pos[0] + math.degrees(north / EARTH_RADIUS)
    lon = pos[1] + math.degrees(east / (EARTH_RADIUS * math.cos(math.radians(pos[0]))))
    alt = pos[2] + up
    if alt < 1:
        print("That would put the drone below 1 m - refusing.")
        return
    if drone.is_px4:
        with drone.send_lock:
            drone.master.mav.command_int_send(
                drone.master.target_system, drone.master.target_component,
                mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, mavlink.MAV_CMD_DO_REPOSITION, 0, 0,
                -1, mavlink.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE, 0, math.nan,
                int(lat * 1e7), int(lon * 1e7), alt,
            )
    else:
        if drone.mode != "GUIDED" and not drone.set_mode("GUIDED"):
            print("Could not switch to GUIDED mode, which goto needs.")
            return
        position_only = 0b0000111111111000  # ignore velocity, acceleration and yaw fields
        with drone.send_lock:
            drone.master.mav.set_position_target_global_int_send(
                0, drone.master.target_system, drone.master.target_component,
                mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, position_only,
                int(lat * 1e7), int(lon * 1e7), alt, 0, 0, 0, 0, 0, 0, 0, 0,
            )
    print(f"Flying {north:g} m north, {east:g} m east, to {alt:.1f} m altitude. Use 'watch' to follow it.")


def cmd_speed(drone, args):
    speed = float(args[0])
    report("Speed", drone.command(mavlink.MAV_CMD_DO_CHANGE_SPEED, 1, speed, -1))


def cmd_land(drone, args):
    cmd_mode(drone, ["LAND"])


def cmd_rtl(drone, args):
    cmd_mode(drone, ["RTL"])


def cmd_param(drone, args):
    name = args[0]
    if len(args) == 1:
        value = drone.get_param(name)
        print(f"{name.upper()} = {value:g}" if value is not None else f"No reply for parameter {name.upper()}")
        return
    value = drone.set_param(name, float(args[1]))
    print(f"{name.upper()} is now {value:g}" if value is not None else "No reply - parameter may not exist.")


def cmd_help(drone, args):
    width = max(len(usage) for usage, _ in COMMANDS.values())
    for name, (usage, text) in COMMANDS.items():
        print(f"  {usage:<{width}}  {text}")


COMMANDS = {
    "status": ("status", "show mode, battery, GPS, altitude"),
    "watch": ("watch", "live altitude/speed every second (Ctrl+C to stop)"),
    "modes": ("modes", "list flight modes this autopilot knows"),
    "mode": ("mode <NAME>", "change flight mode, e.g. mode GUIDED"),
    "arm": ("arm", "arm the motors (asks for confirmation)"),
    "disarm": ("disarm", "disarm the motors"),
    "takeoff": ("takeoff <metres>", "take off to an altitude above home"),
    "goto": ("goto <north> <east> [up]", "fly by offsets in metres from where it is now"),
    "speed": ("speed <m/s>", "set horizontal speed for goto"),
    "land": ("land", "land where it is"),
    "rtl": ("rtl", "return to launch point and land"),
    "param": ("param <NAME> [value]", "read or change an autopilot parameter"),
    "help": ("help", "this list"),
    "quit": ("quit", "close the console (the drone keeps its current mode)"),
}
HANDLERS = {name: globals()[f"cmd_{name}"] for name in COMMANDS if name != "quit"}


def main():
    parser = argparse.ArgumentParser(description="Console for controlling the MARS drone over MAVLink.")
    parser.add_argument(
        "address", nargs="?", default=os.environ.get("DRONE_ADDRESS", f"tcp:127.0.0.1:{DEFAULT_PORT}"),
        help="Pi IP address (e.g. 192.168.1.50), a full MAVLink address like tcp:IP:5760, or a serial port",
    )
    parser.add_argument("--baud", type=int, default=57600, help="baud rate, only used for serial ports")
    opts = parser.parse_args()

    address = normalise_address(opts.address)
    print(f"Connecting to {address} ...")
    try:
        drone = Drone(address, opts.baud)
        drone.connect()
    except Exception as exc:
        print(f"Could not connect: {exc}")
        raise SystemExit(1)
    print(f"Connected to {drone.firmware}, system id {drone.master.target_system}. Type 'help' for commands.")

    try:
        while True:
            try:
                line = input("drone> ").strip()
            except KeyboardInterrupt:
                print()
                continue
            if not line:
                continue
            name, *args = shlex.split(line)
            name = name.lower()
            if name in ("quit", "exit"):
                break
            handler = HANDLERS.get(name)
            if handler is None:
                print(f"Unknown command '{name}'. Type 'help'.")
                continue
            try:
                handler(drone, args)
            except (ValueError, IndexError):
                print(f"Usage: {COMMANDS[name][0]}")
            except KeyboardInterrupt:
                print("\n(stopped watching)")
    except EOFError:
        pass
    finally:
        drone.close()
        print("Bye.")


if __name__ == "__main__":
    main()
