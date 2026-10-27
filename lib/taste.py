"""ytm taste (0.8.2): what the play log says about when and what you listen to.
learns the listening patterns from the log. stdlib only, no network, no numpy.

explainable on purpose: every number is a smoothed finish-rate someone can read back. per context (mode × hour band) and
per thing (artist, the song itself, tempo band, energy band, bass/treble, quiet, look, source, length), a beta-style
shrink toward the rate one level up (song in work·afternoon → song in work → work·afternoon overall → work → everything),
so two plays move a number a little and never make a rule. a song's score is the context's rate plus a weighted mean of
how far each of its things sits from that rate; its confidence is how much evidence stood behind that.

the log stays the source of truth: DATA/.taste.json is only a cache of slim rows + byte offsets, so each run reads just the
lines appended since the last one (a file that shrank is read again). bounded: ≤ MAX_ROWS rows, ≤ KEEP_DAYS days."""
import json, os, random, time
from datetime import datetime, timedelta
from pathlib import Path

V = 2   # 2: legacy mix skips re-read as partial (a cache from v1 is rebuilt)
K = 8.0                 # prior strength, in plays: a thing needs ~8 plays before it says as much as its context does
RULE_N = 4              # fewer judged plays than this is never called a pattern in the report or the ui
MAX_ROWS = 20000        # ~3 MB of cache at most: years of listening
KEEP_DAYS = 120
MAX_U8 = 8 << 20        # analysis files are ≤ 1.3 MB (12 min cap); anything bigger isn't ours to read whole
FIRM_PLAYS, FIRM_DAYS = 300, 14
FAST = 30               # a skip in under 30 s is a strong no: weighed twice
EXPLORE = 4             # 1 in 4 ranked picks is left to chance (new first), so the ranking never folds into a rut

BANDS = (("night", 0, 5), ("morning", 6, 11), ("afternoon", 12, 16), ("evening", 17, 21), ("late", 22, 23))
DIMS = {"song": 0.35, "artist": 0.3, "bpm": 0.1, "energy": 0.1, "tilt": 0.05, "quiet": 0.05, "look": 0.08,
        "source": 0.07, "length": 0.07}   # how much each thing counts in a score: the song and its artist say most
SOURCES = {"refill": "radio", "again": "pick", "add": "search", "yt-history": "history"}


def data(): return Path(os.environ.get("YTM_DATA", Path.home() / ".local/share/ytm"))


def band(h):
    if h is None: return None
    return next(n for n, a, b in BANDS if a <= int(h) % 24 <= b)


def now_ctx(mode="normal", t=None):
    return mode if mode == "work" else "normal", band(time.localtime(t).tm_hour)


# ----- the features -----
def bpm_band(b): return None if not b else "slow" if b < 90 else "fast" if b >= 118 else "mid"    # autolook's lines
def energy_band(d): return None if d is None else "low" if d < 0.35 else "high" if d >= 0.55 else "mid"
def tilt_band(t): return None if t is None else "bassy" if t >= 0.35 else "bright" if t <= 0.24 else "even"
def length_band(s):
    if not s: return None
    return "short" if s < 150 else "song" if s < 360 else "long" if s < 1200 else "mix"


def outcome_score(o, heard, dur):
    """-> (score, weight). finished 1; partial 0.2–0.6 by how much was heard; skipped 0, twice as loud under 30 s;
    stopped (ytm stop, a crash) and backfilled rows say nothing about the song: weight 0"""
    heard, dur = float(heard or 0), float(dur or 0)
    if o == "finished": return 1.0, 1.0
    if o == "partial": return 0.2 + 0.4 * (min(1.0, heard / dur) if dur > 0 else 0.5), 0.6
    if o == "skipped": return 0.0, 2.0 if heard < FAST else 1.0
    return 0.0, 0.0


def u8_signals(path, width=29):
    """lib/autolook.audio_signals without numpy: the same numbers from the same file (bass−treble, density, onset, quiet)"""
    p = Path(path)
    if p.stat().st_size > MAX_U8: return None
    b = p.read_bytes(); n = len(b) // width
    if n == 0: return None
    b = b[: n * width]
    col = lambda c: sum(b[c::width]) / n / 255
    bass = sum(col(c) for c in range(8)) / 8; high = sum(col(c) for c in range(16, 24)) / 8
    return {"tilt": round(bass - high, 4), "density": round(col(24), 4), "onset": round(col(25), 4),
            "quiet": round(sum(1 for x in b[26::width] if x < 89) / n, 4)}


