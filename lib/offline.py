"""ytm offline: a bounded local audio cache so the music never stops.

the goal: keep about 1 GB of audio locally so there is always something to play, even when the network drops.
  - files live in DATA/audio, named watch?v=<id>.<ext>: ytm-log.lua reads the song id from "?v=" in the path, so a cached play is
    logged, tasted and lit exactly like a streamed one
  - what is cached: the song playing + the next few in the queue (so a dropped network never cuts the queue), then, when that is
    covered, the songs you finish most (from the plays log) until the cap. the signed-in format chain gives the premium file
  - bounded by design (lesson 10-23: media work gets bounded memory + one at a time + no retry loop): yt-dlp streams to disk, never
    memory; one download at a time (a lock); max 40 MB a file; the cache is cut back to the cap oldest-used first; free disk under
    5 GB or an unplugged battery under 30% pauses the filling; a failed song rests an hour; three failures in a row = offline, sleep 5 min
"""
import fcntl, glob, json, os, random, re, subprocess, time
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("YTM_DATA", HOME / ".local/share/ytm"))
DIR = DATA / "audio"
COOKIES = DATA / "run" / "yt.cookies"
CAP = int(os.environ.get("YTM_CACHE_MB", "1024")) * 1024 * 1024
MAX_FILE = "40M"
MIN_FREE = 5 * 1024 ** 3
EXTS = ("opus", "webm", "m4a", "mp4", "mp3")
VID = re.compile(r"^[\w-]{11}$")
URLS = DATA / "urls.json"
PREMIUM_FORMAT = "774/141/251/140/234/233/bestaudio"   # the same chain bin/ytm gives mpv
RESOLVE_FAILED = {}
FAILED = {}   # vid -> time of the last failure (this process only)
SKIPPED = set()   # vids yt-dlp refused for size (a 2-hour mix): not a network failure, never retried this run


def _files():
    out = []
    for e in EXTS:
        out += glob.glob(str(DIR / f"watch?v=*.{e}"))   # glob would read "?" as a wildcard in the dir part only; the name part is a pattern anyway
    return out


def _vid_of(p):
    m = re.search(r"v=([\w-]{11})\.\w+$", p)
    return m.group(1) if m else None


def local(vid, touch=True):
    """-> the cached file for vid, or None. a hit marks it used (the cut-back goes oldest-used first)."""
    if not VID.match(vid or ""): return None
    for e in EXTS:
        p = DIR / f"watch?v={vid}.{e}"
        try:
            if p.stat().st_size > 50_000:
                if touch: os.utime(p)
                return str(p)
        except OSError: pass
    return None


def size():
    n = 0
    for p in _files():
        try: n += os.path.getsize(p)
        except OSError: pass
    return n


def cached_vids():
    return [v for v in (_vid_of(p) for p in _files()) if v]


def trim(cap=None):
    """cut back to the cap, oldest-used first. also sweeps leftovers of a download that died."""
    cap = CAP if cap is None else cap
    for p in glob.glob(str(DIR / ".tmp-*")):
        try:
            if time.time() - os.path.getmtime(p) > 600: os.unlink(p)
        except OSError: pass
    rows = []
    for p in _files():
        try: st = os.stat(p); rows.append((st.st_mtime, st.st_size, p))
        except OSError: pass
    total = sum(r[1] for r in rows)
    for _, sz, p in sorted(rows):
        if total <= cap: break
        try: os.unlink(p); total -= sz
        except OSError: pass
    return total


def clear():
    n = 0
    for p in _files() + glob.glob(str(DIR / ".tmp-*")):
        try: os.unlink(p); n += 1
        except OSError: pass
    return n


def _ok_to_fill():
    """False when the disk is nearly full or the mac is on a low battery: filling waits, playing never does."""
    try:
        st = os.statvfs(DATA)
        if st.f_bavail * st.f_frsize < MIN_FREE: return False
    except OSError: return False
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
        if "Battery Power" in out:
            m = re.search(r"(\d+)%", out)
            if m and int(m.group(1)) < 30: return False
    except Exception: pass
    return True


