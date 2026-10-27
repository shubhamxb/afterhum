"""ytm intent: what did the words mean? pure, offline, stdlib.

the problem: YouTube Music search is a keyword match on song
titles: "something to focus" finds Beatles' "Something", "songs like Low Roar" finds Low Roar, "arijit singh new" finds old songs,
"gym workout" finds hour-long uploads. parse() reads the words first and says which of these it is; rank() cleans up a plain search.

  parse(q, modes) -> {"kind": "mode", "mode": name}              "something for coding" · "music to study" · "gym mode"
                     {"kind": "like_now"}                         "more like this" · "something similar" · "same vibe"
                     {"kind": "like", "subject": "low roar"}      "songs like low roar" · "similar to x" · "sounds like x"
                     {"kind": "new", "artist": "arijit singh"}    "arijit singh new" · "latest songs by x" · "new music from x"
                     {"kind": "plain", "q": "..."}                everything else, cleaned: "play creep by radiohead" -> "creep radiohead"

it only reads intent from clear cues, never from a single word that is also a song or a band ("gym class heroes", "drive",
"like a rolling stone" stay plain searches). tests/test_intent.py has the cases both ways.
"""
import json, re
from pathlib import Path

# activity -> the words people use for it. a mode name itself always counts ("coding mode").
ACTIVITIES = {
    "work":    ["work", "working", "office", "emails", "email", "admin", "spreadsheets", "spreadsheet", "meetings", "meeting"],
    "focus":   ["focus", "concentrate", "concentration", "deep work", "productivity", "productive", "get things done", "in the zone"],
    "coding":  ["coding", "code", "programming", "program", "developing", "dev", "hacking", "debugging", "build"],
    "design":  ["design", "designing", "figma", "creative", "creating", "drawing", "sketching", "illustration", "editing"],
    "writing": ["writing", "write", "essay", "blogging", "journaling", "journal", "novel", "drafting"],
    "reading": ["reading", "read", "book", "books"],
    "study":   ["study", "studying", "homework", "exam", "exams", "revision", "revising", "learning", "learn", "lecture"],
    "gym":     ["gym", "workout", "work out", "lifting", "weights", "exercise", "exercising", "training", "pump"],
    "run":     ["running", "run", "jogging", "jog", "cardio", "treadmill", "marathon"],
    "cook":    ["cooking", "cook", "kitchen", "baking", "dinner", "chores", "cleaning"],
    "drive":   ["driving", "drive", "road trip", "roadtrip", "commute", "commuting", "ride", "riding", "night drive"],
    "gaming":  ["gaming", "games", "game", "play games", "playing games", "gamer"],
    "party":   ["party", "partying", "dance", "dancing", "pregame", "celebrate", "celebration", "club"],
    "morning": ["morning", "wake up", "waking up", "sunrise", "breakfast"],
    "winddown": ["wind down", "unwind", "decompress", "relax", "relaxing", "evening", "after work"],
    "cafe":    ["cafe", "coffee", "coffee shop", "coworking", "co working", "library"],
    "chill":   ["chill", "chilling", "mellow", "vibe", "vibing", "easy"],
    "energy":  ["hype", "energy", "energetic", "motivation", "motivated", "pumped", "angry", "adrenaline"],
    "sleep":   ["sleep", "sleeping", "nap", "bedtime", "insomnia", "going to bed", "fall asleep"],
}
# words that say "I am describing an occasion, not naming a song"
CUES = {"for", "to", "while", "when", "mode", "music", "songs", "song", "tracks", "beats", "playlist", "mix", "vibes", "vibe",
        "something", "anything", "stuff", "tunes", "sounds", "background"}
# unambiguous enough to trigger a mode alone (a single word like "drive" or "party" is also a song: it needs a cue)
STRONG = {"coding", "programming", "studying", "workout", "work out", "gym", "cooking", "jogging", "gaming", "deep work", "road trip",
          "wind down", "unwind", "productivity", "homework", "journaling"}
