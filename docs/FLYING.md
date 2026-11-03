# Flying the MARS drone from the laptop

This guide is software only: putting the code on the drone's Raspberry Pi, starting everything and flying from the laptop's terminal. Wiring, calibration and the physical checks are in [HARDWARE.md](HARDWARE.md). **Do the hardware guide first, before any real flight.**

In the commands below, replace:
- `<pi-ip>` with the Pi's address. It's currently `10.124.114.17`; on the Pi, `hostname -I` shows it.
- `EngineeringStudent` with the Pi's username, if your Pi uses a different one.

Laptop commands work in **PowerShell** (from `C:\Users\hp570684\Mars-Drone`) or in the **Ubuntu** window (from `~/mars-drone`, using `.venv/bin/python` instead of `python`).

---

## Part 1: Put the software on the drone's Pi (once)

Everything goes over SSH, so the Pi can stay on the drone.

**Quick way: one command from the laptop.** Power the drone on, so the Pixhawk is on and plugged into the Pi, then in PowerShell run:

```bash
ssh -t EngineeringStudent@<pi-ip> "git clone https://github.com/Horia205/Mars-Drone.git ~/mars-drone 2>/dev/null || git -C ~/mars-drone pull; cd ~/mars-drone && sudo bash pi/setup_pi.sh && sudo reboot"
```

It asks for the Pi's password twice: once to log in and once for `sudo`. It downloads (or updates) the code, runs the setup and restarts the Pi. After about a minute, do step 6 below to check it worked. If something fails, or the Pi has no internet, follow the steps one by one instead.

**1. Log in to the Pi** from the laptop:

```bash
ssh EngineeringStudent@<pi-ip>
```

Every command until step 7 runs on the Pi.

**2. Check the Pi has internet.** The setup downloads software.

```bash
ping -c 3 github.com
```

If there are no replies, connect the Pi to a network with internet, e.g. your phone's hotspot. Run `sudo nmtui`, choose **Activate a connection**, pick the network and enter its password.

**3. Download the code:**

```bash
git clone https://github.com/Horia205/Mars-Drone.git ~/mars-drone
```

If it says the folder already exists, update it instead: `cd ~/mars-drone && git pull`.

> Can't use git on the Pi? Copy the code from the laptop instead. In PowerShell on the laptop:
> `scp -r C:\Users\hp570684\Mars-Drone EngineeringStudent@<pi-ip>:~/mars-drone`
> The setup in step 5 still needs internet, for its software packages.

**4. Make sure the Pixhawk is powered and its USB cable is plugged into the Pi.** The setup detects it.

**5. Run the setup.** `sudo` asks for the Pi's password.

```bash
cd ~/mars-drone && sudo bash pi/setup_pi.sh
```

```bash
sudo reboot
```

**6. Check it worked.** Wait about a minute, SSH in again (step 1), then run:

```bash
systemctl is-active mars-drone-bridge
```

It should print `active`. If it doesn't, find the Pixhawk:

```bash
sudo systemctl stop mars-drone-bridge
```

```bash
/opt/mars-drone/venv/bin/python ~/mars-drone/pi/check_pixhawk.py
```

Put the port it prints into the settings (`sudo nano /etc/default/mars-drone`), then:

```bash
sudo systemctl start mars-drone-bridge
```

**7. Laptop (once).** Already done on the current laptop. On another laptop, install Python and Git, clone the repository, then:

```bash
python -m pip install pymavlink
```

### Updating the Pi later

When the code on GitHub changes, run this on the Pi. Re-running the setup is safe: it keeps your settings.

```bash
cd ~/mars-drone && git pull && sudo bash pi/setup_pi.sh && sudo systemctl restart mars-drone-bridge
```

> **Pi HS9 (already set up): run this update once now.** It turns off a bridge heartbeat that would stop the Pixhawk from noticing a lost laptop link (Part 2).

---

## Part 2: Safety settings in the Pixhawk (once)

Connect the console (Part 3, steps 1–3) and type these. The values stay saved on the Pixhawk.

**Return home if the laptop link is lost:**

```
drone> param FS_GCS_ENABLE 1
```

The console tells the Pixhawk once a second that it is still connected. With this setting, if that stops for 5 seconds, because of WiFi dropping, the laptop sleeping or the console being closed, the drone flies home and lands. **So don't close the console while the drone is flying.**

**Settings to agree with your team.** Check each one with `param <NAME>`:

| Parameter | What it is | Example |
|---|---|---|
| `FENCE_ENABLE` | Geofence on (1) / off (0) | `1` |
| `FENCE_ALT_MAX` | Highest allowed altitude, metres | `30` |
| `FENCE_RADIUS` | Furthest allowed distance from home, metres | `100` |
| `RTL_ALT` | Height it climbs to before flying home, in **centimetres** | `1500` (15 m) |

---

## Part 3: Every flight

**1. Power on the drone** (battery in). Wait about 1 minute: the Pi starts up and the bridge starts by itself.

**2. Check the bridge is running.** On the laptop:

```bash
ssh EngineeringStudent@<pi-ip> systemctl is-active mars-drone-bridge
```

It should print `active`. If the Pi's IP has changed and you can't connect, try `ssh EngineeringStudent@HS9.local`. A **static IP from the Help Desk** fixes this for good.

**3. Start the console:**

