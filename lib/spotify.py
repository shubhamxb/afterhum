"""spotify for ytm: pkce login, your taste (top, recent, saved), mapped onto youtube music so ytm can play it.

no client secret on the mac (oauth authorization code + pkce, rfc 7636). you make one app at developer.spotify.com with
redirect http://127.0.0.1:8977/callback; its client id comes from $SPOTIFY_CLIENT_ID or the keychain (ytm-spotify / client_id).
tokens live in the keychain only (ytm-spotify / tokens), never a file, never git. urllib only, no new deps.
playback stays on youtube: spotify playback needs premium + their sdk.

  python spotify.py login | status | logout | client-id <id> | artists | top | recent | saved | match <top|recent|saved>
"""
import base64, hashlib, json, os, secrets, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
PORT = 8977
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
SCOPES = "user-top-read user-read-recently-played user-library-read"
SERVICE = os.environ.get("YTM_SPOTIFY_SERVICE", "ytm-spotify")  # tests use their own service name and delete it after
CAP = 50  # spotify's per-request max; every data call is one request
MATCH_CAP = 25  # one youtube music search per track, no more


class SpotifyError(Exception):
    pass


# pkce (rfc 7636): a random verifier, its sha256 as the challenge, both base64url without padding
def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def verifier():
    return b64url(secrets.token_bytes(64))  # 86 chars, inside the 43..128 the rfc allows

def challenge(v):
    return b64url(hashlib.sha256(v.encode("ascii")).digest())

def authorize_url(cid, chal, state, redirect=REDIRECT, scopes=SCOPES):
    q = {"client_id": cid, "response_type": "code", "redirect_uri": redirect, "code_challenge_method": "S256",
         "code_challenge": chal, "state": state, "scope": scopes}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


# keychain: the `security` cli. values go in base64 over stdin (security -i), so they never sit in argv or need quoting
def kc_get(account, service=None):
    r = subprocess.run(["security", "find-generic-password", "-s", service or SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True)
    if r.returncode: return None
    try: return base64.b64decode(r.stdout.strip()).decode()
    except Exception: return None

def kc_set(account, value, service=None):
    v = base64.b64encode(value.encode()).decode()  # base64 has no quotes or spaces
    cmd = f"add-generic-password -U -s {service or SERVICE} -a {account} -w {v}\n"
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True)
    if r.returncode: raise SpotifyError(f"keychain write failed: {r.stderr.strip()[:200]}")

def kc_del(account, service=None):
    subprocess.run(["security", "delete-generic-password", "-s", service or SERVICE, "-a", account], capture_output=True)


# transport: one function, swapped for a fake in the tests. returns (status, parsed json or {})
def urllib_transport(method, url, data=None, headers=None, timeout=15):
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, hdr = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, status, hdr = e.read(), e.code, e.headers
    except urllib.error.URLError as e:
        raise SpotifyError(f"spotify is out of reach: {e.reason}")
    try: out = json.loads(raw) if raw else {}
    except ValueError: out = {}
    if status == 429 and isinstance(out, dict): out["_retry_after"] = (hdr or {}).get("Retry-After", "?")
    return status, out

TRANSPORT = urllib_transport
CLOCK = time.time


def client_id():
    return os.environ.get("SPOTIFY_CLIENT_ID") or kc_get("client_id")

def load_tokens():
    s = kc_get("tokens")
    try: return json.loads(s) if s else None
    except ValueError: return None

def save_tokens(t):
    kc_set("tokens", json.dumps(t, separators=(",", ":")))

def _stamp(resp, old=None):
    """token endpoint reply → what we keep. spotify may leave out a new refresh token: keep the old one then"""
    if "access_token" not in resp: raise SpotifyError(f"spotify said no: {resp.get('error_description') or resp.get('error') or resp}")
    return {"access_token": resp["access_token"], "refresh_token": resp.get("refresh_token") or (old or {}).get("refresh_token"),
            "expires_at": int(CLOCK()) + int(resp.get("expires_in", 3600)), "scope": resp.get("scope", (old or {}).get("scope", ""))}