def fetch(vid):
    """cache one song. True if it is on disk after this. one at a time, nice'd, no retry."""
    if not VID.match(vid or ""): return False
    if local(vid, touch=False): return True
    if time.time() - FAILED.get(vid, 0) < 3600: return False
    DIR.mkdir(parents=True, exist_ok=True)
    cookies = ["--cookies", str(COOKIES)] if COOKIES.exists() else []
    fmt = "774/141/251/140" if cookies else "251/140"
    tmp = str(DIR / f".tmp-{vid}.%(ext)s")
    try:
        r = subprocess.run(["nice", "-n", "15", "yt-dlp", "-q", "--no-warnings", "--no-playlist", "--no-mtime", "--max-filesize", MAX_FILE,
                            *cookies, "-f", fmt, "-o", tmp, f"https://music.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=240)
    except Exception:
        r = None
    got = [p for p in glob.glob(str(DIR / f".tmp-{vid}.*")) if not p.endswith((".part", ".ytdl"))]
    if r is not None and r.returncode == 0 and not got:
        SKIPPED.add(vid)   # exit 0 and no file: over --max-filesize
    if r is not None and r.returncode == 0 and got:
        ext = got[0].rsplit(".", 1)[-1]
        if ext in EXTS:
            os.replace(got[0], DIR / f"watch?v={vid}.{ext}")
            trim()
            return True
    for p in glob.glob(str(DIR / f".tmp-{vid}.*")):
        try: os.unlink(p)
        except OSError: pass
    FAILED[vid] = time.time()
    return False



def _urls():
    try:
        d = json.loads(URLS.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def fresh_url(vid, now=None):
    """-> a resolved stream url good for 15+ more minutes, or None."""
    r = _urls().get(vid)
    now = time.time() if now is None else now
    return r["url"] if isinstance(r, dict) and r.get("url") and r.get("exp", 0) - now > 900 else None


def put_url(vid, url, now=None):
    """store a resolved url with its own expiry (googlevideo urls say expire=<epoch>). at most 300 kept, soonest-to-expire dropped.
    written whole, then renamed: the player's lua reads this file at every track open."""
    m = re.search(r"[?&/]expire[=/](\d{9,11})", url)
    now = time.time() if now is None else now
    exp = int(m.group(1)) if m else int(now) + 4 * 3600
    d = _urls()
    d = {k: v for k, v in d.items() if isinstance(v, dict) and v.get("exp", 0) > now}
    d[vid] = {"url": url, "exp": exp}
    if len(d) > 300: d = dict(sorted(d.items(), key=lambda kv: kv[1]["exp"])[-300:])
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = URLS.with_name(f".urls.{os.getpid()}.json")
    tmp.write_text(json.dumps(d)); tmp.replace(URLS)


def resolve(vid):
    """resolve one song's stream url ahead of the play (4-5 s of yt-dlp that the play then skips). True if a fresh url is stored."""
    if not VID.match(vid or ""): return False
    if fresh_url(vid): return True
    if time.time() - RESOLVE_FAILED.get(vid, 0) < 900: return False
    cookies = ["--cookies", str(COOKIES)] if COOKIES.exists() else []
    fmt = PREMIUM_FORMAT if cookies else "234/233/bestaudio"
    try:
        r = subprocess.run(["nice", "-n", "10", "yt-dlp", "-q", "--no-warnings", "--no-playlist", *cookies, "-f", fmt, "-g",
                            f"https://music.youtube.com/watch?v={vid}"], capture_output=True, text=True, timeout=60)
        url = r.stdout.strip().split("\n")[0] if r.returncode == 0 else ""
    except Exception:
        url = ""
    if url.startswith("http"):
        put_url(vid, url); return True
    RESOLVE_FAILED[vid] = time.time(); return False


def resolve_many(vids, workers=3):
    """the search cards' top few, in parallel (bounded): by the time a card is clicked its url is waiting."""
    from concurrent.futures import ThreadPoolExecutor
    vids = [v for v in dict.fromkeys(vids) if VID.match(v or "")][:5]
    with ThreadPoolExecutor(max_workers=min(workers, len(vids) or 1)) as ex:
        return list(ex.map(resolve, vids))


def favourites(limit=300):
    """the songs finished most, from the plays log (no network): the filler once the queue is covered."""
    counts = {}
    for f in sorted(glob.glob(str(DATA / "plays" / "*.jsonl")))[-3:]:
        try:
            for line in open(f, encoding="utf-8"):
                try: r = json.loads(line)
                except ValueError: continue
                if r.get("outcome") == "finished" and VID.match(r.get("vid") or ""):
                    counts[r["vid"]] = counts.get(r["vid"], 0) + 1
        except OSError: pass
    return [v for v, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:limit]


def shuffled_cached():
    v = cached_vids(); random.shuffle(v); return v


def status():
    return {"files": len(cached_vids()), "bytes": size(), "cap": CAP, "dir": str(DIR)}


def watcher(wanted_fn, alive_fn, log=lambda *_: None, tick_fn=None):
    """the background loop. wanted_fn() -> the vids the queue needs (playing first); alive_fn() -> False once the player is gone.
    singleton (a lock); exits when the player does."""
    DIR.mkdir(parents=True, exist_ok=True)
    lock = (DATA / ".offline.lock").open("w")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return 0
    os.nice(10)
    fails = 0
    while alive_fn():
        did = False
        try:
            queue = [v for v in wanted_fn() if not local(v, touch=False) and not fresh_url(v)
                     and (v not in SKIPPED or time.time() - RESOLVE_FAILED.get(v, 0) > 900)
                     and (time.time() - FAILED.get(v, 0) > 3600 or time.time() - RESOLVE_FAILED.get(v, 0) > 900)]
            if queue:
                v = queue[0]; did = True
                if v in SKIPPED or not _ok_to_fill():
                    ok = resolve(v)       # too big to cache, or the disk / battery says wait: a resolved url still skips the 5 s
                else:
                    ok = fetch(v) or resolve(v)   # a failed download still leaves the song one resolve away from instant
                fails = 0 if (ok or v in SKIPPED) else fails + 1
            elif _ok_to_fill() and size() < CAP - 60 * 1024 * 1024:
                have = set(cached_vids())
                nxt = next((v for v in favourites() if v not in have and time.time() - FAILED.get(v, 0) > 3600), None)
                if nxt:
                    ok = fetch(nxt); did = True
                    fails = 0 if (ok or nxt in SKIPPED) else fails + 1
        except Exception as e:
            log("offline: " + str(e)[:120]); fails += 1
        wait = 300 if fails >= 3 else (4 if did else 20)
        while wait > 0 and alive_fn():   # sleep in 3 s slices: the output guard ticks between them
            if tick_fn:
                try: tick_fn()
                except Exception as e: log("tick: " + str(e)[:120])
            time.sleep(min(3, wait)); wait -= 3
    return 0
