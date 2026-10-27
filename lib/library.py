"""ytm library: the account's side of the music (lane 2, DESIGN.md "accounts").
history, likes, playlists, home mixes, thumbs, and a recommender that blends the local log with the account's home.
every function takes an authed YTMusic-like client (lane 1's auth.client()) or None; None, or any failure, degrades to [].
one request per function at most, answers cached in DATA/cache/*.json with a ttl, page counts capped. nothing leaves
the mac except the youtube music calls themselves; everything personal stays under DATA, never in the repo."""
import json, os, re, time, uuid
from datetime import date, datetime, timedelta
from pathlib import Path

TTL = {"history": 600, "liked": 3600, "playlists": 6 * 3600, "home": 3 * 3600, "watch": 3 * 3600}
CAP = {"history": 200, "liked": 200, "playlists": 50, "home": 6, "watch": 50}  # liked 200 ≈ 3 pages; home 6 rows = 1 continuation
RATINGS = ("LIKE", "DISLIKE", "INDIFFERENT")
VID = re.compile(r"^[\w-]{11}$")
try: import taste as T
except ImportError: from . import taste as T
_AUTO = object()  # "work the client out yourself": lane 1's auth.client(), or None when there's no login


def data(): return Path(os.environ.get("YTM_DATA", Path.home() / ".local/share/ytm"))  # read per call: tests swap it


def _client(c):
    if c is not _AUTO: return c
    try:
        try: import auth
        except ImportError: from . import auth
        return auth.client()
    except Exception: return None


# ----- cache: {"t": epoch, "v": value}, written atomically -----
def _cache_path(key): return data() / "cache" / f"{re.sub(r'[^\w.-]', '_', key)}.json"


def _cache_get(key, ttl):
    """(value, fresh) or (None, False). a stale value is still handed back so a failed call can fall back to it"""
    try: d = json.loads(_cache_path(key).read_text())
    except (OSError, ValueError): return None, False
    return d.get("v"), time.time() - d.get("t", 0) < ttl


def _cache_put(key, v):
    p = _cache_path(key); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps({"t": time.time(), "v": v}, ensure_ascii=False)); os.replace(tmp, p)


def _cache_drop(*keys):
    for k in keys:
        try: _cache_path(k).unlink()
        except OSError: pass


def _cached(key, ttl, client, fetch):
    """one fetch per ttl. no client (logged out): [] whatever the cache holds. a failed fetch: the stale cache, else []"""
    if client is None: return []
    v, fresh = _cache_get(key, ttl)
    if fresh and v is not None: return v
    try: v = fetch(client)
    except Exception: return _cache_get(key, 10 ** 12)[0] or []
    _cache_put(key, v); return v


# ----- slim tracks: ytmusicapi's shape, only the fields bin/ytm's label()/remember()/load() read -----
def slim(t, *extra):
    if not isinstance(t, dict) or not t.get("videoId"): return None
    al = t.get("album")
    out = {"videoId": t["videoId"], "title": t.get("title"),
           "artists": [{"name": a.get("name"), "id": a.get("id")} for a in t.get("artists") or [] if isinstance(a, dict) and a.get("name")],
           "album": {"name": al.get("name"), "id": al.get("id")} if isinstance(al, dict) else ({"name": al} if al else None),
           "duration_seconds": t.get("duration_seconds"), "thumbnails": (t.get("thumbnails") or [])[-1:]}
    for k in extra:
        if t.get(k) is not None: out[k] = t[k]
    return out


def _tracks(rows, cap, *extra): return [s for s in (slim(t, *extra) for t in (rows or [])[:cap]) if s]
def artist_of(t): return ", ".join(a["name"] for a in t.get("artists") or [] if a.get("name"))


# ----- the account -----
def history(n=50, client=_AUTO):
    """recent plays on the account, newest first, each with "played" (the history shelf: "Today", "Yesterday", …)"""
    return _cached("history", TTL["history"], _client(client), lambda c: _tracks(c.get_history(), CAP["history"], "played"))[:n]


def liked(n=100, client=_AUTO):
    n = max(1, min(int(n), CAP["liked"]))
    return _cached(f"liked-{n}", TTL["liked"], _client(client), lambda c: _tracks((c.get_liked_songs(limit=n) or {}).get("tracks"), n))[:n]


def playlists(client=_AUTO):
    def fetch(c):
        return [{"playlistId": p["playlistId"], "title": p.get("title"), "count": p.get("count"), "thumbnails": (p.get("thumbnails") or [])[-1:]}
                for p in (c.get_library_playlists(limit=CAP["playlists"]) or [])[:CAP["playlists"]] if isinstance(p, dict) and p.get("playlistId")]
    return _cached("playlists", TTL["playlists"], _client(client), fetch)


