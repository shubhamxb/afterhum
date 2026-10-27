"""auto look v1 (0.8.1): song signals → (look, accent, motion, confidence, reason). decide() is pure, deterministic, no io.
mirrors AutoLook.swift rule for rule; tests/autolook_cases.json is the shared table both sides must agree on.
the app makes the pick that counts (it holds the cover pixels and the analysis) and caches it in DATA/looks.json; for_song()
reads that, else works it out here from the files on disk (no network)."""
import json, re, time

RULES_V = 1
# words: strong (3) are genre names that mean one look; weak (1) are moods that lean. weak words are only read in the title and
# album, never the artist ("moon.walken", "lost sky", "nina sky" are names, not skies). a lofi title that says "midnight" is still lofi:
# rain's strong words outweigh neon's weak one.
WORDS = {
    "rain":  ([r"lo-?fi", r"chillhop", r"jazzhop", r"beats to (?:study|relax|sleep)", r"lofi hip hop"],
              [r"rain\w*", r"study", r"cozy", r"cafe", r"coffee", r"sleep\w*", r"chill", r"window"]),
    "neon":  ([r"synthwave", r"retrowave", r"outrun(?!n)", r"cyber\w*", r"darksynth", r"vaporwave", r"phonk", r"nightcall"],
              [r"neon", r"night ?drive", r"drive", r"midnight", r"after ?dark", r"80s", r"turbo", r"city"]),
    "stars": ([r"ambient", r"star ?gaz\w*", r"space ?music", r"interstellar", r"nebula", r"cosmos", r"cosmic", r"meditation", r"drone(?!s)"],
              [r"stars?", r"galaxy", r"moon\w*", r"orbit", r"astral", r"sky", r"floating", r"dream\w*"]),
    "dusk":  ([r"acoustic", r"piano", r"orchestra\w*", r"trailer score"],
              [r"soundtrack", r"\bost\b", r"score", r"sunset", r"summer", r"golden", r"dusk", r"sunrise", r"beach", r"tropical", r"guitar", r"strings"]),
    "paper": ([r"deep work", r"concentration", r"productivity", r"focus music", r"music for (?:focus|studying|work)"],
              [r"focus", r"classical", r"background music"]),
}
_RX = {lk: (re.compile(r"(?:%s)" % "|".join(s)), re.compile(r"\b(?:%s)\b" % "|".join(w))) for lk, (s, w) in WORDS.items()}


def word_points(title, artist, album):
    """-> {look: (points, words that hit)}. a strong word counts anywhere, even inside an artist name ("harrylofi");
    weak words count in the title and album only, one point each, two at most ("midnight city" leans neon twice)"""
    named = f"{title} {album}".lower(); anyw = f"{named} {artist}".lower()
    out = {}
    for lk, (strong, weak) in _RX.items():
        m = strong.search(anyw)
        if m: out[lk] = (3.0, [m.group(0)]); continue
        ws = list(dict.fromkeys(x.group(0) for x in weak.finditer(named)))[:2]
        if ws: out[lk] = (float(len(ws)), ws)
    return out