def song_signals(d, vid, memo):
    """analysis/<vid>.json + .u8 -> {bpm, tilt, density, onset, quiet}; memo (the cache's) keeps them, the u8 is read once"""
    if vid in memo: return memo[vid]
    try:
        meta = json.loads((d / "analysis" / f"{vid}.json").read_text())
        s = u8_signals(d / "analysis" / f"{vid}.u8", int(meta.get("width", 29))) or {}
        s["bpm"] = meta.get("bpm")
    except (OSError, ValueError, TypeError, AttributeError): return None   # not analysed (yet): asked again next time
    memo[vid] = s
    return s


def features(r, sig=None, look=None):
    """one play (a slim row) -> {dim: value}, whatever is known"""
    sig = sig or {}
    f = {"song": r.get("vid"), "artist": (r.get("a") or "").split(", ")[0].strip().lower() or None,
         "source": r.get("src"), "length": length_band(r.get("d")), "look": look,
         "bpm": bpm_band(sig.get("bpm")), "energy": energy_band(sig.get("density")), "tilt": tilt_band(sig.get("tilt")),
         "quiet": None if sig.get("quiet") is None else ("spacious" if sig["quiet"] >= 0.30 else "full")}
    return {k: v for k, v in f.items() if v}


def track_features(t, d, memo, looks):
    """a ytmusicapi track (a radio candidate) -> the same features, from what's on disk about it"""
    vid = t.get("videoId")
    s = t.get("duration_seconds") or 0
    if not s:
        try: s = sum(int(x) * 60 ** i for i, x in enumerate(reversed(str(t.get("length") or "").split(":"))))
        except ValueError: s = 0
    a = ", ".join(x.get("name") or "" for x in t.get("artists") or [] if isinstance(x, dict))
    return features({"vid": vid, "a": a, "d": s}, song_signals(d, vid, memo) if vid else None, (looks.get(vid) or {}).get("look"))


# ----- the log, read incrementally -----
LONG = 1200   # ytm-log.lua's M.LONG: a song over 20 min is never a skip


def slim(r, stem):
    st = r.get("start") or ""
    src = r.get("source") or "search"
    o = r.get("outcome")
    if o == "skipped" and float(r.get("dur") or 0) > LONG: o = "partial"   # rows from before 0.8.2 read by 0.8.2's rule: mixes heard for minutes taught false skips
    return {"id": r.get("id"), "f": stem, "t": st, "vid": r.get("vid"), "a": r.get("artist"), "ti": r.get("title"),
            "h": r.get("hour"), "dow": r.get("dow"), "m": "work" if r.get("mode") == "work" else "normal",
            "src": SOURCES.get(src, src), "o": o, "l": r.get("listened"), "d": r.get("dur")}


def _empty(): return {"v": V, "files": {}, "rows": [], "sig": {}}


def load(d=None, cache=True, now=None):
    """-> the slim rows (oldest first), reading only what was appended since the cached offsets. cache=False reads
    everything and writes nothing (read-only runs on the real log)"""
    d = Path(d or data()); idx = d / ".taste.json"
    st = _empty()
    if cache:
        try:
            st = json.loads(idx.read_text())
            if st.get("v") != V or not isinstance(st.get("rows"), list): st = _empty()
        except (OSError, ValueError): st = _empty()
    now = now or time.time()
    cut = datetime.fromtimestamp(now - KEEP_DAYS * 86400).strftime("%Y-%m")
    seen = {r.get("id") for r in st["rows"] if r.get("id")}
    changed = False
    for f in sorted((d / "plays").glob("*.jsonl")):
        if f.stem < cut: continue
        try: size = f.stat().st_size
        except OSError: continue
        off = st["files"].get(f.name, 0)
        if size < off:   # rewritten under us: forget its rows, read it again
            st["rows"] = [r for r in st["rows"] if r.get("f") != f.stem]; seen = {r.get("id") for r in st["rows"]}; off = 0
        if size == off: continue
        with open(f, "rb") as fh: fh.seek(off); chunk = fh.read(size - off)
        end = chunk.rfind(b"\n") + 1          # a line still being written waits for the next run
        for line in chunk[:end].splitlines():
            try: r = json.loads(line)
            except ValueError: continue
            if not isinstance(r, dict) or not r.get("start"): continue
            if r.get("id") and r["id"] in seen: continue
            seen.add(r.get("id")); st["rows"].append(slim(r, f.stem))
        st["files"][f.name] = off + end; changed = True
    keep = (datetime.fromtimestamp(now) - timedelta(days=KEEP_DAYS)).isoformat()[:10]
    if st["rows"] and st["rows"][0].get("t", "")[:10] < keep: st["rows"] = [r for r in st["rows"] if r.get("t", "")[:10] >= keep]
    st["rows"] = st["rows"][-MAX_ROWS:]
    st["files"] = {k: v for k, v in st["files"].items() if Path(k).stem >= cut}
    load.state = st
    if cache and changed: _save(idx, st)
    return st["rows"]


