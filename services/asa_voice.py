#!/usr/bin/env python3
"""ASA voice alerts + health monitor + voice commands (v4).
   - PPE alerts, reminders, thank-you and compliance greeting from the vision /state
   - Announces reboot / power loss / crash recovery at startup
   - Announces vision outages and recovery; tracks the speaker
   - Command API on :8091 (local + Docker only): /report = spoken live status, /say?key=<clip>
   Health events go to the evidence audit trail (type SYSTEM).
   Default = dry-run. Add --live to play through the speaker."""
import datetime, json, os, queue, re, shutil, signal, subprocess, sys, threading, time, urllib.request, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

STATE_URL = "http://127.0.0.1:8090/state"
VOICE_DIR = "/home/arduino/asa-voice"
EVID_DIR = "/home/arduino/asa-evidence"
SPEAKER = "/home/arduino/asa_bt_speaker.sh"
HEALTH = "/home/arduino/.asa_health.json"
PIPER_MODEL = "/home/arduino/voices/en_US-lessac-medium.onnx"
UNIT_ID = "ASA-01"
CMD_PORT = 8091
POLL_S, REMIND_S, MAX_REMIND = 0.25, 10.0, 3
OFFLINE_AFTER_S, BOOT_GRACE_S = 5.0, 90.0
OFFLINE_REPEAT_S, OFFLINE_MAX = 60.0, 3
SPK_CHECK_S, HB_S = 30.0, 30.0
GREET_HOLD_S, GREET_RESET_S, GREET_GAP_S = 2.0, 10.0, 30.0
REPORT_GAP_S = 8.0
LIVE = "--live" in sys.argv
BOOT_ID = open("/proc/sys/kernel/random/boot_id").read().strip()
q = queue.Queue(maxsize=4)
spk = {"ok": None}
spk_lock = threading.Lock()
PLAY = {"n": 0}           # clips playing right now
REP = {"pending": False}  # status report being built/queued

# ---------- site language (v5): live Piper voice + self-maintaining phrase cache ----------
import hashlib
LANG_FILE = "/home/arduino/.asa_lang"

# ---------- speaker status + volume (v5.1) ----------
import re
VOL_FILE = "/home/arduino/.asa_volume"
VOL_MIN, VOL_MAX = 0.30, 1.00   # floor: a safety robot must never be muted from the dashboard

def _pw_env():
    return dict(os.environ, XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")

def speaker_info():
    """(online, sink_id, volume) of the Bluetooth speaker, read live from PipeWire."""
    try:
        out = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=3, env=_pw_env()).stdout
    except Exception:
        return False, None, None
    i = out.find("Sinks:")
    if i < 0:
        return False, None, None
    j = out.find("Sources:", i)
    for line in out[i:j if j > 0 else None].splitlines():
        if "clipper" in line.lower():
            m = re.search(r"(\d+)\.", line); v = re.search(r"vol:\s*([\d.]+)", line)
            return True, (m.group(1) if m else None), (float(v.group(1)) if v else None)
    return False, None, None

def vol_saved():
    try:
        return min(VOL_MAX, max(VOL_MIN, float(open(VOL_FILE).read().strip())))
    except Exception:
        return 0.70

def set_volume(v, src="api"):
    v = float(v)
    if v > 1.5:
        v /= 100.0                      # accept percentages, e.g. set=80
    v = round(min(VOL_MAX, max(VOL_MIN, v)), 2)
    old = vol_saved()
    with open(VOL_FILE, "w") as f:
        f.write(f"{v:.2f}\n")
    online, sid, _ = speaker_info()
    if online and sid:
        subprocess.run(["wpctl", "set-volume", sid, f"{v:.2f}"], capture_output=True, timeout=3, env=_pw_env())
    if abs(old - v) >= 0.005:
        audit(f"speaker volume {round(old * 100)}% -> {round(v * 100)}%", source=src)
        log(f"speaker volume {round(old * 100)}% -> {round(v * 100)}%" + ("" if online else " (saved; speaker offline)"))
    return v
I18N_FILE = "/home/arduino/asa-voice/phrases_i18n.json"
CACHE_DIR = "/home/arduino/asa-voice/cache"
LANG = {"cur": "en", "cached": 0, "total": 0}
tts_lock = threading.Lock()   # the espeak phonemizer is not thread-safe

def i18n():
    try:
        with open(I18N_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"i18n file unreadable: {e}")
        return {}

