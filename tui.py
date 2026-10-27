"""ytm ui: the clickable face of the light player (textual, inside ghostty).

a remote, not the engine: music keeps playing when this closes. one persistent connection to mpv that *listens*
(observe_property) instead of polling, so the ui costs nearly nothing while it's open.
the look follows the r/unixporn music setups (ncmpcpp + art, kew): album art on the left, the colour of the cover
running through the title and the progress line, a quiet queue below. nothing ever passes the window edge.
keys: space pause · n next · b back · / search · esc leave search · w work · r radio · +/- volume · q close
0.8: a sibling of ytm.app, not a cousin: l like · L dislike · 5 for you · s look · a re-read the account.
the look lives in DATA/prefs.json, shared with the app and the cli; a cheap stat every 2 s brings the app's changes over.
"""
import colorsys, importlib.machinery, importlib.util, io, json, os, socket, threading, urllib.request
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from rich.cells import cell_len, set_cell_size
from textual.widgets import Button, ContentSwitcher, Footer, Input, Label, ListItem, ListView, ProgressBar, Static

_l = importlib.machinery.SourceFileLoader("ytmcli", str(Path(__file__).resolve().parent / "bin/ytm"))
_s = importlib.util.spec_from_loader("ytmcli", _l); Y = importlib.util.module_from_spec(_s); _l.exec_module(Y)
ART = Y.DATA / "art"
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent / "live"))
try:
    import live as LIVE   # the window becomes the album (ytm/live): only when launched with YTM_LIVE=1
except Exception:
    LIVE = None
SPIN = "◐◓◑◒"
# the shared prefs (contract in CHANGELOG 0.8.0). the cli owns prefs_read/prefs_write; the fallbacks below keep this ui
# working against an older bin/ytm, and write the same way (read-merge-write, tmp + rename, only the keys we change)
from looks import LOOKS, LOOK_NAMES   # the shared tables (lib/looks.py)
CYCLE = LOOKS + ["auto"]   # s walks every look, then auto (the song picks), then round again
PREFS = Y.DATA / "prefs.json"
DEFAULTS = {"v": 1, "look": "auto", "accent": "cover", "motion": "calm", "moments": True}


def _raw_prefs():
    try: p = json.loads(PREFS.read_text())
    except (OSError, ValueError): return {}
    return p if isinstance(p, dict) else {}


def prefs():
    f = getattr(Y, "prefs_read", None)
    try: p = f() if f else {**DEFAULTS, **_raw_prefs()}
    except Exception: p = {**DEFAULTS, **_raw_prefs()}
    if p.get("look") not in CYCLE: p["look"] = "auto"   # a garbled look reads as the default, never a crash
    return p


_PICKS = {"stamp": None, "map": {}}
def auto_pick(vid):
    """the app's pick for vid from DATA/looks.json (re-read only when it changed), else None. cheap enough for every tick"""
    f = Y.DATA / "looks.json"
    try: st = f.stat().st_mtime_ns
    except OSError: return None
    if st != _PICKS["stamp"]:
        try: m = json.loads(f.read_text())
        except (OSError, ValueError): m = {}
        _PICKS.update(stamp=st, map=m if isinstance(m, dict) else {})
    e = _PICKS["map"].get(vid) if vid else None
    return e if isinstance(e, dict) and e.get("look") in LOOKS else None


def prefs_set(**kv):
    f = getattr(Y, "prefs_write", None)
    if f: return f(**kv)
    p = {**_raw_prefs(), **kv}; PREFS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREFS.with_name(f".prefs.{os.getpid()}.tmp"); tmp.write_text(json.dumps(p)); os.replace(tmp, PREFS)
    return {**DEFAULTS, **p}


def prefs_stamp():
    try: return PREFS.stat().st_mtime_ns
    except OSError: return None


def mode():
    try: return (Y.DATA / "mode").read_text().strip()
    except OSError: return "normal"
DEFAULT_ACCENT = "#ffb35c"   # amber, until a cover gives us its own colour

try:
    from textual_image.widget import Image as ArtImage   # kitty graphics in ghostty, half-blocks elsewhere
except Exception:                                        # still a good ui without it
    ArtImage = None


