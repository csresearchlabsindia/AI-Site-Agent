# ASA — AI-Site-Agent

**Autonomous construction-site safety agent on Arduino UNO Q + OAK-D**
Team CSRL · CS Research Labs, Chennai · Unit ASA-01

Built for *Invent the Future with Arduino UNO Q and App Lab*.

**Source:** https://github.com/csresearchlabsindia/AI-Site-Agent

---

## The problem

On an Indian construction site, PPE compliance is checked by a supervisor walking
around and looking. It happens a few times a shift, it is not recorded, and by the
time a violation is written down the worker has moved on. Nobody has a defensible
record of who was unprotected, where, and when.

ASA is a mobile unit that does that walk continuously. It sees people, checks
whether a hard hat is actually *on their head* rather than merely in frame, speaks
to the worker in their own language, and writes a timestamped evidence record.

---

## What it actually does

**Sees.** An OAK-D runs two neural networks on its Myriad X VPU — YOLOv6-nano for
people, YOLOv8n for hard hats — at around 8 FPS, with stereo depth giving each
detection a 3D position.

**Judges.** A hat detected anywhere in frame is not compliance. ASA applies an
on-head geometric rule using the 3D coordinates of the hat relative to the person's
head region, and requires the condition to hold for 7 of 10 consecutive frames
before calling a violation. Detection zone is 0.3–2.8 m, which is conversational
distance on a site.

**Speaks.** Piper TTS synthesises alerts locally, no cloud. Site language is
switchable between English, Tamil, Hindi and French. A worker without a hat hears a
spoken warning in the language of the site; a worker who puts one on gets
acknowledged.

**Listens.** An Edge Impulse keyword-spotting model listens for "hey arduino" on a
USB mini microphone and triggers a live spoken status report.

**Shows.** The UNO Q's LED matrix carries an animated face — eyes that scan the
site when idle, a talking face while speaking, a thinking state during synthesis
gaps, and a hard hat with a tick or cross on compliance events. A worker can read
the machine's state from across a room without looking at a screen.

**Records.** Every violation writes a JPEG and a CSV row to an on-board evidence
trail, downloadable from the dashboard.

**Moves.** Four-wheel skid steer on a 2020 aluminium frame, commanded through
Arduino App Lab's RouterBridge from a hold-to-drive pad on the dashboard. First
drive under battery power on 1 October 2026; all four directions verified.

**Knows when it has been stopped.** The MCU senses the e-stop-switched motor rail
through a resistor divider. Press the mushroom and the dashboard shows a hazard
band within a second, the drive pad disarms, and the firmware refuses drive
commands until the button is released. The same reading gives live pack voltage
and a battery-life estimate.

---

## Architecture

The UNO Q's dual-brain design is what makes this work on one board.

**Qualcomm QRB2210 (Linux side)** runs three Python services: vision, voice, and
the App Lab Mission Control app. It holds the compliance logic, the evidence
trail, the TTS engine and the web dashboard.

**STM32U585 (MCU side)** runs the real-time work: the LED matrix animation loop at
50 Hz, and motor control with a hardware-level safety timer. Nothing about the
robot's ability to stop depends on Linux being healthy.

**Arduino App Lab RouterBridge** is the link. The Linux side calls
`set_status(code)` to drive the face and `drive(l, r)` to drive the wheels; the MCU
executes both without Linux in the loop.

```
OAK-D ──USB3──► QRB2210 (vision, voice, dashboard, power log)
                   │
                   ├─ WebUI :7000  ──► phone / laptop
                   ├─ Piper TTS ──► Bluetooth speaker
                   ├─ asa-powerlog :8096 ──► voltage CSV + chart
                   └─ RouterBridge
                          │
                   STM32U585 ──► LED face
                          ├────► 2× BTS7960 ──► 4× motor
                          ◄──── A0: motor-rail voltage (e-stop + pack V)
```

### Motor control and the safety argument

`drive(l, r)` takes signed PWM per side, −200 to +200. Three things sit between
that call and the wheels:

- **Deadman.** If no `drive()` arrives within 400 ms the MCU coasts on its own. A
  hung bridge, a crashed app or a closed browser tab all stop the robot without
  anything having to notice.
