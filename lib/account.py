"""account: everything the youtube music account will tell us, pulled once into a local snapshot, then read for insights.

pull(client) → DATA/account/snapshot.json: history, liked songs, library songs / artists / albums / subscriptions / playlists,
the home page (mixes), youtube's own taste profile of you, account info. capped (a few thousand rows at most), ~1 request a
second, one pull at a time. it stays on this mac (DATA is outside the repo, never synced).
insights(snapshot, log rows) → a dict: who you listen to, how (languages, official audio vs videos vs uploads, mixes), how loyal
vs exploratory, what youtube thinks vs what you actually play, and what to try next. explainable counts, no black box.
"""
import json, os, re, time
from collections import Counter, defaultdict
from pathlib import Path

CAPS = {"history": 200, "liked": 1500, "songs": 1500, "artists": 600, "albums": 400, "subscriptions": 600, "playlists": 200}


def data(): return Path(os.environ.get("YTM_DATA", Path.home() / ".local/share/ytm"))
def snap_path(): return data() / "account" / "snapshot.json"


def _artists(t): return [a.get("name") for a in (t.get("artists") or []) if isinstance(a, dict) and a.get("name")]


def _slim(t):
    if not isinstance(t, dict): return None
    al = t.get("album")
    return {"vid": t.get("videoId"), "title": t.get("title") or "", "artists": _artists(t),
            "aids": [a.get("id") for a in (t.get("artists") or []) if isinstance(a, dict) and a.get("id")][:2],
            "album": al.get("name") if isinstance(al, dict) else al, "type": t.get("videoType"),
            "secs": t.get("duration_seconds"), "played": t.get("played"), "liked": t.get("likeStatus") == "LIKE"}


def pull(client, log=print):
    """one snapshot of the account. each call is wrapped: a section that fails is recorded as failed, the rest still lands"""
    out, failed = {"at": int(time.time())}, []
    def step(name, fn):
        try:
            out[name] = fn(); log(f"  {name}: {len(out[name]) if hasattr(out[name], '__len__') else 'ok'}")
        except Exception as e:
            failed.append(name); log(f"  {name}: skipped ({e.__class__.__name__})")
        time.sleep(0.8)   # gentle: about a request a second
    step("account", lambda: {k: v for k, v in (client.get_account_info() or {}).items() if k in ("accountName", "channelHandle")})
    step("history", lambda: [x for x in (_slim(t) for t in (client.get_history() or [])[:CAPS["history"]]) if x])
    step("liked", lambda: [x for x in (_slim(t) for t in ((client.get_liked_songs(limit=CAPS["liked"]) or {}).get("tracks") or [])[:CAPS["liked"]]) if x])
    step("songs", lambda: [x for x in (_slim(t) for t in (client.get_library_songs(limit=CAPS["songs"]) or [])[:CAPS["songs"]]) if x])
    step("artists", lambda: [{"name": a.get("artist"), "songs": a.get("songs")} for a in (client.get_library_artists(limit=CAPS["artists"]) or [])[:CAPS["artists"]]])
    step("albums", lambda: [{"title": a.get("title"), "artists": _artists(a), "year": a.get("year"), "type": a.get("type")}
                             for a in (client.get_library_albums(limit=CAPS["albums"]) or [])[:CAPS["albums"]]])
    step("subscriptions", lambda: [{"name": a.get("artist"), "subscribers": a.get("subscribers")} for a in (client.get_library_subscriptions(limit=CAPS["subscriptions"]) or [])[:CAPS["subscriptions"]]])
    step("playlists", lambda: [{"title": p.get("title"), "count": p.get("count"), "id": p.get("playlistId")} for p in (client.get_library_playlists(limit=CAPS["playlists"]) or [])[:CAPS["playlists"]]])
    def all_playlists():   # every playlist's tracks (capped): the history you curated yourself
        got, budget = {}, 6000
        for pl in out.get("playlists") or []:
            if not pl.get("id") or pl["id"] == "LM" or budget <= 0: continue
            try: rows = [x for x in (_slim(t) for t in playlist_tracks(client, pl["id"], min(1000, budget))) if x]
            except Exception: rows = []
            got[pl["id"]] = rows; budget -= len(rows); time.sleep(0.6)
        return got
    step("playlist_tracks", all_playlists)
    def related():   # the map around you: youtube's related artists for your 40 most-saved artists (one request each)
        ids, names = Counter(), {}
        titles = {p.get("id"): p.get("title") or "" for p in out.get("playlists") or []}
        rows = (out.get("liked") or []) + (out.get("history") or []) + [t for pid, v in (out.get("playlist_tracks") or {}).items()
                                                                          if re.search(r"recap|replay", titles.get(pid, ""), re.I) for t in v]
        for t in rows:
            for a, i in zip(t.get("artists", []), t.get("aids", [])):
                if i: ids[i] += 1; names[i] = a
        got = {}
        for i, _ in ids.most_common(40):
            try: rel = ((client.get_artist(i) or {}).get("related") or {}).get("results") or []
            except Exception: rel = []
            got[names[i]] = [r.get("title") for r in rel if r.get("title")][:12]
            time.sleep(0.6)
        return got
    step("related", related)
    step("home", lambda: [{"title": r.get("title"), "items": [i.get("title") for i in (r.get("contents") or [])[:12] if isinstance(i, dict)]} for r in (client.get_home(limit=8) or [])])
    step("tasteprofile", lambda: sorted((client.get_tasteprofile() or {}).keys()))
    out["failed"] = failed
    p = snap_path(); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(out, ensure_ascii=False)); tmp.replace(p)
    return out


