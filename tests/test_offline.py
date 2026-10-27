"""ytm offline cache: no network, scratch dir only, never the real cache.
run: python3 tests/test_offline.py"""
import importlib.machinery, importlib.util, json, os, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-test-")
os.environ["YTM_DATA"] = SCRATCH
os.environ["YTM_CACHE_MB"] = "1"          # a 1 MB cap, so trim is testable with small files
l = importlib.machinery.SourceFileLoader("ytmoffline", str(ROOT / "lib/offline.py"))
s = importlib.util.spec_from_loader("ytmoffline", l); O = importlib.util.module_from_spec(s); l.exec_module(O)

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

O.DIR.mkdir(parents=True)
def put(vid, kb, age):
    p = O.DIR / f"watch?v={vid}.webm"; p.write_bytes(b"x" * kb * 1024); t = time.time() - age; os.utime(p, (t, t)); return p

a, b, c = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"
put(a, 600, 300); put(b, 600, 200); put(c, 600, 100)
check("local finds a file, rejects junk", O.local(a) is not None and O.local("nope") is None and O.local("../../etc/pw") is None)
check("cached_vids sees all three", sorted(O.cached_vids()) == sorted([a, b, c]))
O.local(a)                                   # a hit marks it used: a is now the newest
O.trim()
left = sorted(O.cached_vids())
check("trim cuts to the cap, oldest-used first (b goes, the just-used a stays)", b not in left and a in left and O.size() <= O.CAP, str(left))
(O.DIR / ".tmp-zzzzzzzzzzz.webm").write_bytes(b"x"); os.utime(O.DIR / ".tmp-zzzzzzzzzzz.webm", (1, 1))
O.trim()
check("trim sweeps a dead download's leftovers", not list(O.DIR.glob(".tmp-*")))
check("a tiny stub is not a hit", (O.DIR / "watch?v=ddddddddddd.webm").write_bytes(b"x") and O.local("ddddddddddd") is None)
check("fetch rejects a bad id without running anything", O.fetch("bad id") is False)
O.FAILED["eeeeeeeeeee"] = time.time()
check("a failed song rests (no retry loop)", O.fetch("eeeeeeeeeee") is False)
(Path(SCRATCH) / "plays").mkdir()
rows = [{"vid": "fffffffffff", "outcome": "finished"}] * 3 + [{"vid": "ggggggggggg", "outcome": "finished"}, {"vid": "hhhhhhhhhhh", "outcome": "skipped"}]
(Path(SCRATCH) / "plays" / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
check("favourites: most-finished first, skips ignored", O.favourites() == ["fffffffffff", "ggggggggggg"], str(O.favourites()))
check("clear empties the cache", O.clear() >= 1 and not O.cached_vids())
# watcher: alive_fn false at once -> returns 0 without touching the network; a second one yields to the lock
check("watcher exits when the player is gone", O.watcher(lambda: [], lambda: False) == 0)
# the resolved-url cache (read by the lua hook at every track open)
now = time.time()
O.put_url("uuuuuuuuuuu", "https://rr1.googlevideo.com/videoplayback?expire=%d&x=1" % (now + 7200), now)
check("put_url / fresh_url: a good url is returned", O.fresh_url("uuuuuuuuuuu", now) and "googlevideo" in O.fresh_url("uuuuuuuuuuu", now))
check("fresh_url: 15 minutes from expiry is stale", O.fresh_url("uuuuuuuuuuu", now + 7200 - 600) is None)
O.put_url("vvvvvvvvvvv", "https://x/noexpire", now)
check("a url without expire= is trusted for 4 h, not forever", O.fresh_url("vvvvvvvvvvv", now + 3 * 3600) and O.fresh_url("vvvvvvvvvvv", now + 4 * 3600) is None)
O.put_url("wwwwwwwwwww", "https://x/a?expire=%d" % (now - 10), now)
O.put_url("xxxxxxxxxxx", "https://x/b?expire=%d" % (now + 9999), now)
check("expired entries are pruned on the next write", "wwwwwwwwwww" not in json.loads(O.URLS.read_text()))
check("resolve rejects a bad id without running anything", O.resolve("bad id") is False)
check("resolve returns True without a network call when the url is fresh", O.resolve("uuuuuuuuuuu") is True)
check("urls.json is valid json after every write", isinstance(json.loads(O.URLS.read_text()), dict))
# output guard (bin/ytm): a fake mpv, no sockets
import runpy, sys
os.environ["YTM_NO_REEXEC"] = "1"; sys.argv = ["ytm"]
Y = runpy.run_path(str(ROOT / "bin/ytm"), run_name="lib")
calls, state = [], {}
def fake_ipc(*cmd, start=True):
    if cmd[0] == "get_property": return state.get(cmd[1])
    calls.append(cmd); return None
Y["ipc"].__globals__["ipc"] = fake_ipc
G, OUT = Y["output_guard"], Y["_OUT"]
bt, spk = {"name": "coreaudio/80-99:output", "description": "WH"}, {"name": "coreaudio/BuiltInSpeakerDevice", "description": "Speakers"}
state.update({"audio-device-list": [{"name": "auto"}, spk], "pause": False, "idle-active": False, "time-pos": 1.0})
G(); calls.clear()
state["audio-device-list"] = [{"name": "auto"}, bt, spk]; G()
check("a device appearing reloads the output (playback follows it)", ("ao-reload",) in calls and ("set_property", "pause", True) not in calls, str(calls))
calls.clear(); OUT["reloaded"] = 0; state["audio-device-list"] = [{"name": "auto"}, spk]; G()
check("headphones vanishing mid-play pauses (never blasts the speakers), then reloads", ("set_property", "pause", True) in calls and ("ao-reload",) in calls, str(calls))
calls.clear(); OUT["reloaded"] = 0; state.update({"pause": True}); state["audio-device-list"] = [{"name": "auto"}, bt, spk]; G(); calls.clear(); state["audio-device-list"] = [{"name": "auto"}, spk]; OUT["reloaded"] = 0; G()
check("already paused: no pause call", ("set_property", "pause", True) not in calls, str(calls))
calls.clear(); OUT["reloaded"] = 0; OUT["still"] = 0; state.update({"pause": False, "time-pos": 5.0, "paused-for-cache": False})
for _ in range(5): G()
check("a stalled song (time-pos frozen 3 ticks) reloads once", calls.count(("ao-reload",)) == 1, str(calls))
calls.clear(); OUT["reloaded"] = 0; OUT["still"] = 0; state["paused-for-cache"] = True
for _ in range(6): G()
check("buffering is not a stall", ("ao-reload",) not in calls, str(calls))
print("FAILED" if fails else "all ok", fails)
raise SystemExit(1 if fails else 0)