- **Slew limiting.** Output ramps to target over 250 ms rather than stepping,
  which protects the gearboxes and gives an operator time to react.
- **Shared hardware enable.** Both drivers' `R_EN`/`L_EN` tie to one pin. Dropping
  it disables both H-bridges regardless of what state the PWM registers are in.

- **E-stop latch.** A0 samples the switched motor rail every 20 ms. Three
  readings below 8 V latch `estop`, which calls `driveOff()` and makes `drive()`
  return false. The latch clears only when the rail is back above 9 V *and* no
  drive command has arrived for a second — so releasing the mushroom while an
  operator is still holding "forward" cannot make the robot lurch.

The deliberate choice worth naming: the emergency stop cuts *drive power only*, not
the compute branch. Hit it and the wheels die while ASA stays online and can report
that it was stopped. Killing everything would look identical to a crash and tell
the operator nothing. The e-stop on this build has only one contact block, and it
carries 12 V — so instead of a second contact to a GPIO, the firmware reads the
rail itself, which also happens to be the battery gauge.

---

## How ASA uses Arduino App Lab

App Lab is not a convenience here — it is the thing that makes a dual-brain board
usable by one person on a deadline. Four capabilities carried the build.

### One app, two processors

ASA's Linux-side Python and its STM32 sketch live in a single App Lab app at
`~/ArduinoApps/asa-mission-control`:

```
asa-mission-control/
├── python/main.py       ← QRB2210: vision polling, voice, dashboard API
├── sketch/sketch.ino    ← STM32U585: LED face + motor control
└── assets/index.html    ← dashboard served by the WebUI Brick
```

One command compiles the sketch, flashes the MCU, and restarts the Python side:

```bash
arduino-app-cli app restart user:asa-mission-control
```

That single restart is the whole edit-test loop. Setting the app as default means
it auto-starts on boot, which is most of why the unit is power-loss proof — there
is no separate service to forget to enable.

### WebUI Brick — the dashboard

The Mission Control dashboard on port 7000 is a WebUI Brick, a single
self-contained HTML file with no build step. The current design (October 2026)
puts what a site supervisor needs at a glance above the fold and everything else
behind four tabs:

- **Hero.** Site status as a full-colour block — green *All clear*, pulsing red
  *Violation* with reason and distance — beside the live OAK-D view. Three live
  figures under it: people in view, violations this session, AI frames/s.
- **Tile strip.** Pack voltage with a charge bar, estimated time left, drive
  state, site language and speaker. The three header pills (vision, speaker,
  power log) go green or red as each service answers.
- **E-stop band.** A black-and-yellow hazard stripe across the top of the page
  while the mushroom is pressed. The drive pad disarms with it.
- **Live** tab: event log and voice controls (language, volume, test alert).
- **Drive** tab: arm switch, speed slider, hold-to-drive pad, STOP AND DISARM,
  and left/right PWM plus pack voltage under load.
- **Power** tab: pack voltage over 1 / 4 / 24 hours with the 11.1 V and 10.5 V
  lines drawn in, drop over the last ten minutes, session low, time-to-10.5 V
  estimate, and a CSV download.
- **Evidence** tab: the gallery with filter chips, filmstrip viewer and audit CSV.

What made it more than a static page is `expose_api`, which registers a FastAPI
route and maps function arguments straight to query parameters. ASA exposes seven:

```python
ui.expose_api("GET", "/asa/status", api_status)
ui.expose_api("GET", "/asa/lang",   api_lang)
ui.expose_api("GET", "/asa/volume", api_volume)
ui.expose_api("GET", "/asa/test",   api_test)
ui.expose_api("GET", "/asa/drive",  api_drive)
ui.expose_api("GET", "/asa/halt",   api_halt)
ui.expose_api("GET", "/asa/power",  api_power)   # pack_v, estop
```

This gave a clean security boundary for free. The voice service on :8091 and the
vision service on :8090 stay bound privately; the browser only ever talks to App
Lab, which proxies. The header controls — speaker status, language selector, volume
slider, Test — all go through that path.

### Keyword Spotting Brick — hands-free operation

