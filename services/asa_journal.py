#!/usr/bin/env python3
"""asa-journal - session and health journal for the ASA unit."""
import json, os, signal, socket, subprocess, sys, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/var/lib/asa/journal"
EVENTS = os.path.join(ROOT, "events.jsonl")
STATE = os.path.join(ROOT, "state.json")
HEARTBEAT = os.path.join(ROOT, "heartbeat")
VERSION_FILE = "/var/lib/asa/version.json"
CHANGELOG_PATH = "/home/arduino/asa-repo/CHANGELOG.md"
HEARTBEAT_SEC = 60
WATCH_SEC = 30
QUIET_CYCLES = 4
PORT = 8095
WATCHED = ["asa-vision", "asa-voice"]
_stop = threading.Event()

def now(): return datetime.now(timezone.utc)
def iso(dt=None): return (dt or now()).isoformat(timespec="seconds")

def kernel_uptime():
    try:
        with open("/proc/uptime") as f: return round(float(f.read().split()[0]), 1)
    except Exception: return None

def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f: return f.read().strip()
    except Exception: return "unknown"

def deployed_version():
    try:
        with open(VERSION_FILE) as f: v = json.load(f)
        return {"version": v.get("version"), "commit": v.get("commit")}
    except Exception: return {"version": None, "commit": None}

def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def emit(kind, **fields):
    rec = {"ts": iso(), "kind": kind, "uptime_s": kernel_uptime()}
    rec.update(fields)
    try:
        os.makedirs(ROOT, exist_ok=True)
        with open(EVENTS, "a") as f:
            f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
    except Exception as e:
        print("journal: could not write event: %s" % e, file=sys.stderr)
    return rec

def load_state():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return None

def save_state(**fields): write_atomic(STATE, json.dumps(fields, indent=1))

def last_heartbeat():
    try:
        with open(HEARTBEAT) as f: return float(f.read().strip())
    except Exception: return None

def reconcile_previous_session():
    prev = load_state(); started = time.time(); ver = deployed_version()
    if prev is None:
        emit("boot", first_run=True, boot_id=boot_id(), **ver); return
    if prev.get("boot_id") == boot_id():
        emit("session_restart", service="asa-journal",
             prev_started=prev.get("started_iso"), **ver); return
    clean = prev.get("clean_shutdown_ts")
    hb = last_heartbeat() or prev.get("last_heartbeat")
    if clean:
        emit("boot", boot_id=boot_id(), clean_previous=True,
             downtime_s=round(started - clean, 1), **ver)
    else:
        died = hb or prev.get("started")
        down = started - died if died else None
        emit("power_loss", boot_id=prev.get("boot_id"),
             last_seen=iso(datetime.fromtimestamp(died, timezone.utc)) if died else None,
             uncertainty_s=HEARTBEAT_SEC)
        emit("boot", boot_id=boot_id(), clean_previous=False,
             downtime_s=round(down, 1) if down else None, **ver)

def open_session():
    os.makedirs(ROOT, exist_ok=True)
    reconcile_previous_session()
    emit("session_start", boot_id=boot_id(), **deployed_version())
    save_state(boot_id=boot_id(), started=time.time(), started_iso=iso(),
               last_heartbeat=time.time(), clean_shutdown_ts=None,
               **deployed_version())

def close_session(reason="sigterm"):
    ts = time.time(); st = load_state() or {}
    emit("shutdown", reason=reason, session_s=round(ts - st.get("started", ts), 1))
    st["clean_shutdown_ts"] = ts; st["last_heartbeat"] = ts
    save_state(**st)

def heartbeat_loop():
    while not _stop.wait(HEARTBEAT_SEC):
        try:
            write_atomic(HEARTBEAT, str(time.time()))
        except Exception as e:
            print("journal: heartbeat failed: %s" % e, file=sys.stderr)

