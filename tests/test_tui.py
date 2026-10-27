"""ytm terminal ui: headless (textual pilot), never a real window. scratch data only (YTM_DATA → a temp dir), the Y
functions monkeypatched with fakes: no network, no mpv, no keychain, no browser cookies. synthetic names only.
the live layer runs for real into the scratch dir, with its ghostty signal stubbed out (this may be running inside ghostty).
run: python3 tests/test_tui.py"""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path

SCRATCH = tempfile.mkdtemp(prefix="ytm-tui-test-")
os.environ["YTM_DATA"] = SCRATCH; os.environ["YTM_NO_REEXEC"] = "1"
os.environ["YTM_AUTH_SERVICE"] = "ytm-test-auth"; os.environ["YTM_SPOTIFY_SERVICE"] = "ytm-test-spotify"
os.environ.pop("YTM_LIVE", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tui as T
from rich.cells import cell_len
from textual.widgets import ContentSwitcher, Footer, ListView, Static

Y, DATA, PREFS = T.Y, Path(SCRATCH), Path(SCRATCH) / "prefs.json"
(DATA / "plays").mkdir(exist_ok=True)
(DATA / "plays" / "2026-10.jsonl").write_text("".join(json.dumps({"id": f"r{i}", "start": f"2026-10-2{i % 3}T14:0{i % 10}:00+05:30",
    "vid": f"vid{i:08d}", "title": f"test song {i}", "artist": "a test artist with a very long name indeed, and friends", "dur": 200,
    "listened": 200, "outcome": "finished", "source": "radio", "hour": 14, "dow": 5, "mode": "normal"}) + "\n" for i in range(12)))
assert str(Y.DATA) == SCRATCH and str(Y.SOCK).startswith(SCRATCH), "refusing to run against real data"

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")


def song(i): return {"videoId": f"vid{i:08d}", "title": f"test song {i}", "artists": [{"name": "test artist"}]}


class Fake:
    """stands in for the 0.8 cli functions: records every call, never touches the network or the player"""
    def __init__(self): self.logged_in = False; self.rated = []; self.played = []; self.asked = 0; self.resets = 0; self.ipc = []
    def account(self): return {"ytmusic": {"logged_in": self.logged_in, "account": "test listener" if self.logged_in else None},
                               "spotify": {"logged_in": False}}
    def rate_current(self, rating):   # the real one's words: RATED[rating] + the song (bin/ytm rate_current)
        self.rated.append(rating); return (True, f"{Y.RATED[rating]} test artist – test song 1")
    def reset_account(self): self.resets += 1
    def for_you(self, n=20): self.asked += 1; return [song(i) for i in range(3)]
    def play_tracks(self, tracks, source, shuffle=False): self.played.append(([t["videoId"] for t in tracks], source)); return len(tracks)


def forbid(name):
    def f(*a, **k): raise AssertionError(f"test touched Y.{name}")
    return f


def install(fake):
    for n in ("account", "rate_current", "for_you", "play_tracks", "reset_account"): setattr(Y, n, getattr(fake, n))
    for n in ("search", "radio_from", "load", "work", "daemon", "yt", "set_mode"): setattr(Y, n, forbid(n))
    Y.ipc = lambda *a, **k: fake.ipc.append(a) and None   # the player isn't there; fill_queue reads an empty playlist


class FakeLive:
    def __init__(self): self.calls = []
    def apply(self, *a, **k): self.calls.append((a, k))


def text(app, sel): return str(app.query_one(sel, Static).render())


def overflow(app, w, h):
    """every widget drawn on screen stays inside it. the footer scrolls its own keys, so only the footer itself counts."""
    bad = []
    for wd in app.screen.walk_children():
        if not wd.display or wd.region.width == 0 or any(isinstance(a, Footer) for a in wd.ancestors): continue
        r = wd.region
        if r.right > w or r.bottom > h or r.x < 0: bad.append(f"{wd.__class__.__name__}#{wd.id} {r}")
    # the title bar: the sum of its pieces fits the row (that's where a long account name would push ✕ off)
    top = app.query_one("#topbar")
    used = sum(c.outer_size.width for c in top.children if c.display)
    if used > w: bad.append(f"topbar {used} cells > {w}")
    return bad


async def settle(app, pilot):
    for _ in range(3): await app.workers.wait_for_complete(); await pilot.pause()


async def run(size, own_prefs):
    w, h = size; tag = f"{w}x{h}{'' if own_prefs else ' fallback'}"
    for f in (PREFS, DATA / "mode"): f.unlink(missing_ok=True)
    PREFS.write_text(json.dumps({"v": 1, "look": "dusk", "accent": "ember", "zz": "unknown key kept"}))
    fake = Fake(); install(fake); live = FakeLive(); T.LIVE = live
    app = T.YtmUI()
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        sw = app.query_one("#views", ContentSwitcher)

        # the account line, logged out then (after `a`) logged in
        acct = app.query_one("#acct", Static)
        shown = text(app, "#acct")
        check(f"{tag}: account logged out", shown.startswith("○") and (w < 72 or "ytm login" in shown), repr(shown))
        check(f"{tag}: account text fits its cells", cell_len(shown) <= acct.size.width, f"{acct.size.width} cells")
        if "login" not in shown: check(f"{tag}: no room in the bar → hint in status", "ytm login" in text(app, "#status"), text(app, "#status"))
        fake.logged_in = True; await pilot.press("a"); await settle(app, pilot)
        want = "●" if acct.size.width < 15 else "● test listener"
        check(f"{tag}: account logged in after a", text(app, "#acct") == want, repr(text(app, "#acct")))
        check(f"{tag}: a forgets the cli's cached login too", fake.resets == 1, str(fake.resets))

        # views switch from keys and nav buttons
        for key, view in (("3", "queue"), ("4", "history"), ("5", "foryou"), ("1", "now")):
            await pilot.press(key); await settle(app, pilot)
            check(f"{tag}: key {key} → {view}", sw.current == view, sw.current)
            if view == "history":   # 0.8.2: the 'you' line, 2-3 headlines from lib/taste.py, cut to the cells there are
                you = app.query_one("#you", Static); lines = text(app, "#you").splitlines()
                check(f"{tag}: the you line says something and fits", 1 <= len(lines) <= 3 and all(cell_len(x) <= you.size.width for x in lines)
                      and not overflow(app, w, h), f"{lines} in {you.size.width} cells")
        await pilot.press("2"); check(f"{tag}: key 2 → find", sw.current == "find")
        await pilot.press("l"); check(f"{tag}: l types in search, doesn't rate", fake.rated == [] and app.query_one("#search").value == "l")
        await pilot.press("escape"); check(f"{tag}: escape leaves search", sw.current == "now", sw.current)
        # search shows three (0.8.2): the cli's cap, asked for by name; ten was a wall to choose from
        asked = []
        Y.search = lambda q, n=10: (asked.append((q, n)), [song(i) for i in range(10)][:n])[1]
        await pilot.press("2"); app.query_one("#search").value = "test words"; await pilot.press("enter"); await settle(app, pilot)
        rl = app.query_one("#rlist", ListView)
        check(f"{tag}: search lists 3", len(rl.children) == 3 and asked == [("test words", 3)], f"{len(rl.children)} rows, asked {asked}")
        check(f"{tag}: …says 3", text(app, "#status").startswith("3 for 'test words'"), text(app, "#status"))
        bad = overflow(app, w, h); check(f"{tag}: three results fit", not bad, "; ".join(bad[:3]))
        Y.search = forbid("search"); app.query_one("#search").value = ""
        await pilot.press("escape"); await pilot.press("escape"); await settle(app, pilot)
        await pilot.click("#nav-foryou"); await settle(app, pilot)
        check(f"{tag}: nav button → for you", sw.current == "foryou" and app.query_one("#nav-foryou").has_class("here"))

        # for you: the list, enter plays from that song
        lv = app.query_one("#ylist", ListView)
        check(f"{tag}: for you lists for_you()", len(lv.children) == 3 and fake.asked == 1, f"{len(lv.children)} rows, asked {fake.asked}")
        lv.focus(); lv.index = 1; await pilot.press("enter"); await settle(app, pilot)
        check(f"{tag}: enter plays from that index", fake.played == [(["vid00000001", "vid00000002"], "for-you")], str(fake.played))
        check(f"{tag}: back on now playing after play", sw.current == "now", sw.current)

        # like / dislike say the result
        await pilot.press("l"); await settle(app, pilot)
        check(f"{tag}: l likes, said once", fake.rated == ["LIKE"] and text(app, "#status") == "♥ liked test artist – test song 1", text(app, "#status"))
        check(f"{tag}: l doesn't skip", ("playlist-next", "force") not in fake.ipc)
        await pilot.press("L"); await settle(app, pilot)
        check(f"{tag}: L dislikes", fake.rated == ["LIKE", "DISLIKE"] and text(app, "#status").startswith("✕ disliked"), text(app, "#status"))
        check(f"{tag}: L skips the song, as the cli does", ("playlist-next", "force") in fake.ipc, str(fake.ipc[-2:]))

        # the look: s cycles + writes prefs.json (merge: other keys kept), the state line shows it, live gets it
        app.art_vid = "vid00000001"
        await pilot.press("s"); await settle(app, pilot); await pilot.pause(0.4); await settle(app, pilot)   # live is debounced 0.3 s
        p = json.loads(PREFS.read_text())
        check(f"{tag}: s → rain in prefs.json", p.get("look") == "rain", str(p))
        check(f"{tag}: prefs merge keeps other keys", p.get("accent") == "ember" and p.get("zz") == "unknown key kept", str(p))
        check(f"{tag}: state line shows the look", "rain window" in text(app, "#state"), repr(text(app, "#state")))
        check(f"{tag}: live gets the look", live.calls and live.calls[-1][1].get("look") == "rain", str(live.calls[-1:]))
        n = len(live.calls)
        for _ in range(len(Y.LOOKS) - 1): await pilot.press("s")   # from rain: the rest of the looks, then auto
        await pilot.pause(0.4); await settle(app, pilot)
        check(f"{tag}: after the last look comes auto", json.loads(PREFS.read_text())["look"] == "auto")
        check(f"{tag}: quick s presses → one live reload", len(live.calls) == n + 1, f"{len(live.calls) - n}")
        check(f"{tag}: auto with no pick for the song draws dusk", live.calls[-1][1].get("look") == "dusk" and "auto · dusk" in text(app, "#state"),
              f"{live.calls[-1:]} {text(app, '#state')!r}")
        # the app's pick for this song (DATA/looks.json) is what auto draws here too
        (DATA / "looks.json").write_text(json.dumps({"vid00000001": {"look": "neon", "reason": "neon drive · 129 bpm", "rules": 1, "from": "words+audio"}}))
        app.on_look(); await pilot.pause(0.4); await settle(app, pilot)
        check(f"{tag}: auto resolves from looks.json", app.look() == "neon" and "auto · neon drive" in text(app, "#state")
              and live.calls[-1][1].get("look") == "neon", f"{text(app, '#state')!r} {live.calls[-1:]}")
        (DATA / "looks.json").write_text("{garbled"); app.tick(); await pilot.pause()
        check(f"{tag}: garbled looks.json → dusk, no crash", app.look() == "dusk", app.look())
        (DATA / "looks.json").unlink()
        await pilot.press("s"); await pilot.pause(0.4); await settle(app, pilot)
        check(f"{tag}: look wraps back to dusk", json.loads(PREFS.read_text())["look"] == "dusk")

        # a change made elsewhere (ytm.app) shows up within the 2 s stat
        PREFS.write_text(json.dumps({**json.loads(PREFS.read_text()), "look": "stars"}))
        os.utime(PREFS, ns=(time.time_ns(), time.time_ns() + 5_000_000))
        await pilot.pause(2.6)
        check(f"{tag}: external change picked up", app.prefs["look"] == "stars" and "stargaze" in text(app, "#state"), repr(text(app, "#state")))
        PREFS.write_text("{garbled"); os.utime(PREFS, ns=(time.time_ns(), time.time_ns() + 9_000_000))
        app.watch_prefs(); await pilot.pause()
        check(f"{tag}: garbled prefs → defaults (auto), no crash", app.prefs["look"] == "auto", str(app.prefs))

        # work mode shows paper while the look is auto; a hand pick wins (10-25)
        (DATA / "mode").write_text("work"); app.tick(); await pilot.pause()
        check(f"{tag}: work mode on auto shows paper", "paper" in text(app, "#state") and "dusk" not in text(app, "#state"), repr(text(app, "#state")))
        app.prefs = dict(app.prefs, look="neon"); app.tick(); await pilot.pause()
        check(f"{tag}: work mode + a hand-picked look shows that look", app.look() == "neon", app.look())
        app.prefs = dict(app.prefs, look="auto")
        (DATA / "mode").unlink()

        # logged out: for you says how to log in, and plays nothing
        fake.logged_in = False; await pilot.press("5"); await pilot.press("a"); await settle(app, pilot)
        rows = [str(r.query_one("Label").render()) for r in app.query_one("#ylist", ListView).children]
        check(f"{tag}: logged-out for you says how", rows and "ytm login" in rows[0], str(rows))

        # a long account name: shortened to what fits, ✕ stays on screen
        app.show_account({"logged_in": True, "account": "a very long synthetic account name for testing " * 2}); await pilot.pause()
        shown = text(app, "#acct")
        check(f"{tag}: long name cut to fit", cell_len(shown) <= acct.size.width and not overflow(app, w, h)
              and (acct.size.width < 6 or shown.endswith("…")), repr(shown))

        # nothing past the edge, on every view
        for key in "12345":
            if key == "2": await pilot.press("2"); await pilot.press("escape"); await pilot.press("2")
            else: await pilot.press(key)
            await settle(app, pilot)
            bad = overflow(app, w, h)
            check(f"{tag}: view {sw.current} fits", not bad, "; ".join(bad[:4]))
            if key == "2": await pilot.press("escape")
        await pilot.press("q")


def live_layer():
    """the real live.apply into the scratch dir: the look caps the shader's energy, the old call shape still works"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "live"))
    import live as L
    L.ghostty_pid = lambda: None   # never signal a real terminal
    os.environ["YTM_LIVE"] = "1"
    try:
        energy = lambda: float(next(l for l in (L.LIVE / "alive.glsl").read_text().splitlines() if "const float ENERGY" in l).split("=")[1].split(";")[0])
        check("live: lives in scratch", str(L.LIVE).startswith(SCRATCH), str(L.LIVE))
        for look, want in (("paper", 0.15), ("aurora", 0.3), ("tide", 0.3), ("stars", 0.4), ("rain", 0.4), ("ember", 0.5), ("neon", 0.7), ("dusk", 0.7)):
            L.apply(None, None, True, 0.7, look=look); check(f"live: {look} energy {want}", abs(energy() - want) < 1e-6, str(energy()))
        L.apply(None, None, True, 0.6); check("live: old signature still works", abs(energy() - 0.6) < 1e-6)
    finally:
        os.environ.pop("YTM_LIVE", None)


async def main():
    has = all(getattr(Y, n, None) for n in ("prefs_read", "prefs_write"))
    print(f"bin/ytm prefs_read/prefs_write: {'present' if has else 'missing (ui fallback only)'}")
    for size in ((100, 30), (52, 24), (50, 24), (48, 20)): await run(size, True)
    saved = {n: getattr(Y, n, None) for n in ("prefs_read", "prefs_write")}
    for n in saved: setattr(Y, n, None)   # the ui's own fallback writer, as if bin/ytm were older
    try: await run((80, 24), False)
    finally:
        for n, f in saved.items(): setattr(Y, n, f)
    live_layer()


asyncio.run(main())
print(f"\n{'all ok' if not fails else f'{fails} FAILED'}  (scratch {SCRATCH})")
sys.exit(1 if fails else 0)
