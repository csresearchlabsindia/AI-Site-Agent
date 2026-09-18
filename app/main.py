"""ASA Mission Control v3 - LED states via Bridge + "Hey Arduino" voice command (keyword spotting)."""
import json, threading, time, urllib.request
from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.keyword_spotting import KeywordSpotting

ui = WebUI()   # serves assets/index.html on port 7000

CANDIDATES = ["http://172.17.0.1:8090/state", "http://192.168.1.122:8090/state",
              "http://host.docker.internal:8090/state", "http://127.0.0.1:8090/state"]
VOICE_CMD = ["http://172.17.0.1:8091", "http://192.168.1.122:8091"]
IDLE, VIOLATION, OFFLINE, THANKS, BOOT, COMPLIANT, CHECKING, NOT_ON_HEAD, LISTEN, THINK = range(10)
NAMES = ["IDLE", "VIOLATION", "OFFLINE", "THANKS", "BOOT", "COMPLIANT", "CHECKING", "NOT_ON_HEAD", "LISTEN", "THINK", "ATTENTION"]
st = {"url": None, "good": None, "code": None, "sent": 0.0, "status": None, "seen": False}
kw = {"t": 0.0, "hold": False, "start": 0.0, "idle": 0}

def fetch(u, timeout=0.8):
    with urllib.request.urlopen(u, timeout=timeout) as r:
        return json.load(r)

def find_url():
    order = ([st["good"]] if st["good"] else []) + [u for u in CANDIDATES if u != st["good"]]
    for u in order:
        try:
            fetch(u, 0.5)
            if u != st["good"]:
                print(f"[asa-mc] vision service found at {u}", flush=True)
            st["good"] = u
            return u
        except Exception:
            pass
    return None

def send(code):
    try:
        Bridge.call("set_status", code)
    except Exception as e:
        print(f"[asa-mc] bridge error: {e}", flush=True)

# ---------- voice command ----------
def request_report():
    err = None
    for base in VOICE_CMD:
        try:
            urllib.request.urlopen(base + "/report?src=keyword", timeout=2).read()
            return
        except Exception as e:
            err = e
    print(f"[asa-mc] voice service unreachable: {err}", flush=True)

def on_keyword():
    now = time.monotonic()
    if kw["hold"] or now - kw["t"] < 8:
        return
    kw["t"] = now
    kw.update(hold=True, start=now, idle=0, playing=False)
    print("[asa-mc] keyword 'hey arduino' -> status report", flush=True)
    send(THINK)
    threading.Thread(target=request_report, daemon=True).start()

# ---------- voice settings API (dashboard -> App Lab -> voice service) ----------
import urllib.error
from urllib.parse import quote

def voice_get(path, timeout=3):
    err = None
    for base in VOICE_CMD:
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            try:
                return json.loads(body)
            except ValueError:
                return {"ok": True}
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {"error": f"HTTP {e.code}"}
        except Exception as e:
            err = e
    return {"error": f"voice service unreachable: {err}"}

def api_status():
    return voice_get("/status")

def api_lang(value: str = ""):
    return voice_get("/lang?set=" + quote(value.strip().lower())) if value else voice_get("/lang")

def api_volume(value: str = ""):
    return voice_get("/volume?set=" + quote(value.strip())) if value else voice_get("/volume")

def api_test():
    return voice_get("/say?key=startup")

ui.expose_api("GET", "/asa/status", api_status)
ui.expose_api("GET", "/asa/lang", api_lang)
ui.expose_api("GET", "/asa/volume", api_volume)
ui.expose_api("GET", "/asa/test", api_test)

_bl = threading.Lock()
drv = {"t": 0.0, "moving": False}

def api_drive(l: str = "0", r: str = "0"):
    try:
        li = max(-200, min(200, int(float(l)))); ri = max(-200, min(200, int(float(r))))
    except ValueError:
        return {"error": "bad value"}
    try:
        with _bl: Bridge.call("drive", li, ri)
    except Exception as e:
        return {"error": str(e)}
    drv["t"], drv["moving"] = time.monotonic(), (li != 0 or ri != 0)
    return {"ok": True, "l": li, "r": ri}

def api_halt():
    drv["moving"] = False
    try:
        with _bl: Bridge.call("halt")
    except Exception as e:
        return {"error": str(e)}
    return {"ok": True}

