"""ytm taste: the listening model, offline. a synthetic log in a scratch YTM_DATA (never the real one), synthetic names only;
no network, no mpv, no keychain. run: python3 tests/test_taste.py"""
import importlib.machinery, importlib.util, io, json, os, random, subprocess, sys, tempfile, time, contextlib
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = Path(tempfile.mkdtemp(prefix="ytm-taste-test-"))
os.environ.update(YTM_DATA=str(SCRATCH), YTM_NO_REEXEC="1", YTM_AUTH_SERVICE="ytm-test-auth", YTM_SPOTIFY_SERVICE="ytm-test-spotify")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))
import taste as T

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")


NOW = time.mktime((2026, 10, 25, 15, 0, 0, 0, 0, -1))
_n = [0]
def row(vid, artist, outcome="finished", listened=None, dur=200.0, mode="normal", hour=14, day=0, src=None):
    """one play as ytm-log.lua writes it. day = days before NOW"""
    _n[0] += 1
    st = datetime.fromtimestamp(NOW) - timedelta(days=day)
    st = st.replace(hour=hour, minute=_n[0] % 60)
    heard = dur if listened is None and outcome == "finished" else (listened if listened is not None else 60.0)
    return {"v": 1, "id": f"t{_n[0]:08x}", "start": st.isoformat(), "end": st.isoformat(), "vid": vid, "title": f"song {vid}",
            "artist": artist, "album": None, "dur": dur, "listened": heard, "outcome": outcome,
            "source": src or ("work" if mode == "work" else "radio"), "hour": hour, "dow": (st.weekday() + 1) % 7, "mode": mode}


def fresh_dir():
    d = Path(tempfile.mkdtemp(dir=SCRATCH)); (d / "plays").mkdir(); return d


def write(d, rows, name="2026-10.jsonl", mode="w"):
    with open(d / "plays" / name, mode) as f: f.write("".join(json.dumps(r) + "\n" for r in rows))


def vid(i): return f"v{i:010d}"


# 1. outcome scores: finished 1, partial by how much, skips 0 and twice as loud under 30 s, stopped says nothing
check("score: finished", T.outcome_score("finished", 200, 200) == (1.0, 1.0))
p1, p2 = T.outcome_score("partial", 40, 200), T.outcome_score("partial", 180, 200)
check("score: partial grows with listening, stays under a finish", 0.2 <= p1[0] < p2[0] <= 0.6 and p1[1] < 1, f"{p1} {p2}")
check("score: fast skip weighs twice a slow one", T.outcome_score("skipped", 10, 200) == (0.0, 2.0) and T.outcome_score("skipped", 90, 200) == (0.0, 1.0))
check("score: stopped and unknown are no evidence", T.outcome_score("stopped", 50, 200)[1] == 0 and T.outcome_score("unknown", 0, 0)[1] == 0)
check("bands: hours", [T.band(h) for h in (0, 5, 6, 11, 12, 16, 17, 21, 22, 23)] ==
      ["night", "night", "morning", "morning", "afternoon", "afternoon", "evening", "evening", "late", "late"])

d = fresh_dir(); write(d, [row(vid(1), "mix", "skipped", 40, dur=4800), row(vid(2), "song", "skipped", 40)])
check("legacy: a mix logged as skipped (pre-0.8.2) reads as partial", [r["o"] for r in T.load(d)] == ["partial", "skipped"])

# 2. smoothing: two plays never make a rule
d = fresh_dir()
base = [row(vid(i), f"filler {i % 7}", "finished" if i % 3 else "skipped", 90) for i in range(40)]
two = [row(vid(900), "two skips artist", "skipped", 5), row(vid(901), "two skips artist", "skipped", 5)]
solid = [row(vid(950 + i), "solid artist") for i in range(10)] + [row(vid(990), "solid artist", "skipped", 5), row(vid(991), "solid artist", "skipped", 5)]
write(d, base + two + solid)
m = T.model(d)
b0 = m.base("normal", "afternoon")
r2, n2 = m.rate("artist", "two skips artist", "normal", "afternoon")
rs, _ = m.rate("artist", "solid artist", "normal", "afternoon")
check("smoothing: 2 fast skips pull an artist down, not to zero", 0 < b0 - r2 < 0.35 and r2 > 0.3, f"base {b0:.2f} → {r2:.2f}")
check("smoothing: 2 fast skips don't flip 10 finishes", rs > b0, f"solid {rs:.2f} vs base {b0:.2f}")
s = T.summary(d)
check("smoothing: the ui never names a 2-play artist", not any("two skips" in h for h in s["headlines"]), str(s["headlines"]))
one = [row(vid(1), "a", mode="work", hour=3)]
write(d, base + two + solid + one)
s = T.summary(d)
c = next(c for c in s["contexts"] if c["mode"] == "work")
check("smoothing: a 1-play context is 'too few to say'", c["judged"] == 1 and "too few to say" in T.report(s).split("work   night")[1].split("\n")[0])