A worker on a site has gloves on and is holding something. The Keyword Spotting
Brick listens for `hey_arduino` on the USB mic and fires a callback:

```python
spotter = KeywordSpotting()
spotter.on_detect("hey_arduino", on_keyword)
```

That triggers a live Piper-synthesised status report, with an 8-second cooldown so
the robot cannot retrigger on its own voice. The Brick handled the audio pipeline
entirely — the only tuning needed was on the microphone itself (card 1, gain 8/16,
AGC off).

### RouterBridge — the real-time boundary

RouterBridge is how the Linux side reaches the MCU. The sketch registers handlers:

```cpp
Bridge.provide("set_status", set_status);
Bridge.provide("drive", drive);
Bridge.provide("halt", halt);
Bridge.provide("pack_v", pack_v);      // float, calibrated
Bridge.provide("estop", estop_state);  // bool, MCU-side latch
```

and Python calls them:

```python
Bridge.call("set_status", code)   # LED face state
Bridge.call("drive", li, ri)      # signed PWM per side
```

The split matters. Anything that must happen on time — the 50 Hz matrix refresh,
the 400 ms motor deadman, the output ramp — lives on the STM32 and keeps running
whether or not Linux is healthy. Anything that needs a filesystem, a network stack
or a neural network lives on the QRB2210. RouterBridge is the only seam, and it is
five function calls wide.

One practical note: `Bridge.call` is invoked from two threads in ASA — the main
loop pushing LED state every 250 ms, and the dashboard's API thread pushing drive
commands at up to 10 Hz. Both take a lock. Interleaved RPC frames on a single
transport is the kind of fault that surfaces as a random bridge hang an hour into a
demo.

### Edge Impulse integration

The keyword model comes from Edge Impulse. The scrap-classification FOMO model is
built on the same path — Roboflow CONVR2022 dataset → Edge Impulse → `.eim` build —
and runs as a fourth service pinned to cores 0–1, pulling frames over HTTP from the
vision service so it never contends with the two live nets on the OAK-D.

---

## Bill of materials

### Required by the contest

| Item | Note |
|---|---|
| **Arduino UNO Q** (4 GB) | QRB2210 + STM32U585, the whole compute platform |
| **Arduino App Lab** | Development environment, WebUI Brick, Keyword Spotting Brick, RouterBridge |

### Sensing and interaction

| Item | Note |
|---|---|
| Luxonis OAK-D | Stereo depth + Myriad X, runs both nets on-device |
| USB mini microphone | Card 1, gain 8/16, AGC off — 21.7 dB SNR at 30 cm |
| Zebronics ZEB-CLIPPER | Bluetooth A2DP speaker, mast-mounted |
| dr.com USB hub | OAK-D and mic |

### Drive and chassis

| Item | Qty |
|---|---|
| 2020 aluminium extrusion, 400 × 340 mm frame | — |
| Johnson 12 V 100 RPM geared motor | 4 |
| OE-28 encoder | 4 (fitted, not yet wired) |
| 130 mm off-road wheel, dished rim | 4 |
| BTS7960 / IBT-2 driver | 2 |
| Camera mast + OAK-D mount | 1 |

### Power

| Item | Note |
|---|---|
| ZOP 3S 2200 mAh 25C LiPo, XT60 | Single pack feeds both rails; 25C chosen for low internal resistance, not peak current |
| SmartElex TPS565201 buck, 5 V 3 A | Compute rail: UNO Q, OAK-D, hub, mic. 220 µF electrolytic on VIN against input ringing |
| Blade fuse + kill switch | Battery protection, before the rail split |
| E-stop, single contact block | Drive branch only |
| 33 kΩ + 10 kΩ / 10 kΩ, ¼ W | Rail-sense divider into A0 (measured 32.6 k, 9.93 k, 9.91 k) |

---

## Chassis design

The chassis was designed around one question: where does the motor go?

### The in-rim mount

The obvious layout puts the motors inboard, shafts through the rails, wheels
outboard. That was the first design — and it cost 165 mm of stack per side, forced
a 45 mm fore-aft stagger between left and right so the motor bodies could clear
each other across the frame, and made the internal deck a fight for space.

