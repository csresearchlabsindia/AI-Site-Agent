# ASA SLAM

On-board 2-D mapping for ASA on DepthAI 3.10, no ROS. The production path lives in `services/asa_vision.py`
(RTABMapVIO → RTABMapSLAM, sharing the PPE depth stream); this folder holds the camera probe, the development scripts
and the calibration backup.

| File | Purpose |
|---|---|
| `oak_probe.py` | Camera inventory: IMU, sensors, baseline, USB speed, calibration |
| `oak_calib_backup.json` | Factory calibration of the OAK-D (BW1098OBC) |
| `slam_s1_vio.py` | Basalt VIO stationary test with runtime IMU extrinsics |
| `slam_s2_motion.py` | VIO under motion; logs the path and renders it to PNG |
| `slam_s3b_rtabvio.py` | RTABMapVIO → RTABMapSLAM standalone, 2-D grid to PNG |
| `slam_s4_rgbd_test.py` | RGB-D variant matching the production pipeline (colour-aligned depth) |
| `slam_s4_probe.py` | Reports the depth/NN frame sizes the detectors configure |
| `slam_s4_ab.py` | Pipeline rate check with and without the SLAM node |

Notes
- Camera lens is 0.70 m above the floor; grid height filters are relative to the lens.
- The OAK-D ships without IMU calibration in EEPROM; the scripts inject the IMU-to-CAM_A extrinsics from Luxonis's
  OAK-D board file at runtime. Nothing is written to the EEPROM.
- Grid encoding from `MapData.map`: 0 free, 89 unknown, 178 occupied; rows = y, columns = x; origin `minX`/`minY`.
- Only one process may own the camera, so SLAM runs inside `asa-vision`; `ASA_SLAM=0` disables it.
