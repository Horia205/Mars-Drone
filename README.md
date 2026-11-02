# MARS drone control

Control the drone's Pixhawk from a laptop terminal, through the Raspberry Pi on the drone.

```
 LAPTOP                         RASPBERRY PI 4 (on drone)              PIXHAWK
 drone_console.py  ── WiFi ──►  mars-drone-bridge service   ── GPIO ──►  flight controller
 (you type commands)   TCP 5760  (MAVProxy, forwards MAVLink)   serial
```

- **Laptop** runs `drone_console.py`: you type `arm`, `takeoff 5`, `land`, ... and it shows what the drone answers.
- **Pi** only forwards MAVLink between the Pixhawk's serial wire and the network. It starts by itself at boot.
- MAVLink is sent with **pymavlink**. We use it instead of DroneKit because DroneKit is unmaintained and its released version crashes on Python 3.10+. pymavlink is the library DroneKit is built on.

| File | What it is |
|---|---|
| `drone_console.py` | The console you run on the laptop |
| `pi/setup_pi.sh` | One-time Pi setup: serial port, MAVProxy, auto-start service |
| `pi/check_pixhawk.py` | Finds which port/baud the Pixhawk is on, and whether it runs ArduPilot or PX4 |
| `sim/run_sim.sh` | Runs a simulated drone on the laptop for practice |

---

## 1. Laptop setup (once)

In the Linux/WSL terminal:

```bash
sudo apt install -y python3-venv python3-pip
cd ~/mars-drone
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Practise on the simulator (no hardware)

Terminal 1:

```bash
bash sim/run_sim.sh
```

Terminal 2:

```bash
.venv/bin/python drone_console.py tcp:127.0.0.1:5760
```

Then try: `status`, `mode GUIDED`, `arm`, `takeoff 5`, `goto 10 0`, `watch`, `rtl`.

## 3. Wire the Pixhawk to the Pi

Use the Pixhawk's **TELEM2** port:

| Pixhawk TELEM2 | Raspberry Pi 4 |
|---|---|
| TX | pin 10 (GPIO 15, RXD) |
| RX | pin 8 (GPIO 14, TXD) |
| GND | pin 6 (GND) |
| 5V (red) | **do not connect** |

TX goes to RX and RX goes to TX (crossed). Power the Pi from its own 5 V / 3 A supply or BEC. The Pixhawk's TELEM port can't power a Pi 4.

On the Pixhawk (set with Mission Planner / QGroundControl over USB):
- **ArduPilot:** `SERIAL2_PROTOCOL = 2` (MAVLink2), `SERIAL2_BAUD = 57` (57600)
- **PX4:** `MAV_1_CONFIG = TELEM 2`, `SER_TEL2_BAUD = 57600`

## 4. Pi setup (once)

You can watch the Pi's terminal through VNC Viewer.

1. **Get this repository onto the Pi.** Either `git clone <repo-url> ~/mars-drone` on the Pi, or copy it from the laptop:
   ```bash
   rsync -av --exclude .venv ~/mars-drone/ <user>@<pi-ip>:~/mars-drone/
   ```
2. On the Pi:
   ```bash
   cd ~/mars-drone
   sudo bash pi/setup_pi.sh
   sudo reboot
   ```
3. After the reboot, find the Pixhawk:
   ```bash
   sudo systemctl stop mars-drone-bridge
   /opt/mars-drone/venv/bin/python ~/mars-drone/pi/check_pixhawk.py
   ```
   It prints the firmware (ArduPilot or PX4) and the `SERIAL_PORT` / `BAUD` to use. If they differ from the defaults, edit them with `sudo nano /etc/default/mars-drone`.
4. Start the bridge and check it's running:
   ```bash
   sudo systemctl start mars-drone-bridge
   systemctl status mars-drone-bridge
   ```
   To see its log, run `journalctl -u mars-drone-bridge -f`.

## 5. Network: a fixed IP for the Pi

On the Pi:

```bash
ip -br addr show wlan0
cat /sys/class/net/wlan0/address
```

The first line shows the Pi's current IP. The second shows its MAC address (the `ether` value in `ifconfig`).

Give the MAC address to the Help Desk and ask for:
1. **a static IP / DHCP reservation** for that MAC, and
2. confirmation that **devices on the WiFi can talk to each other** (no "client isolation"). Without this, the laptop can't reach the Pi.

If the network blocks device-to-device traffic, the fallback is to make the Pi its own WiFi hotspot.

Quick test from the laptop:

```bash
ping <pi-ip>
```

## 6. Fly

```bash
.venv/bin/python drone_console.py <pi-ip>
```

(A bare IP means `tcp:<pi-ip>:5760`. You can also set `export DRONE_ADDRESS=<pi-ip>` once.)

Port 5761 is a second free port, so Mission Planner / QGroundControl can connect at the same time (TCP, `<pi-ip>`, 5761).

### Commands

| Command | Does |
|---|---|
| `status` | mode, armed, battery, GPS, altitude, link |
| `watch` | live altitude/speed every second (Ctrl+C stops watching) |
| `modes` / `mode <NAME>` | list / change flight mode |
| `arm` / `disarm` | asks for confirmation; disarm warns if airborne |
| `takeoff <m>` | climb to that height above home (switches to GUIDED) |
| `goto <north> <east> [up]` | move by metres from the current position |
| `speed <m/s>` | horizontal speed for goto |
| `land` / `rtl` | land here / return home and land |
| `param <NAME> [value]` | read or set an autopilot parameter |
| `quit` | close the console (the drone keeps its current mode) |

ArduPilot disarms by itself about 10 s after arming if you don't take off, so type `arm` and then `takeoff` straight away.

### Safety order

1. Simulator first.
2. **Propellers off** on the bench: `status`, `mode`, `arm`, `disarm` only.
3. First real flights outdoors, with a pilot holding the RC transmitter who can take over. Failsafes (RC loss, battery, geofence) must be set on the Pixhawk.

## Option B: run the console on the Pi itself

If the network setup fails, SSH (or VNC) into the Pi and talk to the Pixhawk directly:

```bash
sudo systemctl stop mars-drone-bridge
/opt/mars-drone/venv/bin/python ~/mars-drone/drone_console.py /dev/serial0 --baud 57600
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `Could not connect ... no heartbeat` from the laptop | Pi IP right? `ping` works? `systemctl status mars-drone-bridge` on the Pi |
| `check_pixhawk.py` finds nothing | Pixhawk powered, TX/RX crossed, `SERIAL2_PROTOCOL = 2`, rebooted after `setup_pi.sh` |
| `Arm: FAILED` | Read the `[drone] PreArm: ...` line above it: it says why (GPS, calibration, RC...) |
| `[link] WARNING: no heartbeat` | WiFi range or the Pi lost power |
