"""ytm login: youtube music in an instant, no copy-pasting headers.

where the session comes from (login(source)):
  auto              vivaldi, chrome, brave, safari, firefox, then the youtube music app: the first one signed in wins
  vivaldi[:profile] one browser; profile is the folder ("Profile 3") or the name you gave it ("MAIN"). default: the last used
  app               the th-ch "YouTube Music" electron app's own session (~/Library/Application Support/YouTube Music)

the browser's cookies (via yt-dlp's cookie reader) become ytmusicapi browser-auth headers. they live in the macOS keychain
(service ytm-ytmusic, account browser), never on disk; DATA/auth.json only keeps source, account name and saved_at.

the one-time keychain prompt: chromium browsers lock their cookies with a key in the keychain ("Vivaldi Safe Storage",
"Chrome Safe Storage", ...). the first login from a browser, macOS asks: "security wants to use your confidential information
stored in 'Vivaldi Safe Storage' in your keychain". type the mac password and press "Allow", NOT "Always Allow": always
would put the security tool on that key's access list for good, so any program running as you could silently decrypt every
cookie and saved password in that browser (login review). login is rare: one prompt per login is the right price.
reading ytm's own item (ytm-ytmusic) never prompts: the security tool made it, so it may read it.
safari needs full disk access for the terminal (its cookies sit in a sandboxed container); firefox needs no keychain at all.
a login lasts as long as the browser session does (google rotates cookies; logging in again just re-reads the browser).
"""
import hashlib, json, os, shutil, sqlite3, subprocess, tempfile, time
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("YTM_DATA", HOME / ".local/share/ytm"))
META = DATA / "auth.json"                                    # no secrets: source, account, saved_at
SERVICE = os.environ.get("YTM_AUTH_SERVICE", "ytm-ytmusic")  # tests override to ytm-test-auth
ACCOUNT = "browser"
ORIGIN = "https://music.youtube.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
AUTO = ["vivaldi", "chrome", "brave", "safari", "firefox", "app"]
APP_DIR = HOME / "Library/Application Support/YouTube Music"
SUPPORT = HOME / "Library/Application Support"
CHROMIUM = {"vivaldi": SUPPORT / "Vivaldi", "chrome": SUPPORT / "Google/Chrome",
            "brave": SUPPORT / "BraveSoftware/Brave-Browser"}
NEEDED = ("SAPISID", "__Secure-3PAPISID")                    # either one signs requests; ytmusicapi reads the 3P one

_CLIENT = None  # authed YTMusic, cached per process


class AuthError(Exception):
    pass


class _Quiet:  # yt-dlp's cookie reader is chatty; ytm says one line of its own instead
    def debug(self, *a, **k): pass
    info = warning = error = debug


# ---- cookies -> ytmusicapi headers (pure, no network, no keychain) ----

def headers_from_cookies(cookies, authuser="0", now=None):
    """cookies: iterable of (domain, name, value) tuples or cookiejar cookies -> ytmusicapi browser headers (dict)."""
    jar = {}
    for c in cookies:
        dom, name, value = (c.domain, c.name, c.value) if hasattr(c, "domain") else c
        if not value or not dom.lstrip(".").endswith("youtube.com"):
            continue
        if name not in jar or dom == ".youtube.com":  # the site-wide cookie beats a subdomain's
            jar[name] = value
    sapisid = jar.get("__Secure-3PAPISID") or jar.get("SAPISID")
    if not sapisid:
        raise AuthError("not signed in there: no youtube.com session cookie")
    jar.setdefault("__Secure-3PAPISID", sapisid)
    ts = str(int(now if now is not None else time.time()))
    mac = hashlib.sha1(f"{ts} {sapisid} {ORIGIN}".encode()).hexdigest()
    return {"accept": "*/*", "content-type": "application/json", "origin": ORIGIN, "x-origin": ORIGIN,
            "user-agent": UA, "x-goog-authuser": str(authuser),
            "authorization": f"SAPISIDHASH {ts}_{mac}",
            "cookie": "; ".join(f"{k}={v}" for k, v in sorted(jar.items()))}


# ---- reading a browser's cookies ----