FILLER = {"play", "put", "on", "some", "me", "give", "i", "want", "need", "to", "hear", "listen", "listening", "something", "anything",
          "good", "nice", "great", "best", "the", "a", "an", "my", "and", "of", "with", "im", "i'm", "am", "is", "while", "when",
          "for", "music", "songs", "song", "tracks", "beats", "playlist", "mix", "vibes", "vibe", "stuff", "tunes", "sounds",
          "background", "mode", "please", "pls", "start", "turn", "can", "you", "just", "that", "fits", "suits", "goes", "well",
          "perfect", "ideal", "right", "some", "kind", "type", "lets", "let's", "now", "today", "tonight"}

NOW = {"this", "it", "that", "this song", "this one", "the current song", "whats playing", "what's playing", "the current one",
       "what is playing", "this track"}
SAME = {"more like this", "more of this", "something similar", "similar", "same vibe", "the same vibe", "keep going", "more like that",
        "more", "similar songs", "similar music", "songs like this", "music like this", "like this", "something like this",
        "same energy", "same but different"}

_LIKE = re.compile(r"^(?:play\s+)?(?:some\s+)?(?:(?:songs?|music|tracks?|artists?|bands?|stuff|something|more|singers?|tunes)\s+)"
                   r"(?:like|similar\s+to|that\s+sounds?\s+like|which\s+sounds?\s+like|in\s+the\s+(?:style|vibe)\s+of)\s+(.+)$")
_LIKE2 = re.compile(r"^(?:play\s+)?(?:some\s+)?(?:similar\s+to|sounds?\s+like|in\s+the\s+(?:style|vibe)\s+of|reminds?\s+me\s+of)\s+(.+)$")
_NEW = re.compile(r"^(.+?)\s+(?:new|newest|latest|recent|new\s+(?:songs?|music|release|releases|stuff)|latest\s+(?:songs?|music|release|releases)"
                  r"|newest\s+(?:songs?|music|release|releases))$")
_NEW2 = re.compile(r"^(?:play\s+)?(?:the\s+)?(?:new|newest|latest|recent)\s+(?:songs?\s+|music\s+|releases?\s+|stuff\s+)?(?:by|from|of)\s+(.+)$")
_BY = re.compile(r"^(.+?)\s+by\s+(.+)$")


def _norm(q):
    q = (q or "").lower().replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", q).strip()


def _words(q): return re.findall(r"[\w']+", _norm(q))