# 3. contexts stay apart: an artist finished at leisure and skipped at work
d = fresh_dir()
rows = [row(vid(i), "split artist", "finished") for i in range(8)] + [row(vid(100 + i), "split artist", "skipped", 8, mode="work") for i in range(8)]
rows += [row(vid(200 + i), f"other {i}", "finished" if i % 2 else "skipped", 100) for i in range(10)]
rows += [row(vid(300 + i), f"other {i}", "finished" if i % 2 else "skipped", 100, mode="work") for i in range(10)]
write(d, rows); m = T.model(d)
rn, _ = m.rate("artist", "split artist", "normal", "afternoon"); rw, _ = m.rate("artist", "split artist", "work", "afternoon")
check("contexts: normal says finish, work says skip", rn > m.base("normal", "afternoon") and rw < m.base("work", "afternoon"), f"normal {rn:.2f} work {rw:.2f}")
fn = m.score({"artist": "split artist"}, "normal", "afternoon"); fw = m.score({"artist": "split artist"}, "work", "afternoon")
check("contexts: the score follows the context", fn["score"] > fw["score"] + 0.2, f"{fn} {fw}")
check("contexts: why names the thing", fn["why"] and fn["why"][0].startswith("artist split artist"), str(fn["why"]))

# 4. fast skips weigh more than slow ones
d = fresh_dir()
rows = [row(vid(i), "fast skipped", "skipped", 6) for i in range(3)] + [row(vid(10 + i), "slow skipped", "skipped", 95) for i in range(3)]
rows += [row(vid(20 + i), f"x{i}", "finished") for i in range(10)]
write(d, rows); m = T.model(d)
rf, _ = m.rate("artist", "fast skipped", "normal"); rl, _ = m.rate("artist", "slow skipped", "normal")
check("fast skips: under 30 s counts as a stronger no", rf < rl, f"fast {rf:.2f} slow {rl:.2f}")

# 5. work ranking: loved first, 1 in 4 left to chance (the unheard first), nothing dropped
d = fresh_dir()
rows = [row(vid(i), "loved producer", mode="work") for i in range(12)] + [row(vid(50 + i), "skipped producer", "skipped", 7, mode="work") for i in range(12)]
write(d, rows); m = T.model(d)
def trk(i, artist, secs=180): return {"videoId": vid(1000 + i), "title": f"t{i}", "artists": [{"name": artist}], "length": f"{secs // 60}:{secs % 60:02d}"}
cands = [trk(i, "loved producer") for i in range(8)] + [trk(10 + i, "skipped producer") for i in range(8)] + [trk(20 + i, f"unheard {i}") for i in range(4)]
out = m.rank(cands, "work", "afternoon", random.Random(7))
how = [h for _, h in out]; got = [t for t, _ in out]
check("rank: nothing dropped, nothing doubled", sorted(t["videoId"] for t in got) == sorted(t["videoId"] for t in cands) and len(got) == 20)
check("rank: 1 in 4 is exploration", how.count("explore") == 5 and all(how[i] == "explore" for i in (3, 7, 11, 15, 19)), str(how))
check("rank: the first ranked picks are the loved producer", all(t["artists"][0]["name"] == "loved producer" for t, h in out[:3]), str([t["artists"][0]["name"] for t in got[:4]]))
ex = [t for t, h in out if h == "explore"]
left = lambda k: [t for t, _ in out[k:] if t["artists"][0]["name"].startswith("unheard")]
check("rank: exploration takes the unheard while any are left", all(t["artists"][0]["name"].startswith("unheard") for i, (t, h) in enumerate(out) if h == "explore" and left(i)),
      str([t["artists"][0]["name"] for t in ex]))
ranked = [t for t, h in out if h == "ranked"]
check("rank: skipped producer sinks to the end", all(t["artists"][0]["name"] == "skipped producer" for t in ranked[-6:]))
shares = []
for seed in range(20):
    o = m.rank(cands, "work", "afternoon", random.Random(seed)); shares.append(sum(h == "explore" for _, h in o) / len(o))
