"""ytm library: the account's data, offline. a fake client stands in for ytmusicapi (no network, no login, no cookies);
scratch data only (YTM_DATA → a temp dir). synthetic names only.
run: python3 tests/test_library.py"""
import json, os, sys, tempfile, time
from datetime import date
from pathlib import Path

SCRATCH = tempfile.mkdtemp(prefix="ytm-lib-test-")
os.environ["YTM_DATA"] = SCRATCH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import library as L

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

def song(i, artist="test artist a", played=None, dur=200):
    t = {"videoId": f"vid{i:08d}", "title": f"test song {i}", "artists": [{"name": artist, "id": "UCx"}],
         "album": {"name": "test album", "id": "MPx"}, "duration_seconds": dur, "thumbnails": [{"url": "a"}, {"url": "b=w60-h60"}],
         "feedbackToken": "drop-me", "setVideoId": "drop-me-too"}
    if played: t["played"] = played
    return t

class Fake:
    """counts every call, so 'one request per function' and the ttl are checkable"""
    def __init__(self): self.calls = {}; self.hist = []; self.fail = False; self.rated = []
    def _n(self, k):
        self.calls[k] = self.calls.get(k, 0) + 1
        if self.fail: raise RuntimeError("offline")
    def get_history(self): self._n("history"); return self.hist
    def get_liked_songs(self, limit=100): self._n("liked"); self.liked_limit = limit; return {"tracks": [song(i) for i in range(min(limit, 1000))]}
    def get_library_playlists(self, limit=25): self._n("playlists"); self.pl_limit = limit; return [{"playlistId": f"PL{i}", "title": f"list {i}", "count": i} for i in range(500)]
    def get_home(self, limit=3):
        self._n("home"); self.home_limit = limit
        return [{"title": "Mixed for you", "contents": [{"playlistId": "RDTMAKmix1", "title": "My Mix 1"}, {"playlistId": "RDTMAKmix2", "title": "My Mix 2"}]},
                {"title": "Quick picks", "contents": [song(100, "test artist a"), song(101, "test artist b"), song(102, "test artist a"),
                                                      song(103, "test artist a"), song(104, "test artist c"), {"title": "no id"}]}] * 10
    def get_watch_playlist(self, playlistId=None, limit=25, **k): self._n("watch"); return {"tracks": [song(200 + i, "test artist d") for i in range(60)]}
    def rate_song(self, vid, rating): self._n("rate"); self.rated.append((vid, rating))

def calls(c, k): return c.calls.get(k, 0)

# 1. no client: everything is [] / False / zero, nothing written, no crash
for name, fn in [("history", lambda: L.history(10, None)), ("liked", lambda: L.liked(10, None)), ("playlists", lambda: L.playlists(None)),
                 ("mixes", lambda: L.mixes(None)), ("recommend", lambda: L.recommend(10, None))]:
    check(f"no client: {name} is []", fn() == [])
check("no client: rate is False", L.rate("vid00000001", "LIKE", None) is False)
check("no client: backfill adds nothing", L.backfill_log(None) == {"added": 0, "skipped": 0, "unparsed": 0})
check("no client: no plays dir made", not (Path(SCRATCH) / "plays").exists())

# 2. ttl cache: one request per ttl, then again after expiry; a failure falls back to the stale copy
c = Fake(); c.hist = [song(i, played="Today") for i in range(5)]
a = L.history(3, c); b = L.history(5, c)
check("history: one request for two reads", calls(c, "history") == 1, f"{calls(c, 'history')} calls")
check("history: n respected, slimmed", len(a) == 3 and len(b) == 5 and "feedbackToken" not in a[0] and a[0]["played"] == "Today")
check("cache lives in DATA/cache", (Path(SCRATCH) / "cache/history.json").exists())
check("slim keeps what label()/load() read", a[0]["artists"][0]["name"] == "test artist a" and a[0]["album"]["name"] == "test album"
      and a[0]["thumbnails"] == [{"url": "b=w60-h60"}])
p = Path(SCRATCH) / "cache/history.json"; d = json.loads(p.read_text()); d["t"] = time.time() - L.TTL["history"] - 1; p.write_text(json.dumps(d))
L.history(5, c); check("history: refetched after the ttl", calls(c, "history") == 2)
d = json.loads(p.read_text()); d["t"] = 0; p.write_text(json.dumps(d)); c.fail = True
r = L.history(5, c); check("a failed call hands back the stale cache", len(r) == 5 and calls(c, "history") == 3)
L._cache_drop("history"); check("a failed call with no cache is []", L.history(5, c) == [])
c.fail = False; L._cache_drop("history")
check("logged out: a warm cache is not handed back", (L.history(5, c), L.history(5, None))[1] == [])

