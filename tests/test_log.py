"""ytm-log.lua (0.8.2): the rules and the whole script under a fake mp in luajit (tests/log_harness.lua), then one real mpv
on a scratch socket: muted (volume 0, no audio device, no now playing), a 2 s tone made by ffmpeg, the cli swapped for a
recorder. no network, no yt-dlp, never the real player or ~/.local/share/ytm. the player is `ytm stop`ped (same YTM_DATA)
and checked gone. run: python3 tests/test_log.py"""
import json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

HERE = Path(__file__).resolve().parent
LUA = HERE.parent / "ytm-log.lua"
BIN = ROOT / "bin/ytm"
PY = sys.executable
REAL_SOCK = Path.home() / ".local/share/ytm/mpv.sock"

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

# 1. the harness
scratch = tempfile.mkdtemp(prefix="ytm-log-harness-")
r = subprocess.run([shutil.which("luajit") or "luajit", str(HERE / "log_harness.lua"), str(LUA)], capture_output=True, text=True, timeout=30,
                   env={**os.environ, "YTM_DATA": scratch, "YTM_BIN": "/nonexistent/ytm"})
print(r.stdout.rstrip()); print(r.stderr.rstrip()) if r.stderr.strip() else None
check("the lua harness passes", r.returncode == 0 and "all ok" in r.stdout, f"exit {r.returncode}")

# 2. a real mpv, muted, scratch everything
D = Path(tempfile.mkdtemp(prefix="ytm-log-live-"))
SOCK = D / "mpv.sock"
assert str(SOCK) != str(REAL_SOCK) and not str(SOCK).startswith(str(Path.home() / ".local/share/ytm"))
rec = D / "fake-ytm"; spawned = D / "spawned.txt"
rec.write_text(f"#!/bin/sh\necho \"$@\" >> '{spawned}'\n"); rec.chmod(0o755)
tone = D / "song?v=vidLIVE0001.wav"   # a v= in the path: the script reads the song id from it, as from a watch url
subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ac", "1", str(tone)], check=True, timeout=30)
env = {**os.environ, "YTM_DATA": str(D), "YTM_BIN": str(rec), "YTM_NO_REEXEC": "1"}
p = subprocess.Popen(["mpv", "--idle=yes", "--no-video", "--no-terminal", "--force-window=no", f"--input-ipc-server={SOCK}", "--ao=null",
                      "--volume=0", "--media-controls=no", "--no-config", f"--script={LUA}", "--msg-level=all=warn"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, start_new_session=True)

def ipc(*cmd):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(3); s.connect(str(SOCK)); s.sendall((json.dumps({"command": list(cmd)}) + "\n").encode()); buf = b""
        while True:
            buf += s.recv(65536)
            for line in buf.split(b"\n")[:-1]:
                m = json.loads(line)
                if "event" not in m: return m.get("data")

def wait(cond, secs=8):
    t = time.time()
    while time.time() - t < secs:
        try:
            if cond(): return True
        except (OSError, ValueError): pass
        time.sleep(0.1)
    return False

try:
    check("live: mpv took the script (it returns M) and opened its socket", wait(SOCK.exists) and p.poll() is None)
    title = "a song, with a comma"
    ipc("loadfile", str(tone), "replace", -1, f"force-media-title=%{len(title.encode())}%{title}")
    check("live: media-title is the bare title we gave (commas whole)", wait(lambda: ipc("get_property", "media-title") == title), ipc("get_property", "media-title"))
    check("live: the song ends and a refill is asked for, a second later", wait(lambda: spawned.exists(), 8),
          spawned.read_text() if spawned.exists() else "nothing spawned")
    check("live: once, from that song", spawned.exists() and spawned.read_text().split("\n")[0] == "radio --refill vidLIVE0001", spawned.read_text() if spawned.exists() else "")
    rows = [json.loads(l) for f in (D / "plays").glob("*.jsonl") for l in f.read_text().splitlines() if l.strip()]
    check("live: one row, finished, the id from the path", len(rows) == 1 and rows[0]["outcome"] == "finished" and rows[0]["vid"] == "vidLIVE0001", str(rows))
    ipc("loadfile", str(tone), "replace"); wait(lambda: (ipc("get_property", "time-pos") or 0) > 0.3, 4); ipc("stop")
    time.sleep(1.5)
    check("live: a stop asks for nothing", spawned.read_text().count("\n") == 1, spawned.read_text())
    ipc("loadfile", str(D / "missing?v=vidLIVE0002.wav"), "replace"); time.sleep(1.5)
    check("live: a file that won't open is tried once more, then left (no march, no refill)", spawned.read_text().count("\n") == 1 and p.poll() is None)
finally:
    r = subprocess.run([PY, str(BIN), "stop"], capture_output=True, text=True, timeout=20, env={**os.environ, "YTM_DATA": str(D), "YTM_NO_REEXEC": "1"})
    gone = wait(lambda: p.poll() is not None, 5)
    if not gone: p.kill(); p.wait(5)   # ours only: the pid this test started
    check("live: ytm stop (same YTM_DATA) ended that player", gone, r.stdout.strip())
    left = subprocess.run(["/bin/ps", "-axo", "pid,command"], capture_output=True, text=True).stdout
    check("live: no stray scratch mpv", str(SOCK) not in left)
    check("live: the real player's socket was never ours", REAL_SOCK != SOCK)

print(f"\n{'all ok' if not fails else f'{fails} FAILED'}  (scratch {scratch} · {D})")
sys.exit(1 if fails else 0)