def fmt(s):
    s = int(s or 0); return f"{s // 60}:{s % 60:02d}"


class MPV:
    """one socket to the player: commands go out, property changes come back as events into self.state."""
    WATCH = ["pause", "media-title", "time-pos", "duration", "path", "volume", "paused-for-cache", "playlist-pos", "idle-active"]

    def __init__(self):
        self.state, self.sock, self.lock, self.n = {}, None, threading.Lock(), 0

    def connect(self):
        if not Y.SOCK.exists(): return False
        try:
            s = socket.socket(socket.AF_UNIX); s.connect(str(Y.SOCK)); self.sock = s
        except OSError:
            return False
        threading.Thread(target=self.read, args=(s,), daemon=True).start()
        for i, p in enumerate(self.WATCH, 1): self.send("observe_property", i, p)
        return True

    def send(self, *cmd):
        if not self.sock and not self.connect(): return
        with self.lock:
            self.n += 1
            try: self.sock.sendall((json.dumps({"command": list(cmd), "request_id": self.n}) + "\n").encode())
            except OSError: self.sock = None

    def read(self, s):
        buf = b""
        while self.sock is s:
            try: chunk = s.recv(65536)
            except OSError: chunk = b""
            if not chunk: self.sock = None; self.state.clear(); break
            buf += chunk; *lines, buf = buf.split(b"\n")
            for line in lines:
                try: m = json.loads(line)
                except ValueError: continue
                if m.get("event") == "property-change": self.state[m["name"]] = m.get("data")


FULL = {"now": "now playing", "find": "search", "queue": "queue", "history": "history", "foryou": "for you"}
SHORT = {"now": "now", "find": "find", "queue": "queue", "history": "hist", "foryou": "you"}   # under 96 cols


class Row(ListItem):
    def __init__(self, text, payload):
        super().__init__(Label(text, markup=False)); self.payload = payload