def exchange(code, ver, cid, redirect=REDIRECT):
    st, r = TRANSPORT("POST", TOKEN_URL, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                          "client_id": cid, "code_verifier": ver},
                      {"Content-Type": "application/x-www-form-urlencoded"})
    return _stamp(r)

def refresh(tok, cid):
    if not tok.get("refresh_token"): raise SpotifyError("no refresh token: run ytm spotify login")
    st, r = TRANSPORT("POST", TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid},
                      {"Content-Type": "application/x-www-form-urlencoded"})
    if st == 400 and r.get("error") == "invalid_grant": raise SpotifyError("spotify revoked the login: run ytm spotify login")
    return _stamp(r, tok)

def access(load=None, save=None, cid=None):
    """a valid access token, refreshed (and saved) when it is within a minute of expiring"""
    load, save = load or load_tokens, save or save_tokens
    tok = load()
    if not tok: raise SpotifyError("not logged in to spotify: run ytm spotify login")
    if tok.get("expires_at", 0) - 60 <= CLOCK():
        tok = refresh(tok, cid or client_id()); save(tok)
    return tok["access_token"]


# the one-shot callback server: 127.0.0.1 only, gone after the right callback or the timeout
PAGE = ("<!doctype html><meta charset=utf-8><title>ytm</title><body style='background:#0b0b12;color:#cfd2ff;"
        "font:16px -apple-system,sans-serif;display:grid;place-items:center;height:90vh'><p>{}</p>")

def wait_for_code(state, port=PORT, timeout=120, ready=None):
    """block until spotify redirects back with our state; returns the code. a wrong state is refused and we keep waiting"""
    got = {}
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
            one = lambda k: (q.get(k) or [None])[0]
            if u.path != "/callback": return self._say(404, "nothing here.")
            if one("state") != state: return self._say(400, "that callback isn't ours (state mismatch). ignored.")
            if one("error"): got["error"] = one("error"); return self._say(400, f"spotify said: {one('error')}. back to the terminal.")
            if not one("code"): return self._say(400, "no code in that callback.")
            got["code"] = one("code"); self._say(200, "spotify is linked. you can close this tab.")
        def _say(self, status, msg):
            b = PAGE.format(msg).encode()
            self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    srv = HTTPServer(("127.0.0.1", port), H)
    srv.timeout = 1
    try:
        if ready: ready(srv.server_address[1])
        end = time.monotonic() + timeout
        while not got and time.monotonic() < end: srv.handle_request()
    finally:
        srv.server_close()
    if "error" in got: raise SpotifyError(f"login refused: {got['error']}")
    if "code" not in got: raise SpotifyError(f"no callback within {timeout} s")
    return got["code"]

def login(open_browser=webbrowser.open, timeout=120):
    cid = client_id()
    if not cid:
        raise SpotifyError("no spotify client id. make an app at developer.spotify.com (redirect " + REDIRECT +
                           "), then: ytm spotify client-id <id>  (or set SPOTIFY_CLIENT_ID)")
    ver, state = verifier(), secrets.token_urlsafe(24)
    url = authorize_url(cid, challenge(ver), state)
    def opened(_port):
        print("opening spotify in the browser… (if nothing opens:", url, ")", file=sys.stderr)
        open_browser(url)
    code = wait_for_code(state, PORT, timeout, ready=opened)
    tok = exchange(code, ver, cid); save_tokens(tok)
    return tok

def logout():
    kc_del("tokens")

def status():
    tok = load_tokens()
    out = {"client_id": bool(client_id()), "logged_in": bool(tok), "redirect": REDIRECT}
    if tok:
        out["expires_in"] = int(tok.get("expires_at", 0) - CLOCK()); out["scope"] = tok.get("scope", "")
        out["refreshable"] = bool(tok.get("refresh_token"))
    return out