def lang_read():
    try:
        l = open(LANG_FILE).read().strip()
    except Exception:
        l = "en"
    return l if l == "en" or l in i18n() else "en"

def voice_model(lang):
    return PIPER_MODEL if lang == "en" else (i18n().get(lang, {}).get("voice") or PIPER_MODEL)

def cache_path(lang, key, text):
    h = hashlib.sha1((voice_model(lang) + "|" + text).encode("utf-8")).hexdigest()[:10]
    return f"{CACHE_DIR}/{lang}/{key}-{h}.wav"

def synth_to(text, path):
    if tts.get("voice") is None:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with tts_lock:
            vv = tts["voice"]
            fn = vv.synthesize_wav if hasattr(vv, "synthesize_wav") else vv.synthesize
            with wave.open(tmp, "wb") as wf:
                fn(text, wf)
        os.replace(tmp, path)
        return True
    except Exception as e:
        log(f"synth failed ({os.path.basename(path)}): {e}")
        return False

def clip_path(key):
    """Clip in the site language (cached, or synthesised now). English clip = safety fallback."""
    lang, en = LANG["cur"], f"{VOICE_DIR}/{key}.wav"
    if lang == "en":
        return en
    text = i18n().get(lang, {}).get("phrases", {}).get(key)
    if not text:
        return en
    p = cache_path(lang, key, text)
    if os.path.isfile(p):
        return p
    if tts_ready.is_set() and tts.get("lang") == lang and synth_to(text, p):
        return p
    return en

def load_tts():
    """Load the site-language voice (English if that fails), then warm the phrase cache."""
    try:
        try:
            from piper import PiperVoice
        except ImportError:
            from piper.voice import PiperVoice
        for l in dict.fromkeys([lang_read(), "en"]):
            try:
                t0 = time.time()
                with tts_lock:
                    tts["voice"] = None
                    tts["voice"] = PiperVoice.load(voice_model(l))
                tts["lang"] = LANG["cur"] = l
                log(f"TTS ready: {l} ({time.time() - t0:.0f}s)")
                break
            except Exception as e:
                log(f"TTS voice {l} unavailable: {e}")
    except Exception as e:
        log(f"TTS unavailable: {e}")
    tts_ready.set()
    threading.Thread(target=warmup, daemon=True).start()

def warmup():
    lang = LANG["cur"]
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)   # yield CPU to vision
    except Exception:
        pass
    ph = i18n().get(lang, {}).get("phrases", {}) if lang != "en" else {}
    LANG.update(cached=0, total=len(ph))
    t0, made = time.time(), 0
    for key, text in ph.items():
        if LANG["cur"] != lang or tts.get("lang") != lang:
            return
        p = cache_path(lang, key, text)
        if not os.path.isfile(p):
            made += synth_to(text, p)
        LANG["cached"] += 1
    if ph:
        log(f"phrase cache {lang}: {LANG['cached']}/{LANG['total']} ready, {made} new ({time.time() - t0:.0f}s)")

def set_lang(lang, src="api"):
    if lang != "en" and lang not in i18n():
        return False
    with open(LANG_FILE, "w") as f:
        f.write(lang + "\n")
    old, LANG["cur"] = LANG["cur"], lang
    ph = i18n().get(lang, {}).get("phrases", {}) if lang != "en" else {}
    LANG.update(cached=sum(os.path.isfile(cache_path(lang, k, t)) for k, t in ph.items()), total=len(ph))
    if old != lang:
        audit(f"site language {old} -> {lang}", source=src)
        log(f"site language {old} -> {lang}")
        tts_ready.clear()
        threading.Thread(target=load_tts, daemon=True).start()
    return True

def status_text():
    lang = LANG["cur"]
    R = i18n().get(lang, {}).get("report") if lang != "en" else None
    if not R:
        return status_text_en()
    try:
        s = fetch()
    except Exception:
        return R["offline"]
    people = s.get("persons") or []
    n = len(people)
    if s.get("status") == "VIOLATION":
        now = R["violation"]
    elif not people:
        now = R["clear"]
    elif all(p.get("status") == "SAFE" for p in people):
        now = R["all_safe"].format(n=n)
    else:
        now = R["checking"].format(n=n)
    c = today_counts()
    return now + " " + R["today"].format(c=c["COMPLIANT"], v=c["VIOLATION"], r=c["RESOLVED"])