def _signed_in(db):
    """a copy of a chromium cookies db: is there a youtube session cookie? no keychain, no decrypting, no values read."""
    with tempfile.TemporaryDirectory(prefix="ytm-auth-") as tmp:
        cp = Path(tmp) / "c.db"
        shutil.copyfile(db, cp)
        con = sqlite3.connect(cp)
        try:
            q = ("select count(*) from cookies where host_key like '%youtube.com' "
                 f"and name in ({','.join('?' * len(NEEDED))})")
            return con.execute(q, NEEDED).fetchone()[0] > 0
        except sqlite3.Error:
            return False
        finally:
            con.close()


def _profile_dir(browser, profile):
    """resolve 'Profile 3' or a display name ('MAIN') to a profile folder under the chromium browser dir."""
    root = CHROMIUM[browser]
    if profile:
        if (root / profile).is_dir():
            return root / profile
        # match the name the person gave the profile (profile.name in its Preferences)
        for p in sorted(root.glob("*/Preferences")):
            try:
                if json.loads(p.read_text()).get("profile", {}).get("name") == profile:
                    return p.parent
            except (OSError, ValueError):
                pass
        raise AuthError(f"no {browser} profile '{profile}'")
    # default: the profile the browser used last, else Default
    try:
        last = json.loads((root / "Local State").read_text()).get("profile", {}).get("last_used")
        if last and (root / last).is_dir():
            return root / last
    except (OSError, ValueError):
        pass
    return root / "Default"


def _cookies_chromium(browser, profile=None):
    """yt-dlp decrypts the chromium cookie jar (may prompt for the keychain once). returns a cookiejar."""
    from yt_dlp.cookies import extract_cookies_from_browser
    folder = _profile_dir(browser, profile)
    # yt-dlp takes a profile as a path when it is one; '/' marks a folder vs a name
    jar = extract_cookies_from_browser(browser, profile=str(folder), logger=_Quiet())
    # the session cookie is there by name but came back empty: the keychain said no (denied, closed, or never answered:
    # the app's connect hit exactly this and only said "not signed in"). say what actually happened
    got = any(c.name in NEEDED and c.value for c in jar if "youtube.com" in (c.domain or ""))
    if not got and _signed_in(folder / "Cookies"):
        raise AuthError(f"couldn't unlock {browser.title()}'s cookies: the macOS keychain prompt was denied or closed. "
                        f"run `ytm login` in a terminal and press Allow (not Always Allow) when macOS asks")
    return jar


def _cookies_firefox():
    from yt_dlp.cookies import extract_cookies_from_browser
    return extract_cookies_from_browser("firefox", logger=_Quiet())


def _cookies_safari():
    from yt_dlp.cookies import extract_cookies_from_browser
    return extract_cookies_from_browser("safari", logger=_Quiet())


def _cookies_app():
    """the th-ch electron app keeps a chromium cookie db at APP_DIR/Cookies, locked with the 'Chromium Safe Storage' key.

    yt-dlp can decrypt it: point its chrome reader at the db by giving the app dir as the profile path, and tell it the
    keyring is 'Chromium' (the default electron safe-storage name). the app itself has no "Safe Storage" keychain item,
    so this only works once the person has logged into the app; investigated 2026-10-23, structure confirmed, values never read.
    """
    from yt_dlp.cookies import extract_cookies_from_browser
    if not (APP_DIR / "Cookies").is_file():
        raise AuthError("the youtube music app isn't installed, or has never been opened")
    return extract_cookies_from_browser("chrome", profile=str(APP_DIR), keyring="Chromium", logger=_Quiet())


def _cookies(source):
    """source -> cookiejar. source is 'auto', a browser ('vivaldi'), 'browser:profile', or 'app'."""
    name, _, profile = source.partition(":")
    name = name.lower()
    if name in CHROMIUM:
        return _cookies_chromium(name, profile or None)
    if name == "firefox":
        return _cookies_firefox()
    if name == "safari":
        return _cookies_safari()
    if name == "app":
        return _cookies_app()
    raise AuthError(f"unknown login source '{source}'")


def _candidates():
    """auto order, skipping what obviously isn't there or isn't signed in (cheap, no keychain). yields source names."""
    for name in AUTO:
        if name in CHROMIUM:
            root = CHROMIUM[name]
            if not root.is_dir():
                continue
            try:
                db = _profile_dir(name, None) / "Cookies"
            except AuthError:
                continue
            if db.is_file() and _signed_in(db):
                yield name
        elif name == "app":
            if (APP_DIR / "Cookies").is_file() and _signed_in(APP_DIR / "Cookies"):
                yield name
        elif name == "safari":
            if (HOME / "Library/Containers/com.apple.Safari/Data/Library/Cookies").exists() \
                    or (HOME / "Library/Cookies/Cookies.binarycookies").exists():
                yield name
        elif name == "firefox":
            if (SUPPORT / "Firefox/Profiles").is_dir():
                yield name


