import depthai as dai, time, os, math, sys, csv
import numpy as np, cv2
W, H, FPS, DUR = (int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])) if len(sys.argv) > 4 else (640, 400, 30, 120)
TAG = f"{W}x{H}@{FPS}"; OUT = "/var/lib/asa/slam"
CA = dai.CameraBoardSocket.CAM_A
R_IMU = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]; T_IMU = [1.5, 1.3662, 0.0]
dev = dai.Device(); cal = dev.readCalibration()
try: cal.getImuToCameraExtrinsics(CA)
except Exception: cal.setImuExtrinsics(CA, R_IMU, T_IMU, T_IMU)
dev.setCalibration(cal)
with dai.Pipeline(dev) as p:
    p.setCalibrationData(cal)
    L = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
    R = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
    imu = p.create(dai.node.IMU)
    imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 200)
    imu.setBatchReportThreshold(1); imu.setMaxBatchReports(10)
    vio = p.create(dai.node.BasaltVIO)
    L.requestOutput((W, H), fps=FPS).link(vio.left)
    R.requestOutput((W, H), fps=FPS).link(vio.right)
    imu.out.link(vio.imu)
    q = vio.transform.createOutputQueue(maxSize=8, blocking=False)
    p.start()
    t0 = time.time(); c0 = sum(os.times()[:2]); n = 0; last = 0; origin = None; path = []
    print(f"[{TAG}] VIO up. Wait for 'GO', then drive the square and return to the start mark.", flush=True)
    while p.isRunning() and time.time() - t0 < DUR:
        m = q.tryGet()
        if m is None: time.sleep(0.003); continue
        tr = m.getTranslation(); n += 1; t = time.time() - t0
        pt = (tr.x, tr.y, tr.z); path.append((t,) + pt)
        if origin is None and t > 5: origin = pt; print("GO", flush=True)
        if origin and time.time() - last >= 2:
            last = time.time(); d = math.dist(origin, pt)
            print(f"{t:5.0f}s  from_start={d*100:6.1f} cm  xyz=({tr.x:+.2f},{tr.y:+.2f},{tr.z:+.2f})  {int(DUR-t)}s left", flush=True)
    el = time.time() - t0; cpu = (sum(os.times()[:2]) - c0) / el * 100
    err = math.dist(origin, path[-1][1:]) * 100 if origin and path else float('nan')
    with open(f"{OUT}/s2_{TAG}.csv", "w", newline="") as f:
        csv.writer(f).writerows([("t", "x", "y", "z")] + path)
    # top-down plot of the path (x,z plane), 1 px = 1 cm
    if path:
        xs = np.array([q_[1] for q_ in path]) * 100; zs = np.array([q_[3] for q_ in path]) * 100
        pad = 30; w = int(xs.max() - xs.min()) + 2 * pad; h = int(zs.max() - zs.min()) + 2 * pad
        img = np.full((max(h, 100), max(w, 100), 3), 255, np.uint8)
        pts = np.stack([xs - xs.min() + pad, zs - zs.min() + pad], 1).astype(np.int32)
        cv2.polylines(img, [pts], False, (180, 60, 0), 2)
        cv2.circle(img, tuple(pts[0]), 6, (0, 160, 0), -1); cv2.circle(img, tuple(pts[-1]), 6, (0, 0, 200), -1)
        cv2.putText(img, f"{TAG} err={err:.1f}cm cpu={cpu:.0f}%", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
        cv2.imwrite(f"{OUT}/s2_{TAG}.png", img)
    print(f"RESULT {TAG} rate={n/el:.1f}Hz cpu={cpu:.0f}% loop_err_cm={err:.1f} samples={n}", flush=True)
    os._exit(0)
