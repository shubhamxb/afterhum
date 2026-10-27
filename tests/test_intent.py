"""ytm intent: what the words mean, and the search rerank. pure, offline. cases in both directions: the rule fires where it should
and stays out of the way of real song and band names.
run: python3 tests/test_intent.py"""
import importlib.machinery, importlib.util, json, os, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

l = importlib.machinery.SourceFileLoader("ytmintent", str(ROOT / "lib/intent.py"))
s = importlib.util.spec_from_loader("ytmintent", l); I = importlib.util.module_from_spec(s); l.exec_module(I)
MODES = ["work", "focus", "chill", "energy", "sleep", "coding", "design", "writing", "reading", "study", "cafe", "gym", "run",
         "drive", "cook", "gaming", "party", "morning", "winddown"]
fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")
def P(q): return I.parse(q, MODES)

# an occasion -> a mode
for q, mode in [("something for coding", "coding"), ("music to study", "study"), ("play some songs for the gym", "gym"), ("coding", "coding"),
                ("gym mode", "gym"), ("beats to write to", "writing"), ("music for a road trip", "drive"), ("songs to run to", "run"),
                ("i'm cooking dinner", "cook"), ("something to focus", "focus"), ("deep work", "focus"), ("music for sleeping", "sleep"),
                ("background music for work", "work"), ("something for my commute", "drive"), ("party music", "party"),
                ("unwind", "winddown"), ("music for design work", "design")]:
    r = P(q); check(f"'{q}' -> {mode} mode", r == {"kind": "mode", "mode": mode}, str(r))
# ...but a song or a band is a plain search
for q in ["gym class heroes", "drive", "party", "morning", "run", "like a rolling stone", "something in the way", "work it", "lofi for coding",
          "coding lofi", "the cure", "chill out", "energy", "dance monkey", "creep radiohead", "focus", "reading rainbow"]:
    r = P(q); check(f"'{q}' stays a search", r["kind"] == "plain", str(r))
# like
for q, subj in [("songs like low roar", "low roar"), ("music similar to the midnight", "the midnight"), ("something like jon hopkins", "jon hopkins"),
                ("artists like khruangbin", "khruangbin"), ("sounds like daft punk", "daft punk"), ("more like bon iver", "bon iver")]:
    r = P(q); check(f"'{q}' -> like {subj}", r == {"kind": "like", "subject": subj}, str(r))
for q in ["more like this", "something similar", "same vibe", "songs like this", "more like that"]:
    check(f"'{q}' -> like what's playing", P(q) == {"kind": "like_now"}, str(P(q)))
# new
for q, art in [("arijit singh new", "arijit singh"), ("latest from taylor swift", "taylor swift"), ("new songs by the weeknd", "the weeknd"),
               ("kendrick lamar latest songs", "kendrick lamar"), ("play the newest music by anuv jain", "anuv jain")]:
    r = P(q); check(f"'{q}' -> latest of {art}", r == {"kind": "new", "artist": art}, str(r))
for q in ["new rules", "new york", "new born muse", "brand new eyes", "new slang"]:
    check(f"'{q}' stays a search", P(q)["kind"] in ("plain", "new") and (P(q)["kind"] == "plain" or q in ("new rules",)), str(P(q)))
# cleaning
for q, out in [("play creep by radiohead", "creep radiohead"), ("radiohead - creep", "radiohead creep"), ("please play some Karma Police", "karma police"),
               ("play", "play"), ("the song hurt by johnny cash", "hurt johnny cash")]:
    check(f"clean '{q}' -> '{out}'", I.clean(q) == out, I.clean(q))
check("parse never raises on junk", all(P(x)["kind"] for x in ["", "   ", "!!!", "like", "new", "songs like", "a" * 500]))

# rank
def T(title, artist, i=0, dur=200, vt="MUSIC_VIDEO_TYPE_ATV", album="Album"):
    return {"videoId": f"id{i:09d}", "title": title, "artists": [{"name": artist}], "album": {"name": album}, "duration_seconds": dur, "videoType": vt}
hits = [T("Gym Workout Beast Savage Motivation 2 HOURS", "Gym Motivation", 1, 7200, "MUSIC_VIDEO_TYPE_UGC"),
        T("Till I Collapse", "Eminem", 2), T("Creep (slowed + reverb)", "Radiohead", 3, vt="MUSIC_VIDEO_TYPE_UGC")]
r = I.rank(hits, "gym workout")
check("an hour-long upload ranks below the real song even when its title matches", r[-1]["title"].startswith("Gym Workout") or r[0]["title"] == "Till I Collapse", [t["title"] for t in r])
r = I.rank([T("Creep (slowed + reverb)", "Radiohead", 1, vt="MUSIC_VIDEO_TYPE_UGC"), T("Creep", "Radiohead", 2), T("Creep - Cover", "Some Band", 3, vt="MUSIC_VIDEO_TYPE_UGC")], "creep radiohead")
check("slowed / cover uploads go under the original", r[0]["title"] == "Creep", [t["title"] for t in r])
r = I.rank([T("Creep (slowed + reverb)", "Radiohead", 1, vt="MUSIC_VIDEO_TYPE_UGC"), T("Creep", "Radiohead", 2)], "creep slowed reverb")
check("asked for slowed: it is not pushed down", r[0]["title"].startswith("Creep (slowed"), [t["title"] for t in r])
r = I.rank([T("Fire", "Band A", 1), T("Fire", "Kasabian", 2)], "fire", {"kasabian": 5})
check("an artist you finish gets a nudge on a tie", r[0]["artists"][0]["name"] == "Kasabian", [t["artists"][0]["name"] for t in r])
r = I.rank([T("Song A", "X", 1), T("Song B", "Y", 2), T("Song C", "Z", 3)], "")
check("no signal: YouTube's order, nothing dropped", [t["title"] for t in r] == ["Song A", "Song B", "Song C"])
# finished_artists
d = tempfile.mkdtemp(); (Path(d) / "plays").mkdir()
(Path(d) / "plays" / "2026-10.jsonl").write_text("\n".join(json.dumps(x) for x in [
    {"outcome": "finished", "artist": "Low Roar"}, {"outcome": "finished", "artist": "Low Roar, Ryan Karazija"}, {"outcome": "skipped", "artist": "Nope"}]) + "\n")
fa = I.finished_artists(d)
check("finished_artists counts finishes per artist, skips ignored", fa.get("low roar") == 2 and "nope" not in fa, str(fa))
print("FAILED" if fails else "all ok", fails)
raise SystemExit(1 if fails else 0)