# ---- keychain (the macOS 'security' cli; headers never touch disk) ----

CHUNK = 3000   # `security -i` reads one line of ~4 KB at most; a real cookie blob is bigger, so it goes in slices


def _kc(*args, stdin=None):
    return subprocess.run(["security", *args], input=stdin, capture_output=True, text=True)


def _kc_write(headers):
    """the headers go over stdin (security -i), base64, in slices: never in argv, where `ps` would show the cookies."""
    import base64, zlib
    v = base64.b64encode(zlib.compress(json.dumps(headers).encode(), 9)).decode()   # base64: no quotes or spaces to escape
    parts = [v[i:i + CHUNK] for i in range(0, len(v), CHUNK)] or [""]
    lines = [f"add-generic-password -U -s {SERVICE} -a {ACCOUNT if i == 0 else f'{ACCOUNT}.{i}'} -w "
             + (f"z1:{len(parts)}:{p}" if i == 0 else p) for i, p in enumerate(parts)]
    r = _kc("-i", stdin="\n".join(lines) + "\n")
    if r.returncode: raise AuthError(f"keychain write failed: {r.stderr.strip()[:200]}")
    _kc_delete(start=len(parts))   # slices a longer old session left behind


def _kc_get(account):
    r = _kc("find-generic-password", "-s", SERVICE, "-a", account, "-w")
    return r.stdout.strip() if r.returncode == 0 else None


def _kc_read():
    import base64, zlib
    head = _kc_get(ACCOUNT)
    if head is None: return None
    try:
        if not head.startswith("z1:"): return json.loads(head)   # a 0.8.0 item: plain json, still readable
        n, _, v = head[3:].partition(":")
        for i in range(1, int(n)):
            p = _kc_get(f"{ACCOUNT}.{i}")
            if p is None: return None
            v += p
        return json.loads(zlib.decompress(base64.b64decode(v)))
    except (ValueError, zlib.error):
        return None


def _kc_delete(start=0):
    """drop the item (from slice `start` on). stops at the first slice that isn't there: at most a few processes."""
    for i in range(start, 64):
        if _kc("delete-generic-password", "-s", SERVICE, "-a", ACCOUNT if i == 0 else f"{ACCOUNT}.{i}").returncode and i: break


def _forget_cache():
    """DATA/cache is the account's (likes, home, history): another login, or none, must not see the last one's."""
    for p in (DATA / "cache").glob("*.json"):
        try: p.unlink()
        except OSError: pass


def _meta_write(source, account):
    DATA.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps({"source": source, "account": account, "saved_at": int(time.time())}))


# ---- the public api ----

def profiles():
    """every browser profile you could log in from, for a chooser: no keychain, no cookie values, nothing decrypted.
    [{source, browser, profile, name, accounts: [email…] (chrome-family profiles signed in to the browser itself), signed_in}]
    signed_in = a youtube session cookie exists there (by name only). vivaldi doesn't sign in to google at the browser level,
    so its profiles show their names (MAIN, dvin…) and the account is confirmed after login (the 'logged in as' line)."""
    out = []
    for browser, root in CHROMIUM.items():
        if not root.is_dir(): continue
        for pref in sorted(root.glob("*/Preferences")):
            d = pref.parent
            if d.name in ("System Profile", "Guest Profile") or not (d / "Cookies").is_file(): continue
            try: p = json.loads(pref.read_text())
            except (OSError, ValueError): p = {}
            emails = [a.get("email") for a in p.get("account_info") or [] if a.get("email")]
            try: signed = _signed_in(d / "Cookies")
            except Exception: signed = False
            out.append({"source": f"{browser}:{d.name}", "browser": browser, "profile": d.name,
                        "name": (p.get("profile") or {}).get("name") or d.name, "accounts": emails, "signed_in": signed})
    if (APP_DIR / "Cookies").is_file():
        try: signed = _signed_in(APP_DIR / "Cookies")
        except Exception: signed = False
        out.append({"source": "app", "browser": "app", "profile": "", "name": "the YouTube Music app", "accounts": [], "signed_in": signed})
    out.sort(key=lambda x: (not x["signed_in"], x["browser"] != "vivaldi", x["name"].lower()))
    return out