def _save(idx, st):
    if len(st["sig"]) > 3000: st["sig"] = dict(list(st["sig"].items())[-2000:])
    try:
        tmp = idx.with_name(f".taste.{os.getpid()}.json")
        tmp.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":"))); os.replace(tmp, idx)
    except OSError: pass   # a read-only disk just means reading it all next time


def _looks(d):
    try: m = json.loads((d / "looks.json").read_text())
    except (OSError, ValueError): return {}
    return m if isinstance(m, dict) else {}


# ----- the model -----
def shrink(ws, w, prior, k=K): return (ws + k * prior) / (w + k)


class Model:
    """counts: c[(mode, band)][dim][value] = [weight, weighted score, plays]; mode-wide under (mode, None),
    everything under (None, None). built in one pass over the rows, nothing kept but sums"""

    def __init__(self, rows, d=None, cache=True):
        d = Path(d or data()); st = getattr(load, "state", None) or _empty()
        self.memo = st["sig"]; self.looks = _looks(d); self.d = d; had = len(self.memo)
        self.c, self.tot = {}, {}
        self.judged = 0
        for r in rows:
            sc, w = outcome_score(r.get("o"), r.get("l"), r.get("d"))
            if w == 0: continue
            self.judged += 1
            sig = song_signals(d, r["vid"], self.memo) if r.get("vid") else None
            f = features(r, sig, (self.looks.get(r.get("vid")) or {}).get("look"))
            m, b = r.get("m") or "normal", band(r.get("h"))
            for ctx in ((None, None), (m, None), (m, b)):
                t = self.tot.setdefault(ctx, [0.0, 0.0, 0]); t[0] += w; t[1] += w * sc; t[2] += 1
                cc = self.c.setdefault(ctx, {})
                for dim, v in f.items():
                    x = cc.setdefault(dim, {}).setdefault(v, [0.0, 0.0, 0, 0]); x[0] += w; x[1] += w * sc; x[2] += 1
                    x[3] += r.get("o") == "finished"
        if cache and st is getattr(load, "state", None) and len(self.memo) > had: _save(d / ".taste.json", st)   # newly analysed songs, once

    @staticmethod
    def _chain(levels, prior):
        """levels: counts outermost first, each containing the next. every level shrinks toward the one above it *without*
        the plays it shares with it, so a play is counted once, never once per level (two skips stay two skips)"""
        for i, x in enumerate(levels):
            inner = levels[i + 1] if i + 1 < len(levels) else [0, 0, 0]
            prior = shrink(x[1] - inner[1], x[0] - inner[0], prior)
        return prior

    def base(self, mode, b=None):
        """the context's own finish-rate: 0.6 → everything else → the rest of the mode → the mode at this hour band"""
        z = [0, 0, 0]
        lv = [self.tot.get((None, None), z), self.tot.get((mode, None), z)] + ([self.tot.get((mode, b), z)] if b else [])
        return self._chain(lv, 0.6)

    def rate(self, dim, v, mode, b=None):
        """-> (rate, plays behind it) for one thing in one context, shrunk toward the context's base"""
        z = [0, 0, 0]
        lv = [self.c.get((mode, None), {}).get(dim, {}).get(v, z)] + ([self.c.get((mode, b), {}).get(dim, {}).get(v, z)] if b else [])
        return self._chain(lv, self.base(mode, b)), lv[0][2]

    def score(self, f, mode="normal", b=None):
        """f: features -> {score, conf, why}. score is a finish chance 0..1; conf 0..1 is how much evidence it rests on"""
        root = self.base(mode, b)
        num = den = ev = 0.0; why = []
        for dim, v in f.items():
            if dim not in DIMS: continue
            r, n = self.rate(dim, v, mode, b)
            lam = DIMS[dim]; den += lam             # an unheard thing still counts, as "no different": a weak hint can't pass for a strong one
            if n == 0: continue
            num += lam * (r - root); ev += lam * n / (n + K)
            why.append((lam * (r - root), dim, v, n))
        s = root + (num / den if den else 0.0)
        why.sort(key=lambda x: -abs(x[0]))
        thin = min(1.0, self.judged / FIRM_PLAYS)
        return {"score": round(max(0.0, min(1.0, s)), 3), "conf": round(min(1.0, ev / sum(DIMS.values()) * 2) * (0.5 + 0.5 * thin), 2),
                "why": [f"{dim} {v} ({n})" for _, dim, v, n in why[:2]]}

    def rank(self, tracks, mode="normal", b=None, rng=None, explore=EXPLORE):
        """tracks (ytmusicapi rows) -> [(track, how)] best first, how = "ranked" or "explore". nothing is dropped: every
        explore-th slot goes to a random one not yet placed, ones the log has never heard of first"""
        rng = rng or random.Random()
        pool = list(tracks); rng.shuffle(pool)                 # ties (a thin log: most of them) stay a shuffle, as before
        sc = []
        for t in pool:
            f = track_features(t, self.d, self.memo, self.looks)
            s = self.score(f, mode, b)
            new = not any(self.rate(dim, f[dim], mode)[1] for dim in ("song", "artist") if dim in f)
            sc.append((s["score"], new, t))
        ranked = sorted(sc, key=lambda x: -x[0])              # stable: equal scores keep the shuffle
        out, used = [], set()
        while len(out) < len(ranked):
            if explore and (len(out) + 1) % explore == 0:
                left = [x for x in ranked if id(x[2]) not in used]
                fresh = [x for x in left if x[1]] or left
                x = rng.choice(fresh); how = "explore"
            else:
                x = next(x for x in ranked if id(x[2]) not in used); how = "ranked"
            used.add(id(x[2])); out.append((x[2], how))
        return out