stopping = {"v": False}
tts = {"voice": None, "n": 0}
tts_ready = threading.Event()
last_report = {"t": -1e9}

def log(msg):
    print(f"[asa-voice] {time.strftime('%H:%M:%S')} {msg}", flush=True)

def audit(reason, **extra):
    now = datetime.datetime.now()
    rec = {"type": "SYSTEM", "reason": reason, "ts": now.isoformat(timespec="seconds"), "unit": UNIT_ID}
    rec.update(extra)
    try:
        d = os.path.join(EVID_DIR, now.strftime("%Y-%m-%d"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "events.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        log(f"audit write failed: {e}")
    log(f"SYSTEM: {reason}")

# ---------- reboot / crash detection ----------
def read_health():
    try:
        with open(HEALTH) as f:
            return json.load(f)
    except Exception:
        return {}

def write_health(clean):
    tmp = HEALTH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"boot_id": BOOT_ID, "hb": time.time(), "clean": clean}, f)
        os.replace(tmp, HEALTH)
    except Exception as e:
        log(f"health write failed: {e}")

def heartbeat():
    while not stopping["v"]:
        write_health(False)
        time.sleep(HB_S)

def startup_check():
    h = read_health()
    boot_t = time.time() - float(open("/proc/uptime").read().split()[0])
    if not h:
        audit("health tracking started", kind="start")
        return None
    if h.get("boot_id") != BOOT_ID:
        down = max(0, round(boot_t - h.get("hb", boot_t)))
        if h.get("clean"):
            audit(f"board rebooted - clean shutdown, off for {down} s", kind="reboot", downtime_s=down)
            return "reboot_notice"
        audit(f"board recovered from UNEXPECTED shutdown or power loss - off for about {down} s",
              kind="power_loss", downtime_s=down)
        return "power_loss"
    if h.get("clean"):
        audit("voice service restarted", kind="service_restart")
        return None
    audit("voice service recovered after a crash", kind="service_crash")
    return "service_recovered"

def on_term(*_):
    stopping["v"] = True
    write_health(True)
    log("stopping (clean)")
    sys.exit(0)

def vision_restarts():
    try:
        r = subprocess.run(["systemctl", "show", "-p", "NRestarts", "--value", "asa-vision"],
                           capture_output=True, text=True, timeout=3)
        return int(r.stdout.strip() or 0)
    except Exception:
        return None