ui.expose_api("GET", "/asa/drive", api_drive)
ui.expose_api("GET", "/asa/halt", api_halt)
print(f"[asa-mc] voice settings API at '{getattr(ui, '_api_path_prefix', '')}/asa/*'", flush=True)

spotter = KeywordSpotting()
spotter.on_detect("hey_arduino", on_keyword)

def voice_busy():
    for base in VOICE_CMD:
        try:
            d = fetch(base + "/busy", 0.5)
            kw["playing"] = bool(d.get("playing", d.get("busy")))
            return bool(d.get("busy"))
        except Exception:
            pass
    return None

def hold_listen():
    """Keep LISTEN on the matrix until the spoken report ends. True = still holding."""
    el = time.monotonic() - kw["start"]
    b = voice_busy()
    kw["idle"] = kw["idle"] + 1 if b is False else 0
    alarm = False
    if st["url"]:
        try:
            alarm = fetch(st["url"], 0.5).get("status") == "VIOLATION"
        except Exception:
            pass
    if el > 25 or alarm or (b is None and el > 3) or (el > 1.5 and kw["idle"] >= 2):
        kw["hold"] = False
        st["code"] = None   # push the vision state out immediately
        print(f"[asa-mc] LISTEN released after {el:.1f}s", flush=True)
        return False
    send(LISTEN if kw.get("playing") else THINK)
    time.sleep(0.25)
    return True

# ---------- degraded conditions ----------
ATTENTION = 10
spk = {"ok": True, "t": 0.0, "miss": 0}

def speaker_ok():
    """Cached speaker state. Polls :8091 every 30 s; 90 s boot grace; 2-strike debounce."""
    now = time.monotonic()
    if now < 90:
        return True
    if now - spk["t"] < 30:
        return spk["ok"]
    spk["t"] = now
    try:
        j = voice_get("/status", timeout=2)
        if "error" in j:
            return spk["ok"]
        up = bool(j.get("speaker"))
    except Exception:
        return spk["ok"]          # a failed poll is not evidence of a dead speaker
    if up:
        if not spk["ok"]:
            print("[asa-mc] speaker back - clearing ATTENTION", flush=True)
        spk["miss"] = 0
        spk["ok"] = True
    else:
        spk["miss"] += 1
        if spk["miss"] >= 2 and spk["ok"]:
            spk["ok"] = False
            print("[asa-mc] speaker offline - LED ATTENTION", flush=True)
    return spk["ok"]

# ---------- LED state ----------
def classify(s):
    status = s.get("status")
    if status == "VIOLATION":
        v = s.get("violation") or {}
        return NOT_ON_HEAD if v.get("reason") == "hardhat not on head" else VIOLATION
    if status != "CLEAR":
        return BOOT
    people = s.get("persons") or []
    if not people:
        return IDLE if speaker_ok() else ATTENTION
    return COMPLIANT if all(p.get("status") == "SAFE" for p in people) else CHECKING

def loop():
    if drv["moving"] and time.monotonic() - drv["t"] > 0.5:
        api_halt()
    if kw["hold"] and hold_listen():
        return
    if st["url"] is None:
        st["url"] = find_url()
        if st["url"] is None:
            code = OFFLINE if st["seen"] else BOOT
            if code != st["code"]:
                print(f"[asa-mc] LED -> {NAMES[code]}", flush=True)
            send(code)
            st["code"] = code
            time.sleep(1.0)
            return
    try:
        s = fetch(st["url"])
    except Exception:
        st["url"] = None
        return
    st["seen"] = True
    status, code = s.get("status"), classify(s)
    if st["status"] == "VIOLATION" and status == "CLEAR" and s.get("clear_reason") == "hardhat on head":
        send(THANKS)
        print("[asa-mc] LED -> THANKS", flush=True)
    now = time.monotonic()
    if code != st["code"] or now - st["sent"] > 1.0:
        if code != st["code"]:
            print(f"[asa-mc] LED -> {NAMES[code]}", flush=True)
        send(code)
        st["code"], st["sent"] = code, now
    if status in ("CLEAR", "VIOLATION"):
        st["status"] = status
    time.sleep(0.25)

App.run(user_loop=loop)