def model(d=None, cache=True):
    d = Path(d or data()); rows = load(d, cache)
    return Model(rows, d, cache)


def rank_tracks(tracks, mode="normal", d=None, rng=None):
    """the cli's hook (work, refills): tracks reordered by the log, never fewer. any trouble: the order it came in"""
    try:
        m = model(d); _, b = now_ctx(mode)
        return [t for t, _ in m.rank(tracks, mode, b, rng)]
    except Exception:
        return list(tracks)


# ----- the report: for you, about you -----
def confidence(plays, days):
    lvl = "thin" if plays < 100 or days < 7 else "growing" if plays < FIRM_PLAYS or days < FIRM_DAYS else "firm"
    return {"level": lvl, "plays": plays, "days": days,
            "line": f"confidence: {lvl} ({plays} plays over {days} day{'s' * (days != 1)})"
                    + ("" if lvl == "firm" else f": patterns firm up around ~{FIRM_PLAYS} plays / 2 weeks")}


SPARK = " ▁▂▃▄▅▆▇█"
def spark(xs):
    top = max(xs) if xs else 0
    return "".join(SPARK[0] if not x else SPARK[max(1, round(x / top * 8))] for x in xs) if top else SPARK[0] * len(xs)


def pct(x): return f"{round(100 * x)}%"


def _cut(x, n): return x if len(x) <= n else x[: n - 1] + "…"


def _name(r): return f"{r.get('a') or '?'} – {r.get('ti') or r.get('vid')}"


