# Props-off test: can we always get manual control back?

From the professor's instructions: *load the script onto the drone's Raspberry Pi, test it without propellers, and check that we can regain manual control under all conditions, both with the Pi in range and out of range.* Only after every test below passes do we fly the photo mission ([FLYING.md](FLYING.md), Part 4).

## How control works (read this first)

There are three separate links:

```
 RC transmitter ──radio──► Pixhawk ◄──USB── Raspberry Pi ◄──WiFi── Laptop
 (the pilot: MANUAL)                       (mission.py)            (drone_console.py)
```

- **Manual control is always the RC transmitter.** It talks to the Pixhawk by its own radio, not through the Pi or the laptop.
- When the pilot flips the transmitter's **flight-mode switch**, ArduPilot changes mode at once, whatever the laptop or Pi asked for. Our scripts check for this and stop sending commands.
- ArduPilot only reacts when the switch **moves**. If the switch is already sitting in the position you want, flip it away and back.

What happens when a link is lost:

| Situation | What the drone does | Pilot can take over? |
|---|---|---|
| Laptop out of WiFi range, flying from the **console** | After 5 s: GCS failsafe → returns home and lands (needs `FS_GCS_ENABLE = 1`) | Yes, any time |
| Laptop out of range, **mission.py** running on the Pi | Carries on with the mission: it runs on the Pi, which is on the drone | Yes, any time |
| Pi crashes or mission.py is killed | After 5 s: GCS failsafe → returns home and lands | Yes, any time |
| RC transmitter out of range or switched off | Radio failsafe → returns home and lands (`FS_THR_ENABLE`) | When the RC link is back |
| On the ground, armed, when a failsafe hits | **Disarms** (motors stop), or switches to RTL/LAND and then disarms | — |

On the bench the drone is on the ground, so a failsafe makes it **disarm**. In the air the same failsafe makes it **return home and land**. A disarm on the bench means the failsafe works.

---

## Before you start

1. **Remove all propellers.**
2. Put the drone outside, or near a window, so it gets a GPS fix. Plug the battery in and turn the RC transmitter on.
3. Update the Pi to the latest code ([FLYING.md](FLYING.md), "Updating the Pi later").
4. Connect the console from the laptop (`python drone_console.py <pi-ip>`) and set or check:
   ```
   drone> param FS_GCS_ENABLE 1
   drone> param FS_GCS_TIMEOUT
   drone> param FS_THR_ENABLE
   ```
   `FS_GCS_TIMEOUT` should be about 5. `FS_THR_ENABLE` should be 1 (return home); if it's 0, set it with `param FS_THR_ENABLE 1`. If the RC receiver isn't bound yet, do that first ([HARDWARE.md](HARDWARE.md), section 4).
5. `status` must show a GPS fix and no repeating `PreArm` messages.

Without propellers, `takeoff` makes the motors spin up but the drone stays on the ground. That's what we want: the motors spinning show that ArduPilot thinks it's flying.

---

## The tests

Write the result of each test in the table at the end.

### Test 1: Pilot takes over from the console (laptop in range)

```
drone> mode GUIDED
drone> arm
drone> takeoff 3
```

When the motors are spinning, **the pilot flips the mode switch** (e.g. to STABILIZE or LOITER).
**Pass:** the console prints the new mode (`status` confirms it), and the laptop no longer controls the drone. The pilot then disarms: throttle down, rudder (yaw) left.

### Test 2: Laptop goes out of range while the console controls the drone

Arm and `takeoff 3` as in test 1. When the motors spin, **turn off the laptop's WiFi**. This is the same as the laptop going out of range.
**Pass:** within about 5 seconds the GCS failsafe triggers: the motors stop, or the mode changes to RTL/LAND and the motors stop shortly after. Turn the WiFi back on, reconnect the console, and check `status` says `Armed: no`.

**2b:** repeat, but the pilot flips the mode switch **straight after** the WiFi goes off.
**Pass:** the pilot has control: the mode follows the switch.

### Test 3: Pilot takes over from the mission script

Start the mission on the Pi (see "Running the mission" below) with a low altitude, so a props-off run doesn't take long:

```bash
/opt/mars-drone/venv/bin/python mission.py --alt 2
```

When the motors spin, the pilot flips the mode switch.
**Pass:** the script prints `Mode changed to ... - the pilot has control. Mission stopped`. The pilot disarms.

### Test 4: Laptop goes out of range while the mission runs

Start the mission (`--alt 2`), then **turn off the laptop's WiFi**.
**Pass:**
- The motors keep running: the mission continues on the Pi.
- The pilot can still take over with the switch.
- With no props it never reaches the altitude, so after about 40 seconds the script gives up and lands (disarms).

Turn WiFi back on, SSH in and run `tmux attach -t mission` to read what the script printed.

### Test 5: The Pi stops controlling the drone

Start the mission (`--alt 2`). From a **second** PowerShell window, kill the script without letting it clean up:

```bash
ssh EngineeringStudent@<pi-ip> pkill -9 -f mission.py
```

**Pass:** within about 5 seconds the GCS failsafe triggers, as in test 2. This is what happens if the Pi crashes in flight.

### Test 6: RC transmitter out of range

Arm and `takeoff 3` from the console. When the motors spin, **switch off the RC transmitter**.
**Pass:** the console shows a `Radio Failsafe` message, and the motors stop (directly, or after switching to RTL/LAND). Turn the transmitter back on.

### Test 7: Camera and GPS log (no flying)

On the Pi (in the `~/mars-drone` folder):

```bash
/opt/mars-drone/venv/bin/python mission.py --photo-only
```

**Pass:** it prints `Photo saved` and `Position saved`, and `~/mars-drone/flights/<date-time>/` holds `photo.jpg` and `gps.txt`. Copy them to the laptop to look at them (FLYING.md, Part 4).

If it says *no camera program found* or every camera program fails, check which camera it is:
- `rpicam-hello --list-cameras` for a Pi camera on the ribbon cable;
- `lsusb` for a USB webcam.

Send the output to whoever maintains the code.

---

## Running the mission on the Pi

The mission runs inside **tmux**, so it keeps going if the SSH connection drops. From the laptop:

```bash
ssh -t EngineeringStudent@<pi-ip> tmux new -A -s mission
```

That opens (or re-opens) a tmux window called `mission` on the Pi. In it:

```bash
cd ~/mars-drone && /opt/mars-drone/venv/bin/python mission.py --alt 2
```

| To... | Do |
|---|---|
| leave it running and close SSH | press **Ctrl+B**, then **D** |
| look at it again later | run the same `ssh -t ... tmux new -A -s mission` command |
| stop the mission (it lands) | **Ctrl+C** in the tmux window |

---

## Results

| Test | Pass / Fail | Notes |
|---|---|---|
| 1 Pilot takeover from console | | |
| 2 Laptop out of range (console) | | |
| 2b Pilot takeover after laptop lost | | |
| 3 Pilot takeover from mission | | |
| 4 Laptop out of range (mission) | | |
| 5 Pi stops controlling | | |
| 6 RC transmitter out of range | | |
| 7 Camera + GPS log | | |

**Fly the real mission only when every row says Pass.**