def mode_for(q, modes):
    """-> a mode name if the whole query is an occasion (filler + one activity), else None. `modes`: the known mode names."""
    text = _norm(q)
    toks = _words(text)
    if not toks or len(toks) > 6: return None
    joined = " ".join(toks)
    found = None
    # a mode named outright ("coding mode", "chill") counts, but only as "<name> mode" or with a cue: "focus" alone is also a song
    for name in modes:
        if re.search(rf"\b{re.escape(name)}\s+mode\b", joined) or re.search(rf"\bmode\s+{re.escape(name)}\b", joined):
            found = (name, name); break
    if not found:
        best = None
        for name, words in ACTIVITIES.items():
            if name not in modes: continue
            for w in words:
                if re.search(rf"(?<![\w']){re.escape(w)}(?![\w'])", joined) and (best is None or len(w) > len(best[1])):
                    best = (name, w)
        found = best
    if not found: return None
    name, phrase = found
    rest = re.sub(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", " ", joined, count=1)
    rest = re.sub(rf"\b{re.escape(name)}\b", " ", rest)
    own = {w for ph in ACTIVITIES.get(name, []) for w in ph.split()} | {"session", "sessions", "time", "day", "work", "working"}
    left = [w for w in rest.split() if w not in FILLER and w not in own]   # its own words ("cooking dinner") and "design work" are the same ask
    if left: return None                                  # "gym class heroes", "lofi for coding": something else is being asked for
    if phrase == name and re.search(rf"\b{re.escape(name)}\s+mode\b|\bmode\s+{re.escape(name)}\b", joined): return name
    cue = any(w in CUES for w in toks)
    if cue or phrase in STRONG: return name
    return None


def parse(q, modes=()):
    """see the module doc. never raises, never returns None."""
    raw = (q or "").strip()
    text = _norm(raw)
    if not text: return {"kind": "plain", "q": raw}
    if text in SAME: return {"kind": "like_now"}
    m = _LIKE.match(text) or _LIKE2.match(text)
    if m:
        subj = m.group(1).strip(" .!?")
        if subj in NOW: return {"kind": "like_now"}
        if subj: return {"kind": "like", "subject": subj}
    mode = mode_for(text, list(modes)) if modes else None
    if mode: return {"kind": "mode", "mode": mode}
    m = _NEW2.match(text) or _NEW.match(text)
    if m:
        art = re.sub(r"^(?:play\s+)", "", m.group(1)).strip(" .!?")
        art = re.sub(r"\s+(?:songs?|music|tracks?)$", "", art)
        if art and art not in {"the", "a", "some", "any"} and len(_words(art)) <= 5: return {"kind": "new", "artist": art}
    return {"kind": "plain", "q": clean(text)}


def clean(text):
    """'play creep by radiohead' -> 'creep radiohead'; 'radiohead - creep' -> 'radiohead creep'. never empties the query."""
    t = _norm(text)
    t2 = re.sub(r"^(?:please\s+|pls\s+)?(?:play|put on|start|queue|i want to hear|i wanna hear|play me)\s+", "", t)
    t2 = re.sub(r"^(?:some\s+|the song\s+|song\s+)", "", t2)
    t2 = re.sub(r"\s+(?:please|pls)$", "", t2)
    m = _BY.match(t2)
    if m: t2 = f"{m.group(1)} {m.group(2)}"
    t2 = re.sub(r"\s+-\s+", " ", t2).strip()
    return t2 or t


# ---- ranking a plain search ----
JUNK = re.compile(r"\b(\d+\s*(?:hours?|hrs?|h)\b|hour\s+mix|non[\s-]?stop|jukebox|compilation|playlist|best of|full album|slowed|reverb|"
                  r"sped\s*up|8d|nightcore|karaoke|cover|remix|mashup|loop|extended|bass boosted|lyrics?)\b", re.I)


def finished_artists(data, limit=60):
    """artist (lowercase) -> times finished, from the plays log (this and the last two months). the 'you finish this' signal."""
    from glob import glob
    counts = {}
    for f in sorted(glob(str(Path(data) / "plays" / "*.jsonl")))[-3:]:
        try:
            for line in open(f, encoding="utf-8"):
                try: r = json.loads(line)
                except ValueError: continue
                if r.get("outcome") == "finished" and r.get("artist"):
                    for a in str(r["artist"]).split(","):
                        a = a.strip().lower()
                        if a: counts[a] = counts.get(a, 0) + 1
        except OSError: pass
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:limit])


def _text(t):
    arts = " ".join(a.get("name", "") for a in t.get("artists") or [] if isinstance(a, dict))
    alb = t.get("album"); alb = alb.get("name", "") if isinstance(alb, dict) else (alb or "")
    return f"{t.get('title', '')} {arts} {alb}".lower()


def rank(hits, q, finished=None, disliked=None):
    """reorder search hits: query words covered by title + artist + album, official audio over uploads, a small push for artists
    you finish, a pull-down for hour-long mixes, slowed/reverb, covers and lyric uploads unless the query asked for them.
    stable: with no signal the order is YouTube's. never drops a hit."""
    qt = [w for w in _words(q) if w not in FILLER and len(w) > 1]
    qs = _norm(q)
    finished = finished or {}
    disliked = disliked or ()
    scored = []
    for i, t in enumerate(hits):
        text = _text(t)
        cover = (sum(1 for w in qt if re.search(rf"(?<![\w']){re.escape(w)}", text)) / len(qt)) if qt else 0.0
        s = cover * 3.0 - i * 0.15
        if t.get("videoId") in disliked: s -= 10   # a song you thumbed down goes last, never first
        title = (t.get("title") or "")
        for m in JUNK.finditer(title):
            if m.group(0).lower() not in qs: s -= 1.2; break
        dur = t.get("duration_seconds") or 0
        if dur > 900 and "mix" not in qs and "hour" not in qs: s -= 1.5
        vt = t.get("videoType") or ""
        if vt.endswith("ATV"): s += 0.5
        elif vt.endswith("UGC"): s -= 0.5
        for a in t.get("artists") or []:
            n = (a.get("name") or "").lower() if isinstance(a, dict) else ""
            if n and n in finished: s += min(0.9, 0.3 + 0.1 * finished[n]); break
        scored.append((-s, i, t))
    return [t for _, _, t in sorted(scored, key=lambda x: (x[0], x[1]))]