# ---------- speaker ----------
def clip_len(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()

def speaker_check():
    if not LIVE:
        return True
    try:
        r = subprocess.run([SPEAKER, "once"], capture_output=True, text=True, timeout=25)
        return "not available" not in r.stdout
    except Exception:
        return False

def speaker_state(ok):
    with spk_lock:
        prev = spk["ok"]
        if ok == prev:
            return
        spk["ok"] = ok
    if not ok:
        audit("speaker offline - voice alerts cannot be heard", kind="speaker_offline")
    elif prev is False:
        audit("speaker back online", kind="speaker_online")

def speaker_monitor():
    while True:
        time.sleep(SPK_CHECK_S)
        speaker_state(speaker_check())

# ---------- text to speech (live status report) ----------
def load_tts_v4():
    try:
        try:
            from piper import PiperVoice
        except ImportError:
            from piper.voice import PiperVoice
        t0 = time.time()
        tts["voice"] = PiperVoice.load(PIPER_MODEL)
        log(f"TTS ready ({time.time() - t0:.0f}s)")
    except Exception as e:
        log(f"TTS unavailable: {e}")
    tts_ready.set()

def synth(text):
    tts_ready.wait(60)
    v = tts["voice"]
    if v is None:
        return None
    tts["n"] = (tts["n"] + 1) % 3
    path = f"/tmp/asa_tts_{tts['n']}.wav"
    with tts_lock:
        with wave.open(path, "wb") as wf:
            (v.synthesize_wav if hasattr(v, "synthesize_wav") else v.synthesize)(text, wf)
    return path

# ---------- playback ----------
def player():
    cmd = shutil.which("pw-play") or shutil.which("aplay")
    while True:
        item = q.get()
        if isinstance(item, tuple):
            path, label = item[1], "live status report"
        else:
            path, label = clip_path(item), f"{item} [{LANG['cur']}]"
        if not os.path.isfile(path):
            log(f"missing clip {label}")
            continue
        if not LIVE or not cmd:
            log(f"(dry-run) would play {label}  [{clip_len(path):.1f}s]")
            time.sleep(clip_len(path))
            continue
        ok = speaker_check()
        speaker_state(ok)
        log(f"playing {label}" + ("" if ok else "  (speaker offline!)"))
        PLAY["n"] += 1
        try:
            r = subprocess.run([cmd, path], capture_output=True, text=True)
        finally:
            PLAY["n"] -= 1
        if r.returncode != 0:
            log(f"playback failed: {r.stderr.strip()[:200]}")

def say(item, urgent=False):
    if urgent:
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass
    try:
        q.put_nowait(item)
    except queue.Full:
        log(f"skipped {item} (busy)")

def fetch():
    with urllib.request.urlopen(STATE_URL, timeout=1.0) as r:
        return json.load(r)

# ---------- status report ----------
def today_counts():
    c = {"COMPLIANT": 0, "VIOLATION": 0, "RESOLVED": 0}
    f = os.path.join(EVID_DIR, datetime.date.today().isoformat(), "events.jsonl")
    try:
        with open(f) as fh:
            for line in fh:
                try:
                    t = json.loads(line).get("type")
                except Exception:
                    continue
                if t in c:
                    c[t] += 1
    except FileNotFoundError:
        pass
    return c

def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"

def status_text_en():
    try:
        s = fetch()
    except Exception:
        return "Warning. The safety camera is offline. Monitoring is paused."
    people = s.get("persons") or []
    n = len(people)
    if s.get("status") == "VIOLATION":
        now = "Attention. A worker in the zone is not wearing a hard hat."
    elif not people:
        now = "The zone is clear."
    elif all(p.get("status") == "SAFE" for p in people):
        now = f"{plural(n, 'person', 'people')} in the zone, all wearing hard hats."
    else:
        now = f"{plural(n, 'person', 'people')} in the zone. Checking hard hats."
    c = today_counts()
    return (f"{now} Today: {plural(c['COMPLIANT'], 'compliance check', 'compliance checks')} passed, "
            f"{plural(c['VIOLATION'], 'violation', 'violations')}, and {c['RESOLVED']} resolved on the spot. "
            f"Monitoring is active.")

def report(src):
    now = time.monotonic()
    if now - last_report["t"] < REPORT_GAP_S:
        log("status report ignored (too soon)")
        return False
    last_report["t"] = now
    audit(f"voice command: status report ({src})", kind="voice_command")
    say("status_ack")
    def work():
        text = status_text()
        t0 = time.time()
        path = synth(text)
        if path:
            log(f"status report ready in {time.time() - t0:.1f}s: {text}")
            say(("wav", path))
        else:
            log("status report: TTS unavailable")
    def _tracked():
        try:
            work()
        finally:
            REP["pending"] = False
    REP["pending"] = True
    threading.Thread(target=_tracked, daemon=True).start()
    return True

class Cmd(BaseHTTPRequestHandler):
    def do_GET(self):
        ip = self.client_address[0]
        if not (ip.startswith("127.") or ip.startswith("172.")):
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/status":
            online, _, vol = speaker_info()
            return self._send(200, {"lang": LANG["cur"], "available": ["en"] + sorted(i18n()),
                                    "voice_ready": tts_ready.is_set() and tts.get("lang") == LANG["cur"],
                                    "cached": LANG["cached"], "total": LANG["total"],
                                    "speaker": online, "volume": vol if vol is not None else vol_saved(),
                                    "vol_min": VOL_MIN, "vol_max": VOL_MAX,
                                    "busy": PLAY["n"] > 0 or REP["pending"]})
        if u.path == "/volume":
            want = (parse_qs(u.query).get("set") or [""])[0].strip()
            if want:
                try:
                    set_volume(want, "command")
                except ValueError:
                    return self._send(400, {"error": "bad volume", "value": want})
            online, _, vol = speaker_info()
            return self._send(200, {"volume": vol if vol is not None else vol_saved(),
                                    "saved": vol_saved(), "speaker": online})
        if u.path == "/lang":
            want = (parse_qs(u.query).get("set") or [""])[0].strip().lower()
            if want and not set_lang(want, "command"):
                return self._send(400, {"error": "unknown language", "lang": want})
            return self._send(200, {"lang": LANG["cur"],
                                    "voice_ready": tts_ready.is_set() and tts.get("lang") == LANG["cur"],
                                    "cached": LANG["cached"], "total": LANG["total"],
                                    "available": ["en"] + sorted(i18n())})
        if u.path == "/busy":
            return self._send(200, {"busy": PLAY["n"] > 0 or REP["pending"], "playing": PLAY["n"] > 0})
        if u.path == "/report":
            return self._send(200, {"ok": report(qs.get("src", ["api"])[0])})
        if u.path == "/say":
            key = qs.get("key", [""])[0]
            if re.fullmatch(r"[a-z_]+", key) and os.path.isfile(f"{VOICE_DIR}/{key}.wav"):
                say(key)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "unknown clip"})
        self._send(404, {"error": "not found"})
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, *a):
        pass

