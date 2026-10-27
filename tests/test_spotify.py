"""ytm spotify: pkce, authorize url, token refresh, the callback server, matching. offline, no real tokens, nothing personal.
run: python3 tests/test_spotify.py   (stdlib only; the keychain check uses a throwaway service and deletes it)"""
import importlib.util, json, os, socket, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

os.environ["YTM_DATA"] = tempfile.mkdtemp(prefix="ytm-test-")
os.environ["YTM_SPOTIFY_SERVICE"] = f"ytm-spotify-test-{os.getpid()}"  # never the real one
os.environ.pop("SPOTIFY_CLIENT_ID", None)
spec = importlib.util.spec_from_file_location("spotify", ROOT / "lib/spotify.py")
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

# 1. pkce: the rfc 7636 appendix b example, and a fresh verifier's shape
check("rfc 7636 challenge", S.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
v = S.verifier()
check("verifier is 43..128 unreserved chars", 43 <= len(v) <= 128 and all(c.isalnum() or c in "-._~" for c in v), f"{len(v)} chars")
check("verifiers differ each time", S.verifier() != v)

# 2. authorize url
u = urllib.parse.urlparse(S.authorize_url("cid123", "chal", "st8"))
q = dict(urllib.parse.parse_qsl(u.query))
check("authorize host + path", (u.scheme, u.netloc, u.path) == ("https", "accounts.spotify.com", "/authorize"))
check("authorize params", q == {"client_id": "cid123", "response_type": "code", "redirect_uri": "http://127.0.0.1:8977/callback",
                                 "code_challenge_method": "S256", "code_challenge": "chal", "state": "st8",
                                 "scope": "user-top-read user-read-recently-played user-library-read"}, str(q))

# 3. refresh with a fake transport and a fake clock
calls = []
def fake(method, url, data=None, headers=None, timeout=15):
    calls.append((method, url, data, headers))
    if url == S.TOKEN_URL and data.get("grant_type") == "refresh_token":
        if data["refresh_token"] == "dead": return 400, {"error": "invalid_grant"}
        return 200, {"access_token": "new-access", "expires_in": 3600, "scope": "user-top-read"}  # no new refresh token, like spotify often
    if url == S.TOKEN_URL: return 200, {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600, "scope": S.SCOPES}
    if headers.get("Authorization") == "Bearer stale": return 401, {"error": {"status": 401, "message": "expired"}}
    return 200, {"items": [{"id": "t1", "name": "Song", "artists": [{"name": "Band"}], "album": {"name": "LP"}, "duration_ms": 201000}]}
S.TRANSPORT = fake; S.CLOCK = lambda: 1_000_000
store = {}
S.load_tokens = lambda: store.get("t"); S.save_tokens = lambda t: store.__setitem__("t", dict(t)); S.client_id = lambda: "cid123"

store["t"] = {"access_token": "fresh", "refresh_token": "r1", "expires_at": 1_000_000 + 600}
check("a fresh token is used as is", S.access() == "fresh" and not calls)
store["t"] = {"access_token": "old", "refresh_token": "r1", "expires_at": 1_000_000 + 30}
a = S.access(); t = store["t"]
check("a token within a minute of expiry gets refreshed", a == "new-access" and calls[-1][2]["grant_type"] == "refresh_token")
check("refresh sends the client id, no secret", calls[-1][2].get("client_id") == "cid123" and "client_secret" not in calls[-1][2])
check("refresh keeps the old refresh token when none comes back", t["refresh_token"] == "r1" and t["expires_at"] == 1_003_600)
store["t"] = {"access_token": "old", "refresh_token": "dead", "expires_at": 0}
try: S.access(); check("revoked refresh says log in again", False)
except S.SpotifyError as e: check("revoked refresh says log in again", "login" in str(e), str(e))
store.clear()
try: S.access(); check("no tokens says log in", False)
except S.SpotifyError as e: check("no tokens says log in", "login" in str(e))

tok = S.exchange("code1", "ver1", "cid123")
check("code exchange posts verifier + redirect", calls[-1][2]["code_verifier"] == "ver1" and calls[-1][2]["redirect_uri"] == S.REDIRECT and tok["refresh_token"] == "r1")

# 4. data calls: capped, one request, 401 → refresh once and retry
store["t"] = {"access_token": "ok", "refresh_token": "r1", "expires_at": 2_000_000}
n0 = len(calls); tt = S.top_tracks(500)
check("top tracks: one request, limit capped at 50", len(calls) - n0 == 1 and "limit=50" in calls[-1][1], calls[-1][1])
check("track shape", tt == [{"id": "t1", "title": "Song", "artists": ["Band"], "album": "LP", "duration": 201, "isrc": None}], str(tt))
store["t"] = {"access_token": "stale", "refresh_token": "r1", "expires_at": 2_000_000}
r = S.saved(5)
check("a 401 refreshes once and retries", r and store["t"]["access_token"] == "new-access")

# 5. matching: one search each, capped at 25, nearest length wins
searched = []
def ysearch(q):
    searched.append(q)
    return [{"videoId": None}, {"videoId": "long", "duration": "9:59"}, {"videoId": "near", "duration_seconds": 205}]
m = S.match_on_youtube([{"title": f"s{i}", "artists": ["a"], "duration": 201} for i in range(40)], search=ysearch)
check("match caps at 25 searches", len(m) == 25 and len(searched) == 25)
check("match prefers the result near spotify's length", m[0]["videoId"] == "near" and searched[0] == "a s0")
check("no result → videoId None", S.match_on_youtube([{"title": "x", "artists": []}], search=lambda q: [])[0]["videoId"] is None)

# 6. the callback server, on a random 127.0.0.1 port
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
def hit(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r: return r.status
    except urllib.error.HTTPError as e: return e.code

port = free_port(); res = {}; up = threading.Event()
th = threading.Thread(target=lambda: res.update(code=S.wait_for_code("good", port, timeout=10, ready=lambda p: up.set())), daemon=True)
th.start(); up.wait(3)
s1 = hit(port, "/callback?code=evil&state=bad"); s0 = hit(port, "/favicon.ico"); s2 = hit(port, "/callback?code=abc&state=good")
th.join(5)
check("wrong state is refused (400)", s1 == 400)
check("other paths 404", s0 == 404)
check("right state returns the code, server exits", s2 == 200 and res.get("code") == "abc" and not th.is_alive(), str(res))
try: socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); check("port is closed after", False)
except OSError: check("port is closed after", True)

port = free_port(); err = {}
def run():
    try: S.wait_for_code("good", port, timeout=10, ready=lambda p: up.set())
    except S.SpotifyError as e: err["e"] = str(e)
up.clear(); th = threading.Thread(target=run, daemon=True); th.start(); up.wait(3)
hit(port, "/callback?error=access_denied&state=good"); th.join(5)
check("a denied login raises", "access_denied" in err.get("e", ""), err.get("e", ""))
t0 = time.monotonic()
try: S.wait_for_code("good", free_port(), timeout=1); check("timeout raises", False)
except S.SpotifyError: check("timeout raises", time.monotonic() - t0 < 4)
src = (ROOT / "lib/spotify.py").read_text()
check("server binds 127.0.0.1 only", 'HTTPServer(("127.0.0.1", port)' in src and "0.0.0.0" not in src)

# 7. keychain round trip under the throwaway service, then gone
spec2 = importlib.util.spec_from_file_location("spotify2", ROOT / "lib/spotify.py")
K = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(K)
val = json.dumps({"access_token": "x y \"quoted\" 'z'", "n": 1})
try:
    K.kc_set("tokens", val)
    check("keychain round trip (test service)", K.kc_get("tokens") == val and K.load_tokens()["n"] == 1)
    K.logout(); check("logout removes the tokens", K.kc_get("tokens") is None and K.status()["logged_in"] is False)
finally:
    K.kc_del("tokens"); K.kc_del("client_id")

# 8. bad words to the cli: a usage line and 2, never a traceback (and no request: they fail before any call)
import contextlib, io
for argv in (["match", "nope"], ["top", "abc"], ["recent", "x"]):
    err = io.StringIO()
    with contextlib.redirect_stderr(err): rc = K.main(argv)
    check(f"spotify {' '.join(argv)} → usage, rc 2", rc == 2 and "usage" in err.getvalue(), err.getvalue().strip())

print(f"\n{'all good' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