The 130 mm wheels have a deeply dished rim. Mounting the motor *inside* that
cavity, with the wheel spinning around a stationary motor and the bracket arm
reaching through the spoke aperture to the rail, removes most of that stack. The
wheel's own volume does the work.

Three consequences followed:

- **The stagger disappeared.** With the motors outboard in the rims, the internal
  gap is clear and the axles sit true. That means a real point turn and simpler
  odometry, instead of a spin that traces a small arc.
- **The deck cleared completely.** Battery, UNO Q, both drivers and the buck all
  sit in one plane with room to reach a connector without dismantling the robot.
- **The rail sits at axle height**, 65 mm, so the motor brackets bolt straight into
  the extrusion slot with no adapter plate. Ground clearance under the rail is
  55 mm, which is enough for site debris.

The honest trade: there's no bearing between rail and hub, so the wheel load is
cantilevered on the gearbox output bushing. Acceptable at this scale and duty, and
recorded as a known limitation rather than hidden.

### Geometry

| Parameter | Value |
|---|---|
| Frame | 2020 extrusion, 400 × 340 mm |
| Rails | 2 × 400 mm on 320 mm centres |
| Cross members | 2 × 300 mm at the axle stations |
| Wheelbase | 300 mm |
| Track | ~410 mm |
| Wheel | 130 mm, axle at 65 mm |
| Mast | 350 mm, 90 mm aft of frame centre |

**Wheelbase ÷ track = 0.73.** This is the number that decides whether a skid-steer
machine turns or fights itself. Above about 1.2 the wheels scrub badly and chew
tyres; at or below 1.0 it turns cleanly. A wider track is a longer moment arm, so
the wide stance actively helps — the cost is that yaw becomes sensitive to small
left/right mismatches, which is why the dashboard speed limit starts conservative.

The mast sits aft of centre deliberately. It puts the OAK-D behind the front wheels
so it sees over the nose rather than down at it, and it balances the battery, which
is forward on the deck.

### Wheel and travel figures

At 130 mm, one wheel revolution is **408 mm** of travel. At the 100 RPM motor
rating that's 0.68 m/s flat out, and about 0.53 m/s at the firmware's PWM ceiling
of 200. The 408 mm figure is what the encoder scaling will use.

Torque is not the binding constraint. Four motors at ~1 N·m over a 65 mm radius
puts roughly 63 N at the contact patches — about the robot's own weight. Traction
runs out before torque does, which is the right way round for a site machine.

### A rocker pivot, considered and deferred

A rigid four-wheel frame lifts a wheel diagonally on uneven ground, losing a
quarter of its traction and pulling off heading. The fix is a front axle on a
longitudinal pivot so all four wheels stay loaded.

It was designed — a free 300 mm cross beam on a shaft at axle height in two bearing
blocks, stops at ±10°, covering obstacles to 58 mm — and then deferred. On a slab
demo the rocker earns nothing, while it adds a shaft, two bearing blocks, stops and
a wiring service loop across a moving joint carrying 43 A motor leads. It also puts
the front axle's yaw stiffness on a single shaft, which matters more in skid steer
than in normal steering because the front motors constantly twist the beam during a
turn.

The frame is unchanged up to the front cross member, so the pivot remains a drop-in
upgrade rather than a redesign.

---

## Wiring

### UNO Q to motor drivers

| UNO Q pin | Left BTS7960 | Right BTS7960 |
|---|---|---|
| D3 | `RPWM` | — |
| D5 | `LPWM` | — |
| D6 | — | `RPWM` |
| D9 | — | `LPWM` |
| D4 | `R_EN` + `L_EN` | `R_EN` + `L_EN` |
| 3V3 | `VCC` | `VCC` |
| GND | — | — |

Driver signal ground returns through `B−` to the star point; the header `GND`
pins are left unconnected so the drivers do not create a second ground path.
`R_IS`/`L_IS` are unconnected — they can exceed 3.3 V.

Firmware direction flags after the phase test: `L_INVERT=true`, `R_INVERT=false`.

### Motor-rail sense (e-stop detection and battery gauge)

```
E-stop output (switched B+) ──[33 kΩ]──[10 kΩ]──┬──► A0
                                                [10 kΩ]
                                                  │
                                                 GND
```

