#!/usr/bin/env python3
"""ASA vision v3 - person-anchored PPE check: the hard hat must be ON the head.
   /  live view   |   /video  MJPEG   |   /state  JSON"""
import json, time, threading, signal, sys
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import cv2
import depthai as dai
import os, io, csv, datetime

HAT_MODEL = "/home/arduino/models/hardhat.tar.xz"   # Hardhat / NO-Hardhat
PERSON_MODEL = "luxonis/yolov6-nano:r2-coco-512x288"                          # COCO 'person'
PORT, FPS = 8090, 8
PERSON_CONF, HAT_CONF, BARE_CONF = 0.50, 0.45, 0.75
Z_MIN, Z_MAX = 300, 2800                  # mm: alert zone
HEAD_UP, HEAD_DOWN, HEAD_PADX = 0.15, 0.28, 0.10   # head zone relative to person box
DZ_MAX = 700                              # mm: hat must be at the person's depth
WIN, ON_N, SAFE_N, HOLD_S = 10, 7, 6, 4.0
HAT_MEMORY_S = 1.0          # ignore hat dropouts shorter than this at the same spot
FONT = cv2.FONT_HERSHEY_SIMPLEX

state = {"ts": 0, "fps": 0.0, "status": "STARTING", "persons": [], "hats": [],
         "violation": None, "events": 0, "clear_reason": None}
lock = threading.Lock()
jpg = {"data": None, "seq": 0}