# ---------- main ----------
def main():
    signal.signal(signal.SIGTERM, on_term)
    log(f"mode: {'LIVE' if LIVE else 'DRY-RUN'} | watching {STATE_URL} | commands on :{CMD_PORT}")
    threading.Thread(target=player, daemon=True).start()
    LANG["cur"] = lang_read()
    threading.Thread(target=load_tts, daemon=True).start()
    notice = startup_check()
    write_health(False)
    threading.Thread(target=heartbeat, daemon=True).start()
    speaker_state(speaker_check())
    if notice:
        say(notice)
    say("startup")
    threading.Thread(target=speaker_monitor, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", CMD_PORT), Cmd)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    t0 = time.monotonic()
    prev, t_last, reminders = "CLEAR", 0.0, 0
    ok_since, empty_since, greeted, last_greet = None, None, False, -1e9
    up, last_ok, down_since, off_n, off_t = None, t0, 0.0, 0, 0.0
    while True:
        now = time.monotonic()
        try:
            s = fetch()
        except Exception:
            s = None
        if s is None:
            limit = OFFLINE_AFTER_S if up else BOOT_GRACE_S
            if up is not False and now - last_ok >= limit:
                up, down_since, off_n, off_t = False, time.time(), 1, now
                audit("vision offline - PPE monitoring paused", kind="vision_offline")
                say("vision_offline", urgent=True)
            elif up is False and off_n < OFFLINE_MAX and now - off_t >= OFFLINE_REPEAT_S:
                off_n, off_t = off_n + 1, now
                log(f"vision still offline -> reminder {off_n}/{OFFLINE_MAX}")
                say("vision_offline")
            time.sleep(1.0)
            continue
        last_ok = now
        if up is None:
            audit("vision online - PPE monitoring active", kind="vision_online")
        elif up is False:
            down = round(time.time() - down_since)
            audit(f"vision back online after {down} s", kind="vision_online",
                  downtime_s=down, vision_restarts=vision_restarts())
            say("vision_online", urgent=True)
            prev = "CLEAR"
        up = True
        st = s.get("status")
        if st == "VIOLATION" and prev != "VIOLATION":
            v = s.get("violation") or {}
            key = "not_on_head" if v.get("reason") == "hardhat not on head" else "no_hardhat"
            log(f"VIOLATION #{s.get('events')}: {v.get('reason')} at Z={v.get('z')} mm")
            say(key, urgent=True)
            t_last, reminders = now, 0
        elif st == "VIOLATION" and now - t_last >= REMIND_S and reminders < MAX_REMIND:
            reminders, t_last = reminders + 1, now
            log(f"still violating -> reminder {reminders}/{MAX_REMIND}")
            say("reminder")
        elif st == "CLEAR" and prev == "VIOLATION":
            reason = s.get("clear_reason")
            log(f"CLEAR ({reason})")
            if reason == "hardhat on head":
                say("thanks", urgent=True)
                greeted, last_greet = True, now
        people = s.get("persons") or []
        if not people:
            empty_since = empty_since or now
            if now - empty_since >= GREET_RESET_S:
                greeted = False
        else:
            empty_since = None
        if st == "CLEAR" and people and all(p.get("status") == "SAFE" for p in people):
            ok_since = ok_since or now
            if not greeted and now - ok_since >= GREET_HOLD_S and now - last_greet >= GREET_GAP_S:
                log(f"COMPLIANT: hard hat confirmed ({len(people)} in view)")
                say("compliant")
                greeted, last_greet = True, now
        else:
            ok_since = None
        if st in ("CLEAR", "VIOLATION"):
            prev = st
        time.sleep(POLL_S)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