| Rail | A0 | ADC (10-bit, 3.3 V) | Meaning |
|---|---|---|---|
| 12.6 V (full) | 2.38 V | 738 | released |
| 10.5 V | 1.98 V | 615 | stop driving |
| < 8 V | < 1.5 V | < 470 | **e-stop pressed** |

Ratio by measured parts 0.1890; by meter 2.37 V / 12.74 V = 0.1860, which is
what the firmware uses (`PACK_K = 0.017340` V per count). Dashboard voltage
matched a multimeter to the hundredth after calibration. Worst case with 5 %
parts is 2.6 V at A0, safely under the 3.3 V pin limit. A blown main fuse is
*not* detected this way — it takes the compute rail down too, so the robot
simply goes dark.

Reserved: D0/D1 UART, D2/D7 for an HC-SR04, D10/D11 for encoder channel A.

### Power

```
LiPo + ──► fuse ──► kill switch ──┬──► E-stop ──┬──► both B+
                                  │             └──► 33k+10k/10k ──► A0
                                  └──► TPS565201 5 V ──► UNO Q (USB-C via PD adapter), OAK-D, hub
LiPo − ──► star point ──► both B−, buck IN−, divider GND
```

Both drivers' `B−` run to a single star point at the battery negative in 14 AWG.
Not daisy-chained — the second module would otherwise read the first one's current
as ground offset.

### Two things that cost hours

**`VCC` goes to 3V3, not 5 V.** The BTS7960 input threshold is roughly 0.6 × VCC.
At 5 V it wants 3.0 V to register a high, and the UNO Q's 3.3 V output sits barely
above that — it works on the bench and fails when the board warms up. On 3.3 V the
threshold drops to about 2 V and you have real margin.

**Never call `pinMode()` on the PWM pins.** On the UNO Q that breaks `analogWrite`
silently. The sketch writes D3/D5/D6/D9 with `analogWrite` directly and only calls
`pinMode` on the digital enable pin. Correct wiring looks completely dead if you
get this wrong.

**Test every Dupont jumper for continuity before blaming firmware.** On first
arming only the right side ran. The sketch was clean; swapping D6/D9 onto the
left driver made the left side run, which proved both drivers and all four
motors. The D3 and D5 jumpers both had open crimps. Two bad wires out of a bag
look exactly like a timer conflict.

**Driver capacitors keep the rail alive after the e-stop opens.** Two 330 µF
caps discharging through a 53 kΩ divider is a 35 s time constant, so an idle
robot reports the press about 15 s late. Under drive the motors drain it in
milliseconds, which is the case that matters. A 2 × 680 Ω bleeder across B+/B−
(≈9 mA) brings idle detection under half a second; it is on the list.

---

## Repository

https://github.com/csresearchlabsindia/AI-Site-Agent

```
AI-Site-Agent/
├── services/
│   ├── asa_vision.py         OAK-D dual-net + on-head rule + evidence  (:8090)
│   ├── asa_voice.py          Piper TTS, 4 languages, phrase cache      (:8091)
│   ├── asa_bt_speaker.sh     Bluetooth reconnect by device name
│   └── phrases_i18n.json     Alert text — edit text only, auto-regenerates
│   ├── asa_journal.py        boot / power-loss / uptime record, web view  (:8095)
│   └── asa-powerlog.py       pack voltage sampler, CSV + /log API          (:8096)
├── app/                      the App Lab app
│   ├── main.py               dashboard API, KWS callback, LED state machine
│   ├── sketch/sketch.ino     LED face + drive(l,r) + deadman + e-stop latch
│   └── index.html            WebUI Brick dashboard (tabbed, Oct 2026 design)
├── scripts/asa-stamp.sh      writes the running version into the journal
└── systemd/                  unit files incl. the voice CPU-affinity drop-in
```

Piper voice models are deliberately not in the repo — 261 MB, and redistributable
from upstream. Download them to `~/voices` during install.

```bash
git clone https://github.com/csresearchlabsindia/AI-Site-Agent.git
```

---

## Installation

Assumes a UNO Q flashed with the standard image, on your network. Substitute your
own hostname or LAN address for `<unoq>`.

### 1. Base system