SCRIPTS = [("japanese", re.compile(r"[぀-ヿ]")), ("korean", re.compile(r"[가-힯]")), ("chinese/japanese kanji", re.compile(r"[一-鿿]")),
           ("hindi / devanagari", re.compile(r"[ऀ-ॿ]")), ("arabic / urdu", re.compile(r"[؀-ۿ]")),
           ("tamil / telugu / kannada", re.compile(r"[஀-೿]")), ("cyrillic", re.compile(r"[Ѐ-ӿ]"))]
MIXY = re.compile(r"\b(mix|hour|hours|playlist|beats to|lofi|lo-fi|study|sleep|relax|ambient|focus|compilation|1 hr|1hr)\b", re.I)


def _n(v):
    """'1,234 songs' / 1234 / None → 1234"""
    if isinstance(v, (int, float)): return int(v)
    d = re.sub(r"[^\d]", "", str(v or ""))
    return int(d) if d else 0


def script_of(s):
    for name, rx in SCRIPTS:
        if rx.search(s or ""): return name
    return "latin (english & co.)"


def insights(snap, plays=()):
    """the numbers behind 'who you are as a listener'. snap = pull()'s dict, plays = rows of DATA/plays/*.jsonl"""
    liked, songs, hist = snap.get("liked") or [], snap.get("songs") or [], snap.get("history") or []
    subs = {s["name"] for s in snap.get("subscriptions") or [] if s.get("name")}
    lib_artists = {a["name"]: a.get("songs") for a in snap.get("artists") or [] if a.get("name")}
    weight, where = Counter(), defaultdict(set)
    for src, rows, w in (("liked", liked, 3), ("library", songs, 2), ("history", hist, 1)):
        for t in rows:
            for a in t["artists"][:2]:
                weight[a] += w; where[a].add(src)
    for a in subs: weight[a] += 2; where[a].add("subscribed")
    local = Counter()
    for r in plays:
        a = (r.get("artist") or "").split(",")[0].strip()
        if a: local[a] += 1 if r.get("outcome") == "finished" else 0
    for a, n in local.items(): weight[a] += n; where[a].add("ytm")
    top = [{"artist": a, "score": s, "seen_in": sorted(where[a])} for a, s in weight.most_common(15)]
    core = [a for a, _ in weight.most_common(60) if len(where[a]) >= 3]

    every = liked + songs + hist
    lang = Counter(script_of(t["title"] + " " + " ".join(t["artists"])) for t in every)
    types = Counter({"MUSIC_VIDEO_TYPE_ATV": "official audio", "MUSIC_VIDEO_TYPE_OMV": "music videos", "MUSIC_VIDEO_TYPE_UGC": "uploads (fan / channels)",
                     "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC": "official audio"}.get(t.get("type"), "other") for t in every if t.get("type"))
    long_hist = [t for t in hist if (t.get("secs") or 0) >= 1200 or MIXY.search(t["title"])]
    liked_artists = {a for t in liked for a in t["artists"]}
    hist_art = Counter(a for t in hist for a in t["artists"][:1])
    loyal = sum(n for a, n in hist_art.items() if a in liked_artists or a in subs) / max(1, sum(hist_art.values()))
    played_not_liked = [a for a, n in hist_art.most_common(30) if n >= 2 and a not in liked_artists and a not in subs][:8]
    repeats = Counter((t["title"], ", ".join(t["artists"][:1])) for t in hist)
    on_repeat = [{"title": k[0], "artist": k[1], "times": n} for k, n in repeats.most_common(8) if n >= 2]
    yt_thinks = snap.get("tasteprofile") or []
    yt_only = [a for a in yt_thinks if a not in weight][:10]
    years = Counter((a.get("year") or "")[:3] + "0s" for a in snap.get("albums") or [] if (a.get("year") or "")[:4].isdigit())
    return {
        "account": (snap.get("account") or {}).get("accountName"), "pulled_at": snap.get("at"), "failed": snap.get("failed") or [],
        "size": {"liked": len(liked), "library songs": len(songs), "library artists": len(lib_artists), "subscriptions": len(subs),
                 "playlists": len(snap.get("playlists") or []), "history": len(hist), "albums": len(snap.get("albums") or []),
                 "distinct artists": len(weight)},
        "top_artists": top, "core": core[:12],
        "languages": [{"script": k, "share": round(v / max(1, sum(lang.values())), 3)} for k, v in lang.most_common()],
        "formats": [{"kind": k, "share": round(v / max(1, sum(types.values())), 3)} for k, v in types.most_common()],
        "mix_share_recent": round(len(long_hist) / max(1, len(hist)), 3),
        "loyalty": round(loyal, 3), "played_not_liked": played_not_liked, "on_repeat": on_repeat,
        "youtube_thinks_only": yt_only, "album_decades": dict(years.most_common()),
        "playlists": sorted(({"title": p.get("title"), "count": p.get("count")} for p in snap.get("playlists") or []), key=lambda p: -_n(p["count"]))[:8],
        "mixes": [r["title"] for r in snap.get("home") or []][:8],
    }


