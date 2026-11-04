#!/usr/bin/env python3
"""Photo mission: take off, face north, take a picture, save the GPS position, land.

Runs ON THE PI (the camera is there), so it carries on if the laptop loses
WiFi. Start it from the laptop over SSH inside tmux - see docs/FLYING.md.

    python mission.py                 # full mission at 20 m
    python mission.py --alt 10        # another altitude
    python mission.py --photo-only    # no flying: test the camera and GPS log

The RC pilot can take over at any moment by changing the flight mode on the
transmitter; the script notices and stops sending commands.
"""

import argparse
import csv
import datetime
import os
import shlex
import shutil
import subprocess
import sys
import time

from drone_console import Drone, ask_yes, mavlink, normalise_address, result_text

DEFAULT_ADDRESS = "tcp:127.0.0.1:5762"  # bridge port reserved for scripts on the Pi
SOURCE_COMPONENT = 191  # not 190, so replies don't mix with the laptop console's
FLIGHTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flights")
MIN_SATELLITES = 8
NORTH_TOLERANCE = 5  # degrees
CAMERA_COMMANDS = [
    "rpicam-still -n -t 1500 -o {file}",  # Pi camera module (Raspberry Pi OS Bookworm)
    "libcamera-still -n -t 1500 -o {file}",  # Pi camera module (older OS)
    "fswebcam -r 1920x1080 --no-banner {file}",  # USB webcam
]
GPS_FIXES = {0: "no GPS", 1: "no fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}


class PilotTookOver(Exception):
    """The flight mode was changed away from GUIDED, by the RC pilot or a failsafe."""


class MissionError(Exception):
    pass


def log(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def heading_error(heading, target=0):
    return abs((heading - target + 180) % 360 - 180)


def heading(drone):
    hud = drone.state.get("VFR_HUD")
    return hud.heading if hud else None


def wait_for(drone, what, condition, timeout):
    """Wait until condition() is true, stopping at once if we lose GUIDED mode."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if drone.mode != "GUIDED":
            raise PilotTookOver(drone.mode)
        if condition():
            return
        time.sleep(0.2)
    raise MissionError(f"timed out waiting for {what}")


# ---- camera and GPS log ----------------------------------------------

def take_photo(path, camera_cmd=None):
    """Take one picture with the first camera program that works."""
    templates = [camera_cmd] if camera_cmd else [
        t for t in CAMERA_COMMANDS if shutil.which(t.split()[0])
    ]
    if not templates:
        raise MissionError("no camera program found (rpicam-still, libcamera-still or fswebcam)")
    for template in templates:
        cmd = shlex.split(template.replace("{file}", shlex.quote(path)))
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as exc:
            log(f"Camera program {cmd[0]} failed: {exc}")
            continue
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return cmd[0]
    raise MissionError("could not take a photo with any camera program")


def save_location(drone, folder, image_name):
    """Write the drone's position to gps.txt and add a row to flights/photos.csv."""
    lat, lon, alt_home, alt_sea = drone.position()
    gps = drone.state.get("GPS_RAW_INT")
    fix = GPS_FIXES.get(gps.fix_type, gps.fix_type) if gps else "unknown"
    sats = gps.satellites_visible if gps else 0
    when = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    facing = heading(drone)
    # Without a fix the Pixhawk reports 0, 0 (or a stale guess) - never map that.
    valid = gps is not None and gps.fix_type >= 3 and (lat, lon) != (0, 0)

    with open(os.path.join(folder, "gps.txt"), "w") as f:
        f.write(
            "MARS drone photo\n"
            f"time_utc: {when}\n"
            f"image: {image_name}\n"
            f"latitude: {lat:.7f}\n"
            f"longitude: {lon:.7f}\n"
            f"altitude_above_home_m: {alt_home:.1f}\n"
            f"altitude_above_sea_m: {alt_sea:.1f}\n"
            f"heading_deg: {facing}\n"
            f"gps: {fix}, {sats} satellites\n"
            f"position_valid: {'yes' if valid else 'NO - no GPS fix, coordinates are meaningless'}\n"
        )

    if not valid:
        log(f"WARNING: no GPS fix ({fix}, {sats} satellites) - the saved position is NOT valid "
            "and was left out of photos.csv. Go outside with a clear sky view and wait for a fix.")
        return
    table = os.path.join(FLIGHTS_DIR, "photos.csv")
    is_new = not os.path.exists(table)
    with open(table, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["time_utc", "image", "latitude", "longitude", "alt_home_m", "alt_sea_m", "heading_deg"])
        image_path = os.path.relpath(os.path.join(folder, image_name), FLIGHTS_DIR)
        writer.writerow([when, image_path, f"{lat:.7f}", f"{lon:.7f}", f"{alt_home:.1f}", f"{alt_sea:.1f}", facing])
    log(f"Position saved: {lat:.7f}, {lon:.7f} at {alt_home:.1f} m, heading {facing} deg")


def photo_and_location(drone, folder, camera_cmd):
    image_name = "photo.jpg"
    os.makedirs(folder, exist_ok=True)
    # Note the position first: the drone is hovering still, and the camera takes a moment.
    save_location(drone, folder, image_name)
    program = take_photo(os.path.join(folder, image_name), camera_cmd)
    log(f"Photo saved with {program}: {os.path.join(folder, image_name)}")


# ---- flight ----------------------------------------------------------

def preflight_checks(drone):
    if drone.position() is None:
        raise MissionError("no position from the Pixhawk yet")
    gps = drone.state.get("GPS_RAW_INT")
    if gps is None or gps.fix_type < 3:
        raise MissionError("no 3D GPS fix")
    if gps.satellites_visible < MIN_SATELLITES:
        raise MissionError(f"only {gps.satellites_visible} satellites, need {MIN_SATELLITES}")
    if drone.armed:
        raise MissionError("the drone is already armed - start the mission from the ground, disarmed")


def fly(drone, alt, folder, camera_cmd):
    log("Switching to GUIDED")
    if not drone.set_mode("GUIDED"):
        raise MissionError(f"could not switch to GUIDED (still {drone.mode})")

    log("Arming")
    result = drone.set_armed(True)
    if result != mavlink.MAV_RESULT_ACCEPTED:
        raise MissionError(f"arming refused: {result_text(result)}")

    log(f"Taking off to {alt:g} m")
    result = drone.command(mavlink.MAV_CMD_NAV_TAKEOFF, p7=alt)
    if result != mavlink.MAV_RESULT_ACCEPTED:
        raise MissionError(f"takeoff refused: {result_text(result)}")
    next_report = 0
    def climbed():
        nonlocal next_report
        current = drone.position()[2]
        if time.time() > next_report:
            log(f"  altitude {current:.1f} m")
            next_report = time.time() + 2
        return current >= alt * 0.95
    wait_for(drone, f"altitude {alt:g} m", climbed, timeout=alt * 3 + 30)
    log("Reached altitude")

    log("Turning to face north")
    # CONDITION_YAW: angle 0 = north, 20 deg/s, shortest way round, absolute angle
    drone.command(mavlink.MAV_CMD_CONDITION_YAW, 0, 20, 0, 0)
    wait_for(drone, "facing north", lambda: heading(drone) is not None and heading_error(heading(drone)) <= NORTH_TOLERANCE, 30)

    log("Waiting for a steady hover")
    try:
        def steady():
            hud = drone.state.get("VFR_HUD")
            return hud is not None and hud.groundspeed < 0.5 and abs(hud.climb) < 0.3
        wait_for(drone, "steady hover", steady, 10)
    except MissionError:
        log("Not perfectly steady after 10 s - taking the photo anyway")
    time.sleep(1)

    photo_and_location(drone, folder, camera_cmd)


def land(drone):
    log("Landing")
    if not drone.set_mode("LAND"):
        log(f"Could not switch to LAND (mode is {drone.mode}) - the pilot must land it!")
        return
    deadline = time.time() + 180
    while drone.armed and time.time() < deadline:
        if drone.mode != "LAND":
            log(f"Mode changed to {drone.mode} during landing - the pilot has control.")
            return
        time.sleep(0.5)
    log("Landed and disarmed." if not drone.armed else "Still armed after 3 minutes - check the drone!")


def main():
    parser = argparse.ArgumentParser(description="Photo mission: take off, face north, photo + GPS, land.")
    parser.add_argument("--alt", type=float, default=20, help="altitude in metres above home (default 20)")
    parser.add_argument("--photo-only", action="store_true", help="don't fly: just take a photo and save the position")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help=f"MAVLink address (default {DEFAULT_ADDRESS})")
    parser.add_argument("--camera-cmd", help="camera command to use instead of auto-detect, with {file} for the output path")
    opts = parser.parse_args()
    if not 2 <= opts.alt <= 50:
        parser.error("--alt must be between 2 and 50 m")

    folder = os.path.join(FLIGHTS_DIR, time.strftime("%Y-%m-%d_%H-%M-%S"))
    suffix = 2
    while os.path.exists(folder):  # two runs in the same second
        folder = os.path.join(FLIGHTS_DIR, time.strftime("%Y-%m-%d_%H-%M-%S") + f"_{suffix}")
        suffix += 1

    log(f"Connecting to {opts.address}")
    drone = Drone(normalise_address(opts.address), source_component=SOURCE_COMPONENT)
    try:
        drone.connect()
    except Exception as exc:
        log(f"Could not connect: {exc}. Is the bridge running? systemctl status mars-drone-bridge")
        return 1
    log(f"Connected to {drone.firmware}, mode {drone.mode}")
    # Position and GPS messages only start arriving a moment after connecting.
    drone.wait_until(lambda: drone.position() is not None and "GPS_RAW_INT" in drone.state, 10)

    try:
        if opts.photo_only:
            if drone.position() is None:
                raise MissionError("no position from the Pixhawk yet")
            photo_and_location(drone, folder, opts.camera_cmd)
            return 0

        preflight_checks(drone)
        print()
        print(f"Mission: take off to {opts.alt:g} m, face north, take a photo, save GPS, land.")
        print("The RC pilot must be holding the transmitter, ready to take over.")
        if not ask_yes("Propellers will spin and the drone will fly."):
            log("Mission cancelled.")
            return 1
        fly(drone, opts.alt, folder, opts.camera_cmd)
        land(drone)
        log(f"Mission complete. Files are in {folder}")
        return 0
    except PilotTookOver as exc:
        log(f"Mode changed to {exc} - the pilot has control. Mission stopped, no more commands sent.")
        return 2
    except (MissionError, KeyboardInterrupt, EOFError) as exc:
        reason = "stopped with Ctrl+C" if isinstance(exc, KeyboardInterrupt) else str(exc)
        log(f"Mission aborted: {reason}")
        if drone.armed and drone.mode == "GUIDED":
            land(drone)
        return 1
    finally:
        drone.close()


if __name__ == "__main__":
    sys.exit(main())
