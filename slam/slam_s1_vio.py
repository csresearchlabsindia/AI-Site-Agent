import depthai as dai, time, os, math, sys
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 60
CA = dai.CameraBoardSocket.CAM_A
BAK = os.path.expanduser("~/oak_calib_backup.json")
# OAK-D (BW1098OBC) BNO086 -> CAM_A, from luxonis/depthai-boards OAK-D.json (cm)
R_IMU = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]; T_IMU = [1.5, 1.3662, 0.0]

dev = dai.Device()
cal = dev.readCalibration()
if not os.path.exists(BAK):
    cal.eepromToJsonFile(BAK); print("backup ->", BAK)
try:
    cal.getImuToCameraExtrinsics(CA); print("imu calib: present in EEPROM")
except Exception:
    cal.setImuExtrinsics(CA, R_IMU, T_IMU, T_IMU); print("imu calib: injected (runtime only)")
if hasattr(dev, "setCalibration"):
    try: dev.setCalibration(cal); print("dev.setCalibration ok")
    except Exception as e: print("dev.setCalibration ?", e)

with dai.Pipeline(dev) as p:
    p.setCalibrationData(cal)
    L = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
    R = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
    imu = p.create(dai.node.IMU)
    imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 200)
    imu.setBatchReportThreshold(1); imu.setMaxBatchReports(10)
    vio = p.create(dai.node.BasaltVIO)
    L.requestOutput((640, 400)).link(vio.left)
    R.requestOutput((640, 400)).link(vio.right)
    imu.out.link(vio.imu)
    q = vio.transform.createOutputQueue(maxSize=4, blocking=False)
    p.start()
    t0 = time.time(); c0 = sum(os.times()[:2]); n = 0; last = 0; first = cur = None
    while p.isRunning() and time.time() - t0 < DUR:
        m = q.tryGet()
        if m is None: time.sleep(0.005); continue
        tr = m.getTranslation(); cur = (tr.x, tr.y, tr.z); n += 1
        if first is None and time.time() - t0 > 5: first = cur
        if time.time() - last >= 5:
            last = time.time()
            print(f"{time.time()-t0:5.0f}s  x={tr.x:+.3f} y={tr.y:+.3f} z={tr.z:+.3f}  msgs={n}", flush=True)
    el = time.time() - t0
    cpu = (sum(os.times()[:2]) - c0) / el * 100
    drift = math.dist(first, cur) * 100 if first and cur else float('nan')
    print(f"RESULT rate={n/el:.1f}Hz cpu={cpu:.0f}% drift_cm={drift:.1f}", flush=True)
    os._exit(0)   # skip BasaltVIO::stop() segfault on teardown