PAGE = b"""<!doctype html><html><head><meta name=viewport content="width=device-width">
<title>ASA Vision</title><style>
body{margin:0;background:#111;color:#eee;font-family:system-ui,sans-serif}
#w{max-width:960px;margin:auto;padding:12px}
img{width:100%;border-radius:8px;background:#000}
#st{font-size:22px;font-weight:700;padding:10px 14px;border-radius:8px;margin:10px 0}
.CLEAR{background:#1b5e20}.VIOLATION{background:#b71c1c}.STARTING{background:#555}
pre{background:#222;padding:10px;border-radius:8px;font-size:12px;overflow:auto;max-height:260px}
</style></head><body><div id=w>
<div id=st class=STARTING>STARTING</div><img src="/video"><pre id=js></pre></div>
<script>
async function tick(){try{const s=await (await fetch('/state')).json();
const e=document.getElementById('st');e.className=s.status;
e.textContent=s.status+(s.violation?' - '+s.violation.reason.toUpperCase()+' at '+(s.violation.z/1000).toFixed(2)+' m':'')+'  |  '+s.fps+' fps  |  events '+s.events;
document.getElementById('js').textContent=JSON.stringify(s,null,1);}catch(err){}}
setInterval(tick,500);tick();
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, "text/html", PAGE)
        elif path == "/evidence":
            self._send(200, "application/json", json.dumps(evidence_list()).encode())
        elif path == "/evidence.csv":
            body = evidence_csv()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", 'attachment; filename="asa_evidence.csv"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/evidence/") and path.endswith(".jpg"):
            f = os.path.normpath(os.path.join(EVID_DIR, path[len("/evidence/"):]))
            if f.startswith(EVID_DIR + os.sep) and os.path.isfile(f):
                with open(f, "rb") as fh:
                    self._send(200, "image/jpeg", fh.read())
            else:
                self._send(404, "text/plain", b"not found")
        elif path == "/state":
            with lock:
                body = json.dumps(state).encode()
            self._send(200, "application/json", body)
        elif path == "/video":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = -1
            try:
                while True:
                    with lock:
                        data, seq = jpg["data"], jpg["seq"]
                    if data is not None and seq != last:
                        last = seq
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._send(404, "text/plain", b"not found")
    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, *a):
        pass

EVID_DIR = "/home/arduino/asa-evidence"
UNIT_ID = "ASA-01"
RETAIN_DAYS = 14
COMPLY_HOLD_S, COMPLY_GAP_S = 3.0, 30.0
recent = deque(maxlen=200)
evid_state = {"day": None, "n": 0}

def load_recent():
    os.makedirs(EVID_DIR, exist_ok=True)
    for d in sorted(os.listdir(EVID_DIR))[-3:]:
        f = os.path.join(EVID_DIR, d, "events.jsonl")
        if os.path.isfile(f):
            for line in open(f):
                try:
                    recent.append(json.loads(line))
                except Exception:
                    pass

def cleanup_old():
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETAIN_DAYS)).isoformat()
    for d in os.listdir(EVID_DIR):
        p = os.path.join(EVID_DIR, d)
        if os.path.isdir(p) and len(d) == 10 and d < cutoff:
            for f in os.listdir(p):
                os.remove(os.path.join(p, f))
            os.rmdir(p)

def save_evidence(rec, jpg_bytes=None):
    now = datetime.datetime.now()
    day = now.strftime("%Y-%m-%d")
    ddir = os.path.join(EVID_DIR, day)
    os.makedirs(ddir, exist_ok=True)
    if evid_state["day"] != day:
        evid_state["day"] = day
        try:
            cleanup_old()
        except Exception as e:
            print(f"[asa-vision] cleanup error: {e}", flush=True)
    evid_state["n"] += 1
    rec = dict(rec, ts=now.isoformat(timespec="seconds"), unit=UNIT_ID)
    if jpg_bytes is not None and rec["type"] != "UNRESOLVED":
        name = f"{now.strftime('%H%M%S')}_{evid_state['n']:04d}_{rec['type'].lower()}.jpg"
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(jpg_bytes)
        rec["image"] = f"{day}/{name}"
    with open(os.path.join(ddir, "events.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    with lock:
        recent.append(rec)
    print(f"[asa-vision] evidence {rec['type']}: {rec.get('image', 'log only')}", flush=True)

def evidence_list():
    out = []
    try:
        for d in sorted(os.listdir(EVID_DIR))[-2:]:
            f = os.path.join(EVID_DIR, d, "events.jsonl")
            if os.path.isfile(f):
                with open(f) as fh:
                    for line in fh:
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            pass
    except Exception:
        pass
    return out[::-1][:100]

def evidence_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["timestamp", "unit", "type", "event", "reason", "distance_m", "response_s", "image"])
    for d in sorted(os.listdir(EVID_DIR)):
        f = os.path.join(EVID_DIR, d, "events.jsonl")
        if not os.path.isfile(f):
            continue
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            z = r.get("z_mm")
            w.writerow([r.get("ts"), r.get("unit", ""), r.get("type"), r.get("event", ""), r.get("reason", ""),
                        f"{z/1000:.2f}" if z else "", r.get("response_s", ""), r.get("image", "")])
    return out.getvalue().encode()

def parse(msg, labels, keep, conf_min):
    out = []
    if msg is None:
        return out
    for d in msg.detections:
        lab, c = labels[d.label], d.spatialCoordinates
        if lab in keep and d.confidence >= conf_min and Z_MIN <= c.z <= Z_MAX:
            out.append({"label": lab, "conf": round(d.confidence, 2),
                        "x1": round(d.xmin, 3), "y1": round(d.ymin, 3),
                        "x2": round(d.xmax, 3), "y2": round(d.ymax, 3),
                        "x": int(c.x), "y": int(c.y), "z": int(c.z)})
    return out

def evaluate(persons, hats, heads):
    for P in persons:
        pw, ph = P["x2"] - P["x1"], P["y2"] - P["y1"]
        zx1, zx2 = P["x1"] - HEAD_PADX * pw, P["x2"] + HEAD_PADX * pw
        zy1, zy2 = P["y1"] - HEAD_UP * ph, P["y1"] + HEAD_DOWN * ph
        worn = held = False
        for H in hats:
            if abs(H["z"] - P["z"]) > DZ_MAX:
                continue
            cx, cy = (H["x1"] + H["x2"]) / 2, (H["y1"] + H["y2"]) / 2
            if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                worn = True
            elif zx1 <= cx <= zx2 and cy > zy2:
                held = True
        if worn:
            P["status"], P["reason"] = "SAFE", "hardhat on head"
        elif P["y1"] < 0.02:
            P["status"], P["reason"] = "CHECKING", "head out of view"
        else:
            bare = any(zx1 <= (B["x1"] + B["x2"]) / 2 <= zx2 and zy1 <= (B["y1"] + B["y2"]) / 2 <= zy2
                       and abs(B["z"] - P["z"]) <= DZ_MAX for B in heads)
            if bare:
                P["status"] = "NO_HARDHAT"
                P["reason"] = "hardhat not on head" if held else "no hardhat"
            else:
                P["status"], P["reason"] = "CHECKING", "no head evidence"
    return persons

def main():
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    load_recent()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bad_hist, safe_hist = deque(maxlen=WIN), deque(maxlen=WIN)
    status, events, last_bad, last_bad_t, clear_reason = "CLEAR", 0, None, 0.0, None
    SIZE = (640, 400)
    with dai.Pipeline() as p:
        cam   = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
        left  = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
        right = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)
        stereo = p.create(dai.node.StereoDepth)
        left.requestOutput(SIZE).link(stereo.left)
        right.requestOutput(SIZE).link(stereo.right)
        hat_nn = p.create(dai.node.SpatialDetectionNetwork).build(cam, stereo, dai.NNArchive(HAT_MODEL), fps=FPS)
        per_nn = p.create(dai.node.SpatialDetectionNetwork).build(cam, stereo, dai.NNModelDescription(PERSON_MODEL), fps=FPS)
        for nn in (hat_nn, per_nn):
            nn.setConfidenceThreshold(0.4)
            nn.setDepthLowerThreshold(100)
            nn.setDepthUpperThreshold(8000)
        hat_labels, per_labels = hat_nn.getClasses(), per_nn.getClasses()
        hq = hat_nn.out.createOutputQueue(maxSize=4, blocking=False)
        pq = per_nn.out.createOutputQueue(maxSize=4, blocking=False)
        vq = hat_nn.passthrough.createOutputQueue(maxSize=4, blocking=False)
        sq = per_nn.passthrough.createOutputQueue(maxSize=1, blocking=False)
        p.start()
        print(f"[asa-vision] v3 running -> http://0.0.0.0:{PORT}/", flush=True)
        n, t0, fps = 0, time.monotonic(), 0.0
        last_p, last_p_t, sizes_done = None, 0.0, False
        pending, viol_t, safe_since, last_comply_t = [], 0.0, None, -1e9
        safe_mem = []
        while p.isRunning():
            hmsg = hq.get(); n += 1
            now = time.monotonic()
            pm = pq.tryGet()
            if pm is not None:
                last_p, last_p_t = pm, now
            if now - last_p_t > 0.5:
                last_p = None
            pf = vq.tryGet()
            frame = pf.getCvFrame() if pf is not None else None
            if not sizes_done and pf is not None:
                sf = sq.tryGet()
                if sf is not None:
                    print(f"[asa-vision] NN inputs: hardhat {pf.getWidth()}x{pf.getHeight()}, "
                          f"person {sf.getWidth()}x{sf.getHeight()}", flush=True)
                    sizes_done = True

            hats = parse(hmsg, hat_labels, ("Hardhat",), HAT_CONF)
            bares = parse(hmsg, hat_labels, ("NO-Hardhat",), BARE_CONF)
            heads = parse(hmsg, hat_labels, ("NO-Hardhat",), 0.45)
            persons = evaluate(parse(last_p, per_labels, ("person",), PERSON_CONF), hats, heads)

            for P in persons:
                if P["status"] == "SAFE":
                    safe_mem.append((now, P["x"], P["z"]))
            safe_mem = [m for m in safe_mem if now - m[0] <= HAT_MEMORY_S]
            bad_people = [P for P in persons if P["status"] == "NO_HARDHAT" and not any(
                abs(P["x"] - mx) < 400 and abs(P["z"] - mz) < 600 for _, mx, mz in safe_mem)]
            frame_bad = bool(bad_people) or (not persons and bool(bares))
            frame_safe = bool(persons) and all(P["status"] == "SAFE" for P in persons)
            if bad_people:
                t = min(bad_people, key=lambda P: P["z"])
                last_bad, last_bad_t = {"reason": t["reason"], "x": t["x"], "y": t["y"], "z": t["z"]}, now
            elif not persons and bares:
                t = min(bares, key=lambda B: B["z"])
                last_bad, last_bad_t = {"reason": "no hardhat", "x": t["x"], "y": t["y"], "z": t["z"]}, now
            bad_hist.append(1 if frame_bad else 0)
            safe_hist.append(1 if frame_safe else 0)
            if frame_safe and status == "CLEAR":
                safe_since = safe_since or now
                if now - safe_since >= COMPLY_HOLD_S and now - last_comply_t >= COMPLY_GAP_S:
                    nearest = min(persons, key=lambda P: P["z"])
                    pending.append({"type": "COMPLIANT", "reason": "hardhat on head",
                                    "people": len(persons), "z_mm": nearest["z"]})
                    last_comply_t = now
            else:
                safe_since = None

            if status == "CLEAR" and sum(bad_hist) >= ON_N and last_bad:
                status, events, clear_reason = "VIOLATION", events + 1, None
                print(f"[asa-vision] VIOLATION #{events}: {last_bad['reason']} at X={last_bad['x']} Z={last_bad['z']} mm", flush=True)
                viol_t = now
                pending.append({"type": "VIOLATION", "event": events, "reason": last_bad["reason"],
                                "x_mm": last_bad["x"], "z_mm": last_bad["z"], "people": len(persons)})
            elif status == "VIOLATION":
                reason = None
                if sum(safe_hist) >= SAFE_N and now - last_bad_t > 1.0:
                    reason = "hardhat on head"
                elif now - last_bad_t > HOLD_S:
                    reason = "left zone"
                if reason:
                    status, clear_reason = "CLEAR", reason
                    print(f"[asa-vision] CLEAR ({reason})", flush=True)
                    kind = "RESOLVED" if reason == "hardhat on head" else "UNRESOLVED"
                    pending.append({"type": kind, "event": events, "reason": reason,
                                    "response_s": round(now - viol_t, 1)})
                    if kind == "RESOLVED":
                        last_comply_t = now

            if now - t0 >= 5:
                fps, n, t0 = n / (now - t0), 0, now

            if frame is not None:
                h, w = frame.shape[:2]
                for P in persons:
                    col = (0, 200, 0) if P["status"] == "SAFE" else (0, 0, 255) if P["status"] == "NO_HARDHAT" else (160, 160, 160)
                    x1, y1, x2, y2 = int(P["x1"]*w), int(P["y1"]*h), int(P["x2"]*w), int(P["y2"]*h)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
                    cv2.putText(frame, f"{P['reason']} {P['z']/1000:.1f}m", (x1 + 3, min(h - 4, y1 + 42)), FONT, 0.45, col, 1)
                for H in hats:
                    cv2.rectangle(frame, (int(H["x1"]*w), int(H["y1"]*h)), (int(H["x2"]*w), int(H["y2"]*h)), (0, 220, 255), 2)
                for B in bares:
                    cv2.rectangle(frame, (int(B["x1"]*w), int(B["y1"]*h)), (int(B["x2"]*w), int(B["y2"]*h)), (0, 0, 255), 1)
                cv2.rectangle(frame, (0, 0), (w, 26), (0, 0, 180) if status == "VIOLATION" else (0, 110, 0), -1)
                cv2.putText(frame, f"ASA-01 {status} | {time.strftime('%d-%m-%Y %H:%M:%S')} | {fps:.0f} fps", (8, 18), FONT, 0.5, (255, 255, 255), 1)
                okj, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if okj:
                    with lock:
                        jpg["data"], jpg["seq"] = enc.tobytes(), jpg["seq"] + 1
                    while pending:
                        save_evidence(pending.pop(0), enc.tobytes())

            with lock:
                state.update(ts=round(time.time(), 2), fps=round(fps, 1), status=status,
                             persons=persons, hats=hats, events=events, clear_reason=clear_reason,
                             violation=last_bad if status == "VIOLATION" else None)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[asa-vision] stopped", flush=True)