def home(client=_AUTO):
    """the home page as [{"title": row, "contents": [...]}], items slimmed: songs keep videoId, mixes keep playlistId"""
    def fetch(c):
        rows = []
        for r in (c.get_home(limit=CAP["home"]) or [])[:CAP["home"]]:
            items = []
            for it in (r.get("contents") or [])[:30]:
                if not isinstance(it, dict): continue
                if it.get("videoId"): items.append(slim(it))
                elif it.get("playlistId"):
                    items.append({"playlistId": it["playlistId"], "title": it.get("title"), "description": it.get("description"),
                                  "thumbnails": (it.get("thumbnails") or [])[-1:]})
            rows.append({"title": r.get("title"), "contents": items})
        return rows
    return _cached("home", TTL["home"], _client(client), fetch)


def mixes(client=_AUTO):
    """the "mixed for you" row (my mix 1…n, discover mix, …): the auto-made RD… playlists, falling back to any home playlist"""
    rows = home(client)
    tagged = [dict(m, row=r["title"]) for r in rows for m in r["contents"] if m.get("playlistId")]
    mine = [m for m in tagged if "mix" in (m["row"] or "").lower()] or [m for m in tagged if m["playlistId"].startswith("RD")]
    return mine or tagged


def rate(vid, rating, client=_AUTO):
    """thumbs on the account. True when youtube took it; a bad vid or rating, or no login, is False without a request"""
    rating = str(rating).upper()
    c = _client(client)
    if c is None or rating not in RATINGS or not VID.match(str(vid or "")): return False
    try: c.rate_song(vid, rating)
    except Exception: return False
    _cache_drop(*[p.stem for p in (data() / "cache").glob("liked-*.json")])  # the likes list just changed
    return True


# ----- history → the listening log -----
MONTHS = {m: i for i, m in enumerate(("january february march april may june july august september october november december").split(), 1)}


def played_day(label, today=None):
    """the history shelf's label → (day, exact). "Today"/"Yesterday" are exact; weeks and months are a best guess; anything else None"""
    today = today or date.today(); s = (label or "").strip().lower()
    if s == "today": return today, True
    if s == "yesterday": return today - timedelta(days=1), True
    if s == "this week": return today - timedelta(days=2), False  # older than yesterday, so two days back at the nearest
    if s == "last week": return today - timedelta(days=today.weekday() + 4), False
    m = re.match(r"^([a-z]+)(?:\s+(\d{4}))?$", s)
    if m and m.group(1) in MONTHS:
        mo = MONTHS[m.group(1)]; yr = int(m.group(2) or (today.year if mo <= today.month else today.year - 1))
        return date(yr, mo, 1), False
    return None, False


def _span(label, day):
    """how wide a guessed day is: a vague label dedupes against any backfilled row of that vid inside its window"""
    s = (label or "").lower()
    if "week" in s: return 14
    if s in ("today", "yesterday"): return 0
    return 31


def _log_index(months):
    """{vid: set of days} for every row, plus {vid: days} for backfilled rows, over the given month files only"""
    seen, back = {}, {}
    for f in months:
        try: lines = f.read_text().splitlines()
        except OSError: continue
        for l in lines:
            try: r = json.loads(l)
            except ValueError: continue
            if not r.get("vid") or not r.get("start"): continue
            d = r["start"][:10]
            seen.setdefault(r["vid"], set()).add(d)
            if r.get("source") == "yt-history": back.setdefault(r["vid"], set()).add(d)
    return seen, back