```bash
python drone_console.py <pi-ip>
```

You should see `Connected to ArduPilot, system id 1`.

**4. Pre-flight check:**

```
drone> status
```

| Line | Must show |
|---|---|
| Link | last heartbeat under 1 s |
| GPS | `3D fix` or better, **10+ satellites** |
| Battery | the voltage of a fully charged battery |
| Armed | `no` |

Also, no `[drone] PreArm: ...` messages should keep appearing. If they do, see HARDWARE.md.

**5. Fly.** For a first flight, use a low height and short moves:

```
drone> mode GUIDED
drone> arm
       Type 'yes' to continue: yes
drone> takeoff 3
drone> watch                 (Ctrl+C to stop watching)
drone> goto 5 0              (5 m north)
drone> goto 0 5              (5 m east)
drone> speed 2               (slower moves from now on)
drone> rtl                   (fly back to the launch point and land)
```

- Type `takeoff` **within 10 seconds of `arm`**, or ArduPilot disarms by itself.
- `goto <north> <east> [up]` is in metres from where the drone is now; negative numbers go south, west or down.
- `land` lands where it is, and `rtl` flies home first.
- After landing, the drone disarms by itself. Check with `status`: it should say `Armed: no`.

**6. Finish:**

```
drone> quit
```

Then shut the Pi down cleanly before unplugging the battery. This protects its SD card.

```bash
ssh -t EngineeringStudent@<pi-ip> sudo poweroff
```

Wait about 20 seconds, then unplug the battery.

---

## Part 4: Photo mission (take off to 20 m, face north, photo + GPS, land)

`mission.py` does the whole flight by itself:
1. switch to GUIDED, arm and climb to 20 m;
2. turn to face **north**, wait for a steady hover;
3. save the GPS position to a text file and take a photo;
4. land and disarm.

It runs **on the Pi**, because the camera is there and it keeps working if the laptop goes out of WiFi range.

**Only fly it after every test in [BENCH_TEST.md](BENCH_TEST.md) passes,** with a pilot on the RC transmitter.

**Before the first mission:**
- `param FENCE_ALT_MAX` must be **above 20** if the fence is on, or the fence stops the climb.
- Calibrate the compass ([HARDWARE.md](HARDWARE.md)); "north" is only as good as the compass.
- The photo's top edge points north only if the camera is mounted facing the drone's nose (front). Check how it is mounted.
- Test the camera on the ground first: `mission.py --photo-only` ([BENCH_TEST.md](BENCH_TEST.md), test 7).

**1. Do Part 3, steps 1–4** (power on, check the bridge, pre-flight `status`). Leave the console open to watch; just don't type commands while the mission runs.

**2. Open a tmux window on the Pi** from a second PowerShell window:

```bash
ssh -t EngineeringStudent@<pi-ip> tmux new -A -s mission
```

**3. Start the mission:**

```bash
cd ~/mars-drone && /opt/mars-drone/venv/bin/python mission.py
```

It checks GPS (3D fix, 8+ satellites), then asks you to type `yes`. Use `--alt 10` for another altitude.

**4. While it flies:**
- It prints each step: altitude, turning north, `Position saved`, `Photo saved`, `Landing`, `Landed and disarmed`.
- **Ctrl+C** in the tmux window stops the mission and lands.
- **The pilot can take over at any moment** with the mode switch. The script then stops sending commands.
- If the SSH window closes, the mission carries on. Run the step 2 command again to see it.

**5. Get the photos and positions onto the laptop.** In PowerShell, from the folder you want them in:

```bash
scp -r EngineeringStudent@<pi-ip>:~/mars-drone/flights .
```

Each mission makes a folder `flights/<date>_<time>/` containing:
- `photo.jpg`;
- `gps.txt`: latitude, longitude, altitude, heading and GPS quality at the moment of the photo.

`flights/photos.csv` lists every photo with its coordinates. It opens in Excel and imports into mapping tools.

---

## If something goes wrong

1. **The pilot on the RC transmitter takes over.** Flipping the flight-mode switch, e.g. to LOITER or STABILIZE, overrides the laptop immediately. This is the main safety net, so always fly with a pilot holding the transmitter.
2. **From the console:** type `land` or `rtl`.
3. **`[link] WARNING: no heartbeat`:** the WiFi link dropped. If `FS_GCS_ENABLE` is 1, the drone starts coming home after 5 seconds. Restart the console to see what it is doing. Let it come home rather than sending new commands.
4. **Never type `disarm` while it is flying:** the motors stop and it falls. The console asks for confirmation if it thinks the drone is in the air.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Could not connect ... no heartbeat` | Wrong IP? Is the bridge `active` (Part 3, step 2)? Is the Pixhawk powered? |
| `Connection refused` | The bridge isn't running: `ssh -t EngineeringStudent@<pi-ip> sudo systemctl restart mars-drone-bridge` |
| `Arm: FAILED` | Read the `[drone] PreArm: ...` lines above it; they say why. See HARDWARE.md |
| `Takeoff: FAILED` | Armed? GPS fix? It needs GUIDED mode, which the console switches to by itself |
| Bridge log | `ssh EngineeringStudent@<pi-ip> journalctl -u mars-drone-bridge -n 30 --no-pager` |

All console commands: type `help`, or see the table in the [README](../README.md).
