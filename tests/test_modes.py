"""ytm modes: every mode is complete (seeds, volume, light feel, look), custom modes round-trip, nothing leaks into the real data.
run: python3 tests/test_modes.py"""
import contextlib, importlib.machinery, importlib.util, io, os, runpy, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-test-")
os.environ["YTM_DATA"] = SCRATCH; os.environ["YTM_NO_REEXEC"] = "1"; sys.argv = ["ytm"]
Y = runpy.run_path(str(ROOT / "bin/ytm"), run_name="lib")
sys.path.insert(0, str(ROOT / "lib")); import looks as L

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")
def say(fn, *a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): r = fn(*a)
    return r, buf.getvalue()

MODES = Y["MODES"]
builtin = [k for k, m in MODES.items() if m.get("group") != "mine"]
check("19 built-in modes (5 mood, 6 working, 6 doing, 2 winding down)", len(builtin) == 19, str(len(builtin)))
bad = [k for k in builtin if not (MODES[k]["seeds"] and 0 < MODES[k]["vol"] <= 100 and MODES[k]["what"] and MODES[k]["max"] >= 300)]
check("every mode has seeds, a sane volume, a description, a length cap", not bad, str(bad))
check("every mode has a look", all(k in L.MODE_LOOK for k in builtin), str([k for k in builtin if k not in L.MODE_LOOK]))
check("every look is a drawn one", set(L.MODE_LOOK.values()) <= set(L.LOOKS), str(set(L.MODE_LOOK.values())))
check("no duplicate seeds within a mode", all(len(set(m["seeds"])) == len(m["seeds"]) for m in MODES.values()))
check("gym / run / party are loud and short, reading / sleep quiet", MODES["gym"]["vol"] >= 60 and MODES["party"]["max"] <= 420 and MODES["reading"]["vol"] <= 35 and MODES["sleep"]["vol"] <= 35)
check("instrumental modes for deep work, not for the gym", all(MODES[k]["instrumental"] for k in ("coding", "writing", "reading", "study")) and not MODES["gym"]["instrumental"])
r, out = say(Y["mode_cmd"], "")
check("`ytm mode` lists every mode under a heading", all(k in out for k in builtin) and "working" in out and "doing" in out and "mood" in out)
# custom modes
r, out = say(Y["mode_new"], ["pottery", "ambient", "folk,", "slow", "jazz", "--instrumental", "--vol", "33", "--what", "hands", "busy"])
check("mode new saves a custom mode", "✓" in out, out.strip())
c = Y["_custom_modes"]()
check("...with its seeds, volume and flag", c.get("pottery", {}).get("vol") == 33 and c["pottery"]["instrumental"] and len(c["pottery"]["seeds"]) == 2, str(c))
r, out = say(Y["mode_new"], ["gym", "x", "y"]); check("a built-in name is refused", r == 2 and "taken" in out, out.strip())
r, out = say(Y["mode_new"], ["Bad Name!", "x", "y"]); check("a bad name is refused", r == 2)
r, out = say(Y["mode_new"], ["solo"]); check("too few words prints usage", r == 2 and "usage" in out)
r, out = say(Y["mode_forget"], "pottery"); check("forget removes it", "forgot" in out and "pottery" not in Y["_custom_modes"]())
r, out = say(Y["mode_forget"], "nope"); check("forgetting a missing one says so", r == 1)
check("a corrupt modes.json is ignored, not a crash", (Path(SCRATCH) / "modes.json").write_text("{nope") and Y["_custom_modes"]() == {})
check("custom modes are written under the scratch dir, never the real data", Path(Y["CUSTOM_MODES"]).parent == Path(SCRATCH), str(Y["CUSTOM_MODES"]))
# shuffle / surprise / the stage's json (fake player, no sockets)
import json as _j
calls, props = [], {}
Y["ipc"].__globals__["ipc"] = lambda *a, **k: (props.get(a[1]) if a[0] == "get_property" else calls.append(a))
props["playlist-count"] = 2
r, out = say(Y["shuffle_cmd"]); check("shuffle: a 2-song queue says so and does nothing", r == 1 and not calls, out.strip())
props["playlist-count"] = 12
r, out = say(Y["shuffle_cmd"]); check("shuffle: a real queue is shuffled by mpv", ("playlist-shuffle",) in calls and "12" in out, out.strip())
seen, shown = set(), []
Y["mode_cmd"].__globals__["mode_cmd"] = lambda name, refill=False: shown.append(name)
Y["current_mode"].__globals__["current_mode"] = lambda: "coding"
for _ in range(120): say(Y["surprise_cmd"])
check("surprise never repeats the mood that is on, and can reach any other", "coding" not in shown and len(set(shown)) > 12, f"{len(set(shown))} distinct")
Y["mode_cmd"].__globals__["mode_cmd"] = Y["mode_cmd"]
props.update({"audio-device-list": [{"name": "auto"}, {"name": "avfoundation/BT:output", "description": "WH"}, {"name": "avfoundation/BuiltInSpeakerDevice", "description": "Speakers"},
                                    {"name": "coreaudio/BT:output", "description": "WH"}], "audio-device": "avfoundation/BT:output", "current-ao": "avfoundation"})
Y["ipc"].__globals__["ipc"] = lambda *a, **k: props.get(a[1]) if a[0] == "get_property" else None
r, out = say(Y["out_cmd"], ["--json"]); j = _j.loads(out)
check("out --json: the active output's devices, each once, the chosen one marked", [d["name"] for d in j["devices"]] == ["WH", "Speakers"] and j["devices"][0]["on"] and not j["devices"][1]["on"] and j["ao"] == "avfoundation", out.strip())
print("FAILED" if fails else "all ok", fails)
raise SystemExit(1 if fails else 0)
