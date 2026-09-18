# Changelog

All notable changes to ASA. Format follows Keep a Changelog; versions follow semantic versioning.

The version in `VERSION` is what `asa-stamp.sh` writes to the unit, and every boot record in the journal carries it — so any line here can be correlated against the uptime and abnormality record on the robot itself.

Categories: Added, Changed, Fixed, Removed, Hardware, Known issues.

## [Unreleased]

### Added
- `asa-journal` service: boot, shutdown and power-loss tracking, uptime/downtime rollup, restart-storm coalescing, web view and `/report` on :8095
- `asa-stamp.sh` deploy stamper writing `/var/lib/asa/version.json`
- asa-journal web view on :8095 with a Changelog tab reading `CHANGELOG.md` from the repo, marking the running version
- Hold-to-drive teleop panel in the Mission Control dashboard: arm gate, speed cap, 10 Hz command repeat, halt on release, tab-switch or focus loss, 2-minute idle auto-disarm
- `S_ATTENTION` LED state with a muted-speaker glyph, shown in place of IDLE while the Bluetooth speaker is offline; 90 s boot grace, 30 s poll, two-strike debounce, clears on first success

### Fixed
- asa-journal HTTP server changed to ThreadingHTTPServer; a single stalled browser connection blocked all requests and hung the SIGTERM shutdown path, causing systemd to SIGKILL the process and skip the clean-shutdown marker
- asa-journal uptime accounting stopped after an in-boot service restart; `session_start` now opens a session alongside `boot`

### Known issues
- KeywordSpotting brick raises `MicrophoneReadError: Attempted to read from Device before starting it` on every app shutdown - upstream teardown-order bug in the Arduino brick, cosmetic, no functional effect
- Container log can be left with null bytes after an unclean stop, which makes `docker logs` abort; a restart rotates it

## [1.3.0] - 2026-09-13

### Added
- Drive control merged into the Mission Control sketch: `drive(l,r)` and `halt()` over RouterBridge, exposed as `/asa/drive` and `/asa/halt` on :7000
- 400 ms MCU-side deadman and 250 ms slew ramp on the drive path
- `_bridge_lock` serialising all RouterBridge calls so the status loop and drive commands cannot interleave frames

### Known issues
- `L_INVERT` / `R_INVERT` are assumed, not measured - the phase test has not been run
- No D-pad page; driving is curl-only
- RouterBridge hangs on a surprise MCU reset; client-side watchdog not yet implemented

## [1.2.0] - 2026-09-12

### Added
- Keyword spotting via the App Lab KWS Brick on `hey_arduino`, with spoken status report
- Nine-state LED matrix face driven from MPU state messages, with a watchdog `?` when the MPU stops talking

### Changed
- `LISTEN` state rebuilt from a fixed 2.5 s animation to a steady state held by polling the voice service `/busy` endpoint

### Removed
- Bluetooth headset microphone path - the link establishes but delivers zero SCO packets on the UNO Q

## [1.1.0] - 2026-09-11

### Added
- `asa-voice` v5.1: Piper TTS in English, Tamil, Hindi and French; A2DP output; self-healing phrase cache; boot-ID health audit distinguishing reboot from power loss
- Evidence trail - annotated JPEG plus `events.jsonl` per detection, CSV export, 14-day retention

### Fixed
- Bluetooth audio stalling under synthesis load - voice pinned to cores 2-3

## [1.0.0] - 2026-09-10

### Added
- `asa-vision`: YOLOv6n person and YOLOv8n hardhat on the OAK-D Myriad X via `SpatialDetectionNetwork`, ~8 FPS
- Person-anchored on-head rule and 7-of-10 frame confirmation vote
- 0.3-2.8 m working zone
- Mission Control WebUI on :7000 with live MJPEG, event log and evidence gallery
- All services reboot-surviving under systemd

### Hardware
- Chassis assembled: 4x Johnson Grade A motors in-rim, 2x BTS7960, 2020 extrusion frame, 350 mm mast, OAK-D, UNO Q