def summary(d=None, cache=True, now=None):
    """everything the report and the ui say, as plain data (`ytm taste --json`)"""
    d = Path(d or data()); now = now or time.time()
    rows = [r for r in load(d, cache, now) if r.get("src") != "history"]   # backfilled rows have no hour and no outcome
    m = Model(rows, d, cache)
    days = len({r["t"][:10] for r in rows})
    out = {"v": V, "plays": len(rows), "hours_listened": round(sum(float(r.get("l") or 0) for r in rows) / 3600, 1),
           "first": rows[0]["t"][:10] if rows else None, "last": rows[-1]["t"][:10] if rows else None,
           "confidence": confidence(len(rows), days)}
    hours = [0.0] * 24
    for r in rows:
        if r.get("h") is not None: hours[int(r["h"]) % 24] += float(r.get("l") or 0) / 60
    out["hours"] = [round(x, 1) for x in hours]
    bt = {}
    for r in rows:
        if r.get("h") is not None: bt[band(r["h"])] = bt.get(band(r["h"]), 0) + float(r.get("l") or 0)
    out["most_band"] = max(bt, key=bt.get) if bt else None
    wk = [r for r in rows if r.get("dow") is not None]
    out["weekend_share"] = round(sum(1 for r in wk if r["dow"] in (0, 6)) / len(wk), 2) if wk else None

    ctxs = []
    for mode in ("normal", "work"):
        for b, _, _ in BANDS:
            rs = [r for r in rows if r.get("m") == mode and band(r.get("h")) == b]
            if not rs: continue
            j = [r for r in rs if outcome_score(r.get("o"), r.get("l"), r.get("d"))[1]]
            ctxs.append({"mode": mode, "band": b, "plays": len(rs), "finished": sum(r["o"] == "finished" for r in j),
                         "skipped": sum(r["o"] == "skipped" for r in j), "partial": sum(r["o"] == "partial" for r in j),
                         "fast": sum(r["o"] == "skipped" and float(r.get("l") or 0) < FAST for r in j),
                         "rate": round(m.base(mode, b), 2), "judged": len(j)})
    out["contexts"] = ctxs

    arts = {}
    for r in rows:
        a = (r.get("a") or "").split(", ")[0].strip()
        if not a or not outcome_score(r.get("o"), r.get("l"), r.get("d"))[1]: continue
        x = arts.setdefault(a.lower(), {"artist": a, "finished": 0, "plays": 0}); x["plays"] += 1; x["finished"] += r["o"] == "finished"
    out["artists"] = sorted((x for x in arts.values() if x["finished"]), key=lambda x: (-x["finished"], -x["finished"] / x["plays"], x["artist"]))[:5]

    sound = {}
    for mode in ("work", "normal"):
        per = {}
        for dim in ("bpm", "energy"):
            vals = m.c.get((mode, None), {}).get(dim, {})
            n = sum(x[2] for x in vals.values())
            bands = sorted(({"band": v, "plays": x[2], "finished": x[3], "rate": round(m.rate(dim, v, mode)[0], 2)}
                            for v, x in vals.items()), key=lambda y: -y["rate"])
            per[dim] = {"analysed": n, "bands": bands, "firm": [y for y in bands if y["plays"] >= RULE_N]}
        sound[mode] = per
    out["sound"] = sound

    fs = {}
    for r in rows:
        if r.get("o") == "skipped" and float(r.get("l") or 0) < FAST:
            k = r.get("vid") or _name(r); x = fs.setdefault(k, {"song": _name(r), "times": 0}); x["times"] += 1
    out["fast_skips"] = sorted(fs.values(), key=lambda x: (-x["times"], x["song"]))[:5]

    week = datetime.fromtimestamp(now - 7 * 86400).isoformat()[:10]
    before = {(r.get("a") or "").lower() for r in rows if r["t"][:10] < week}
    fresh = {}
    for r in rows:
        a = r.get("a")
        if r["t"][:10] >= week and a and a.lower() not in before:
            x = fresh.setdefault(a.lower(), {"artist": a, "plays": 0, "finished": 0}); x["plays"] += 1; x["finished"] += r.get("o") == "finished"
    nw = sorted(fresh.values(), key=lambda x: (-x["finished"], -x["plays"], x["artist"]))
    out["new_this_week"] = {"since": week, "artists": len(nw), "finished_any": sum(1 for x in nw if x["finished"]), "top": nw[:3],
                            "plays": sum(1 for r in rows if r["t"][:10] >= week)}
    out["headlines"] = headlines(out)
    return out


SOUND_WORDS = {"bpm": {"slow": "slow (<90 bpm)", "mid": "mid (90–117 bpm)", "fast": "fast (118+ bpm)"},
               "energy": {"low": "sparse", "mid": "medium", "high": "dense"}}
HOUR_WORDS = {"night": "at night (0–5h)", "morning": "in the morning (6–11h)", "afternoon": "in the afternoon (12–16h)",
              "evening": "in the evening (17–21h)", "late": "late (22–23h)"}


PLURAL = {"night": "nights", "morning": "mornings", "afternoon": "afternoons", "evening": "evenings", "late": "late nights"}


