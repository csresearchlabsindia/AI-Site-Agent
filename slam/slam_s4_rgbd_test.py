import depthai as dai, time, os, math, sys, numpy as np
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 20
USE_IMU = 'noimu' not in sys.argv
def rpy(q):
    x, y, z, w = q.qx, q.qy, q.qz, q.qw
    return f"rpy=({math.degrees(math.atan2(2*(w*x+y*z),1-2*(x*x+y*y))):+.0f},{math.degrees(math.asin(max(-1,min(1,2*(w*y-z*x))))):+.0f},{math.degrees(math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))):+.0f})"
SIZE = (640, 400)
CA = dai.CameraBoardSocket.CAM_A
R_IMU = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]; T_IMU = [1.5, 1.3662, 0.0]
dev = dai.Device(); cal = dev.readCalibration()
try: cal.getImuToCameraExtrinsics(CA)
except Exception: cal.setImuExtrinsics(CA, R_IMU, T_IMU, T_IMU)
dev.setCalibration(cal)
with dai.Pipeline(dev) as p:
    p.setCalibrationData(cal)
    cam = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
    left = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B); right = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
    stereo = p.create(dai.node.StereoDepth)
    left.requestOutput(SIZE, fps=10).link(stereo.left); right.requestOutput(SIZE, fps=10).link(stereo.right)
    stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A); stereo.setOutputSize(*SIZE)   # as SpatialDetectionNetwork.build does
    rgb = cam.requestOutput(SIZE, dai.ImgFrame.Type.GRAY8, fps=10)
    imu = p.create(dai.node.IMU); imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 200)
    imu.setBatchReportThreshold(1); imu.setMaxBatchReports(10)
    vio = p.create(dai.node.RTABMapVIO); rgb.link(vio.rect); stereo.depth.link(vio.depth)
    if USE_IMU: imu.out.link(vio.imu)
    print('imu', 'on' if USE_IMU else 'off', flush=True)
    slam = p.create(dai.node.RTABMapSLAM)
    slam.setParams({"RGBD/CreateOccupancyGrid": "true", "Grid/3D": "false", "Grid/CellSize": "0.05", "Grid/RangeMax": "4.0",
                    "Grid/MaxObstacleHeight": "1.2", "Grid/MaxGroundHeight": "-0.55", "Grid/MinGroundHeight": "-0.85",
                    "Grid/NormalsSegmentation": "false", "Rtabmap/DetectionRate": "1"})
    rgb.link(slam.rect); stereo.depth.link(slam.depth); vio.transform.link(slam.odom)
    qD = stereo.depth.createOutputQueue(maxSize=1, blocking=False); qR = rgb.createOutputQueue(maxSize=1, blocking=False)
    qV = vio.transform.createOutputQueue(maxSize=1, blocking=False); qS = slam.transform.createOutputQueue(maxSize=1, blocking=False)
    qG = slam.occupancyGridMap.createOutputQueue(maxSize=1, blocking=False)
    p.start(); t0 = time.time(); c0 = sum(os.times()[:2]); last = 0; v = s = g = d = r = None; nV = 0
    while p.isRunning() and time.time() - t0 < DUR:
        try:
            m = qV.tryGet()
            if m is not None: v = m; nV += 1
            s = qS.tryGet() or s; g = qG.tryGet() or g; d = qD.tryGet() or d; r = qR.tryGet() or r
        except Exception as e: print("pipeline ended:", e, flush=True); break
        if time.time() - last >= 5:
            last = time.time(); line = f"{time.time()-t0:3.0f}s vio={nV}"
            if d is not None and r is not None: line += f" depth={d.getWidth()}x{d.getHeight()} rgb={r.getWidth()}x{r.getHeight()}"
            if v is not None: t = v.getTranslation(); line += f" VIO=({t.x:+.2f},{t.y:+.2f},{t.z:+.2f}){rpy(v.getQuaternion())}"
            if s is not None: line += f" SLAM{rpy(s.getQuaternion())}"
            if g is not None: a = g.map.getCvFrame(); line += f" grid={a.shape[1]}x{a.shape[0]} free={int((a==0).sum())} occ={int((a==178).sum())}"
            print(line, flush=True)
        time.sleep(0.005)
    if g is not None:
        a = g.map.getCvFrame(); print("ASCII map (cols = x forward, 1 char = 10 cm; . free  # occ)")
        for r in range(a.shape[0]-1, -1, -2):
            row = "".join("#" if (a[r, c:c+2] == 178).any() else "." if (a[r, c:c+2] == 0).any() else " " for c in range(0, a.shape[1], 2))
            if row.strip(): print("|" + row + "|")
    el = time.time() - t0; print(f"RESULT vio_msgs={nV} cpu={(sum(os.times()[:2])-c0)/el*100:.0f}% over {el:.0f}s", flush=True)
    os._exit(0)