```bash
ssh arduino@<unoq>
sudo apt update && sudo apt install -y python3-venv python3-pip git ffmpeg
```

Kill Wi-Fi power saving, or SSH and the dashboard go laggy under load:

```bash
sudo tee /etc/NetworkManager/conf.d/99-asa-wifi-powersave-off.conf << 'EOF'
[connection]
wifi.powersave = 2
EOF
sudo systemctl restart NetworkManager
```

### 2. Vision service

```bash
python3 -m venv ~/oak-venv
~/oak-venv/bin/pip install depthai==3.10 opencv-python-headless numpy
mkdir -p ~/asa-evidence
# place asa_vision.py in ~
sudo tee /etc/systemd/system/asa-vision.service << 'EOF'
[Unit]
Description=ASA vision
After=network.target
[Service]
User=arduino
ExecStart=/home/arduino/oak-venv/bin/python /home/arduino/asa_vision.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now asa-vision
```

Verify:

```bash
curl -s 127.0.0.1:8090/state | head -c 200; echo
```

### 3. Voice service

Download Piper voices to `~/voices`: `en_US-lessac`, `ta_IN-rasa_female`,
`hi_IN-priyamvada`, `fr_FR-siwis`.

```bash
sudo tee /etc/systemd/system/asa-voice.service << 'EOF'
[Unit]
Description=ASA voice
After=network.target
[Service]
User=arduino
ExecStart=/home/arduino/oak-venv/bin/python /home/arduino/asa_voice.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo mkdir -p /etc/systemd/system/asa-voice.service.d
sudo tee /etc/systemd/system/asa-voice.service.d/cpu.conf << 'EOF'
[Service]
CPUAffinity=2 3
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now asa-voice
```

**The CPU pinning is not optional.** Without it the Bluetooth audio stalls under
synthesis load. Pinned to cores 2–3, all 40 test clips played cleanly at load 4.4.

Verify:

```bash
curl -s 127.0.0.1:8091/status; echo
```

Expected:

```
{"lang":"ta","available":["en","fr","hi","ta"],"voice_ready":true,
 "cached":12,"total":12,"speaker":true,"volume":0.7,"busy":false}
```

### 4. Bluetooth speaker

Pair the ZEB-CLIPPER once with `bluetoothctl`, then let the helper handle
reconnection. It matches **by device name, not node number** — the PipeWire node
number changes every boot, which is a reliable way to have your audio vanish after
a reboot.

```bash
chmod +x ~/asa_bt_speaker.sh
~/asa_bt_speaker.sh
journalctl -t asa-bt -n 20
```

### 5. Mission Control App Lab app

```bash
cd ~/ArduinoApps/asa-mission-control
arduino-app-cli app start user:asa-mission-control
```

This compiles and flashes the STM32 sketch and starts the Python side. Expect:

```
[INFO] Sketch uses 98172 bytes (12%) of program storage space. Maximum is 786432 bytes.
✓ App "ASA Mission Control" restarted successfully
```

Dashboard at `http://<unoq>:7000`.

### 6. Power log

```bash
sudo install -m 755 services/asa-powerlog.py /usr/local/bin/asa-powerlog.py
sudo mkdir -p /var/lib/asa && sudo chown arduino /var/lib/asa
sudo cp systemd/asa-powerlog.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now asa-powerlog
```

It samples `/asa/power` every 30 s into `/var/lib/asa/power.csv` and serves
`/log?hours=N` and `/power.csv` on :8096 for the dashboard's Power tab.

### 7. Verify the whole chain

```bash
curl -s 127.0.0.1:7000/asa/status; echo
curl -s 127.0.0.1:7000/asa/power; echo
curl -s "127.0.0.1:7000/asa/drive?l=80&r=0"; echo
curl -s 127.0.0.1:7000/asa/halt; echo
```

Expected:

```
{"lang":"ta","available":["en","fr","hi","ta"],"voice_ready":true,...}
{"ok":true,"pack_v":12.10,"estop":false}
{"ok":true,"l":80,"r":0}
{"ok":true}
```

That last pair proves the full path — HTTP route, RouterBridge RPC, MCU handler.

