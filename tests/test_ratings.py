"""ytm ratings: the local like / dislike ledger, and what it changes. scratch data dir + a test keychain service: never the real
ratings, never the real account. run: python3 tests/test_ratings.py"""
import contextlib, importlib.machinery, importlib.util, io, json, os, runpy, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-test-")
os.environ.update({"YTM_DATA": SCRATCH, "YTM_AUTH_SERVICE": "ytm-test-ratings", "YTM_NO_REEXEC": "1"}); sys.argv = ["ytm"]
l = importlib.machinery.SourceFileLoader("ytmratings", str(ROOT / "lib/ratings.py"))
s = importlib.util.spec_from_loader("ytmratings", l); R = importlib.util.module_from_spec(s); l.exec_module(R)
fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

A, B, C = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"
check("rate stores a like", R.rate(A, "like", "Song A", "Low Roar") and R.get(A) == "LIKE")
check("rate stores a dislike", R.rate(B, "DISLIKE", "Song B", "Bad Band") and R.get(B) == "DISLIKE")
check("a bad id or rating writes nothing", not R.rate("short", "LIKE") and not R.rate(C, "LOVE") and R.get(C) == "" and set(R.load()) == {A, B})
check("the newest verdict wins (dislike after like)", R.rate(A, "DISLIKE") and R.get(A) == "DISLIKE")
check("INDIFFERENT clears it", R.rate(A, "INDIFFERENT") and R.get(A) == "")
R.rate(A, "LIKE", "Song A", "Low Roar, Ryan K")
check("summary counts", R.summary() == {"liked": 1, "disliked": 1}, str(R.summary()))
check("liked_artists splits 'A, B' and lowercases", R.liked_artists() == {"low roar": 1, "ryan k": 1}, str(R.liked_artists()))
tr = lambda v, a="X": {"videoId": v, "title": v, "artists": [{"name": a}]}
check("filter_tracks drops the disliked, keeps the rest in order", [t["videoId"] for t in R.filter_tracks([tr(A), tr(B), tr(C)])] == [A, C])
check("recent lists newest first with the artist", R.recent("LIKE")[0]["artist"].startswith("Low Roar"))
# blend: a liked-artist slot every 3rd place, nothing dropped
trs = [tr(f"{i:011d}", "Other") for i in range(6)] + [tr("fav00000001", "Low Roar"), tr("fav00000002", "Low Roar")]
out = [t["videoId"] for t in R.blend(trs)]
check("blend puts a liked artist first, then every third place", out[0] == "fav00000001" and out[3] == "fav00000002", str(out))
check("blend never drops or invents", sorted(out) == sorted(t["videoId"] for t in trs))
check("no likes, no change", [t["videoId"] for t in R.blend(trs, d=tempfile.mkdtemp())] == [t["videoId"] for t in trs])
# a corrupt ledger drops nothing and never raises
(Path(SCRATCH) / "ratings.json").write_text("{nope")
check("a corrupt ledger reads as empty, filter drops nothing", R.load() == {} and len(R.filter_tracks([tr(A), tr(B)])) == 2)
R.rate(B, "DISLIKE", "Song B", "Bad Band")
check("it recovers: the next write replaces the corrupt file", R.get(B) == "DISLIKE")
# the cap: oldest drop first
for i in range(R.CAP + 5): R.rate(f"{i:011d}", "LIKE", now=1000 + i)
check("the ledger is bounded and keeps the newest", len(R.load()) <= R.CAP and f"{R.CAP + 4:011d}" in R.load() and f"{0:011d}" not in R.load())
# integration with bin/ytm (signed out: the test keychain service has no session)
Y = runpy.run_path(str(ROOT / "bin/ytm"), run_name="lib")
(Path(SCRATCH) / "ratings.json").unlink()
Y["ipc"].__globals__["ipc"] = lambda *a, **k: "https://music.youtube.com/watch?v=" + A if a[:2] == ("get_property", "path") else None
(Path(SCRATCH) / "titles.json").write_text(json.dumps({A: {"title": "Song A", "artist": "Low Roar", "label": "Low Roar – Song A"}}))
ok, msg = Y["rate_current"]("LIKE")
check("signed out, a like is kept on this mac", ok and R.get(A) == "LIKE" and "kept on this mac" in msg, msg)
ok, msg = Y["rate_current"]("DISLIKE", B)
check("signed out, a dislike by id is kept", ok and R.get(B) == "DISLIKE", msg)
ok, msg = Y["rate_current"]("INDIFFERENT", B)
check("unrate clears it", ok and R.get(B) == "", msg)
check("rate_current refuses nonsense", Y["rate_current"]("LOVE")[0] is False)
queued = []
Y["load"].__globals__["load"] = lambda t, mode, *a, **k: queued.append(t["videoId"])
Y["remember"].__globals__["remember"] = lambda *a: None
R.rate(B, "DISLIKE", "Song B", "Bad")
Y["queue"]([tr(A), tr(B), tr(C)], "radio")
check("queue() never queues a disliked song", queued == [A, C], str(queued))
buf = io.StringIO()
with contextlib.redirect_stdout(buf): Y["account_cmd"]("rated", ["--json"])
j = json.loads(buf.getvalue()); check("`rated --json` for the app", j["summary"] == {"liked": 1, "disliked": 1} and j["liked"][0]["vid"] == A, str(j["summary"]))
sys.path.insert(0, str(ROOT / "lib")); import intent as I
hits = [{"videoId": B, "title": "Song B", "artists": [{"name": "Bad Band"}]}, {"videoId": C, "title": "Song B", "artists": [{"name": "Other"}]}]
check("search ranks a disliked song last", I.rank(hits, "song b", {}, {B})[0]["videoId"] == C)
print("FAILED" if fails else "all ok", fails)
raise SystemExit(1 if fails else 0)