def unit_states(units=None):
    units = units or WATCHED
    try:
        out = subprocess.run(["systemctl", "show", *units, "-p", "ActiveState",
                              "-p", "NRestarts"], capture_output=True,
                             text=True, timeout=5).stdout
        blocks = [b for b in out.strip().split("\n\n") if b.strip()]
        result = {}
        for unit, block in zip(units, blocks):
            d = dict(l.split("=", 1) for l in block.splitlines() if "=" in l)
            result[unit] = (d.get("ActiveState", "unknown"),
                            int(d.get("NRestarts", 0) or 0))
        for u in units: result.setdefault(u, ("unknown", 0))
        return result
    except Exception:
        return {u: ("unknown", 0) for u in units}

def service_watch_loop():
    seen = unit_states(); storms = {}
    for u, (state, _) in seen.items():
        if state not in ("active", "unknown"):
            emit("service_down", service=u, state=state)
    while not _stop.wait(WATCH_SEC):
        current = unit_states()
        for u in WATCHED:
            state, restarts = current[u]
            old_state, old_restarts = seen[u]
            if restarts > old_restarts:
                delta = restarts - old_restarts
                st = storms.get(u)
                if st is None:
                    emit("service_restart", service=u, restarts=restarts)
                    storms[u] = {"since": time.time(), "count": delta, "quiet": 0}
                else:
                    st["count"] += delta; st["quiet"] = 0
            elif u in storms:
                storms[u]["quiet"] += 1
                if storms[u]["quiet"] >= QUIET_CYCLES:
                    st = storms.pop(u)
                    if st["count"] > 1:
                        emit("abnormality", service=u, detail="restart storm",
                             restarts=st["count"],
                             duration_s=round(time.time() - st["since"], 1))
            if state != old_state:
                emit("service_state", service=u, state=state, was=old_state)
                if state == "failed":
                    emit("abnormality", service=u, detail="unit entered failed state")
            seen[u] = (state, restarts)
    for u, st in storms.items():
        if st["count"] > 1:
            emit("abnormality", service=u, detail="restart storm (open at stop)",
                 restarts=st["count"], duration_s=round(time.time() - st["since"], 1))

ABNORMAL = {"power_loss", "abnormality", "service_restart", "service_down"}

def rollup(path=None):
    path = path or EVENTS
    sessions, up, down, abnormal = [], 0.0, 0.0, []
    open_boot = None
    try:
        with open(path) as f: lines = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError: lines = []
    for e in lines:
        k = e.get("kind")
        if k in ABNORMAL: abnormal.append(e)
        if k in ("boot", "session_start"):
            if k == "boot" and e.get("downtime_s"): down += e["downtime_s"]
            open_boot = e
        elif k == "shutdown" and open_boot:
            up += e.get("session_s", 0) or 0
            sessions.append({"from": open_boot["ts"], "to": e["ts"],
                             "seconds": e.get("session_s"), "clean": True,
                             "version": open_boot.get("version")})
            open_boot = None
    live = 0.0
    if open_boot:
        st = load_state() or {}
        live = time.time() - st.get("started", time.time()); up += live
        sessions.append({"from": open_boot["ts"], "to": None,
                         "seconds": round(live, 1), "clean": None,
                         "version": open_boot.get("version")})
    total = up + down
    return {"generated": iso(),
            "boots": sum(1 for e in lines if e.get("kind") == "boot"),
            "power_losses": sum(1 for e in lines if e.get("kind") == "power_loss"),
            "uptime_s": round(up, 1), "downtime_s": round(down, 1),
            "availability_pct": round(up / total * 100, 2) if total else None,
            "current_session_s": round(live, 1), "abnormalities": len(abnormal),
            "recent_abnormalities": abnormal[-10:], "sessions": sessions[-20:],
            "version": deployed_version()}

def human(sec):
    if sec is None: return "-"
    sec = int(sec); d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m, s = divmod(r, 60)
    if d: return "%dd %dh %dm" % (d, h, m)
    if h: return "%dh %dm" % (h, m)
    return "%dm %ds" % (m, s)