check("rank: the exploration share holds for any seed", all(x == 0.25 for x in shares))
empty = T.Model([], d).rank(cands, "work", "afternoon", random.Random(1))
check("rank: an empty log is a shuffle (still everything)", len(empty) == 20 and [t for t, _ in empty] != cands)
check("rank_tracks: garbage in → the same order back, no crash", T.rank_tracks([{"x": 1}, {"y": 2}], "work", d) in ([{"x": 1}, {"y": 2}], [{"y": 2}, {"x": 1}]))

# 6. the report: 0, 5 and 500 rows
d = fresh_dir(); s = T.summary(d); r = T.report(s)
check("report 0 rows: renders, says so, honest", "nothing logged yet" in r and "confidence: thin (0 plays" in r, r)
check("report 0 rows: json and one headline", json.loads(json.dumps(s))["plays"] == 0 and len(s["headlines"]) == 1)
write(d, [row(vid(i), f"five {i}", "finished" if i % 2 else "skipped", 12) for i in range(5)]); s = T.summary(d); r = T.report(s)
check("report 5 rows: renders, thin", "5 plays" in r and "confidence: thin (5 plays" in r and "~300 plays" in r, r)
check("report 5 rows: no pattern claimed", not any("finish most" in h for h in s["headlines"]), str(s["headlines"]))
d = fresh_dir(); rng = random.Random(3); big = []
for i in range(500):
    mode = "work" if i % 3 == 0 else "normal"; h = rng.choice((9, 14, 15, 20, 23, 2))
    a = f"artist {rng.randint(0, 40)}"; o = rng.choice(("finished", "finished", "skipped", "partial"))
    big.append(row(vid(i), a, o, rng.choice((5, 40, 150)), mode=mode, hour=h, day=rng.randint(0, 20)))
big.sort(key=lambda x: x["start"])
write(d, big); t0 = time.time(); s = T.summary(d); dt = time.time() - t0; r = T.report(s)
check("report 500 rows: renders fast", dt < 1.0 and "500 plays" in r, f"{dt * 1000:.0f} ms")
check("report 500 rows: confidence firms up", s["confidence"]["level"] == "firm" and "~300" not in s["confidence"]["line"], s["confidence"]["line"])
check("report 500 rows: every section", all(x in r for x in ("when you listen", "finish vs skip", "artists you finish", "the sound you finish",
                                                                "skipped in under 30 s", "new this week")))
check("report: 24-hour sparkline", len(r.split("\n")[3].strip()) == 24 or len(r.split("\n")[3]) == 26, repr(r.split("\n")[3]))
check("report: headlines ≤ 44 cells, 2-3 of them", 2 <= len(s["headlines"]) <= 3 and all(len(h) <= 44 for h in s["headlines"]), str(s["headlines"]))
check("report: lowercase-leaning, no jargon", not any(w in r.lower() for w in ("bayes", "posterior", "beta", "prior")))

# 7. incremental reading: only appended lines, a half-written line waits, a rewrite rereads, duplicates once
d = fresh_dir(); f = d / "plays" / "2026-10.jsonl"
write(d, [row(vid(i), "inc") for i in range(5)])
check("incremental: first read", len(T.load(d)) == 5)
st = json.loads((d / ".taste.json").read_text())
check("incremental: offset saved at the end", st["files"]["2026-10.jsonl"] == f.stat().st_size)
extra = [row(vid(10 + i), "inc") for i in range(3)]; half = json.dumps(row(vid(20), "inc"))
with open(f, "a") as fh: fh.write("".join(json.dumps(x) + "\n" for x in extra) + half[:30])
check("incremental: appended rows in, a half line waits", len(T.load(d)) == 8)
with open(f, "a") as fh: fh.write(half[30:] + "\n" + json.dumps(extra[0]) + "\n")
rows = T.load(d)
check("incremental: the finished line lands, a duplicate id doesn't", len(rows) == 9, str(len(rows)))
reads = []
orig = open
import builtins
def spy(p, *a, **k):
    if str(p).endswith(".jsonl"): reads.append(p)
    return orig(p, *a, **k)
builtins.open = spy
try: T.load(d)
finally: builtins.open = orig
check("incremental: nothing new → the log isn't opened", reads == [], str(reads))
write(d, [row(vid(i), "inc") for i in range(2)])
check("incremental: a file that shrank is read again", len(T.load(d)) == 2)
write(d, [row(vid(70), "old")], name="2026-01.jsonl")
check("incremental: months past the window are skipped", len(T.load(d, now=NOW)) == 2)
d2 = fresh_dir(); write(d2, [row(vid(1), "ro")]); T.summary(d2, cache=False)
check("read-only: cache=False writes nothing", not (d2 / ".taste.json").exists())
(d / ".taste.json").write_text("{garbled")
check("incremental: a garbled cache is rebuilt, not a crash", len(T.load(d)) == 2)
check("bounded: rows capped", T.MAX_ROWS <= 20000 and T.KEEP_DAYS <= 120)

