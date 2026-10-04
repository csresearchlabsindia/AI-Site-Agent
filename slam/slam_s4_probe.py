import depthai as dai, time, os
HAT_MODEL = "/home/arduino/models/hardhat.tar.xz"; PERSON_MODEL = "luxonis/yolov6-nano:r2-coco-512x288"; FPS = 8
SIZE = (640, 400)
with dai.Pipeline() as p:
    cam = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
    left = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B); right = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
    stereo = p.create(dai.node.StereoDepth)
    left.requestOutput(SIZE).link(stereo.left); right.requestOutput(SIZE).link(stereo.right)
    hat_nn = p.create(dai.node.SpatialDetectionNetwork).build(cam, stereo, dai.NNArchive(HAT_MODEL), fps=FPS)
    per_nn = p.create(dai.node.SpatialDetectionNetwork).build(cam, stereo, dai.NNModelDescription(PERSON_MODEL), fps=FPS)
    qD = stereo.depth.createOutputQueue(maxSize=1, blocking=False)
    qH = hat_nn.passthrough.createOutputQueue(maxSize=1, blocking=False); qP = per_nn.passthrough.createOutputQueue(maxSize=1, blocking=False)
    qL = stereo.rectifiedLeft.createOutputQueue(maxSize=1, blocking=False)
    p.start(); t0 = time.time(); d = h = pr = l = None; nd = 0
    while time.time() - t0 < 12:
        m = qD.tryGet()
        if m is not None: d = m; nd += 1
        h = qH.tryGet() or h; pr = qP.tryGet() or pr; l = qL.tryGet() or l; time.sleep(0.01)
    f = lambda m: f"{m.getWidth()}x{m.getHeight()} {m.getType().name}" if m is not None else "none"
    print(f"depth={f(d)} depth_fps~{nd/12:.1f}  hat_in={f(h)}  person_in={f(pr)}  rectL={f(l)}", flush=True)
    os._exit(0)
