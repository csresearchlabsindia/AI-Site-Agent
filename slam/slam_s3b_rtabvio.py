import depthai as dai, time, os, math, sys, numpy as np, cv2
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 60
OUT = "/var/lib/asa/slam"; os.makedirs(OUT, exist_ok=True)
CA = dai.CameraBoardSocket.CAM_A; CB = dai.CameraBoardSocket.CAM_B; CC = dai.CameraBoardSocket.CAM_C
R_IMU = [[-1, 0, 0], [0, -1, 0], [0, 0, 1]]; T_IMU = [1.5, 1.3662, 0.0]
def rpy(q):
    x, y, z, w = q.qx, q.qy, q.qz, q.qw
    r = math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y)); p = math.asin(max(-1, min(1, 2*(w*y-z*x)))); yw = math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
    return f"rpy=({math.degrees(r):+.0f},{math.degrees(p):+.0f},{math.degrees(yw):+.0f})"
def bounds(pc):
    pts = np.asarray(pc.getPoints()); pts = pts[np.isfinite(pts).all(1)]
    return f"{len(pts)}pts x[{pts[:,0].min():.2f},{pts[:,0].max():.2f}] y[{pts[:,1].min():.2f},{pts[:,1].max():.2f}] z[{pts[:,2].min():.2f},{pts[:,2].max():.2f}]" if pts.size else "0pts"
dev = dai.Device(); cal = dev.readCalibration()
try: cal.getImuToCameraExtrinsics(CA)
except Exception: cal.setImuExtrinsics(CA, R_IMU, T_IMU, T_IMU)
dev.setCalibration(cal)
with dai.Pipeline(dev) as p:
    p.setCalibrationData(cal)
    L = p.create(dai.node.Camera).build(CB); R = p.create(dai.node.Camera).build(CC)
    st = p.create(dai.node.StereoDepth); st.setLeftRightCheck(True); st.setRectifyEdgeFillColor(0)
    L.requestOutput((640, 400), fps=10).link(st.left); R.requestOutput((640, 400), fps=10).link(st.right)
    imu = p.create(dai.node.IMU); imu.enableIMUSensor([dai.IMUSensor.ACCELEROMETER_RAW, dai.IMUSensor.GYROSCOPE_RAW], 200)
    imu.setBatchReportThreshold(1); imu.setMaxBatchReports(10)
    vio = p.create(dai.node.RTABMapVIO)
    print("RTABMapVIO inputs:", [i.getName() for i in vio.getInputRefs()], flush=True)
    st.rectifiedLeft.link(vio.rect); st.depth.link(vio.depth)
    try: imu.out.link(vio.imu); print("imu linked", flush=True)
    except Exception as e: print("imu link ?", e, flush=True)
    slam = p.create(dai.node.RTABMapSLAM)
    slam.setParams({"RGBD/CreateOccupancyGrid": "true", "Grid/3D": "false", "Grid/CellSize": "0.05", "Grid/RangeMax": "4.0",
                    "Grid/MaxObstacleHeight": "1.2", "Grid/MaxGroundHeight": "-0.55", "Grid/MinGroundHeight": "-0.85", "Grid/NormalsSegmentation": "false", "Rtabmap/DetectionRate": "1"})
    st.rectifiedLeft.link(slam.rect); st.depth.link(slam.depth); vio.transform.link(slam.odom)
    qV = vio.transform.createOutputQueue(maxSize=1, blocking=False); qS = slam.transform.createOutputQueue(maxSize=1, blocking=False)
    qO = slam.obstaclePCL.createOutputQueue(maxSize=1, blocking=False); qG = slam.occupancyGridMap.createOutputQueue(maxSize=1, blocking=False)
    p.start(); t0 = time.time(); c0 = sum(os.times()[:2]); last = 0; v = s = o = g = None; nV = 0
    while p.isRunning() and time.time() - t0 < DUR:
        try:
            m = qV.tryGet()
            if m is not None: v = m; nV += 1
            s = qS.tryGet() or s; o = qO.tryGet() or o; g = qG.tryGet() or g
        except Exception as e: print("pipeline ended:", e, flush=True); break
        if time.time() - last >= 10:
            last = time.time(); el = time.time() - t0; line = f"{el:3.0f}s vio={nV}"
            if v is not None: t = v.getTranslation(); line += f" VIO=({t.x:+.2f},{t.y:+.2f},{t.z:+.2f}){rpy(v.getQuaternion())}"
            if s is not None: line += f" SLAM{rpy(s.getQuaternion())}"
            if o is not None: line += f" obst {bounds(o)}"
            if g is not None: a = g.map.getCvFrame(); u, c = np.unique(a, return_counts=True); line += f" grid={a.shape[1]}x{a.shape[0]} {a.dtype} vals={dict(zip(u.tolist(), c.tolist()))}"
            print(line, flush=True)
        time.sleep(0.005)
    el = time.time() - t0; cpu = (sum(os.times()[:2]) - c0) / el * 100
    if g is not None:
        a = g.map.getCvFrame(); img = np.full(a.shape[:2], 127, np.uint8); img[a == 0] = 255; img[a == 178] = 0
        print("ASCII map (top = +x forward, 1 char = 10 cm; . free  # occupied  blank unknown)")
        for r in range(a.shape[0]-1, -1, -2):
            row = "".join("#" if (a[r, c:c+2] == 178).any() else "." if (a[r, c:c+2] == 0).any() else " " for c in range(0, a.shape[1], 2))
            if row.strip(): print("|" + row + "|")
        f = max(1, min(6, 800 // max(img.shape))); img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(f"{OUT}/s3b_grid.png", img); print("saved s3b_grid.png", img.shape, flush=True)
    print(f"RESULT vio_msgs={nV} cpu={cpu:.0f}% over {el:.0f}s", flush=True)
    os._exit(0)