# ---- quick play: your own playlists ----

def playlist_tracks(client, pid, cap=600):
    """a playlist's tracks, ytmusicapi-shaped (play_tracks takes them as they are). 'LM' is liked music"""
    pl = client.get_playlist(pid, limit=cap) or {}
    return [t for t in (pl.get("tracks") or []) if isinstance(t, dict) and t.get("videoId")][:cap]


def find_playlist(snap, words):
    """'3' (its number in `ytm playlists`) or words from its title ('midnight techno', 'tech') → the playlist dict"""
    pls = [p for p in snap.get("playlists") or [] if p.get("id")]
    w = " ".join(words).strip().lower()
    if w.isdigit() and 1 <= int(w) <= len(pls): return pls[int(w) - 1]
    exact = [p for p in pls if (p.get("title") or "").lower() == w]
    part = [p for p in pls if w and w in (p.get("title") or "").lower()]
    return (exact or part or [None])[0]


# ---- explore: the anti-saturation engine ----
# "i want 2624 artists, youtube algo only lets me explore the ones that show on my feed and i click on often, it's
# somehow saturated". the feed's loop: it shows what you click, you click what it shows, the circle shrinks. this walks the
# whole map on purpose: artists you saved once and forgot, and artists youtube knows you'd like but never surfaces.
EXPLORE_COOLDOWN = 14 * 86400      # an artist offered and not finished rests two weeks
EXPLORE_NOPE = 60 * 86400          # skipped inside 30 s: two months
def explore_state_path(): return data() / "account" / "explore.json"