# 3. caps: no unbounded pages
c = Fake()
check("liked capped", len(L.liked(10 ** 6, c)) == L.CAP["liked"] and c.liked_limit == L.CAP["liked"], f"limit={c.liked_limit}")
check("playlists capped", len(L.playlists(c)) == L.CAP["playlists"] and c.pl_limit == L.CAP["playlists"])
c.hist = [song(i, played="Today") for i in range(1000)]; L._cache_drop("history")
check("history capped", len(L.history(10 ** 6, c)) == L.CAP["history"])
check("home rows capped", len(L.home(c)) == L.CAP["home"] and c.home_limit == L.CAP["home"])
m = L.mixes(c); check("mixes: the mixed-for-you row", [x["playlistId"] for x in m[:2]] == ["RDTMAKmix1", "RDTMAKmix2"] and m[0]["row"] == "Mixed for you")
check("mixes reuse the home cache", calls(c, "home") == 1)

# 4. rate: validates before any request, drops the likes cache when it lands
c = Fake(); L.liked(5, c)
check("rate: bad rating refused", L.rate("vid00000001", "LOVE", c) is False and calls(c, "rate") == 0)
check("rate: bad vid refused", L.rate("nope", "LIKE", c) is False and calls(c, "rate") == 0)
check("rate: like lands", L.rate("vid00000001", "like", c) is True and c.rated == [("vid00000001", "LIKE")])
check("rate: likes cache dropped", not list((Path(SCRATCH) / "cache").glob("liked-*.json")))

# 5. backfill: row shape, dedupe by vid+day, re-runs quiet, vague labels, unknown labels skipped
today = date(2026, 10, 23); plays = Path(SCRATCH) / "plays"; plays.mkdir(exist_ok=True)
own = {"v": 1, "id": "x", "start": "2026-10-23T09:00:00+05:30", "vid": "vid00000001", "outcome": "finished", "source": "search", "app": "ytm"}
(plays / "2026-10.jsonl").write_text(json.dumps(own) + "\n")
c = Fake(); c.hist = [song(0, played="Today"), song(1, played="Today"), song(0, played="Today"), song(2, played="Yesterday"),
                      song(3, played="This week"), song(4, played="September 2026"), song(5, played="Heute")]
L._cache_drop("history"); r = L.backfill_log(c, today=today)
check("backfill: counts", r == {"added": 4, "skipped": 2, "unparsed": 1}, str(r))
rows = [json.loads(l) for f in sorted(plays.glob("*.jsonl")) for l in f.read_text().splitlines()]
new = [x for x in rows if x["source"] == "yt-history"]
check("backfill: marked yt-history / ytmusic / unknown", all(x["app"] == "ytmusic" and x["outcome"] == "unknown" for x in new))
check("backfill: same vid same day once, and not over ytm's own play", sum(x["vid"] == "vid00000000" for x in new) == 1
      and not any(x["vid"] == "vid00000001" for x in new))
check("backfill: row has the log's fields", all(k in new[0] for k in ("v", "id", "start", "end", "vid", "title", "artist", "album", "dur", "listened", "hour", "dow", "mode")))
check("backfill: month label lands in its month file", any(x["vid"] == "vid00000004" for x in map(json.loads, (plays / "2026-09.jsonl").read_text().splitlines())))
check("backfill: exact days exact", {x["vid"]: x["start"][:10] for x in new}.get("vid00000002") == "2026-10-22")
check("backfill: the user's own row untouched", json.loads((plays / "2026-10.jsonl").read_text().splitlines()[0]) == own)
L._cache_drop("history"); r2 = L.backfill_log(c, today=today)
check("backfill: a re-run adds nothing", r2["added"] == 0, str(r2))
c.hist = [song(0, played="Yesterday"), song(3, played="Last week"), song(4, played="September 2026")]
L._cache_drop("history"); r3 = L.backfill_log(c, today=date(2026, 10, 24))
check("backfill: next day, labels shifted, still nothing new", r3["added"] == 0, str(r3))

# 6. recommend: log artist scores rank, skipped songs never return, ≤2 per artist, mix tops it up when home is short
log = [{"vid": "vid00000104", "artist": "test artist c", "outcome": "finished", "start": "2026-10-23T10:00:00+05:30"}] * 5 + \
      [{"vid": "vid00000101", "artist": "test artist b", "outcome": "skipped", "start": "2026-10-23T10:00:00+05:30"}] * 2
with open(plays / "2026-10.jsonl", "a") as f: f.write("".join(json.dumps(x) + "\n" for x in log))
L._cache_drop("home"); c = Fake(); rec = L.recommend(10, c); ids = [t["videoId"] for t in rec]
check("recommend: a finished artist comes first", ids[0] == "vid00000104", str(ids[:3]))
check("recommend: an often-skipped song stays out", "vid00000101" not in ids)
check("recommend: at most 2 per artist", max(sum(1 for t in rec if L.artist_of(t) == a) for a in {L.artist_of(t) for t in rec}) <= 2)
check("recommend: topped up from one mix, ≤ 2 requests", len(rec) <= 10 and calls(c, "watch") == 1 and calls(c, "home") <= 1,
      f"{len(rec)} tracks, calls {c.calls}")
L.recommend(10, c); check("recommend: second call is all cache", calls(c, "watch") == 1 and calls(c, "home") <= 1)
check("recommend: tracks load()-ready", all(t.get("videoId") and t.get("title") is not None and isinstance(t.get("artists"), list) for t in rec))

print(f"\n{'all passed' if not fails else f'{fails} failed'} · scratch {SCRATCH}")
sys.exit(1 if fails else 0)