def print_report():
    r = rollup()
    print("ASA journal - %s" % r["generated"])
    print("  version        %s (%s)" % (r["version"]["version"] or "-",
                                        (r["version"]["commit"] or "")[:8]))
    print("  boots          %s   power losses %s" % (r["boots"], r["power_losses"]))
    print("  uptime         %s" % human(r["uptime_s"]))
    print("  downtime       %s" % human(r["downtime_s"]))
    print("  availability   %s%%" % r["availability_pct"])
    print("  this session   %s" % human(r["current_session_s"]))
    print("  abnormalities  %s" % r["abnormalities"])
    for e in r["recent_abnormalities"]:
        print("    %s  %-16s %s" % (e["ts"], e["kind"],
                                    e.get("service") or e.get("detail") or ""))

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ASA journal</title><style>
:root{--bg:#15181c;--card:#191d22;--line:#333b44;--dim:#98a4b0;--mute:#5a6672;
--fg:#e9edf1;--ok:#3fb27f;--warn:#f0a202;--bad:#ef5a52}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-sans-serif,"Segoe UI",Roboto,system-ui,sans-serif;padding:20px}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:19px;margin:0 0 3px}
.sub{color:var(--mute);font-size:12.5px;margin:0 0 18px}
.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:11px;margin-bottom:16px}
.kpi div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.kpi b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.kpi span{font-size:11px;color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 15px;margin-bottom:13px}
h2{font-size:12px;color:var(--dim);margin:0 0 10px;font-weight:650}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td{padding:6px 8px 6px 0;border-bottom:1px solid #262d34;font-size:12.5px}
tr:last-child td{border-bottom:none}
td.d{color:var(--dim)}td.n{text-align:right;color:var(--dim)}
.pill{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
.live{color:var(--ok)}
.empty{color:var(--mute);font-size:12.5px}
.nav{display:flex;gap:7px;margin-bottom:14px}
.nav button{background:var(--card);border:1px solid var(--line);color:var(--dim);
border-radius:8px;padding:6px 14px;font:inherit;font-size:12.5px;cursor:pointer}
.nav button.on{color:var(--fg);border-color:var(--mute)}
.md h3{font-size:14px;margin:18px 0 3px;color:var(--fg)}
.md h3:first-child{margin-top:0}
.md h4{font-size:11.5px;margin:11px 0 4px;color:var(--dim);font-weight:650;
letter-spacing:.04em;text-transform:uppercase}
.md ul{margin:0;padding-left:18px}
.md li{font-size:12.5px;color:var(--dim);margin-bottom:3px}
.md code{background:#0f1215;border-radius:4px;padding:1px 5px;font-size:11.5px;color:var(--fg)}
.md .cur{color:var(--ok)}
.md p{font-size:12.5px;color:var(--mute);margin:0 0 12px}
@media(max-width:560px){.kpi{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<h1>ASA journal</h1><p class="sub" id="sub">loading</p>
<div class="nav"><button id="t1b" class="on" onclick="tab(0)">Journal</button><button id="t2b" onclick="tab(1)">Changelog</button></div>
<div id="v0">
<div class="kpi">
<div><b id="k1">-</b><span>uptime</span></div>
<div><b id="k2">-</b><span>availability</span></div>
<div><b id="k3">-</b><span>boots</span></div>
<div><b id="k4">-</b><span>power losses</span></div></div>
<div class="card"><h2>SESSIONS</h2><table id="ses"></table></div>
<div class="card"><h2>ABNORMALITIES</h2><table id="abn"></table></div>
</div>
<div id="v1" style="display:none"><div class="card md" id="md">loading</div></div>
</div><script>
function hum(s){if(s==null)return '-';s=Math.floor(s);
var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);
if(d)return d+'d '+h+'h';if(h)return h+'h '+m+'m';return m+'m '+(s%60)+'s';}
function when(t){return t?t.replace('T',' ').replace('+00:00',' UTC'):'-';}
function cls(k){return k=='power_loss'||k=='abnormality'?'bad':'warn';}
function load(){fetch('/report').then(r=>r.json()).then(function(r){
 document.getElementById('k1').textContent=hum(r.uptime_s);
 document.getElementById('k2').textContent=(r.availability_pct==null?'-':r.availability_pct+'%');
 document.getElementById('k3').textContent=r.boots;
 document.getElementById('k4').textContent=r.power_losses;
 window._ver=r.version.version||'';
 document.getElementById('sub').innerHTML='version '+(r.version.version||'-')+
  ' &middot; '+(r.version.commit||'').slice(0,8)+' &middot; down '+hum(r.downtime_s)+
  ' &middot; <span class="live">this session '+hum(r.current_session_s)+'</span>';
 var s=r.sessions.slice().reverse().map(function(x){
  return '<tr><td><span class="pill '+(x.to?'ok':'live ok')+'"></span>'+when(x.from)+
   '</td><td class="d">'+(x.to?when(x.to):'running')+'</td><td class="n">'+
   hum(x.seconds)+'</td></tr>';}).join('');
 document.getElementById('ses').innerHTML=s||'<tr><td class="empty">none yet</td></tr>';
 var a=r.recent_abnormalities.slice().reverse().map(function(e){
  return '<tr><td><span class="pill '+cls(e.kind)+'"></span>'+when(e.ts)+
   '</td><td class="d">'+e.kind+'</td><td class="d">'+
   (e.service||e.detail||'')+'</td></tr>';}).join('');
 document.getElementById('abn').innerHTML=a||'<tr><td class="empty">none</td></tr>';
}).catch(function(){document.getElementById('sub').textContent='journal unreachable';});}
function tab(i){document.getElementById('v0').style.display=i?'none':'block';
 document.getElementById('v1').style.display=i?'block':'none';
 document.getElementById('t1b').className=i?'':'on';
 document.getElementById('t2b').className=i?'on':'';
 if(i&&!window._md)loadmd();}
function esc(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function loadmd(){window._md=1;fetch('/changelog').then(r=>r.text()).then(function(t){
 var cur=(window._ver||''),out=[],ul=false;
 t.split('\\n').forEach(function(l){
  l=esc(l).replace(/`([^`]+)`/g,'<code>$1</code>');
  var m;
  if(m=l.match(/^## (.*)/)){if(ul){out.push('</ul>');ul=false;}
   var hit=cur&&m[1].indexOf('['+cur+']')===0;
   out.push('<h3'+(hit?' class="cur"':'')+'>'+m[1]+(hit?' &larr; running':'')+'</h3>');}
  else if(m=l.match(/^### (.*)/)){if(ul){out.push('</ul>');ul=false;}
   out.push('<h4>'+m[1]+'</h4>');}
  else if(m=l.match(/^# (.*)/)){}
  else if(m=l.match(/^[-*] (.*)/)){if(!ul){out.push('<ul>');ul=true;}
   out.push('<li>'+m[1]+'</li>');}
  else if(l.trim()){if(ul){out.push('</ul>');ul=false;}out.push('<p>'+l+'</p>');}
 });
 if(ul)out.push('</ul>');
 document.getElementById('md').innerHTML=out.join('');
}).catch(function(){document.getElementById('md').textContent='changelog unavailable';});}
load();setInterval(load,10000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    timeout = 15
    protocol_version = "HTTP/1.0"
    def _send(self, obj, code=200):
        body = json.dumps(obj, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/ui"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/health"):
            st = load_state() or {}
            self._send({"ok": True, "host": socket.gethostname(),
                        "session_s": round(time.time() - st.get("started", time.time()), 1)})
        elif self.path.startswith("/report"):
            self._send(rollup())
        elif self.path.startswith("/changelog"):
            try:
                with open(CHANGELOG_PATH, encoding="utf-8") as f:
                    body = f.read().encode()
            except Exception:
                body = b"# No changelog\n\nCHANGELOG.md not found on this unit."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/events"):
            try:
                with open(EVENTS) as f: lines = [json.loads(l) for l in f if l.strip()]
            except FileNotFoundError: lines = []
            self._send(lines[-200:])
        else:
            self._send({"error": "not found"}, 404)
    def log_message(self, *a): pass

def serve():
    open_session()
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=service_watch_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("asa-journal listening on :%d" % PORT, flush=True)
    _stop.wait()
    close_session("sigterm")
    threading.Thread(target=httpd.shutdown, daemon=True).start()
    print("asa-journal stopped cleanly", flush=True)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve": serve()
    elif cmd == "report": print_report()
    elif cmd == "note": emit("note", text=" ".join(sys.argv[2:])); print("noted")
    else: print(__doc__); sys.exit(1)