# 8. audio signals without numpy match autolook's numpy version
d = fresh_dir(); (d / "analysis").mkdir()
rng = random.Random(5); raw = bytes(rng.randint(0, 255) for _ in range(29 * 600))
(d / "analysis" / f"{vid(1)}.u8").write_bytes(raw); (d / "analysis" / f"{vid(1)}.json").write_text(json.dumps({"bpm": 124.0, "width": 29}))
mine = T.song_signals(d, vid(1), {})
try:
    import autolook
    theirs = autolook.audio_signals(d / "analysis" / f"{vid(1)}.u8", {"bpm": 124.0, "width": 29})
    check("signals: stdlib matches autolook (numpy)", all(abs(mine[k] - theirs[k]) < 1e-3 for k in ("tilt", "density", "onset", "quiet")) and mine["bpm"] == 124.0,
          f"{mine} vs {theirs}")
except ImportError as e: check("signals: numpy missing, compared skipped", True, str(e))
check("signals: features from them", T.features({"vid": vid(1), "a": "x", "d": 200}, mine)["bpm"] == "fast")
write(d, [row(vid(1), "sig artist")] * 1)
m = T.model(d)
check("signals: the u8 is read once and cached", vid(1) in json.loads((d / ".taste.json").read_text())["sig"])

# 9. the cli: ytm taste and --json on scratch data, no player
PY = sys.executable; YTM = str(ROOT / "bin/ytm")
d = fresh_dir(); write(d, big[:60])
env = dict(os.environ, YTM_DATA=str(d))
p = subprocess.run([PY, YTM, "taste"], capture_output=True, text=True, env=env, timeout=30)
check("cli: ytm taste", p.returncode == 0 and "confidence:" in p.stdout and "finish vs skip" in p.stdout, p.stderr[-300:])
p = subprocess.run([PY, YTM, "taste", "--json"], capture_output=True, text=True, env=env, timeout=30)
try: j = json.loads(p.stdout); okj = j["plays"] == 60 and "headlines" in j and len(j["hours"]) == 24
except ValueError: okj = False
check("cli: ytm taste --json", p.returncode == 0 and okj and not p.stderr, p.stderr[-300:])
check("cli: no player was started", not (d / "mpv.sock").exists())

# 10. bin/ytm's work mode ranks (no network, no mpv: every edge faked)
_l = importlib.machinery.SourceFileLoader("ytmcli_t", YTM); _s = importlib.util.spec_from_loader("ytmcli_t", _l)
Y = importlib.util.module_from_spec(_s); os.environ["YTM_DATA"] = str(SCRATCH / "cli"); (SCRATCH / "cli" / "plays").mkdir(parents=True); _l.exec_module(Y)
assert str(Y.DATA).startswith(str(SCRATCH))
write(SCRATCH / "cli", [row(vid(i), "loved producer", mode="work", hour=time.localtime().tm_hour) for i in range(12)])
radio = [trk(i, "loved producer") for i in range(6)] + [trk(10 + i, "someone") for i in range(6)] + [trk(30, "vocal ft. x"), trk(31, "a mix", 3600)]
radio[12]["title"] = "song feat. singer"
class FakeYT:
    def get_watch_playlist(self, **k): return {"tracks": [dict(x) for x in radio]}
queued = []
Y.search = lambda q, n=3: [trk(99, "seed")]; Y.yt = lambda: FakeYT(); Y.ipc = lambda *a, **k: None; Y.set_mode = lambda m: None
Y.load = lambda *a, **k: None; Y.queue = lambda tracks, src, seed=None, first="append": queued.extend(tracks) or len(tracks)
with contextlib.redirect_stdout(io.StringIO()): Y.work()
names = [t["artists"][0]["name"] for t in queued]
check("work: vocals and mixes still filtered", len(queued) == 12 and "a mix" not in names and not any("feat" in t["title"] for t in queued), str(names))
check("work: ranked, the loved producer leads", names[:3] == ["loved producer"] * 3, str(names))
check("work: a broken taste falls back to youtube's order", Y.taste_rank([{"bad": 1}], "work") == [{"bad": 1}])

print(f"\n{'all passed' if not fails else f'{fails} failed'} · scratch {SCRATCH}")
sys.exit(1 if fails else 0)