class YtmUI(App):
    TITLE = "ytm"
    CSS = """
    Screen { background: ansi_default; color: #d9d4f0; }
    ListView, ContentSwitcher, #deck, #info, Horizontal, Vertical { background: transparent; }
    #topbar { height: 1; background: #10132a 80%; padding: 0 1; }
    #logo { width: auto; color: #e88fb4; text-style: bold; margin: 0 2 0 0; }
    Button.nav { background: transparent; color: #8b86a8; margin: 0; padding: 0 1; }
    Button.nav:hover { color: #d9d4f0; background: #26204a; }
    Button.nav.here { color: #d9d4f0; text-style: bold underline; }
    #close:hover { color: #ffffff; background: #a23b55; }
    #acct { width: 1fr; color: #6f6a8e; content-align: right middle; text-wrap: nowrap; text-overflow: ellipsis; margin: 0 0 0 1; }
    #acct.in { color: #8fd4b4; }
    #clock { width: auto; color: #6f6a8e; margin: 0 1; }
    #views { height: 1fr; }
    .section { color: #6f6a8e; padding: 0 2; margin: 1 0 0 0; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #upnext { height: 1fr; margin: 0 1; }
    #find #search { margin: 1 2; }
    #rlist, #qlist, #hlist, #ylist { margin: 0 1; }
    #you { color: #b8b2d6; padding: 0 2; height: auto; max-height: 3; text-wrap: nowrap; text-overflow: ellipsis; }
    Footer { background: #10132a 80%; }
    #deck { height: 12; padding: 1 2 0 2; }
    #art { width: 22; height: 11; margin: 0 2 0 0; }
    #artbox { width: 22; height: 11; margin: 0 2 0 0; color: #3a3560; content-align: center middle; border: round #26204a; }
    #info { width: 1fr; height: 11; }
    #title { text-style: bold; height: 2; color: #ffb35c; }
    #artist { color: #b8b2d6; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #album { color: #6f6a8e; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #state { color: #8b86a8; height: 1; margin: 1 0 0 0; text-wrap: nowrap; text-overflow: ellipsis; }
    #barrow { height: 1; margin: 1 0 0 0; }
    #bar { width: 1fr; height: 1; }
    #bar > Bar { width: 1fr; }
    #bar > Bar > .bar--bar { background: #1c1f3a; }
    #time { color: #8b86a8; width: auto; margin: 0 0 0 1; }
    #controls { height: 1; margin: 1 0 0 0; }
    Button { min-width: 3; width: auto; height: 1; padding: 0 1; margin: 0 1 0 0; background: #161a33 70%; border: none; color: #d9d4f0; text-style: none; }
    Button:hover { background: #26204a; }
    Button.on { background: #3a2a6a; }
    #search { height: 3; margin: 0 2; border: tall #2a2650; background: transparent; }
    #search:focus { border: tall #3a3560; }
    ListView { background: transparent; }
    ListItem { padding: 0 1; height: 1; }
    ListItem Label { width: 1fr; text-overflow: ellipsis; text-wrap: nowrap; }
    ListItem.-highlight { background: #1c1f3a; }
    #status { color: #6f6a8e; padding: 0 2; height: 1; text-overflow: ellipsis; text-wrap: nowrap; }
    """
    BINDINGS = [("space", "pause", "play/pause"), ("n", "next", "next"), ("b", "back", "back"),
                ("slash", "find", "search"), ("1", "view('now')", "now"), ("2", "view('find')", ""), ("3", "view('queue')", "queue"),
                ("4", "view('history')", "history"), ("5", "view('foryou')", "for you"), ("escape", "view('now')", ""),
                ("w", "work", "work mode"), ("r", "radio", "radio"), ("l", "rate('LIKE')", "like"), ("L", "rate('DISLIKE')", ""),
                ("shift+l", "rate('DISLIKE')", ""), ("s", "look", "look"), ("a", "account", ""),
                ("plus", "vol(5)", "vol+"), ("equals_sign", "vol(5)", ""), ("minus", "vol(-5)", "vol−"), ("q", "quit", "close")]

    def __init__(self):
        super().__init__(); self.mpv = MPV(); self.art_vid = None; self.queue_pos = -2
        self.frame = 0; self.title_full = ""; self.live_state = None; self.cover = None
        self.acct = None; self.foryou = None; self.prefs = prefs(); self.prefs_seen = prefs_stamp(); self.width = 100

    def compose(self) -> ComposeResult:
        # the app's own chrome: a title bar you can navigate from, and a close that actually closes the window
        with Horizontal(id="topbar"):
            yield Static("◐ ytm", id="logo")
            for key, name in FULL.items():
                yield Button(name, id=f"nav-{key}", classes="nav")
            yield Static("", id="acct", markup=False)   # takes the slack, so it's the first thing to shrink (ellipsis, never past ✕)
            yield Static("", id="clock")
            yield Button("✕", id="close", classes="nav")
        with ContentSwitcher(initial="now", id="views"):
            with Vertical(id="now"):
                with Horizontal(id="deck"):
                    yield Static("♪", id="artbox")
                    with Vertical(id="info"):
                        yield Static("nothing playing", id="title", markup=False)
                        yield Static("search, or ◐ work for chill no-lyrics music", id="artist", markup=False)
                        yield Static("", id="album", markup=False)
                        yield Static("", id="state", markup=False)
                        with Horizontal(id="barrow"):
                            yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="bar")
                            yield Static("0:00/0:00", id="time")
                        with Horizontal(id="controls"):
                            yield Button("⏮", id="back"); yield Button("▶", id="pause"); yield Button("⏭", id="next")
                            yield Button("−", id="voldown"); yield Button("+", id="volup")
                            yield Button("◐ work", id="work"); yield Button("↻", id="radio")
                yield Static("up next", classes="section")
                yield ListView(id="upnext")
            with Vertical(id="find"):
                yield Input(placeholder="search youtube music · enter", id="search")
                yield ListView(id="rlist")
            with Vertical(id="queue"):
                yield Static("queue · click a song to jump to it", classes="section")
                yield ListView(id="qlist")
            with Vertical(id="history"):
                yield Static("you · what the log has learned (ytm taste for the rest)", classes="section")
                yield Static("", id="you", markup=False)
                yield Static("history · ● finished  ◐ partial  ○ skipped  ■ stopped · click to play again", classes="section")
                yield ListView(id="hlist")
            with Vertical(id="foryou"):
                yield Static("for you · from your youtube music account · enter plays from that song", classes="section")
                yield ListView(id="ylist")
        yield Static("", id="status", markup=False)
        yield Footer()

    def show_view(self, name):
        self.query_one("#views", ContentSwitcher).current = name
        for b in self.query(".nav"): b.set_class(b.id == f"nav-{name}", "here")
        self.query_one({"find": "#search", "queue": "#qlist", "history": "#hlist", "now": "#upnext", "foryou": "#ylist"}[name]).focus()
        if name == "history": self.fill_history()
        if name == "foryou" and self.foryou is None: self.fill_foryou()   # once; `a` re-reads the account and the list

    def on_mount(self):
        self.mpv.connect()
        self.set_interval(0.5, self.tick); self.set_interval(0.25, self.animate); self.tick(); self.fill_history()
        self.show_view("now")                 # opens on the player, not the search box: space means pause from the first second
        clock = lambda: self.query_one("#clock", Static).update(__import__("time").strftime("%H:%M"))
        self.set_interval(15, clock); clock()
        self.set_interval(2, self.watch_prefs)   # one stat, no read unless the file changed: ytm.app's look shows up here
        self.show_account(None); self.load_account()

    def on_resize(self, ev):
        # narrow windows: the art steps aside so every control stays reachable (never past the edge, never hidden)
        w = ev.size.width
        # the title bar shrinks before anything falls off it: short labels under 96 cols, no clock under 72
        self.width = w
        for k in SHORT: self.query_one(f"#nav-{k}", Button).label = (SHORT if w < 96 else FULL)[k]
        self.call_after_refresh(self.fit_account)   # after layout: the account gets whatever width is really left
        self.query_one("#clock").styles.display = "none" if w < 72 else "block"
        self.query_one("#logo", Static).update("◐" if w < 60 else "◐ ytm")
        tight = w < 52   # 5 tabs + the account: under 52 the gaps give their cells so ✕ stays on screen (48 still fits)
        self.query_one("#logo").styles.margin = (0, 1 if tight else 2, 0, 0)
        self.query_one("#acct").styles.margin = (0, 0, 0, 0 if tight else 1)
        narrow = w < 80
        for sel in ("#art", "#artbox"):
            for w in self.query(sel): w.styles.display = "none" if narrow else "block"
        if not narrow and self.query("#art"): self.query_one("#artbox").styles.display = "none"

    # ----- the account, quietly in the title bar -----
    @work(thread=True, exclusive=True, group="acct")
    def load_account(self):
        f = getattr(Y, "account", None)
        try: a = ((f() if f else None) or {}).get("ytmusic") or {"logged_in": False}
        except Exception: a = {"logged_in": False}   # account() promises not to raise; belt and braces
        self.call_from_thread(self.show_account, a, True)

    def show_account(self, a, fresh=False):
        """None = still asking (blank). ● name when signed in; ○ and the one command that fixes it when not."""
        self.acct = a; self.fit_account()
        if fresh and a and not a.get("logged_in") and "login" not in str(self.query_one("#acct", Static).render()):
            self.say("○ not signed in · ytm login in a terminal, then a")   # the title bar had no room to say it
        if fresh and self.foryou is None and self.query_one("#views", ContentSwitcher).current == "foryou": self.fill_foryou()

    def fit_account(self):
        """the longest wording that fits the cells the title bar has left (at 52 cols that's two: just ● or ○)"""
        a, box = self.acct, self.query_one("#acct", Static); box.set_class(bool(a and a.get("logged_in")), "in")
        if a is None: return box.update("")
        room = box.size.width or self.width
        if a.get("logged_in"):
            who = f"● {a.get('account') or 'signed in'}"
            words = [who, set_cell_size(who, room - 1) + "…" if room >= 6 else "", "●"]   # a long name gets cut, not dropped
        else:
            words = ["○ not signed in · ytm login", "○ signed out · ytm login", "○ ytm login", "○"]
        box.update(next((t for t in words if t and cell_len(t) <= room), words[-1]))

    def action_account(self):
        if self.typing(): return
        reset = getattr(Y, "reset_account", None)
        if reset: reset()   # a `ytm login` elsewhere: likes and for you look again, not just the title bar
        self.say("re-reading the account…"); self.foryou = None; self.load_account()   # the for you list follows it

    # ----- the look: one prefs file for the app, this ui and the cli -----
    def look(self):
        """the drawn look: a chosen look always wins; on auto, work mode is paper, else the app's pick for this song, else dusk"""
        lk = self.prefs.get("look", "auto")
        if lk != "auto": return lk
        md = mode()
        from looks import MODE_LOOK   # one table for the cli, this ui and the stage (the stage keeps its own copy: AutoLook.swift)
        if md in MODE_LOOK: return MODE_LOOK[md]
        p = auto_pick(self.art_vid)
        return p["look"] if p else "dusk"

    def look_label(self):
        """auto · neon drive, or just the chosen look's name"""
        lk = self.look()
        return f"auto · {LOOK_NAMES[lk]}" if self.prefs.get("look") == "auto" and mode() != "work" else LOOK_NAMES.get(lk, lk)

    def action_look(self):
        if self.typing(): return
        cur = self.prefs.get("look"); nxt = CYCLE[(CYCLE.index(cur) + 1) % len(CYCLE)] if cur in CYCLE else CYCLE[0]
        try: self.prefs = {**self.prefs, **prefs_set(look=nxt)}
        except Exception as e: return self.say(f"couldn't save the look: {e}")
        self.prefs_seen = prefs_stamp()   # our own write: don't announce it again on the next stat
        self.say(f"◇ look: {LOOK_NAMES.get(nxt, nxt)}" + (" · work mode shows paper while it's on auto" if mode() == "work" and nxt == "auto" else ""))
        self.on_look()

    def watch_prefs(self):
        stamp = prefs_stamp()
        if stamp == self.prefs_seen: return
        self.prefs_seen = stamp; old = self.prefs.get("look"); self.prefs = prefs()
        if self.prefs.get("look") != old:
            self.say(f"◇ look → {LOOK_NAMES.get(self.prefs['look'], self.prefs['look'])} (changed elsewhere)"); self.on_look()

    def on_look(self):
        self.tick()
        if not (LIVE and self.art_vid): return
        # the living layer follows the look, not just the track. debounced: s s s s is one ghostty reload, not four
        # (a thread worker can't be cancelled once it runs, so exclusive alone would still stack the shader compiles)
        if getattr(self, "look_timer", None): self.look_timer.stop()
        self.look_timer = self.set_timer(0.3, self.push_live)

    # ----- likes + for you: the account's verbs, all off the ui thread (they're network calls) -----
    def action_rate(self, rating):
        if self.typing(): return
        self.say("♥ …" if rating == "LIKE" else "✕ …"); self.run_rate(rating)

    @work(thread=True, exclusive=True, group="rate")
    def run_rate(self, rating):
        f = getattr(Y, "rate_current", None)
        if not f: return self.call_from_thread(self.say, "likes need the 0.8 cli (bin/ytm rate_current)")
        try: ok, what = f(rating)
        except Exception as e: ok, what = False, f"couldn't rate: {e}"
        if ok and rating == "DISLIKE": Y.ipc("playlist-next", "force", start=False)   # as `ytm dislike` (and youtube music) does
        self.call_from_thread(self.say, str(what))   # rate_current already says "♥ liked <song>"

    @work(thread=True, exclusive=True, group="foryou")
    def fill_foryou(self):
        """tracks → the list (kept until `a`); a message → one quiet row, and the next visit asks again."""
        say = lambda tracks, msg="": self.call_from_thread(self.foryou_rows, tracks, msg)
        say(None, "asking youtube music…")
        f, a = getattr(Y, "for_you", None), self.acct
        if a is not None and not a.get("logged_in"): return say(None, "not signed in · run `ytm login` in a terminal, then press a here")
        if not f: return say(None, "for you needs the 0.8 cli (bin/ytm for_you)")
        try: tracks = [t for t in (f(20) or []) if t.get("videoId")]
        except Exception as e: return say(None, f"couldn't reach youtube music: {e}")
        say(tracks, "nothing for you yet · listen a little, then press a")

    def foryou_rows(self, tracks, msg):
        self.foryou = tracks
        lv = self.query_one("#ylist", ListView); lv.clear()
        for i, t in enumerate(tracks or []): lv.append(Row(Y.label(t), ("foryou", i)))
        if not tracks: lv.append(Row(msg, ("none", None)))

    @work(thread=True, exclusive=True, group="play")
    def play_foryou(self, i):
        tracks = (self.foryou or [])[i:]; f = getattr(Y, "play_tracks", None)
        if not tracks or not f: return self.call_from_thread(self.say, "nothing to play" if f else "needs the 0.8 cli (bin/ytm play_tracks)")
        self.call_from_thread(self.say, f"▶ {Y.label(tracks[0])} · queueing for you…")
        try: n = f(tracks, "for-you")
        except Exception as e: return self.call_from_thread(self.say, f"couldn't play: {e}")
        self.call_from_thread(lambda: (self.say(f"▶ {Y.label(tracks[0])} · {n} from for you"), self.fill_queue(), self.show_view("now")))

    def names(self):
        try: return json.loads(Y.TITLES.read_text())
        except (OSError, ValueError): return {}

    # ----- what's playing, from the event stream (no polling of the player) -----
    def tick(self):
        if not self.mpv.sock: self.mpv.connect()
        st = self.mpv.state
        path = st.get("path") or ""
        if not path or st.get("idle-active"):
            self.title_full = "nothing playing"; self.set_state("idle", None)
            if self.queue_pos != -1: self.queue_pos = -1; self.fill_queue()
            return
        vid = Y.vid_of(path)   # a query item, as the cli and the app read it (a later v= gave a wrong id)
        meta = self.names().get(vid, {}) if vid else {}
        meta = meta if isinstance(meta, dict) else {"label": meta}
        self.title_full = meta.get("title") or (st.get("media-title") or "").split(" – ")[-1] or "…"
        self.query_one("#artist", Static).update(meta.get("artist") or "")
        self.query_one("#album", Static).update(meta.get("album") or "")
        pos, dur = st.get("time-pos") or 0, st.get("duration") or 0
        loading = bool(st.get("paused-for-cache")) or (not dur and not st.get("pause"))
        self.set_state("paused" if st.get("pause") else ("loading" if loading else "playing"), st.get("volume"))
        self.query_one("#bar", ProgressBar).update(total=max(dur, 1), progress=pos)
        self.query_one("#time", Static).update(f"{fmt(pos)}/{fmt(dur)}")
        if vid and vid != self.art_vid:
            self.art_vid = vid; self.load_art(vid, meta.get("art"))
        if st.get("playlist-pos") != self.queue_pos:
            self.queue_pos = st.get("playlist-pos"); self.fill_queue()

    def animate(self):
        """the small motion: a turning record while it plays, and long titles that drift instead of being cut."""
        self.frame += 1
        st = self.mpv.state
        playing = bool(st.get("path")) and not st.get("pause") and not st.get("idle-active")
        t = self.query_one("#title", Static)
        width = max(10, t.size.width - 3)
        full = self.title_full
        if len(full) > width and playing:
            loop = full + "   ·   "
            k = (self.frame // 2) % len(loop)
            shown = (loop + loop)[k:k + width]
        else:
            shown = full if len(full) <= width else full[: width - 1] + "…"
        t.update((SPIN[self.frame % 4] + " " if playing else "  ") + shown)
        if LIVE and self.art_vid and self.live_state is not None and self.live_state != playing:
            self.live_state = playing; self.push_live()   # the living layer calms down when paused

    @work(thread=True, exclusive=True, group="live")
    def push_live(self):
        try: LIVE.apply(self.art_vid, self.cover, bool(self.live_state), 0.7, look=self.look())   # the look caps it (paper calmest)
        except Exception as e: self.call_from_thread(self.say, f"live layer: {e}")

    def set_state(self, state, vol):
        m = mode()
        words = {"playing": "▶ playing", "paused": "⏸ paused", "loading": "◌ loading…", "idle": ""}[state]
        bits = [words, f"vol {int(vol)}" if vol is not None and state != "idle" else "", "◐ work mode" if m == "work" else "",
                f"◇ {self.look_label()}"]
        sep = "  ·  " if self.width >= 80 else " · "   # narrow: tighter, and the css ellipsis catches the rest
        self.query_one("#state", Static).update(sep.join(b for b in bits if b))
        self.query_one("#pause", Button).label = "⏸" if state in ("playing", "loading") else "▶"
        self.query_one("#work", Button).set_class(m == "work", "on")

    # ----- album art + the colour it gives the deck -----
    @work(thread=True, exclusive=True, group="art")
    def load_art(self, vid, url):
        ART.mkdir(parents=True, exist_ok=True)
        f = ART / f"{vid}.jpg"
        if not f.exists() and url:
            try:
                with urllib.request.urlopen(url, timeout=8) as r: f.write_bytes(r.read())
            except OSError: pass
        accent = DEFAULT_ACCENT
        if f.exists():
            try:
                from PIL import Image as P
                img = P.open(f).convert("RGB").resize((24, 24))
                # the most colourful common colour of the cover, lifted so it reads on the dark deck
                best = max(img.getcolors(576), key=lambda c: c[0] * (0.2 + colorsys.rgb_to_hsv(*[x / 255 for x in c[1]])[1]))[1]
                h, s, v = colorsys.rgb_to_hsv(*[x / 255 for x in best])
                r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.45), max(v, 0.85))
                accent = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
            except Exception: pass
        self.call_from_thread(self.show_art, f if f.exists() else None, accent)

    def show_art(self, f, accent):
        self.cover = str(f) if f else None
        if LIVE: self.live_state = not self.mpv.state.get("pause"); self.push_live()   # new track: the room changes with it
        self.query_one("#title", Static).styles.color = accent
        try: self.query_one("#bar Bar").styles.color = accent
        except Exception: pass
        for old in self.query("#art"): old.remove()
        box = self.query_one("#artbox", Static)
        if f and ArtImage:
            self.query_one("#deck").mount(ArtImage(str(f), id="art"), before=self.query_one("#info")); box.display = False
        else:
            box.display = True

    def say(self, text): self.query_one("#status", Static).update(text)

    # ----- lists -----
    def fill_queue(self):
        lv = self.query_one("#qlist", ListView); keep = lv.index
        pl = Y.ipc("get_property", "playlist", start=False) or []
        names = self.names(); lv.clear()
        for i, e in enumerate(pl):
            vid = Y.vid_of(e.get("filename")) or ""
            n = names.get(vid, vid); n = n.get("label", vid) if isinstance(n, dict) else n
            lv.append(Row(("▶ " if e.get("current") else "  ") + (n if n and n != vid else e.get("title") or vid), ("jump", i)))
        if keep is not None and keep < len(pl): lv.index = keep
        self.fill_upnext(pl, names)

    def fill_upnext(self, pl, names):
        lv = self.query_one("#upnext", ListView); lv.clear()
        cur = next((i for i, e in enumerate(pl) if e.get("current")), -1)
        for i, e in list(enumerate(pl))[cur + 1: cur + 9]:
            vid = Y.vid_of(e.get("filename")) or ""
            n = names.get(vid, vid); n = n.get("label", vid) if isinstance(n, dict) else n
            lv.append(Row("  " + (n if n and n != vid else e.get("title") or vid), ("jump", i)))
        if not pl: lv.append(Row("  nothing queued · / to search, w for work mode", ("none", None)))

    def fill_you(self):
        """2-3 headline patterns from lib/taste.py (offline, ~50 ms), each cut to the cells there are: never past the edge"""
        try:
            if str(Y.LIB) not in _sys.path: _sys.path.insert(0, str(Y.LIB))
            import taste
            lines = taste.summary(Y.DATA)["headlines"]
        except Exception: lines = []
        room = max(8, (self.query_one("#you").size.width or self.width) - 4)
        cut = lambda x: x if cell_len(x) <= room else set_cell_size(x, room - 1) + "…"
        self.query_one("#you", Static).update("\n".join(cut(x) for x in lines) or "nothing learned yet")

    def fill_history(self):
        self.fill_you()
        lv = self.query_one("#hlist", ListView); lv.clear()
        files = sorted((Y.DATA / "plays").glob("*.jsonl"))[-2:]
        rows = [json.loads(l) for f in files for l in f.read_text().splitlines() if l.strip()][-40:]
        mark = {"finished": "●", "partial": "◐", "skipped": "○", "stopped": "■"}
        for r in reversed(rows):
            who = f"{r.get('artist') or ''} – {r.get('title') or r.get('vid')}".strip(" –")
            lv.append(Row(f"{mark.get(r['outcome'], '?')} {r['start'][11:16]}  {who}", ("again", r)))
        if not rows: lv.append(Row("nothing yet: plays land here as you listen", ("none", None)))

    # ----- search -----
    def on_input_submitted(self, ev: Input.Submitted):
        q = ev.value.strip()
        if q: self.say(f"searching '{q}'…"); self.do_search(q)

    @work(thread=True, exclusive=True, group="search")   # its own group: a new search never cancels a radio being built
    def do_search(self, q):
        hits = Y.search(q, Y.SEARCH_N)   # three: ten was a wall to choose from , one is no choice
        def show():
            lv = self.query_one("#rlist", ListView); lv.clear()
            for t in hits: lv.append(Row(Y.label(t), ("play", (t, q))))
            self.show_view("find"); lv.focus()
            self.say(f"{len(hits)} for '{q}' · click one, or ↑↓ + enter")
        self.call_from_thread(show)

    def on_list_view_selected(self, ev: ListView.Selected):
        kind, data = getattr(ev.item, "payload", ("none", None))
        if kind == "play": self.play_track(*data)
        elif kind == "foryou": self.play_foryou(data)
        elif kind == "jump": self.mpv.send("playlist-play-index", data); self.mpv.send("set_property", "pause", False)
        elif kind == "again" and data and data.get("vid"):
            self.play_track({"videoId": data["vid"], "title": data.get("title"), "artists": [{"name": data.get("artist")}]}, None, "again")

    @work(thread=True, exclusive=True, group="play")
    def play_track(self, t, q, source="pick"):
        self.call_from_thread(self.say, f"▶ {Y.label(t)} · building a radio…")
        Y.set_mode("normal"); Y.load(t, "replace", source, q); Y.ipc("set_property", "pause", False)
        n = Y.radio_from(t["videoId"])
        def done():
            self.say(f"▶ {Y.label(t)} · +{n} in the radio"); self.fill_queue(); self.show_view("now")
        self.call_from_thread(done)

    # ----- controls: keys do nothing while you're typing in search -----
    def typing(self): return isinstance(self.focused, Input)

    def on_button_pressed(self, ev: Button.Pressed):
        if ev.button.id == "close": return self.exit()
        if ev.button.id.startswith("nav-"): return self.show_view(ev.button.id[4:])
        {"back": self.action_back, "pause": self.action_pause, "next": self.action_next, "voldown": lambda: self.action_vol(-5),
         "volup": lambda: self.action_vol(5), "work": self.action_work, "radio": self.action_radio}[ev.button.id]()

    def action_pause(self):
        if not self.typing(): self.mpv.send("cycle", "pause")
    def action_next(self):
        if not self.typing(): self.mpv.send("playlist-next", "force"); self.mpv.send("set_property", "pause", False)   # next always plays
    def action_back(self):
        if not self.typing(): self.mpv.send("playlist-prev", "force"); self.mpv.send("set_property", "pause", False)
    def action_vol(self, d):
        if not self.typing(): self.mpv.send("set_property", "volume", max(0, min(100, (self.mpv.state.get("volume") or 0) + d)))
    def action_find(self): self.show_view("find")
    def action_view(self, name): self.show_view(name)

    def action_work(self):
        if not self.typing(): self.say("◐ work mode: picking chill, no-lyrics tracks…"); self.run_work()

    @work(thread=True, exclusive=True, group="play")
    def run_work(self):
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()) as out: Y.work()
        self.call_from_thread(lambda: (self.say(out.getvalue().strip()), self.fill_queue()))

    def action_radio(self):
        if not self.typing(): self.run_radio()

    @work(thread=True, exclusive=True, group="play")
    def run_radio(self):
        vid = Y.current_id()
        if not vid: return self.call_from_thread(self.say, "nothing playing to grow a radio from")
        Y.ipc("playlist-clear", start=False); n = Y.radio_from(vid)
        self.call_from_thread(lambda: (self.say(f"↻ +{n} in a fresh radio"), self.fill_queue()))


def run():
    YtmUI().run()