def decide(s):
    """s: dict of signals, any may be missing (None):
       title artist album · mode source hour · video_type length · bpm tilt density onset quiet drops · lum sat colourful warm cool dark neonpx
    -> dict(look, accent, motion, conf, reason, why=[...])"""
    pts = {"dusk": 1.0, "rain": 0.0, "neon": 0.0, "stars": 0.0, "paper": 0.0}   # dusk is the floor: thin evidence lands there
    why = {k: [] for k in pts}
    def add(lk, p, w): pts[lk] += p; why[lk].append(w)

    if s.get("mode") == "work":
        return dict(look="paper", accent=_accent("paper", s), motion="calm", conf=1.0, reason="paper · work mode", why=["work mode"])

    for lk, (p, w) in word_points(s.get("title") or "", s.get("artist") or "", s.get("album") or "").items():
        add(lk, p, " ".join(f"'{x}'" for x in w))

    length = s.get("length") or 0
    if length >= 1200: add("rain", 0.5, "an hour-long mix")                  # 20 min+: a stream, almost always lofi / focus beds

    bpm = s.get("bpm")
    if bpm:
        if bpm >= 118: add("neon", 1.5, f"{bpm:.0f} bpm")
        elif bpm <= 90: add("rain", 0.5, f"{bpm:.0f} bpm"); add("stars", 0.5, f"{bpm:.0f} bpm")
    tilt = s.get("tilt")
    if tilt is not None:
        if tilt >= 0.35: add("neon", 1.0, "heavy bass")
        elif tilt <= 0.24: add("dusk", 0.5, "bright, open top end")
    on, quiet, dens = s.get("onset"), s.get("quiet"), s.get("density")
    if on is not None and on <= 0.14: add("stars", 1.0, "sparse, long notes")
    if quiet is not None and quiet >= 0.30: add("stars", 0.5, "lots of quiet")
    if dens is not None and quiet is not None and dens >= 0.55 and quiet <= 0.10: add("neon", 1.0, "a wall of sound")
    if (s.get("drops") or 0) >= 2: add("neon", 0.5, f"{s['drops']} drops")

    if s.get("sat") is not None:
        lum, sat, col, warm, cool, dark, npx = (s[k] for k in ("lum", "sat", "colourful", "warm", "cool", "dark", "neonpx"))
        if npx >= 0.05 or (sat >= 0.6 and cool >= 0.7 and dark >= 0.6): add("neon", 1.0, "a dark electric cover")
        elif dark >= 0.7 and cool >= 0.6: add("stars", 1.0, "a night-sky cover")
        if warm >= 0.8 and lum >= 0.25: add("dusk", 1.0, "a warm cover")
        if lum >= 0.6 and sat <= 0.25: add("paper", 1.0, "a pale cover")
        elif sat <= 0.3 and cool >= 0.5 and lum < 0.5: add("rain", 0.5, "a grey-blue cover")

    h = s.get("hour")
    if h is not None:
        if h >= 21 or h < 5: add("stars", 0.25, "late"); add("neon", 0.25, "late")
        elif 17 <= h < 20: add("dusk", 0.25, "evening")

    order = sorted(pts, key=lambda k: (-pts[k], list(pts).index(k)))       # ties go to the earlier look: dusk, rain, neon, stars
    top, nxt = order[0], order[1]
    margin = pts[top] - pts[nxt]
    conf = round(max(0.0, min(1.0, (pts[top] / 4) * min(1.0, margin / 1.5))), 2)
    if top != "dusk" and pts[top] - pts["dusk"] < 0.75:                     # barely past the floor is not a pick
        top, conf = "dusk", 0.0
    reasons = why[top][:3]
    reason = f"{NAMES[top]} · {', '.join(reasons)}" if reasons else f"{NAMES[top]} · nothing stood out"
    if top != "dusk" and margin < 0.5 and nxt != "dusk": reason += f" · a close call with {NAMES[nxt]}"
    return dict(look=top, accent=_accent(top, s), motion=_motion(top, s), conf=conf, reason=reason, pts=pts)


NAMES = {"dusk": "dusk", "rain": "rain window", "neon": "neon drive", "stars": "stargaze", "paper": "paper"}


def _accent(look, s):
    """the cover's colours when it has any; a grey or missing cover gets the look's own set"""
    if s.get("colourful") is None or s["colourful"] >= 0.3: return "cover"   # no cover yet: it arrives, then speaks
    return {"dusk": "ember", "rain": "sea", "neon": "night", "stars": "night", "paper": "mono"}[look]


def _motion(look, s):
    """never 'still' on its own: still is a choice (reduced motion), not a mood"""
    if look == "paper": return "calm"
    bpm, dens = s.get("bpm") or 0, s.get("density") or 0
    return "lively" if bpm >= 118 and dens >= 0.45 else "calm"


# ---- the signals, from files already on disk (no network). the app computes the same from what it already holds; its pick
# (DATA/looks.json) is the one that counts, this is for `ytm look why` and the terminal ui when the app hasn't seen the song.
def audio_signals(u8_path, meta):
    """analysis/<vid>.u8 (frames × 29: bands×24, energy, onset, intensity, ahead, beat) -> shape numbers. one pass, flat memory."""
    import numpy as np
    w = int(meta.get("width", 29))
    a = np.memmap(u8_path, dtype=np.uint8, mode="r").reshape(-1, w)
    f = lambda c: float(a[:, c].mean()) / 255
    bass = float(a[:, :8].mean()) / 255; high = float(a[:, 16:24].mean()) / 255
    ms = meta.get("moments") or []
    return {"bpm": meta.get("bpm"), "tilt": bass - high, "density": f(24), "onset": f(25),
            "quiet": float((a[:, 26] < 89).mean()), "drops": sum(1 for m in ms if m.get("kind") == "drop")}