# data: one capped request each
def get(path, params=None, _retry=True):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    st, r = TRANSPORT("GET", url, None, {"Authorization": "Bearer " + access()})
    if st == 401 and _retry:  # expired early or revoked: refresh once, try again
        tok = load_tokens()
        if tok: tok["expires_at"] = 0; save_tokens(tok)
        return get(path, params, _retry=False)
    if st == 429: raise SpotifyError(f"spotify says slow down: retry in {r.get('_retry_after', '?')} s")
    if st >= 400:
        e = r.get("error"); raise SpotifyError(f"spotify {st}: {e.get('message') if isinstance(e, dict) else e or r}")
    return r

def _n(n): return max(1, min(int(n), CAP))

def track(t):
    """spotify track → the small shape ytm wants"""
    return {"id": t.get("id"), "title": t.get("name", "?"), "artists": [a.get("name") for a in t.get("artists") or [] if a.get("name")],
            "album": (t.get("album") or {}).get("name", ""), "duration": (t.get("duration_ms") or 0) // 1000, "isrc": (t.get("external_ids") or {}).get("isrc")}

def top_artists(n=20, span="medium_term"):
    r = get("/me/top/artists", {"limit": _n(n), "time_range": span})
    return [{"id": a.get("id"), "name": a.get("name"), "genres": a.get("genres") or []} for a in r.get("items") or []]

def top_tracks(n=20, span="medium_term"):
    return [track(t) for t in get("/me/top/tracks", {"limit": _n(n), "time_range": span}).get("items") or []]

def recent(n=20):
    return [dict(track(i.get("track") or {}), played_at=i.get("played_at")) for i in get("/me/player/recently-played", {"limit": _n(n)}).get("items") or []]

def saved(n=20):
    return [dict(track(i.get("track") or {}), added_at=i.get("added_at")) for i in get("/me/tracks", {"limit": _n(n)}).get("items") or []]


def _yt_search(q):
    from ytmusicapi import YTMusic
    return YTMusic().search(q, filter="songs", limit=5)

def _secs(r):
    if r.get("duration_seconds"): return int(r["duration_seconds"])
    try:
        p = [int(x) for x in str(r.get("duration", "")).split(":")]
        return sum(v * 60 ** i for i, v in enumerate(reversed(p)))
    except ValueError: return 0

def match_on_youtube(tracks, search=None, cap=MATCH_CAP):
    """spotify tracks → ytmusic videoIds, one search each (capped). prefers a result within 10 s of spotify's length"""
    search = search or _yt_search
    out = []
    for t in list(tracks)[:cap]:
        q = " ".join(t.get("artists") or []) + " " + t.get("title", "")
        try: res = [r for r in search(q.strip()) or [] if r.get("videoId")]
        except Exception: res = []
        near = [r for r in res if t.get("duration") and abs(_secs(r) - t["duration"]) <= 10]
        best = (near or res or [None])[0]
        out.append(dict(t, videoId=best["videoId"] if best else None))
    return out


def main(argv):
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "login":
            tok = login(); print(f"spotify linked. scopes: {tok.get('scope') or SCOPES}")
        elif cmd == "logout": logout(); print("spotify unlinked (tokens gone from the keychain).")
        elif cmd == "client-id":
            if len(argv) < 2: raise SpotifyError("usage: client-id <id>")
            kc_set("client_id", argv[1]); print("client id saved to the keychain.")
        elif cmd == "status": print(json.dumps(status()))
        elif cmd in ("artists", "top", "recent", "saved"):
            f = {"artists": top_artists, "top": top_tracks, "recent": recent, "saved": saved}[cmd]
            if len(argv) > 1 and not argv[1].isdigit(): print(f"usage: ytm spotify {cmd} [n]", file=sys.stderr); return 2
            print(json.dumps(f(int(argv[1]) if len(argv) > 1 else 20), ensure_ascii=False))
        elif cmd == "match":
            src = {"top": top_tracks, "recent": recent, "saved": saved}
            f = src.get(argv[1] if len(argv) > 1 else "top")
            if f is None: print("usage: ytm spotify match [top|recent|saved]", file=sys.stderr); return 2   # a usage line, not a KeyError
            print(json.dumps(match_on_youtube(f(MATCH_CAP)), ensure_ascii=False))
        else: print(__doc__.strip()); return 2
    except SpotifyError as e:
        print("spotify:", e, file=sys.stderr); return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
