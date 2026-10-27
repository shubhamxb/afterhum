"""ytm cli (0.8): prefs, look, auth status, thumbs, play_tracks, the authed→anonymous fallback, --version. offline:
fake clients only, ipc recorded (never a real mpv), scratch YTM_DATA, keychain under test service names only.
run: python3 tests/test_cli.py"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-cli-test-")
ENV = {"YTM_DATA": SCRATCH, "YTM_NO_REEXEC": "1", "YTM_AUTH_SERVICE": "ytm-test-auth", "YTM_SPOTIFY_SERVICE": "ytm-test-spotify"}
os.environ.update(ENV); os.environ.pop("SPOTIFY_CLIENT_ID", None)  # a real client id in the shell would leak into status
BIN = ROOT / "bin/ytm"
PY = sys.executable
l = importlib.machinery.SourceFileLoader("ytmcli", str(BIN))
s = importlib.util.spec_from_loader("ytmcli", l); Y = importlib.util.module_from_spec(s); l.exec_module(Y)

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

def ytm(*args):
    return subprocess.run([PY, str(BIN), *args], capture_output=True, text=True, timeout=20, env={**os.environ, **ENV})

def song(i, artist="test artist a"):
    return {"videoId": f"vid{i:08d}", "title": f"test song {i}", "artists": [{"name": artist, "id": "UCx"}],
            "album": {"name": "test album", "id": "MPx"}, "duration_seconds": 200, "thumbnails": [{"url": "a=w60-h60"}]}

class Fake:
    """an authed client: records thumbs, serves a home page and a mix; fail=True makes every call raise"""
    def __init__(self, fail=False): self.fail = fail; self.rated = []; self.calls = []
    def _n(self, k):
        self.calls.append(k)
        if self.fail: raise RuntimeError("cookies rotated")
    def rate_song(self, vid, rating): self._n("rate"); self.rated.append((vid, rating))
    def search(self, q, filter=None, limit=10): self._n("search"); return [song(1)]
    def get_home(self, limit=3):
        self._n("home")
        return [{"title": "Mixed for you", "contents": [{"playlistId": "RDTMAKmix1", "title": "My Mix 1"}, {"playlistId": "RDTMAKmix2", "title": "My Mix 2"}]},
                {"title": "Quick picks", "contents": [song(100), song(101, "test artist b"), song(102, "test artist c")]}]
    def get_watch_playlist(self, playlistId=None, videoId=None, limit=25, **k):
        self._n("watch"); return {"tracks": [song(300 + i, f"test artist {i % 4}") for i in range(30)]}

class Anon(Fake):
    def search(self, q, filter=None, limit=10): self._n("anon-search"); return [song(2)]

calls = []
def fake_ipc(*cmd, start=True):
    calls.append(cmd); return None
Y.ipc = fake_ipc
REAL = {n: getattr(Y, n) for n in ("account_client", "play")}   # section 4 on swaps these for fakes

# 1. prefs: defaults, garbled, unknown keys, merge, bad values, atomic (no tmp left behind)
P = Path(SCRATCH) / "prefs.json"
check("missing prefs read as the defaults (0.8.1: auto)", Y.prefs_read() == {"v": 1, "look": "auto", "accent": "cover", "motion": "calm", "moments": True})
P.write_text("{garbled")
check("garbled prefs read as the defaults", Y.prefs_read()["look"] == "auto")
P.write_text(json.dumps(["not", "a", "dict"]))
check("a non-dict prefs file reads as the defaults", Y.prefs_read()["accent"] == "cover")
P.write_text(json.dumps({"v": 1, "look": "plaid", "motion": "still", "future_key": 7}))
r = Y.prefs_read()
check("an unknown value falls back, known ones and unknown keys stay", r["look"] == "auto" and r["motion"] == "still" and r["future_key"] == 7, str(r))
r = Y.prefs_write(look="neon")
raw = json.loads(P.read_text())
check("write merges: only the changed key is touched", raw == {"v": 1, "look": "neon", "motion": "still", "future_key": 7}, str(raw))
check("write returns the filled prefs", r["look"] == "neon" and r["accent"] == "cover" and r["moments"] is True)
try: Y.prefs_write(accent="plaid"); bad = False
except ValueError: bad = True
check("a bad value is refused, file unchanged", bad and json.loads(P.read_text()) == raw)
Y.prefs_write(moments=False)
check("moments false sticks", Y.prefs_read()["moments"] is False)
check("no tmp files left", not list(Path(SCRATCH).glob(".prefs.*")))
check("contract lists", Y.LOOKS[:5] == ["dusk", "rain", "neon", "stars", "paper"] and Y.ACCENTS == ["cover", "night", "ember", "sea", "mono"]
      and Y.MOTIONS == ["still", "calm", "lively"])

# 2. look / prefs commands (a real process, scratch data)
P.unlink()
r = ytm("look")
check("ytm look prints the defaults", r.returncode == 0 and "look auto" in r.stdout and "accent cover" in r.stdout, r.stdout + r.stderr)
r = ytm("look", "rain", "window")
check("ytm look 'rain window' sets rain", r.returncode == 0 and json.loads(P.read_text())["look"] == "rain", r.stdout + r.stderr)
r = ytm("look", "stargaze")
check("the app's label works too (stargaze → stars)", json.loads(P.read_text())["look"] == "stars", r.stdout)
ytm("look", "accent", "ember"); ytm("look", "motion", "still")
d = json.loads(P.read_text())
check("look accent / look motion", d["accent"] == "ember" and d["motion"] == "still" and d["look"] == "stars", str(d))
r = ytm("look", "plaid")
check("an unknown look exits 2 and changes nothing", r.returncode == 2 and json.loads(P.read_text())["look"] == "stars", r.stdout)
(Path(SCRATCH) / "mode").write_text("work")
r = ytm("look")
check("work mode + a hand-picked look: no paper note (the pick wins)", "work mode" not in r.stdout, r.stdout)
ytm("look", "auto"); r = ytm("look")
check("work mode on auto says paper shows", "work mode" in r.stdout, r.stdout)
ytm("look", "stars")
(Path(SCRATCH) / "mode").write_text("normal")
r = ytm("prefs", "--json")
check("prefs --json", r.returncode == 0 and json.loads(r.stdout)["accent"] == "ember", r.stdout + r.stderr)

# 2b. auto (0.8.1): ytm look auto writes it, a chosen look still wins, ytm look says what the song got and why
r = ytm("look", "auto")
check("ytm look auto", r.returncode == 0 and json.loads(P.read_text())["look"] == "auto" and "look auto" in r.stdout, r.stdout + r.stderr)
import contextlib, io
LOOKS_F = Path(SCRATCH) / "looks.json"
LOOKS_F.write_text(json.dumps({"KMuVsv3KFaI": {"look": "neon", "accent": "cover", "motion": "lively", "conf": 0.88, "rules": 1,
                                               "reason": "neon drive · 129 bpm, heavy bass, a wall of sound", "from": "words+cover+audio"}}))
was = Y.current_id; Y.current_id = lambda: "KMuVsv3KFaI"
out = io.StringIO()
with contextlib.redirect_stdout(out): Y.look_cmd([])
check("ytm look shows the cached pick + why", "look auto · neon drive (129 bpm, heavy bass, a wall of sound)" in out.getvalue(), out.getvalue())
TF = Path(SCRATCH) / "titles.json"; TF.write_text(json.dumps({"NdMCRVtLCLQ": {"title": "Midnight Window", "artist": "Zyra", "album": "Lo-Fi Nights"}}))
Y.current_id = lambda: "NdMCRVtLCLQ"; out = io.StringIO()
with contextlib.redirect_stdout(out): Y.look_cmd([])
check("no cached pick: worked out from the words on disk", "look auto · rain window ('lo-fi')" in out.getvalue(), out.getvalue())
Y.current_id = lambda: None; out = io.StringIO()
with contextlib.redirect_stdout(out): Y.look_cmd([])
check("nothing playing: just auto", out.getvalue().startswith("look auto · accent"), out.getvalue())
LOOKS_F.write_text("{garbled"); Y.current_id = lambda: "KMuVsv3KFaI"; out = io.StringIO()
with contextlib.redirect_stdout(out): Y.look_cmd([])
check("a garbled looks.json is no cache, no crash", out.getvalue().startswith("look auto"), out.getvalue())
Y.current_id = was; LOOKS_F.unlink(); TF.unlink()
CURRENT_ID = was   # section 4 swaps in a fake; 7e checks the real one
r = ytm("look", "neon")
check("a chosen look replaces auto", json.loads(P.read_text())["look"] == "neon", r.stdout)

# 3. auth status --json: the shape, no login under the test services, and fast
t0 = time.time(); r = ytm("auth", "status", "--json"); dt = time.time() - t0
try: a = json.loads(r.stdout)
except ValueError: a = {}
check("auth status --json shape", set(a) == {"ytmusic", "spotify"} and {"logged_in", "source", "account", "saved_at"} <= set(a["ytmusic"])
      and {"logged_in", "client_id"} <= set(a["spotify"]), r.stdout + r.stderr)
check("logged out under the test services", a.get("ytmusic", {}).get("logged_in") is False and a.get("spotify", {}).get("logged_in") is False)
check("auth status under a second", dt < 1.0, f"{dt:.2f} s")
r = ytm("auth", "status")
check("auth status, human", r.returncode == 0 and "not logged in" in r.stdout, r.stdout)
check("account() never raises", set(Y.account()) == {"ytmusic", "spotify"})

# 4. thumbs: a fake client, a fake current song
fake = Fake(); Y.account_client = lambda: fake; Y.current_id = lambda: "vid00000042"
ok, msg = Y.rate_current("LIKE")
check("like the current song", ok and fake.rated == [("vid00000042", "LIKE")] and "liked" in msg, msg)
ok, msg = Y.rate_current("dislike", "vid00000043")
check("dislike a given vid", ok and fake.rated[-1] == ("vid00000043", "DISLIKE"), msg)
ok, msg = Y.rate_current("MEH")
check("a bad rating is refused without a request", not ok and len(fake.rated) == 2, msg)
Y.current_id = lambda: None
ok, msg = Y.rate_current("LIKE")
check("nothing playing", not ok and "nothing playing" in msg, msg)
Y.current_id = lambda: "vid00000042"; Y.account_client = lambda: None
ok, msg = Y.rate_current("LIKE")
check("logged out: the like is still kept on this mac, and it says so", ok and "kept on this mac" in msg and "sign in" in msg, msg)
Y.account_client = lambda: Fake(fail=True)
ok, msg = Y.rate_current("LIKE")
check("youtube refusing: still kept here, and says youtube didn't take it", ok and "kept here" in msg and "didn't take" in msg, msg)

# 5. play_tracks: queue replaced, the rest appended, play; ipc recorded only
calls.clear()
n = Y.play_tracks([song(1), {"title": "no id"}, song(2), song(3)], "likes")
loads = [c for c in calls if c[0] == "loadfile"]
check("play_tracks queues the playable ones", n == 3 and [c[2] for c in loads] == ["replace", "append", "append"], str([c[:3] for c in loads]))
check("…and starts playing", ("set_property", "pause", False) in calls)
check("…sources tagged for the log", any(c[1] == "user-data/ytm-src-vid00000001" and json.loads(c[2])["source"] == "likes" for c in calls if c[0] == "set_property"))
calls.clear()
n = Y.play_tracks([song(i) for i in range(500)], "likes", shuffle=True)
check("shuffled, capped", n == Y.PLAY_CAP and len([c for c in calls if c[0] == "loadfile"]) == Y.PLAY_CAP)
calls.clear()
check("nothing to play: 0, mpv untouched", Y.play_tracks([], "likes") == 0 and calls == [])

# 6. yt(): authed when logged in; a raising authed call falls back to anonymous once, then stays anonymous
anon = Anon(); Y._anon = lambda: anon
good = Fake(); Y.account_client = lambda: good
check("logged in: search goes through the account", Y.search("x", 1)[0]["videoId"] == "vid00000001" and good.calls == ["search"])
bad = Fake(fail=True); Y.account_client = lambda: bad
hits = Y.search("x", 1)
check("an authed call that raises falls back to anonymous", hits and hits[0]["videoId"] == "vid00000002" and bad.calls == ["search"], str(bad.calls))
Y.search("x", 1)
check("…and the process stays anonymous", bad.calls == ["search"] and anon.calls.count("anon-search") == 2)
Y._ANON = False
Y.account_client = lambda: None
check("logged out: anonymous", Y.search("x", 1)[0]["videoId"] == "vid00000002")

# 7. for you and mixes with the fake account
Y.account_client = lambda: good; calls.clear()
fy = Y.for_you(5)
check("for_you returns tracks from the home page", fy and all(t.get("videoId") for t in fy), str([t["videoId"] for t in fy]))
check("main mixes lists them", Y.main(["mixes"]) is None)
calls.clear()
check("main mix 2 plays the second mix", Y.main(["mix", "2"]) is None and len([c for c in calls if c[0] == "loadfile"]) == 30)
check("mix out of range exits 1", Y.main(["mix", "9"]) == 1)
Y.account_client = lambda: None
check("for_you logged out is []", Y.for_you() == [])
check("likes logged out exits 1", Y.main(["likes"]) == 1)

# 7b. mpv's flags: every one its own string (a missing comma once glued two together, a # ate three)
check("mpv flags are all real flags", all(f.startswith("--") and f.count("--") == 1 for f in Y.MPV_ARGS), str([f for f in Y.MPV_ARGS if f.count("--") != 1]))
for f in ("--ytdl-format=234/233/bestaudio", "--ao=avfoundation,coreaudio", "--cache=yes", "--cache-secs=120", "--audio-buffer=1", "--demuxer-max-bytes=16MiB"):
    check(f"mpv gets {f}", f in Y.MPV_ARGS)

# 7c. the login is looked up again after a miss (the ui lives for hours; `ytm login` happens in another tab)
A = Y._lib("auth"); real_client = A.client; seen = []
A.client = lambda: seen.append(1) or None
Y.account_client = REAL["account_client"]; Y._ACCT = None; Y._ANON = True
check("logged out: None", Y.account_client() is None)
A.client = lambda: seen.append(1) or good
check("a login elsewhere counts on the next call", Y.account_client() is good and len(seen) == 2)
check("…and is kept (one keychain read)", Y.account_client() is good and len(seen) == 2)
Y.reset_account()
check("reset_account forgets the client and the anonymous fallback", Y._ACCT is None and Y._ANON is False and A._CLIENT is None)
A.client = lambda: None
check("…so a logout elsewhere counts after it", Y.account_client() is None)
A.client = real_client; Y._ACCT = None

# 7d. words that are also commands: ytm play / ytm -- search, and like/dislike with words is a song
played = []; Y.play = lambda q: played.append(q); Y.account_client = lambda: None
for argv, want in ((["play", "logout"], "logout"), (["--", "likes"], "likes"), (["play", "look", "back", "in", "anger"], "look back in anger"),
                   (["like", "a", "rolling", "stone"], "like a rolling stone"), (["dislike", "me"], "dislike me")):
    played.clear(); Y.main(argv)
    check(f"ytm {' '.join(argv)} searches", played == [want], str(played))
played.clear(); rc = Y.main(["like", "vid00000042"])
check("like <vid> still rates (no search); signed out it is kept locally, exit 0", played == [] and rc in (None, 0), str(rc))
check("bare ytm play is a usage error", Y.main(["play"]) == 2)
r = ytm("look", "back", "in", "anger")
check("a bad look points at ytm play", r.returncode == 2 and "ytm play look back in anger" in r.stdout, r.stdout)
Y.play = REAL["play"]
SRC = "".join(f.read_text() for f in (ROOT / "stage/Sources/YtmStage").rglob("*.swift"))
import re as _re
_ids = _re.search(r'static let ids = \[([^\]]*)\]', SRC.split("enum Look", 1)[1]).group(1)
check("the stage and the cli agree on the looks (Look.ids == lib/looks.py LOOKS)", _re.findall(r'"(\w+)"', _ids) == Y.LOOKS, _ids)
check("the app searches with play (words or --vid), never bare words", ('player.ytm(["play", q])' in SRC or '["play", "--vid"' in SRC)
      and "player.ytm([q])" not in SRC)

# 7e. the cover (0.8.1): radio rows get an art url and their length, a re-heard song moves to the end, titles.json is whole
TF = Path(SCRATCH) / "titles.json"
watch_row = {"videoId": "radio000001", "title": "a radio song", "artists": [{"name": "someone"}], "length": "3:45", "videoType": "MUSIC_VIDEO_TYPE_ATV",
             "thumbnail": [{"url": "https://lh3.googleusercontent.com/x=w60-h60-l90-rj"}, {"url": "https://lh3.googleusercontent.com/x=w120-h120-l90-rj"}]}
check("art_url reads a watch row's singular thumbnail", Y.art_url(watch_row) == "https://lh3.googleusercontent.com/x=w544-h544-l90-rj", Y.art_url(watch_row))
check("art_url still reads search's thumbnails", Y.art_url(song(1)) == "a=w544-h544")
check("art_url with neither is empty", Y.art_url({}) == "")
Y.remember(watch_row); row = json.loads(TF.read_text())["radio000001"]
check("remember keeps art, length and video type", row["art"].endswith("=w544-h544-l90-rj") and row["secs"] == 225 and row["type"] == "MUSIC_VIDEO_TYPE_ATV", str(row))
check("secs from duration_seconds too", Y.secs(song(1)) == 200 and Y.secs({}) is None and Y.secs({"length": "1:02:03"}) == 3723)
TF.write_text(json.dumps({"vid00000007": {"label": "first seen long ago"}, **{f"old{i:08d}": {"label": f"old {i}"} for i in range(1998)}}))
Y.remember(song(8)); Y.remember(song(7))
m = list(json.loads(TF.read_text()))
check("a re-heard song moves to the end", m[-1] == "vid00000007" and m[-2] == "vid00000008" and len(m) == 2000, str(m[-3:]))
Y.remember(song(9)); m = json.loads(TF.read_text())
check("the prune drops the oldest heard, never what's playing", len(m) == 1500 and "vid00000007" in m and "vid00000009" in m and "old00000000" not in m, str(len(m)))
check("titles.json written whole (no tmp left)", not list(Path(SCRATCH).glob(".titles.*")))
TF.unlink()
for path, want in (("https://music.youtube.com/watch?v=zqKrpw36xnU&pp=ygUv=x", "zqKrpw36xnU"), ("", None), ("/tmp/a.mp3", None),
                   ("https://youtu.be/zqKrpw36xnU", None), ("https://www.youtube.com/watch?list=x&v=zqKrpw36xnU", "zqKrpw36xnU")):
    Y.ipc = lambda *c, start=True, p=path: p
    check(f"current_id({path or 'empty'!r})", CURRENT_ID() == want, str(CURRENT_ID()))
Y.ipc = fake_ipc

# 9. three results (0.8.2): one constant, the cap everywhere, search --json's contract, play --vid, the bare title
class Many(Fake):
    def search(self, q, filter=None, limit=10):
        self._n(f"search:{limit}"); rows = [song(i) for i in range(1, 11)]
        rows[1] = {**rows[1], "duration_seconds": None, "duration": "4:05", "album": None, "thumbnails": []}   # odd rows still fill every key
        return rows
    def get_watch_playlist(self, playlistId=None, videoId=None, limit=25, **k):
        self._n("watch"); return {"tracks": [{**song(300 + i), "duration_seconds": None, "length": "3:00"} for i in range(30)]}
many = Many(); Y.account_client = lambda: None; Y._anon = lambda: many; Y._ANON = False
check("SEARCH_N is 3", Y.SEARCH_N == 3)
check("search() caps at 3 by default", [t["videoId"] for t in Y.search("x")] == ["vid00000001", "vid00000002", "vid00000003"])
out = io.StringIO(); err = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): rc = Y.search_cmd(["--json", "test", "words"])
cards = json.loads(out.getvalue())
check("search --json: one json line, nothing on stderr, exit 0", rc is None and out.getvalue().count("\n") == 1 and err.getvalue() == "", repr(err.getvalue()))
check("search --json: exactly 3", len(cards) == 3, str(len(cards)))
check("search --json: the six keys, always", all(list(c) == ["videoId", "title", "artist", "album", "duration", "art"] for c in cards), str(cards[1]))
check("search --json: types (str, str, str, str, int, str)", all(isinstance(c["duration"], int) and all(isinstance(c[k], str) for k in ("videoId", "title", "artist", "album", "art")) for c in cards))
check("search --json: values", cards[0] == {"videoId": "vid00000001", "title": "test song 1", "artist": "test artist a", "album": "test album", "duration": 200, "art": "a=w226-h226"}, str(cards[0]))
check("search --json: a row missing things says '' and 0 / the m:ss length", cards[1]["album"] == "" and cards[1]["art"] == "" and cards[1]["duration"] == 245, str(cards[1]))
last = json.loads((Path(SCRATCH) / "search.json").read_text())
check("the last search is kept for play --vid", last["q"] == "test words" and [r["videoId"] for r in last["rows"]] == [c["videoId"] for c in cards])
class Down(Fake):
    def search(self, q, filter=None, limit=10): print("noise", file=sys.stderr); raise OSError("no network")
Y._anon = lambda: Down(); out = io.StringIO(); err = io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err): rc = Y.search_cmd(["--json", "x"])
check("search --json offline: [] and exit 1, stderr silent", rc == 1 and out.getvalue().strip() == "[]" and err.getvalue() == "", repr(out.getvalue() + err.getvalue()))
Y._anon = lambda: many; out = io.StringIO()
with contextlib.redirect_stdout(out): Y.search_cmd(["test"])
lines = out.getvalue().splitlines()
check("plain search: three numbered lines", len(lines) == 3 and lines[0] == "1  test artist a – test song 1  3:20" and lines[2].startswith("3  "), str(lines))
r = ytm("search", "--json")
check("search --json with no words: [] and exit 2", r.returncode == 2 and r.stdout.strip() == "[]", r.stdout + r.stderr)
calls.clear(); out = io.StringIO()
with contextlib.redirect_stdout(out): rc = Y.main(["search", "--json", "q"])
check("main routes search, and the player is never asked", rc is None and len(json.loads(out.getvalue())) == 3 and calls == [], str(calls))
check("pick and the tui use the constant", "hits = search(rest)" in BIN.read_text() and "Y.search(q, Y.SEARCH_N)" in (ROOT / "tui.py").read_text())
calls.clear(); Y.load(song(5), "replace")
lf = [c for c in calls if c[0] == "loadfile"][0]
check("load gives mpv the bare title (now playing has the artist already)", lf[-1] == f"force-media-title=%{len('test song 5')}%test song 5", str(lf))
calls.clear(); Y.load({"videoId": "vid00000006"}, "replace")
check("no title known: no force-media-title (yt-dlp names it)", [c for c in calls if c[0] == "loadfile"][0] == ("loadfile", "https://music.youtube.com/watch?v=vid00000006", "replace", -1))

# 9b. play --vid: a card's song now, its radio after; titles.json once per batch
spawned = []; real_popen = Y.subprocess.Popen
Y.subprocess.Popen = lambda args, **k: spawned.append((args, k))
with contextlib.redirect_stdout(io.StringIO()): Y.search_cmd(["--json", "test", "words"])
with contextlib.redirect_stdout(io.StringIO()) as o: rc = Y.main(["play", "--vid", "vid00000002"])
lf = [c for c in calls if c[0] == "loadfile"]; calls_vid = [c for c in calls if c[0] == "set_property" and c[1] == "user-data/ytm-src-vid00000002"]
check("play --vid from the last search: replace, no second lookup", rc is None and lf[-1][1:3] == ("https://music.youtube.com/watch?v=vid00000002", "replace")
      and "watch" not in many.calls, str(lf[-1:]))
check("…logged as the search it came from", calls_vid and json.loads(calls_vid[-1][2])["source"] == "search" and json.loads(calls_vid[-1][2])["query"] == "test words")
check("…the radio in a detached child, never on the caller's pipe", spawned and spawned[-1][0][-2:] == ["_radio", "vid00000002"]
      and spawned[-1][1].get("stdout") == Y.subprocess.DEVNULL and spawned[-1][1].get("start_new_session"), str(spawned[-1:]))
check("…and it says so at once", "its radio follows" in o.getvalue(), o.getvalue())
check("play --vid with a bad id: usage, exit 2", Y.main(["play", "--vid", "nope"]) == 2 and Y.main(["play", "--vid"]) == 2)
(Path(SCRATCH) / "search.json").unlink(); calls.clear(); many.calls.clear()
with contextlib.redirect_stdout(io.StringIO()): Y.main(["play", "--vid", "vidUNKNOWN1"])
lf = [c for c in calls if c[0] == "loadfile"]
check("play --vid unknown: one watch fetch names it and is its radio", many.calls == ["watch"] and lf[0][2] == "replace" and len(lf) == 27, f"{many.calls} {len(lf)}")   # 26 rows max, none of them the song
Y.subprocess.Popen = real_popen
writes = []; real_rem = Y.remember
Y.remember = lambda *t: writes.append(len(t)) or real_rem(*t)
calls.clear(); n = Y.radio_from("vid00000300")
check("radio: titles.json written once for the batch", writes == [n] and n == 25, str(writes))   # 26 kept, the seed skipped
calls.clear(); writes.clear(); Y.play_tracks([song(i) for i in range(10)], "likes")
check("play_tracks: once too", writes == [10], str(writes))
Y.remember = real_rem
rows = json.loads((Path(SCRATCH) / "titles.json").read_text())
check("…and the rows are whole", rows["vid00000301"]["secs"] == 180 and rows["vid00000301"]["label"] == "test artist a – test song 301", str(rows["vid00000301"]))

# 9c. the radio child: only if it's still the radio wanted, the song still queued, the player still there
Y.radio_tracks = lambda v: [song(i) for i in range(400, 426)]
Y.SOCK.touch(); pl = [{"filename": "https://music.youtube.com/watch?v=vid00000400&pp=x"}]
Y.ipc = lambda *c, start=True: (calls.append(c), pl if c[:2] == ("get_property", "playlist") else None)[1]
Y.RADIO_WANT.write_text("vid00000400"); calls.clear()
check("radio child: appends the rest (not the song itself)", Y.radio_child("vid00000400") == 25 and len([c for c in calls if c[0] == "loadfile"]) == 25)
Y.RADIO_WANT.write_text("vid00000999"); calls.clear()
check("radio child: a newer play won, nothing added", Y.radio_child("vid00000400") == 0 and not [c for c in calls if c[0] == "loadfile"])
Y.RADIO_WANT.write_text("vid00000400"); pl = [{"filename": "https://music.youtube.com/watch?v=vid00000777"}]; calls.clear()
check("radio child: the queue was replaced, nothing added", Y.radio_child("vid00000400") == 0 and not [c for c in calls if c[0] == "loadfile"])
Y.SOCK.unlink(); calls.clear()
check("radio child: ytm stop came first, nothing added", Y.radio_child("vid00000400") == 0 and calls == [])

# 9d. refill (ytm-log.lua asks when the queue really ran out): idle only, locked, work mode refills with work
idle = [True]; Y.ipc = lambda *c, start=True: (calls.append(c), idle[0] if c[:2] == ("get_property", "idle-active") else None)[1]
Y._anon = lambda: many; (Path(SCRATCH) / "mode").write_text("normal"); calls.clear()
with contextlib.redirect_stdout(io.StringIO()): Y.main(["radio", "--refill", "vid00000300"])
lf = [c for c in calls if c[0] == "loadfile"]
check("refill: clear, then the first appended plays", ("playlist-clear",) in calls and lf[0][2] == "append-play" and all(c[2] == "append" for c in lf[1:]) and len(lf) == 25, str(lf[:2]))
check("refill: logged as a refill", any(c[0] == "set_property" and c[1].startswith("user-data/ytm-src-") and json.loads(c[2])["source"] == "refill" for c in calls))
idle[0] = False; calls.clear()
check("refill: something playing meanwhile, nothing done", Y.refill("vid00000300") == 0 and not [c for c in calls if c[0] in ("loadfile", "playlist-clear")])
idle[0] = True; worked = []; real_mode = Y.mode_cmd; Y.mode_cmd = lambda name, refill=False: worked.append((name, refill))
for m in ("work", "focus", "sleep"):
    (Path(SCRATCH) / "mode").write_text(m); Y.refill("vid00000300")
Y.mode_cmd = real_mode; (Path(SCRATCH) / "mode").write_text("normal")
check("refill in a mode is more of that mode (and never toggles it off)", worked == [("work", True), ("focus", True), ("sleep", True)], str(worked))
# the toggle: the same mode again turns it off, `off` too, an unknown one exits 2 (no network: the off path never searches)
Y.set_mode = lambda m: (Path(SCRATCH) / "mode").write_text(m)
(Path(SCRATCH) / "mode").write_text("focus")
with contextlib.redirect_stdout(io.StringIO()) as o: Y.mode_cmd("focus")
check("mode focus while in focus: off, back to normal", (Path(SCRATCH) / "mode").read_text() == "normal" and "off" in o.getvalue(), o.getvalue())
(Path(SCRATCH) / "mode").write_text("chill")
with contextlib.redirect_stdout(io.StringIO()): Y.mode_cmd("off")
check("mode off: normal", (Path(SCRATCH) / "mode").read_text() == "normal")
with contextlib.redirect_stdout(io.StringIO()): rc = Y.mode_cmd("disco")
check("an unknown mode exits 2", rc == 2)
(Path(SCRATCH) / "mode").write_text("normal")
import fcntl
with (Path(SCRATCH) / ".refill.lock").open("w") as held:
    fcntl.flock(held, fcntl.LOCK_EX); calls.clear()
    check("refill: one at a time (a second steps aside)", Y.refill("vid00000300") == 0 and calls == [])
check("refill: a bad id is refused", Y.refill("x") == 2)
Y.ipc = fake_ipc

# 9e. ytm q reads the id as current_id does (a later v= in the url gave a wrong id)
(Path(SCRATCH) / "titles.json").write_text(json.dumps({"zqKrpw36xnU": {"label": "test artist – the right song"}}))
Y.ipc = lambda *c, start=True: [{"filename": "https://music.youtube.com/watch?v=zqKrpw36xnU&pp=ygUv=x", "current": True, "title": "bare"}]
out = io.StringIO()
with contextlib.redirect_stdout(out): Y.main(["q"])
check("ytm q: the label, from the query-item id", out.getvalue().strip() == "▶  0  test artist – the right song", repr(out.getvalue()))
check("vid_of", Y.vid_of("https://music.youtube.com/watch?v=zqKrpw36xnU&pp=ygUv=x") == "zqKrpw36xnU" and Y.vid_of(None) is None and Y.vid_of("x?v=short") is None)
Y.ipc = fake_ipc

# 10. updates on a clock: the stamp moves only after it worked, logged, capped, locked, at most once per 6 h
U = Path(SCRATCH) / "upd"; U.mkdir(); Y.STAMP = U / "updated"; Y.TRIED = U / ".update.tried"
os.environ["YTM_UPDATE_CMD"] = f"test ! -e '{Y.STAMP}' && echo upgrading"   # passes only if the stamp isn't there yet while it runs
check("the stamp is touched after the upgrade, not before", Y.run_update("test") is True and Y.STAMP.exists(), Y.UPDATE_LOG.read_text()[-200:])
log = Y.UPDATE_LOG.read_text()
check("update.log: a start line, the output, a done line", "== " in log and "test\nupgrading\n" in log and log.rstrip().endswith("done"), log)
Y.STAMP.unlink(); os.environ["YTM_UPDATE_CMD"] = "echo nope >&2; exit 3"
check("a failed upgrade leaves no stamp", Y.run_update("test") is False and not Y.STAMP.exists())
check("…and logs why", "failed (3)" in Y.UPDATE_LOG.read_text() and "nope" in Y.UPDATE_LOG.read_text())
with (U / ".update.lock").open("w") as held:
    fcntl.flock(held, fcntl.LOCK_EX); err = io.StringIO()
    with contextlib.redirect_stderr(err): ok = Y.run_update("test")
    check("an update already running: the second steps aside", ok is False and "already running" in err.getvalue())
check("broken inside 6 h of a try: nothing runs", Y.update(broken=True) is None)
os.utime(Y.TRIED, (time.time() - 7 * 3600,) * 2); os.environ["YTM_UPDATE_CMD"] = "true"
check("broken after 6 h: it runs, whatever the stamp says", Y.update(broken=True) == 0 and Y.STAMP.exists())
spawned.clear(); Y.subprocess.Popen = lambda args, **k: spawned.append((args, k))
os.utime(Y.TRIED, (time.time() - 7 * 3600,) * 2)
check("a fresh stamp: a starting player spawns nothing", Y.update() is None and spawned == [])
os.utime(Y.STAMP, (time.time() - 4 * 86400,) * 2); Y.update()
check("a 3-day-old stamp: one detached `ytm update --auto`", len(spawned) == 1 and spawned[0][0][-2:] == ["update", "--auto"] and spawned[0][1].get("start_new_session"), str(spawned))
os.environ.pop("YTM_UPDATE_CMD"); spawned.clear()
check("scratch data and no YTM_UPDATE_CMD: never the real brew", Y._update_cmd() is None and Y.update() is None and spawned == [] and Y.run_update("x") is False)
Y.subprocess.Popen = real_popen
Y.UPDATE_LOG.write_bytes(b"".join(b"line %06d of an old log\n" % i for i in range(20000)))
Y._cap_log(Y.UPDATE_LOG)
b = Y.UPDATE_LOG.read_bytes()
check("update.log capped: the last half, from a whole line", len(b) <= Y.UPDATE_LOG_CAP // 2 and b.startswith(b"line ") and b.endswith(b"line 019999 of an old log\n"), str(len(b)))

# 11. the update agent: plist text, written to a scratch folder only, never loaded from a test
LA = Path(SCRATCH) / "LaunchAgents"; REAL_LA = Path.home() / "Library/LaunchAgents" / f"{Y.AGENT}.plist"; had_real = REAL_LA.exists()
import plistlib
pl_ = plistlib.loads(Y.agent_plist().encode())
check("plist: label, runs `ytm update` with the venv python", pl_["Label"] == "dev.afterhum.ytm-update" and pl_["ProgramArguments"][0] == str(Y.VENV_PY)
      and pl_["ProgramArguments"][1:] == [str(BIN.resolve()), "update"], str(pl_["ProgramArguments"]))
check("plist: daily, not at load", pl_["StartCalendarInterval"] == {"Hour": 5, "Minute": 15} and pl_["RunAtLoad"] is False)
check("plist: background, nice 10, low-priority io", pl_["ProcessType"] == "Background" and pl_["Nice"] == 10 and pl_["LowPriorityIO"] is True)
check("plist: brew and uv on PATH, output into update.log", pl_["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:") and pl_["StandardOutPath"] == str(Y.UPDATE_LOG))
AENV = {"YTM_LAUNCHAGENTS": str(LA)}
def ytm_a(*a): return subprocess.run([PY, str(BIN), *a], capture_output=True, text=True, timeout=20, env={**os.environ, **ENV, **AENV})
r = ytm_a("agents", "install"); f = LA / f"{Y.AGENT}.plist"
check("agents install writes the plist (scratch: not loaded)", r.returncode == 0 and f.exists() and "not loaded" in r.stdout, r.stdout + r.stderr)
check("…a valid plist (plutil)", subprocess.run(["plutil", "-lint", str(f)], capture_output=True).returncode == 0)
check("…the same text agent_plist() makes", plistlib.loads(f.read_bytes())["Label"] == Y.AGENT and not list(LA.glob(".*")))
r = ytm_a("agents", "status")
check("agents status", r.returncode == 0 and "installed" in r.stdout and "last good update" in r.stdout and "update.log" in r.stdout, r.stdout + r.stderr)
r = ytm_a("agents", "remove")
check("agents remove", r.returncode == 0 and not f.exists(), r.stdout + r.stderr)
check("agents with a bad word: usage, exit 2", ytm_a("agents", "plant").returncode == 2)
check("the real LaunchAgents folder untouched", REAL_LA.exists() == had_real)

# 8. version
v = (ROOT / "VERSION").read_text().strip()
r = ytm("--version")
check("--version prints VERSION", r.returncode == 0 and r.stdout.strip() == f"ytm {v}", r.stdout + r.stderr)
r = ytm("version")
check("version too", r.stdout.strip() == f"ytm {v}")

print(f"\n{'all passed' if not fails else f'{fails} failed'}  (scratch: {SCRATCH})")
sys.exit(1 if fails else 0)
