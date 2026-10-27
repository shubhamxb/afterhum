"""ytm ratings: what you like and don't, kept on the mac.

a like / dislike option to keep track of what you like and don't. ytm already sent a thumbs to the YouTube Music account
(needs a login, lives on youtube's side). This is the local ledger under it: it works signed out, it never leaves the mac, and it
changes what plays:
  dislike -> the song is skipped now and never queued again (queue() drops it; a search ranks it last)
  like    -> its artist counts as one you finish: a nudge in search, and a liked-artist slot in every mode's radio
DATA/ratings.json: {vid: {r: "LIKE"|"DISLIKE", at: epoch, title, artist}}, written whole then renamed, at most 5000 entries
(the oldest drop first). stdlib only.
"""
import json, os, re, time
from pathlib import Path

VID = re.compile(r"^[\w-]{11}$")
KINDS = ("LIKE", "DISLIKE")
CAP = 5000


def data(d=None):
    return Path(d) if d else Path(os.environ.get("YTM_DATA", Path.home() / ".local/share/ytm"))


def load(d=None):
    try:
        m = json.loads((data(d) / "ratings.json").read_text())
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(m, d=None):
    if len(m) > CAP:
        m = dict(sorted(m.items(), key=lambda kv: kv[1].get("at", 0))[-CAP:])
    p = data(d); p.mkdir(parents=True, exist_ok=True)
    tmp = p / f".ratings.{os.getpid()}.json"
    tmp.write_text(json.dumps(m, ensure_ascii=False)); tmp.replace(p / "ratings.json")


def rate(vid, rating, title="", artist="", d=None, now=None):
    """LIKE / DISLIKE / INDIFFERENT (clears it). -> True when stored. a bad id or rating is False and writes nothing."""
    rating = str(rating).upper()
    if not VID.match(str(vid or "")) or rating not in KINDS + ("INDIFFERENT",): return False
    m = load(d)
    if rating == "INDIFFERENT": m.pop(vid, None)
    else: m[vid] = {"r": rating, "at": int(now if now is not None else time.time()), "title": title or "", "artist": artist or ""}
    _save(m, d)
    return True


def get(vid, d=None):
    return (load(d).get(vid) or {}).get("r", "")


def vids(kind, d=None):
    return {v for v, r in load(d).items() if r.get("r") == kind}


def disliked(d=None): return vids("DISLIKE", d)


def liked_artists(d=None):
    """lowercase artist -> times liked. 'A, B' counts for both."""
    out = {}
    for r in load(d).values():
        if r.get("r") != "LIKE": continue
        for a in str(r.get("artist") or "").split(","):
            a = a.strip().lower()
            if a: out[a] = out.get(a, 0) + 1
    return out


def recent(kind, n=50, d=None):
    rows = [(r.get("at", 0), v, r) for v, r in load(d).items() if r.get("r") == kind]
    return [{"vid": v, "title": r.get("title", ""), "artist": r.get("artist", ""), "at": at} for at, v, r in sorted(rows, reverse=True)[:n]]


def summary(d=None):
    m = load(d)
    return {"liked": sum(1 for r in m.values() if r.get("r") == "LIKE"), "disliked": sum(1 for r in m.values() if r.get("r") == "DISLIKE")}


def filter_tracks(tracks, d=None):
    """drop the disliked songs from tracks about to be queued. never empties the list on a corrupt file: an unreadable ledger drops nothing."""
    bad = disliked(d)
    return [t for t in tracks if t.get("videoId") not in bad] if bad else list(tracks)


def blend(tracks, d=None, every=3):
    """a liked-artist slot in the radio: the tracks by artists you've liked go up front, one in every `every` places, the rest keep their
    order. nothing is dropped; no likes, no change."""
    la = liked_artists(d)
    if not la: return list(tracks)
    def liked(t): return any((a.get("name") or "").lower() in la for a in t.get("artists") or [] if isinstance(a, dict))
    fav = [t for t in tracks if liked(t)]; rest = [t for t in tracks if not liked(t)]
    out = []
    while fav or rest:
        for i in range(every):
            if i == 0 and fav: out.append(fav.pop(0))
            elif rest: out.append(rest.pop(0))
            elif fav: out.append(fav.pop(0))
    return out
