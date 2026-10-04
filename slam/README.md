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