**Run it with the LiPo disconnected first.** You get the same `{"ok":true}` with
nothing able to move, which proves the signal chain before any current is involved.
Then chassis on blocks, battery in, and repeat to confirm direction per side.
Press the e-stop while holding forward: the wheels stop, `/asa/power` reports
`"estop":true`, and the pad refuses to drive until you release and let go for
a second.

### 8. Reboot proof

```bash
sudo reboot
# wait, then
systemctl is-enabled asa-vision asa-voice asa-journal asa-powerlog
curl -s <unoq>:7000/asa/status; echo
```

Both should report `enabled` and the dashboard should come back without
intervention. The App Lab app is set as default so it auto-starts.

---

## Hard-won notes

**Bluetooth headset microphones do not work on the UNO Q.** Tested exhaustively —
the link establishes and then delivers 0 SCO RX packets. USB audio is the only
viable microphone path. The mini USB mic on card 1 at gain 8/16 with AGC off gives
21.7 dB SNR at 30 cm, which is enough for reliable keyword spotting.

**The voice API is unresponsive for about 10 seconds while a Piper model loads**
(at boot or on language switch). Audio playback is unaffected. Language switching
takes 15–20 seconds even cached — don't do it on camera.

**The QCA Bluetooth chip crashed twice under heavy load** before CPU pinning. If
`bluetoothctl show` reports `Powered: no`, it needs a reboot.

**Live video needs a self-healing image source.** The dashboard originally set
`img.src` once at page load; if the vision service happened to be down when the tab
opened, the feed stayed dead forever while telemetry kept updating and made
everything look fine. Fixed with a cache-busted retry on `onerror`.

**`Bridge.update()` does not exist in RouterBridge 0.4.3.** Calling it fails to
compile. The loop services the bridge on its own.

---

## Sustainability

The waste angle is real but honest about its stage. The compliance and evidence
pipeline is built and working. Material classification — a FOMO model trained via
Edge Impulse on construction scrap — is scoped and partially built as a fourth
service pulling frames over HTTP from the vision service, running on cores 0–1 so
it doesn't contend with the two live nets.

The broader case: construction is roughly 30% of global waste, and a large share of
it is rework. A unit that continuously compares as-built against as-planned catches
a misplaced element before the next pour rather than after.

---

## What's next

- Runtime measurement: one full run from a charged pack to 10.5 V with the
  power log recording, then the charger's returned mAh ÷ hours gives true
  average draw. The design estimate is ~1.2 A standing watch, ~4 A driving.
- 2 × 680 Ω bleeder on the motor rail so an idle e-stop press is seen in under
  a second.
- Encoder readout. OE-28s are fitted on all four motors; channel A on one encoder
  per side reads back to D10/D11 for closed-loop speed and odometry.
- HC-SR04 on D2/D7 as a low-latency obstacle reflex on the MCU, independent of
  vision.
- INA219 on the compute branch for logic current, alongside the rail voltage.
- RouterBridge client-side watchdog for a surprise MCU reset.
- FOMO scrap classification to completion.

---

## Media

- `WhatsApp_Image_2026-09-14_at_11_40_31.jpeg` — full unit, mast and OAK-D
- `WhatsApp_Image_2026-09-14_at_11_40_31__1_.jpeg` — electronics deck detail
- `WhatsApp_Image_2026-09-14_at_11_40_30.jpeg` — chassis, motors recessed in rims
- `WhatsApp_Image_2026-09-14_at_11_40_28.jpeg` — UNO Q with LED matrix showing ASA
- `Hat-detection.mp4` — compliant detection, hard hat on head
- `No-Hat.mp4` — violation detection and spoken alert
- `HeyKeyword.mp4` — "hey arduino" keyword spotting → spoken status report
- `faceDisplay.mp4` — LED matrix face animation states
- `[FILL]` — first drive on battery, 1 Oct 2026
- `[FILL]` — e-stop pressed while driving; dashboard hazard band
- `[FILL]` — dashboard screenshot, Power tab after the runtime run

---

Built by Karthikeyan Sundararaman, CS Research Labs, Chennai.

Tamil voice uses AI4Bharat **Rasa** (`ta_IN-rasa_female`), CC-BY-4.0 —
cite INTERSPEECH 2024.