def _heard(snap, plays, days=30):
    """artists heard lately (local log + youtube history): the loop we're trying to step out of"""
    now = time.time(); out = Counter()
    for r in plays:
        try: ts = time.mktime(time.strptime(r.get("start", "")[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError: ts = now
        if now - ts < days * 86400:
            for a in (r.get("artist") or "").split(","): out[a.strip()] += 1
    for t in snap.get("history") or []:
        for a in t.get("artists", [])[:1]: out[a] += 1
    return out


def explore_learn(plays, st):
    """read how explore picks went (source 'explore' in the log): finished = found, a fast skip = not for you"""
    for r in plays:
        if r.get("source") != "explore": continue
        a = (r.get("artist") or "").split(",")[0].strip()
        if not a: continue
        e = st.setdefault(a, {})
        if r.get("outcome") == "finished": e["found"] = e.get("found", 0) + 1
        elif r.get("outcome") == "skipped" and (r.get("listened") or 0) < 30: e["nope"] = time.time()
    return st


def explore_pick(snap, plays, n=12, rng=None):
    """n artists to visit: half 'forgotten' (in your playlists / library / follows, not heard in 30 days), half 'never surfaced'
    (youtube's own pool for you, never in your history at all). cooldowns keep it from circling; finds come back sooner"""
    import random
    rng = rng or random.Random()
    p = explore_state_path()
    try: st = json.loads(p.read_text())
    except (OSError, ValueError): st = {}
    st = explore_learn(plays, st)
    heard = _heard(snap, plays)
    # "you" = what you actually played or chose: likes, history, youtube's recaps / replay of you. not the saved playlists
    # ("those are just the trash playlists i saved because i couldn't find a good engine")
    names = {p.get("id"): p.get("title") or "" for p in snap.get("playlists") or []}
    recaps = [rows for pid, rows in (snap.get("playlist_tracks") or {}).items() if re.search(r"recap|replay", names.get(pid, ""), re.I)]
    mine = set()
    for t in (snap.get("liked") or []) + (snap.get("history") or []) + [x for rows in recaps for x in rows]:
        mine.update(t.get("artists", [])[:1])
    ever = set(mine) | set(heard)
    pool_never = [a for a in snap.get("tasteprofile") or [] if a and a not in ever]
    now = time.time()
    def resting(a):
        e = st.get(a) or {}
        if now - e.get("nope", 0) < EXPLORE_NOPE: return True
        return now - e.get("offered", 0) < (EXPLORE_COOLDOWN / (1 + e.get("found", 0)))
    forgotten = [a for a in mine if a and heard.get(a, 0) == 0 and not resting(a)]
    # neighbours: related artists of the ones you've saved most, never played. the ones youtube's own pool also lists come first
    taste = set(snap.get("tasteprofile") or [])
    near = Counter(r for rel in (snap.get("related") or {}).values() for r in rel if r and r not in ever and not resting(r))
    neighbours = sorted(near, key=lambda a: (-(near[a] + (2 if a in taste else 0)), rng.random()))
    never = [a for a in pool_never if not resting(a) and a not in near]
    rng.shuffle(forgotten); rng.shuffle(never)
    neighbours = neighbours[:max(n * 4, 20)]; rng.shuffle(neighbours)   # the strongest few dozen, in a fresh order each time
    plan = (["forgotten", "neighbour", "forgotten", "neighbour", "neighbour", "forgotten", "neighbour", "wild"] * 5)[:n]
    pools = {"forgotten": forgotten, "neighbour": neighbours, "wild": never}
    picks = []
    for kind in plan:
        src = pools[kind] or forgotten or neighbours or never
        if src: picks.append((src.pop(), {"forgotten": "forgotten", "neighbour": "a neighbour", "wild": "wild card"}[kind] if src is pools[kind] else "forgotten"))
    for a, _ in picks: st.setdefault(a, {})["offered"] = now
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(st, ensure_ascii=False))
    return picks, {"forgotten_pool": len(forgotten) + sum(1 for _, k in picks if k == "forgotten"),
                   "neighbour_pool": len(near), "never_pool": len(never) + sum(1 for _, k in picks if k == "wild card")}


def explore_tracks(client, picks, rng=None, want=None):
    """one song per artist, and not always the #1 hit: a deep cut a third of the time (the feed only ever shows the hit).
    stops at `want` songs: callers pick a few spare artists, since some names find nothing by that exact artist"""
    import random
    rng = rng or random.Random(); out = []
    for a, why in picks:
        if want and len(out) >= want: break
        try: hits = [t for t in client.search(a, filter="songs", limit=6) if t.get("videoId") and a.lower() in " ".join(_artists(t)).lower()]
        except Exception: hits = []
        if hits:
            t = hits[rng.randrange(1, min(4, len(hits)))] if len(hits) > 1 and rng.random() < 0.34 else hits[0]
            out.append(dict(t, _why=why))
        time.sleep(0.4)
    return out


# ---- the whole history: google takeout ----

def takeout_rows(path):
    """google takeout's watch-history.json (YouTube and YouTube Music → history, json) → youtube music plays, oldest first:
    [{vid, title, artist, time}]. only music.youtube.com entries; 'Artist - Topic' channels become the artist"""
    rows = json.loads(Path(path).read_text())
    out = []
    for r in rows if isinstance(rows, list) else []:
        if r.get("header") != "YouTube Music" and "music.youtube.com" not in (r.get("titleUrl") or ""): continue
        url = r.get("titleUrl") or ""
        vid = url.split("v=")[1][:11] if "v=" in url else ""
        title = re.sub(r"^(Watched|Listened to)\s+", "", r.get("title") or "").strip()
        sub = (r.get("subtitles") or [{}])[0].get("name", "")
        out.append({"vid": vid, "title": title, "artist": re.sub(r"\s*-\s*Topic$", "", sub).strip(), "time": r.get("time", "")})
    out.sort(key=lambda x: x["time"])
    return out


def takeout_summary(rows):
    """the long view: per year, the artists you played most, when you listen, how wide you've roamed"""
    by_year, hours = defaultdict(Counter), Counter()
    for r in rows:
        y = r["time"][:4]
        if r["artist"]: by_year[y][r["artist"]] += 1
        try: hours[int(r["time"][11:13])] += 1
        except ValueError: pass
    years = {y: {"plays": sum(c.values()), "artists": len(c), "top": [a for a, _ in c.most_common(8)]} for y, c in sorted(by_year.items())}
    all_art = Counter(a for c in by_year.values() for a in c.elements())
    return {"plays": len(rows), "first": rows[0]["time"][:10] if rows else None, "last": rows[-1]["time"][:10] if rows else None,
            "artists_ever": len(all_art), "one_play_artists": sum(1 for v in all_art.values() if v == 1),
            "years": years, "hours_utc": dict(sorted(hours.items()))}


# ---- fresh radio: the canon trap, fixed (10-26) ----
# a typical two weeks can be the ~40 songs every 'alt radio' returns (creep, in the end, kryptonite, chasing cars). not taste
# narrowing: youtube's radio sliding to each genre's most-played hits. so every radio keeps a little comfort and fills the rest
# with songs you haven't heard, never more than two by one artist.
COMFORT = 0.3

def heard_vids(snap=None, plays=None):
    """every song you are known to have heard: youtube history, the local log, the takeout export when there is one"""
    seen = set()
    snap = snap if snap is not None else (json.loads(snap_path().read_text()) if snap_path().exists() else {})
    seen.update(t.get("vid") for t in snap.get("history") or [] if t.get("vid"))
    if plays is None:
        plays = []
        for f in sorted((data() / "plays").glob("*.jsonl")):
            plays += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    seen.update(r.get("vid") for r in plays if r.get("vid"))
    tk = data() / "account" / "takeout.json"
    if tk.exists():
        try: seen.update(r.get("vid") for r in json.loads(tk.read_text()).get("rows", []) if r.get("vid"))
        except ValueError: pass
    seen.discard(None); seen.discard("")
    return seen


def fresh_mix(tracks, seen, n=26, comfort=COMFORT, per_artist=2):
    """youtube's radio order, reshaped: unheard songs first-class, heard ones capped at `comfort` of the queue, ≤2 per artist"""
    def lead(t): return ((t.get("artists") or [{}])[0] or {}).get("name") or ""
    out, per, familiar = [], Counter(), 0
    cap = max(1, int(n * comfort))
    for t in tracks:
        if len(out) >= n: break
        a = lead(t)
        if per[a] >= per_artist: continue
        old = t.get("videoId") in seen
        if old and familiar >= cap: continue
        out.append(t); per[a] += 1; familiar += old
    return out