def _trim(im):
    """Cover.trim's twin: the black bars off a letterboxed video thumbnail (16:9 in a square or a 4:3), never a dark album"""
    w, h = 16, 48
    px = list(im.resize((w, h)).getdata())
    bar = lambda y: all(max(px[y * w + x]) < 14 for x in range(w))
    top = bot = 0
    while top < h // 2 and bar(top): top += 1
    while bot < h // 2 and bar(h - 1 - bot): bot += 1
    if top < 3 or bot < 3 or abs(top - bot) > 2: return im
    kept = im.height * (h - top - bot) / h
    if kept < 64 or not 1.6 <= im.width / kept <= 2.0: return im
    k = im.height / h
    return im.crop((0, round((top + 0.5) * k), im.width, round(im.height - (bot + 0.5) * k)))


def cover_signals(jpg_path):
    import colorsys
    from PIL import Image
    px = [tuple(c / 255 for c in p) for p in _trim(Image.open(jpg_path).convert("RGB")).resize((32, 32)).getdata()]
    hsv = [colorsys.rgb_to_hsv(*p) for p in px]; n = len(px)
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px]
    col = [h for h, s, v in hsv if s > 0.25]
    share = lambda test: sum(1 for h in col if test(h)) / len(col) if col else 0.5
    return {"lum": sum(lum) / n, "sat": sum(s for _, s, _ in hsv) / n, "colourful": len(col) / n,
            "warm": share(lambda h: h < 0.17 or h > 0.9), "cool": share(lambda h: 0.45 < h < 0.75),
            "dark": sum(1 for l in lum if l < 0.15) / n,
            "neonpx": sum(1 for h, s, v in hsv if s > 0.55 and v > 0.6 and (0.75 < h < 0.95 or 0.45 < h < 0.55)) / n}


# ---- the pick for one song: the app's cached one, else worked out here ----
def evidence(entry):
    return len((entry or {}).get("from", "words").split("+"))


def cached(data, vid):
    """DATA/looks.json[vid] when it was made by these rules, else None. a garbled file is no cache, never a crash"""
    try: e = json.loads((data / "looks.json").read_text()).get(vid)
    except (OSError, ValueError, AttributeError): return None
    return e if isinstance(e, dict) and e.get("rules") == RULES_V and e.get("look") in NAMES else None


def signals_for(data, vid, mode="normal", hour=None):
    """the same signals the app reads, from DATA: titles.json (words, length), analysis/<vid>.json + .u8, art/<vid>.jpg"""
    s, src = {"mode": mode, "hour": time.localtime().tm_hour if hour is None else hour}, ["words"]
    try: row = json.loads((data / "titles.json").read_text()).get(vid)
    except (OSError, ValueError, AttributeError): row = None
    if isinstance(row, dict):
        s.update(title=row.get("title") or "", artist=row.get("artist") or "", album=row.get("album") or "", length=row.get("secs") or 0)
    elif isinstance(row, str):
        a, _, t = row.partition(" – "); s.update(title=t or a, artist=a if t else "")
    try:
        meta = json.loads((data / "analysis" / f"{vid}.json").read_text())
        s.update(audio_signals(data / "analysis" / f"{vid}.u8", meta)); src.append("audio")
    except Exception: pass   # not analysed yet (or numpy missing): words and the cover still speak
    try: s.update(cover_signals(data / "art" / f"{vid}.jpg")); src.insert(1, "cover")
    except Exception: pass
    return s, "+".join(src)


def for_song(data, vid, mode="normal", hour=None):
    """-> {look, accent, motion, conf, reason, from}. the app's cached pick wins unless this one rests on more evidence"""
    c = cached(data, vid)
    if c and evidence(c) >= 3: return c
    s, src = signals_for(data, vid, "normal", hour)
    mine = dict(decide(s), **{"from": src}); mine.pop("pts", None)
    return c if c and evidence(c) >= evidence(mine) else mine
