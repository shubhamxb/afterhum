"""the auto look (0.8.1): lib/autolook.py against the shared table of real songs (tests/autolook_cases.json: their real titles
with the signals as measured, written down as plain numbers), the rule edges, the signal readers on synthetic files, the
cache precedence, and the swift side (YtmStage --autolook / --checks) when a fresh build is there. offline, scratch data only.
run: python3 tests/test_autolook.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import autolook as A

SCRATCH = Path(tempfile.mkdtemp(prefix="ytm-autolook-test-"))
fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

# 1. the table: every real song gets the look, accent, motion, reason and confidence the design says
CASES = json.loads((HERE / "autolook_cases.json").read_text())
check("the table has the real songs", len(CASES) >= 22, str(len(CASES)))
for r in CASES:
    p, e = A.decide(r["signals"]), r["expect"]
    got = (p["look"], p["accent"], p["motion"], p["reason"])
    check(f"case {r['vid']}: {e['look']}", got == (e["look"], e["accent"], e["motion"], e["reason"]) and abs(p["conf"] - r["conf"]) < 0.006,
          f"got {got} {p['conf']}" if got != (e["look"], e["accent"], e["motion"], e["reason"]) else "")

# 2. the edges, on synthetic signals
d = lambda **s: A.decide(s)
check("nothing at all is dusk", d()["look"] == "dusk" and d()["reason"] == "dusk · nothing stood out")
check("work mode is paper whatever else", d(mode="work", title="synthwave", bpm=128, tilt=0.4)["look"] == "paper")
check("lofi beats a 'midnight' (strong over weak)", d(title="Midnight Window", album="Lo-Fi")["look"] == "rain")
check("mood words aren't read in the artist name", d(title="Hats Off", artist="moon.walken")["look"] == "dusk")
check("genre words are read in the artist name", d(title="sleepless", artist="harrylofi")["look"] == "rain")
check("two weak words at most", A.word_points("midnight city drive neon", "", "")["neon"][0] == 2.0)
check("a genre word, not a longer word: 'outrunning karma', 'drones'", "neon" not in A.word_points("Outrunning Karma", "Alec Benjamin", "")
      and "stars" not in A.word_points("Drones", "Muse", "") and A.word_points("Outrun", "", "")["neon"][0] == 3.0)
check("barely past dusk is still dusk", d(bpm=125)["look"] == "dusk" and d(bpm=125)["conf"] == 0.0, str(d(bpm=125)))
mix = d(title="rainy study", length=3600)
check("an hour-long mix leans rain", mix["look"] == "rain" and "an hour-long mix" in mix["reason"], mix["reason"])
check("fast + heavy + loud is neon, lively", d(bpm=128.6, tilt=0.41, density=0.56, quiet=0.09)["look"] == "neon"
      and d(bpm=128.6, tilt=0.41, density=0.56, quiet=0.09)["motion"] == "lively")
check("motion is never still", all(d(**s)["motion"] in ("calm", "lively") for s in ({}, {"bpm": 60}, {"bpm": 180, "density": 1})))
grey = dict(lum=0.3, sat=0.1, colourful=0.1, warm=0.9, cool=0.0, dark=0.0, neonpx=0.0)
check("a grey cover gets the look's own accent", d(**grey)["accent"] == "ember" and d(mode="work", **grey)["accent"] == "mono")
check("a colourful cover keeps its own colours", d(**dict(grey, colourful=0.8))["accent"] == "cover")
check("no cover yet: cover colours", d(title="synthwave")["accent"] == "cover")
check("deterministic", all(A.decide(r["signals"]) == A.decide(r["signals"]) for r in CASES))
cc = d(bpm=128.6, onset=0.12, quiet=0.31, hour=23)   # neon 1.75, stargaze 1.75: the tie goes to neon, and says so
check("a close call says so", cc["look"] == "neon" and cc["reason"].endswith("· a close call with stargaze"), cc["reason"])

# 3. the readers, on synthetic files (the same numbers the app reads from the cover pass and the analysis loop)
import numpy as np
from PIL import Image
art = SCRATCH / "art"; art.mkdir()
Image.new("RGB", (64, 64), (215, 215, 205)).save(art / "pale0000001.jpg")
Image.new("RGB", (64, 64), (40, 230, 240)).save(art / "elec0000001.jpg")
lb = Image.new("RGB", (544, 544), (0, 0, 0)); lb.paste(Image.new("RGB", (544, 306), (40, 230, 240)), (0, 119)); lb.save(art / "boxd0000001.jpg")
cs = A.cover_signals(art / "pale0000001.jpg")
check("pale cover reads pale", cs["lum"] > 0.6 and cs["sat"] < 0.25 and cs["colourful"] == 0, str(cs))
cs = A.cover_signals(art / "elec0000001.jpg")
check("cyan cover reads electric", cs["neonpx"] > 0.9 and cs["cool"] > 0.9, str(cs))
cs = A.cover_signals(art / "boxd0000001.jpg")
check("letterbox bars are trimmed before reading (no fake darkness)", cs["dark"] < 0.1, str(cs))
an = SCRATCH / "analysis"; an.mkdir()
fr = np.zeros((600, 29), np.uint8); fr[:, :8] = 200; fr[:, 16:24] = 50; fr[:, 24] = 150; fr[:, 25] = 30; fr[:300, 26] = 40; fr[300:, 26] = 200
fr.tofile(an / "song0000001.u8")
meta = {"bpm": 120.0, "width": 29, "frames": 600, "moments": [{"t": 5, "kind": "drop"}, {"t": 8, "kind": "lift"}, {"t": 9, "kind": "drop"}]}
(an / "song0000001.json").write_text(json.dumps(meta))
au = A.audio_signals(an / "song0000001.u8", meta)
check("audio: tilt, density, onset, quiet, drops", abs(au["tilt"] - 150 / 255) < 1e-6 and abs(au["density"] - 150 / 255) < 1e-6
      and abs(au["onset"] - 30 / 255) < 1e-6 and au["quiet"] == 0.5 and au["drops"] == 2 and au["bpm"] == 120.0, str(au))

# 4. one song's pick: the app's cache when it saw more, else worked out from disk; a garbled cache is no cache
(SCRATCH / "titles.json").write_text(json.dumps({"song0000001": {"title": "Nightcall", "artist": "x", "album": None, "secs": 200},
                                                  "elec0000001": "someone – a song", "pale0000001": {"title": "Focus", "artist": "y"}}))
p = A.for_song(SCRATCH, "song0000001", hour=12)
check("worked out from words + audio", p["look"] == "neon" and p["from"] == "words+audio" and "'nightcall'" in p["reason"], str(p))
p = A.for_song(SCRATCH, "pale0000001", hour=12)
check("words + cover", p["look"] == "paper" and p["from"] == "words+cover", str(p))
LK = SCRATCH / "looks.json"
LK.write_text(json.dumps({"pale0000001": {"look": "stars", "reason": "stargaze · from the app", "rules": 1, "from": "words+cover+audio"},
                          "song0000001": {"look": "dusk", "reason": "dusk · words", "rules": 1, "from": "words"},
                          "elec0000001": {"look": "rain", "reason": "rain window · old rules", "rules": 0, "from": "words+cover+audio"}}))
check("the app's pick wins when it saw more", A.for_song(SCRATCH, "pale0000001")["reason"] == "stargaze · from the app")
check("a thinner cached pick loses to more evidence here", A.for_song(SCRATCH, "song0000001", hour=12)["look"] == "neon")
old = A.for_song(SCRATCH, "elec0000001", hour=12)
check("an older rules version is ignored", old["look"] != "rain" and old["from"] == "words+cover", str(old))
check("an old string row still gives words", A.signals_for(SCRATCH, "elec0000001", hour=12)[0]["artist"] == "someone")
LK.write_text("{garbled")
check("garbled looks.json: no crash", A.for_song(SCRATCH, "pale0000001", hour=12)["look"] == "paper")
check("an unknown song: dusk, words only", A.for_song(SCRATCH, "nope0000001", hour=12)["look"] == "dusk")

# 5. the swift side agrees, when there's a fresh build (headless flags that exit; never the app)
BIN = HERE.parent / "stage/.build/release/YtmStage"
if BIN.exists():
    r = subprocess.run([str(BIN), "--autolook", str(HERE / "autolook_cases.json")], capture_output=True, text=True, timeout=60)
    check("swift AutoLook.decide agrees with the table", r.returncode == 0 and "0 mismatches" in r.stdout, r.stdout[-400:] + r.stderr[-200:])
    r = subprocess.run([str(BIN), "--checks"], capture_output=True, text=True, timeout=60, env={**os.environ, "YTM_DATA": str(SCRATCH)})
    check("swift --checks (song ids, cover trims, cover numbers, flicker rules)", r.returncode == 0 and "all ok" in r.stdout,
          "\n".join(l for l in r.stdout.splitlines() if l.startswith("FAIL")) or r.stderr[-200:])
else:
    print("skip swift side: no stage/.build/release/YtmStage (nice -n 15 swift build -c release)")

print(f"\n{'all passed' if not fails else f'{fails} failed'} · scratch {SCRATCH}")
sys.exit(1 if fails else 0)