def backfill_log(client=_AUTO, today=None):
    """youtube music's history into DATA/plays as source:"yt-history" rows (app:"ytmusic", outcome:"unknown").
    one request (the cached history). dedupe: one row per vid per day, against the log and within the batch; a vague
    label ("this week", "march") also skips a vid already backfilled inside that window, so re-runs stay quiet.
    returns {"added": n, "skipped": n, "unparsed": n}"""
    today = today or date.today(); c = _client(client)
    if c is None: return {"added": 0, "skipped": 0, "unparsed": 0}
    items = history(CAP["history"], c)
    plays = data() / "plays"; plays.mkdir(parents=True, exist_ok=True)
    rows, skipped, unparsed = [], 0, 0
    todo = []
    for t in items:
        day, exact = played_day(t.get("played"), today)
        if day is None: unparsed += 1; continue
        todo.append((t, day, exact))
    first = min((d for _, d, _ in todo), default=today) - timedelta(days=31)
    months = sorted(f for f in plays.glob("*.jsonl") if f.stem >= first.strftime("%Y-%m"))
    seen, back = _log_index(months)
    tz = datetime.now().astimezone().strftime("%z"); tz = tz[:3] + ":" + tz[3:]
    for t, day, exact in reversed(todo):  # history is newest first; the log reads oldest first
        vid, ds = t["videoId"], day.isoformat()
        w = _span(t.get("played"), day)
        near = any(abs((date.fromisoformat(x) - day).days) <= w for x in back.get(vid, ()))
        if ds in seen.get(vid, ()) or (not exact and near): skipped += 1; continue
        seen.setdefault(vid, set()).add(ds); back.setdefault(vid, set()).add(ds)
        al = t.get("album")
        rows.append({"v": 1, "id": uuid.uuid4().hex, "start": f"{ds}T12:00:00{tz}", "end": f"{ds}T12:00:00{tz}",
                     "vid": vid, "title": t.get("title"), "artist": artist_of(t) or None,
                     "album": al.get("name") if isinstance(al, dict) else al, "dur": float(t.get("duration_seconds") or 0),
                     "listened": 0.0, "outcome": "unknown", "source": "yt-history", "query": None, "seed_vid": None,
                     "app": "ytmusic", "hour": None, "dow": (day.weekday() + 1) % 7, "mode": "normal",
                     "played": t.get("played"), "approx": not exact})
    by_month = {}
    for r in rows: by_month.setdefault(r["start"][:7], []).append(json.dumps(r, ensure_ascii=False) + "\n")
    for mo, lines in by_month.items():
        with open(plays / f"{mo}.jsonl", "a") as f: f.write("".join(lines))  # one write per month file, append only
    return {"added": len(rows), "skipped": skipped, "unparsed": unparsed}


# ----- recommend: the local log's taste × the account's home -----
def taste(months=3, max_rows=5000):
    """from the last few months of the log: artist score (finished +1, partial +0.3, skipped −1) and the vids skipped
    more often than finished. backfilled rows count a little (+0.2: heard, outcome unknown)"""
    files = sorted((data() / "plays").glob("*.jsonl"))[-months:]
    rows = []
    for f in files:
        try: rows += f.read_text().splitlines()[-max_rows:]
        except OSError: pass
    art, vid = {}, {}
    w = {"finished": 1.0, "partial": 0.3, "skipped": -1.0, "unknown": 0.2}
    for l in rows[-max_rows:]:
        try: r = json.loads(l)
        except ValueError: continue
        o = r.get("outcome")
        for a in (r.get("artist") or "").split(", "):
            if a: art[a] = art.get(a, 0) + w.get(o, 0)
        if r.get("vid"): vid[r["vid"]] = vid.get(r["vid"], 0) + (1 if o == "finished" else -1 if o == "skipped" else 0)
    return art, {v for v, s in vid.items() if s < 0}


def _taste_model():
    """lib/taste.py's model at this hour, or None (it's an extra voice: recommend works without it)"""
    try:
        return T.model(data()), T.now_ctx("normal")[1]
    except Exception: return None


def recommend(n=20, client=_AUTO):
    """tracks to queue, best first. candidates: the home page's songs, plus the first mix's tracks when home alone is
    short (one get_watch_playlist, cached). ranked by the log's artist scores blended with taste.py's finish chance for
    this hour; often-skipped songs never come back,
    at most 2 per artist so it doesn't turn into one artist's radio"""
    c = _client(client)
    if c is None: return []
    art, skips = taste()
    model = _taste_model()
    def fit(t):
        """0.8.2: the artist score (clipped, one input) + taste.py's finish chance for this song now, in normal mode"""
        a = max((art.get(x["name"], 0) for x in t["artists"]), default=0)
        if model is None: return max(-3, min(3, a))
        m, b = model
        f = m.score(T.track_features(t, data(), m.memo, m.looks), "normal", b)
        return max(-3, min(3, a)) + 6 * (f["score"] - m.base("normal", b))
    best, seen, per = [], set(), {}
    def pick(cands):
        for i, t in sorted(enumerate(cands), key=lambda it: -(fit(it[1]) - 0.05 * it[0])):
            v, a = t["videoId"], artist_of(t)
            if len(best) >= n: return
            if v in seen or v in skips or per.get(a, 0) >= 2: continue
            seen.add(v); per[a] = per.get(a, 0) + 1; best.append(t)
    pick([x for r in home(c) for x in r["contents"] if x.get("videoId")])
    if len(best) < n and (mx := mixes(c)):
        pid = mx[0]["playlistId"]
        pick(_cached(f"watch-{pid}", TTL["watch"], c, lambda c: _tracks((c.get_watch_playlist(playlistId=pid, limit=25) or {}).get("tracks"), CAP["watch"])))
    return best
