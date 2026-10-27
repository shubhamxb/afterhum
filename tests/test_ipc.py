"""ytm ipc: a slow player is never mistaken for a dead one (10-24 blocker: a timeout unlinked a live socket and started a
second mpv nobody could stop). offline: a silent unix listener in a thread, daemon() stubbed, scratch YTM_DATA, no mpv.
run: python3 tests/test_ipc.py"""
import importlib.machinery, importlib.util, os, socket, sys, tempfile, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-ipc-")
os.environ.update({"YTM_DATA": SCRATCH, "YTM_NO_REEXEC": "1"})
BIN = ROOT / "bin/ytm"
l = importlib.machinery.SourceFileLoader("ytmipc", str(BIN))
s = importlib.util.spec_from_loader("ytmipc", l); Y = importlib.util.module_from_spec(s); l.exec_module(Y)

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

starts = []
Y.daemon = lambda: starts.append(1)   # never a real mpv

# 1. a live but silent player: the reply times out, the socket stays, nothing new starts
srv = socket.socket(socket.AF_UNIX); srv.bind(str(Y.SOCK)); srv.listen(4)
held = []
threading.Thread(target=lambda: [held.append(srv.accept()) for _ in range(1)], daemon=True).start()
t = time.time(); r = Y.ipc("get_property", "pause")
check("a slow player returns None", r is None, f"{time.time() - t:.1f}s")
check("its socket is left alone", Y.SOCK.exists())
check("no second player started", starts == [], str(starts))
srv.close()

# 2. a stale socket (file there, nobody listening): cleared and started once
Y.SOCK.unlink(missing_ok=True)
dead = socket.socket(socket.AF_UNIX); dead.bind(str(Y.SOCK)); dead.close()   # leaves the file, refuses connects
r = Y.ipc("get_property", "pause")
check("a stale socket restarts the player once", r is None and starts == [1], str(starts))
check("the stale socket file was cleared", not Y.SOCK.exists())

# 3. start=False never starts anything
starts.clear(); r = Y.ipc("get_property", "pause", start=False)
check("start=False on a missing socket starts nothing", r is None and starts == [])

# 4. daemon(): a live player behind the lock means no new mpv
# a fresh load for the real daemon() (section 1 stubbed it)
l2 = importlib.machinery.SourceFileLoader("ytmipc2", str(BIN))
s2 = importlib.util.spec_from_loader("ytmipc2", l2); Z = importlib.util.module_from_spec(s2); l2.exec_module(Z)
spawned = []
Z.subprocess.Popen = lambda *a, **k: spawned.append(a)   # never a real mpv
srv = socket.socket(socket.AF_UNIX); srv.bind(str(Z.SOCK)); srv.listen(4)
Z.daemon()
check("daemon() sees a live player and starts nothing", spawned == [], str(spawned))
check("the lock file lives under DATA", (Path(SCRATCH) / ".daemon.lock").exists())
srv.close(); Z.SOCK.unlink(missing_ok=True)

# 5. 0.8.2: the cli's background helpers never start a player. a radio child after `ytm stop`, a refill with no socket,
# search --json: none of them may bring mpv back (the app runs them while you may have just stopped the music)
starts.clear(); Y.SOCK.unlink(missing_ok=True)
Y.radio_tracks = lambda v: [{"videoId": f"vid{i:08d}", "title": "t"} for i in range(5)]
Y.RADIO_WANT.write_text("vid00000000")
check("a radio child after ytm stop adds nothing, starts nothing", Y.radio_child("vid00000000") == 0 and starts == [], str(starts))
check("a refill with no player starts nothing", Y.refill("vid00000000") == 0 and starts == [] and not Y.SOCK.exists())
class _Yt:
    def search(self, q, filter=None, limit=10): return [{"videoId": "vid00000001", "title": "t"}]
Y.yt = lambda: _Yt()
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()): Y.search_cmd(["--json", "x"])
check("search --json starts nothing", starts == [] and not Y.SOCK.exists())

print("all ok" if not fails else f"{fails} failed"); sys.exit(1 if fails else 0)