def login(source="auto", validate=True):
    """get a music.youtube.com session without copy-pasting, store the headers in the keychain. returns status()."""
    global _CLIENT
    tried = list(_candidates()) if source == "auto" else [source]
    if source == "auto" and not tried:
        raise AuthError("no signed-in browser or app found. open youtube music in a browser, sign in, then try again")
    last = None
    for src in tried:
        base, _, au = src.partition("#")   # "vivaldi:Profile 3#1": the second google account signed in to that profile
        try:
            headers = headers_from_cookies(_cookies(base), authuser=au or "0")
        except Exception as e:  # a locked keychain, a missing profile, no session there: try the next source
            last = e
            continue
        account = None
        if validate:
            try: account = _account_name(headers)  # one cheap authed call; raises if the cookies don't actually work
            except AuthError as e:                 # stale cookies in this browser: the next one may be signed in
                last = e
                continue
        _kc_write(headers)
        _meta_write(src, account)
        _forget_cache()
        _CLIENT = None
        return status()
    raise AuthError(f"couldn't log in from {', '.join(tried)}: {last}")


def _account_name(headers):
    """construct an authed client and ask who we are (one cheap request). returns the name, or raises AuthError."""
    from ytmusicapi import YTMusic
    try:
        info = YTMusic(auth=headers).get_account_info()
        return info.get("accountName")
    except Exception as e:
        raise AuthError(f"the session didn't authenticate: {e}") from e


def logout():
    """forget the session: clear the keychain item and the meta file. returns True if there was one."""
    global _CLIENT
    had = _kc_read() is not None
    _kc_delete()
    META.unlink(missing_ok=True)
    _forget_cache()
    remove_cookie_file()
    _CLIENT = None
    return had


def status():
    """-> {logged_in, source, account, saved_at}. cheap: reads meta + whether the keychain item exists, no network."""
    present = _kc_read() is not None
    meta = {}
    try:
        meta = json.loads(META.read_text())
    except (OSError, ValueError):
        pass
    return {"logged_in": present, "source": meta.get("source"),
            "account": meta.get("account"), "saved_at": meta.get("saved_at")}


RUN = DATA / "run"
COOKIE_FILE = RUN / "yt.cookies"


def write_cookie_file():
    """-> path of a netscape cookie file for yt-dlp (so mpv plays the signed-in, premium stream), or None if not logged in.
    by design: a 0600 file in a 0700 dir, written when the player starts, removed by remove_cookie_file()
    (ytm stop, ytm logout). never in argv. it's the one place the session touches disk; the keychain stays the source."""
    headers = _kc_read()
    if not headers or not headers.get("cookie"): return None
    RUN.mkdir(parents=True, exist_ok=True)
    os.chmod(RUN, 0o700)
    lines = ["# Netscape HTTP Cookie File"]
    exp = str(int(time.time()) + 86400 * 30)
    for pair in headers["cookie"].split("; "):
        name, eq, value = pair.partition("=")
        if not eq or not name: continue
        lines.append("\t".join([".youtube.com", "TRUE", "/", "TRUE", exp, name, value]))
    tmp = COOKIE_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: f.write("\n".join(lines) + "\n")
    os.replace(tmp, COOKIE_FILE)
    return str(COOKIE_FILE)


def remove_cookie_file():
    for p in (COOKIE_FILE, COOKIE_FILE.with_suffix(".tmp")):
        try: p.unlink()
        except OSError: pass


def client():
    """-> an authed YTMusic from the stored headers, or None if not logged in. cached per process."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    headers = _kc_read()
    if not headers:
        return None
    from ytmusicapi import YTMusic
    _CLIENT = YTMusic(auth=headers)
    return _CLIENT


if __name__ == "__main__":  # tiny cli so `python lib/auth.py status|login [src]|logout` works standalone
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "login":
        print(json.dumps(login(sys.argv[2] if len(sys.argv) > 2 else "auto"), indent=2))
    elif cmd == "logout":
        print("logged out" if logout() else "wasn't logged in")
    else:
        print(json.dumps(status(), indent=2))