def headlines(s):
    """2-3 short lines (≤ 44 cells) for the terminal ui's 'you' line: only what the log can stand behind. while it's thin,
    the last line always says so"""
    if not s["plays"]: return ["nothing logged yet: play a few songs"]
    out = [f"you listen most {HOUR_WORDS[s['most_band']].split(' (')[0]}"] if s["most_band"] else []
    firm = [c for c in s["contexts"] if c["judged"] >= RULE_N]
    say = []
    if firm:
        hi, lo = max(firm, key=lambda c: c["rate"]), min(firm, key=lambda c: c["rate"])
        if hi["finished"] * 2 > hi["judged"]: say.append((hi["rate"], f"you finish {_ctx_word(hi)}: {hi['finished']} of {hi['judged']}"))
        if lo is not hi and lo["skipped"] * 2 >= lo["judged"]: say.append((1 - lo["rate"], f"you skip {_ctx_word(lo)}: {lo['skipped']} of {lo['judged']}"))
    if s["artists"] and s["artists"][0]["finished"] >= RULE_N // 2:
        a = s["artists"][0]; say.append((0, f"{_cut(a['artist'], 20)}: {a['finished']} of {a['plays']} finished"))
    c = s["confidence"]; room = 3 - len(out) - (c["level"] != "firm")
    out += [x for _, x in sorted(say, key=lambda x: -x[0])[:room]]
    if c["level"] != "firm": out.append(f"{c['level']} so far · {c['plays']} plays")
    return out[:3]


def _ctx_word(c): return ("work " if c["mode"] == "work" else "") + PLURAL[c["band"]]


def report(s):
    """the plain-words page: `ytm taste`"""
    L = []
    if not s["plays"]:
        return "nothing logged yet: play something, come back after a few songs.\n" + s["confidence"]["line"]
    span = s["first"] if s["first"] == s["last"] else f"{s['first'][5:]} → {s['last'][5:]}"
    L.append(f"you · {s['plays']} plays · {s['hours_listened']} h heard · {span}")
    L += ["", "when you listen (minutes heard by hour)", f"  {spark(s['hours'])}", "  0     6     12    18   23"]
    if s["most_band"]: L.append(f"  mostly {HOUR_WORDS[s['most_band']]}" +
                                ("" if s["weekend_share"] is None else f" · {pct(1 - s['weekend_share'])} on weekdays"))
    L += ["", "finish vs skip"]
    for c in s["contexts"]:
        thin = "" if c["judged"] >= RULE_N else "  (too few to say)"
        fast = f", {c['fast']} under 30 s" if c["fast"] else ""
        part = f" · {c['partial']} part-heard" if c["partial"] else ""
        L.append(f"  {c['mode']:6} {c['band']:9} {c['plays']:3} plays · {c['finished']} finished · {c['skipped']} skipped{fast}{part}{thin}")
    if s["artists"]:
        L += ["", "artists you finish"]
        L.append("  " + " · ".join(f"{_cut(a['artist'], 24)} {a['finished']}/{a['plays']}" for a in s["artists"]))
    L += ["", "the sound you finish (songs the stage has analysed; best first)"]
    for mode in ("work", "normal"):
        for dim, word in (("bpm", "tempo"), ("energy", "energy")):
            x = s["sound"][mode][dim]
            bits = [f"{SOUND_WORDS[dim][y['band']]} {y['finished']} of {y['plays']}" for y in x["firm"]]
            L.append(f"  {mode:6} {word:6} " + (" · ".join(bits) if bits else f"too few analysed plays yet ({x['analysed']})"))
    if s["fast_skips"]:
        L += ["", "skipped in under 30 s"]
        L += [f"  {_cut(x['song'], 64)}" + (f"  ×{x['times']}" if x["times"] > 1 else "") for x in s["fast_skips"]]
    w = s["new_this_week"]
    L += ["", "new this week"]
    if s["confidence"]["days"] and s["first"] >= w["since"]:
        L.append(f"  the log only starts {s['first'][5:]}, so all {w['artists']} artists are new: ask again next week")
    elif w["artists"]:
        top = ", ".join(f"{x['artist']}" + (" (finished)" if x["finished"] else "") for x in w["top"])
        L.append(f"  {w['artists']} new artist{'s' * (w['artists'] != 1)}, {w['finished_any']} finished at least once: {top}")
    else: L.append(f"  no new artists ({w['plays']} plays this week)")
    L += ["", s["confidence"]["line"]]
    return "\n".join(L)


def main(args, d=None):
    cache = os.environ.get("YTM_TASTE_CACHE", "1") != "0"
    s = summary(d, cache)
    print(json.dumps(s, ensure_ascii=False) if "--json" in args else report(s))
