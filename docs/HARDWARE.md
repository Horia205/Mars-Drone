# MARS drone hardware

This guide covers wiring, power, Pixhawk calibration and the physical checks. Do all of it before the first real flight. For the software steps, see [FLYING.md](FLYING.md).

**What we know about our drone:**
- Pixhawk **2.4.8**, running **ArduCopter 4.7.2**, frame **QUAD/X**.
- Raspberry Pi 4 (hostname `HS9`), connected to the Pixhawk by **USB**.
- GPS working: 20 satellites seen on the bench.

---

## 1. Pixhawk ↔ Pi connection

### Option A: USB cable (what we use now)

- A micro-USB cable from the Pixhawk's side USB port to one of the Pi's USB ports. It must be a **data** cable, not one meant only for charging.
- **Secure it** with tape or a zip tie at both ends. Vibration can shake a USB plug loose in flight.
- Bridge setting: `SERIAL_PORT=/dev/ttyACM0`, or the `/dev/serial/by-id/...` name the setup picks.

### Option B: TELEM2 to the GPIO pins

The Pixhawk's TELEM2 is already set up for this: `SERIAL2_PROTOCOL = 2`, `SERIAL2_BAUD = 57` (57600 baud).

The TELEM2 plug has 6 pins. Pin 1 is red; counting from it:

| TELEM2 pin | Signal | Raspberry Pi |
|---|---|---|
| 1 (red) | 5V | **not connected** |
| 2 | TX (out) | pin 10, GPIO 15 RXD |
| 3 | RX (in) | pin 8, GPIO 14 TXD |
| 4, 5 | CTS, RTS | not connected |
| 6 | GND | pin 6, GND |

To switch the bridge to it, on the Pi:

```bash
sudo sed -i 's|^SERIAL_PORT=.*|SERIAL_PORT=/dev/serial0|' /etc/default/mars-drone
```

```bash
sudo systemctl restart mars-drone-bridge
```

## 2. Powering the Pi on the drone

- A Pi 4 needs **5 V, 3 A**. Use a separate **5 V BEC** (a small voltage regulator) fed from the flight battery.
- Connect the BEC to the Pi's USB-C port, or to GPIO pin 2/4 (5V) and pin 6 (GND).
- **Don't** power the Pi from the Pixhawk's TELEM port or its power-module connector. They can't supply enough current, and the Pixhawk can brown out (lose power and reset).
- Check the power is good, on the Pi:

  ```bash
  vcgencmd get_throttled
  ```

  `throttled=0x0` means fine. Anything else means the supply is too weak.

## 3. The antenna on the Pi

We still don't know what it is. On the Pi, run `lsusb` and `ip -br link`:
- `wlan1` in the list, or an `802.11` device, means a **USB WiFi stick**. It gives better range to the laptop. It just needs to be connected to the same WiFi network as the laptop.
- A `CP210x` or `FTDI` device means a **telemetry radio**. It needs a partner radio, plugged into the laptop.

## 4. Pixhawk calibration and settings

Use **Mission Planner** (Windows) or **QGroundControl** on the laptop. There's no need to take anything off the drone, because they can connect **through the Pi**: connection type **TCP**, address `<pi-ip>`, port **5761**.

Do these in order. Each one fixes one of the `PreArm` messages we saw.

| Step | Where | Fixes |
|---|---|---|
| Accelerometer calibration | Setup → Mandatory Hardware → Accel Calibration | — |
| Compass calibration (rotate the drone in every direction) | Setup → Compass | `PreArm: Compass 3 not found`. If it persists, untick unused compasses in the Compass page |
| Bind RC receiver to the transmitter, then Radio Calibration | Setup → Radio Calibration | `PreArm: RC not found` |
| Flight modes on a transmitter switch: STABILIZE, LOITER, LAND (team choice) | Setup → Flight Modes | the pilot's way to take over |
| ESC calibration | Setup → ESC Calibration | motors start together |
| Battery monitor (pick the power module, set cell count) | Setup → Battery Monitor | `PreArm: Battery 1 low voltage failsafe` (also needs the real battery in) |
| Failsafes: radio loss → RTL, low battery → RTL or LAND | Setup → Failsafe | — |

**Safety switch:** before arming, press and hold the Pixhawk's flashing safety button until it stays solid. That clears `PreArm: Hardware safety switch`. Disabling it with `BRD_SAFETY_DEFLT = 0` is a team decision.

## 5. Props-off bench test (do before the first flight)

1. **Take all propellers off.**
2. Battery in, RC transmitter on, drone on the ground or a bench.
3. From the laptop console:

   ```
   status          (no PreArm messages, GPS fix, battery OK)
   mode GUIDED
   arm             (motors should spin slowly)
   disarm
   ```

4. In Mission Planner → Setup → Optional Hardware → **Motor Test**, check every motor spins **in the right direction** and in the right order for QUAD/X.

## 6. Pre-flight checklist (every flight)

- [ ] Propellers tight and on the correct motors (CW/CCW)
- [ ] Battery fully charged and strapped down
- [ ] GPS mast firm, pointing up, clear sky view
- [ ] Pixhawk ↔ Pi USB cable and Pi power cable secured
- [ ] Nothing loose that could hit the propellers
- [ ] Open area, people at a safe distance, and flying allowed there
- [ ] RC transmitter **on first**, pilot holding it, flight-mode switch checked
- [ ] Then follow [FLYING.md](FLYING.md), Part 3
