# ASA SLAM (work in progress)

DepthAI 3.10 on-board SLAM, no ROS. Status 4 Oct 2026: VIO + RTAB-Map occupancy grid verified with the robot stationary; motion tests pending.

| Script | Purpose | Result |
|---|---|---|
| `oak_probe.py` | Camera inventory | BNO086 IMU fw 3.9.7, 2x OV9282 global shutter, 7.5 cm baseline, USB3 |
| `slam_s1_vio.py` | BasaltVIO stationary test | 12 Hz, 0.4 cm drift / 60 s, 2.7 cores |
| `slam_s2_motion.py` | VIO under motion, path PNG | 10 fps: 193% CPU (motion test pending) |
| `slam_s3b_rtabvio.py` | RTABMapVIO -> RTABMapSLAM, 2-D grid | Level frames, 5 cm grid, ~300% CPU |

Findings
- This OAK-D (BW1098OBC) ships without IMU-to-camera calibration in EEPROM. `oak_calib_backup.json` is the factory calibration; the scripts inject the IMU extrinsics from Luxonis's OAK-D board file at runtime (no EEPROM write).
- BasaltVIO output is yawed 180 deg relative to the camera frame (IMU mount rotation) and `TransformData` cannot be filled from Python in 3.10, so RTABMapVIO is used for the SLAM pipeline.
- Grid encoding from `MapData.map`: 0 free, 89 unknown, 178 occupied; rows = y, columns = x. Origin in `minX`/`minY`.
- Lens height 0.70 m; grid height filters are relative to the lens.
- Only one process may own the camera: SLAM must join the `asa-vision` pipeline (planned).

## S4 — merged into asa-vision (4 Oct 2026)

`services/asa_vision.py` v4 runs RTABMapVIO -> RTABMapSLAM beside the PPE detectors, feeding SLAM a 512x288 grey frame
from CAM_A plus the detectors' own depth (aligned to CAM_A), so the Myriad does no extra work. New endpoints: `/pose`
(JSON) and `/map.png`; the dashboard has a Map tab. `ASA_SLAM=0` disables SLAM; any SLAM setup error falls back to PPE-only.

- Stationary result: level frame, 5 x 4.4 m grid after 60 s, PPE still 8 fps, asa-vision total ~270% CPU.
- IMU is off by default (`ASA_SLAM_IMU=1` to enable): with the injected BNO086 extrinsics RTAB-Map's start frame came out
  rotated by 90 or 180 deg on different runs; vision-only VIO was level on every run. Rotation convention to be resolved.
- Grid encoding from `MapData.map`: 0 free, 89 unknown, 178 occupied; rows = y, columns = x; origin `minX`/`minY`.
- Pending: motion tests (S2 square, S3 room sweep), loop-closure check, CPU tuning, map persistence across restarts.
