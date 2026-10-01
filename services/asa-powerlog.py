#!/usr/bin/env python3
"""asa-powerlog: samples /asa/power every 30 s into a CSV and serves it on :8096 for the dashboard."""
import csv, json, os, threading, time, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SRC = ["http://127.0.0.1:7000/asa/power", "http://127.0.0.1:7000/api/asa/power"]
CSV = "/var/lib/asa/power.csv"
PORT = 8096
PERIOD = 30

def sample():
    for u in SRC:
        try:
            with urllib.request.urlopen(u, timeout=3) as r:
                d = json.load(r)
            if d.get("ok"):
                return float(d["pack_v"]), int(bool(d.get("estop")))
        except Exception:
            pass
    return None

def logger():
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    new = not os.path.exists(CSV)
    while True:
        s = sample()
        if s:
            with open(CSV, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["ts", "pack_v", "estop"]); new = False
                w.writerow([int(time.time()), f"{s[0]:.2f}", s[1]])
        time.sleep(PERIOD)

def rows_since(t0):
    out = []
    try:
        with open(CSV) as f:
            for r in csv.reader(f):
                if r and r[0].isdigit() and int(r[0]) >= t0:
                    out.append([int(r[0]), float(r[1]), int(r[2])])
    except FileNotFoundError:
        pass
    return out

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/log":
            h = float(parse_qs(u.query).get("hours", ["4"])[0])
            body = json.dumps({"ok": True, "rows": rows_since(time.time() - h * 3600)}).encode()
            return self._send(200, body, "application/json")
        if u.path == "/power.csv":
            try: body = open(CSV, "rb").read()
            except FileNotFoundError: body = b"ts,pack_v,estop\n"
            self.send_response(200); self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", "attachment; filename=asa-power.csv")
            self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); return self.wfile.write(body)
        self._send(404, b"not found", "text/plain")

if __name__ == "__main__":
    threading.Thread(target=logger, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
