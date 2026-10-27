"""ytm auth: offline, fast, light. no browser, no network, fake cookies only; keychain under a test service it deletes.
run: python3 tests/test_auth.py"""
import importlib.machinery, importlib.util, os, subprocess, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-test-")
os.environ["YTM_DATA"] = SCRATCH
os.environ["YTM_AUTH_SERVICE"] = "ytm-test-auth"  # never touch the real ytm-ytmusic item
LIB = ROOT / "lib/auth.py"
l = importlib.machinery.SourceFileLoader("ytmauth", str(LIB))
s = importlib.util.spec_from_loader("ytmauth", l)
A = importlib.util.module_from_spec(s)
l.exec_module(A)

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

# a fake cookie jar: the two youtube session cookies plus noise on another domain
FAKE = [(".youtube.com", "__Secure-3PAPISID", "abc123SAPISID"),
        (".youtube.com", "SID", "sid-value"),
        (".youtube.com", "SAPISID", "abc123SAPISID"),
        ("accounts.google.com", "GAPS", "ignored"),
        (".example.com", "junk", "nope")]

# 1. headers: built correctly from a fake jar
h = A.headers_from_cookies(FAKE, authuser="0", now=1700000000)
check("origin is music.youtube.com", h["origin"] == "https://music.youtube.com")
check("x-goog-authuser set", h["x-goog-authuser"] == "0")
check("user-agent present", h["user-agent"].startswith("Mozilla/"))
check("authorization is a SAPISIDHASH", h["authorization"].startswith("SAPISIDHASH 1700000000_"))
import hashlib
want = hashlib.sha1(f"1700000000 abc123SAPISID https://music.youtube.com".encode()).hexdigest()
check("SAPISIDHASH signs the 3PAPISID over the origin", h["authorization"].endswith(want), want[:10])
check("cookie header carries 3PAPISID", "__Secure-3PAPISID=abc123SAPISID" in h["cookie"])
check("cookie header drops the non-youtube cookie", "junk" not in h["cookie"])
check("cookie header keeps the youtube SID", "SID=sid-value" in h["cookie"])

# 2. ytmusicapi actually accepts these headers as browser auth (offline: construct only, no request)
from ytmusicapi.auth.auth_parse import parse_auth_str, determine_auth_type
from ytmusicapi.auth.types import AuthType
parsed, _ = parse_auth_str(h)
check("ytmusicapi reads them as BROWSER auth", determine_auth_type(parsed) == AuthType.BROWSER)

# 3. only SAPISID present (no 3P): still works, and 3P is filled in from it
h2 = A.headers_from_cookies([(".youtube.com", "SAPISID", "onlythis")], now=1)
check("SAPISID alone is enough", "__Secure-3PAPISID=onlythis" in h2["cookie"])

# 4. no session cookie -> a clear error, no headers
try:
    A.headers_from_cookies([(".youtube.com", "VISITOR_INFO1_LIVE", "x")])
    check("no-session jar raises", False)
except A.AuthError:
    check("no-session jar raises AuthError", True)

# 5. keychain roundtrip under the test service, then gone
A._kc_delete()
check("status is logged out before any write", A.status()["logged_in"] is False)
A._kc_write(h)
A._meta_write("vivaldi", "Test User")
back = A._kc_read()
check("keychain roundtrip returns the same headers", back == h)
st = A.status()
check("status reports logged in", st["logged_in"] is True)
check("status reports the source", st["source"] == "vivaldi")
check("status reports the account", st["account"] == "Test User")
check("status has a saved_at timestamp", isinstance(st["saved_at"], int))

# 6. client() builds a YTMusic from the stored headers without a network call (cached per process)
c1 = A.client()
check("client() returns an authed client", c1 is not None and c1.auth_type == AuthType.BROWSER)
check("client() is cached per process", A.client() is c1)

# 7. logout clears the keychain and the meta, and client() goes back to None
had = A.logout()
check("logout reports there was a session", had is True)
check("keychain item is gone after logout", A._kc_read() is None)
check("status is logged out after logout", A.status()["logged_in"] is False)
check("client() is None after logout", A.client() is None)

# 8. status when absent: no meta file, nothing in the keychain
check("absent status is all-None/false", A.status() == {"logged_in": False, "source": None, "account": None, "saved_at": None})

# 9. a real cookie blob is bigger than one `security -i` line (~4 KB): it goes in slices and comes back whole
import secrets
big = dict(h, cookie="; ".join(f"C{i}={secrets.token_urlsafe(90)}" for i in range(120)))   # ~14 KB, barely compressible
A._kc_write(big)
check("a 14 KB session roundtrips through the keychain", A._kc_read() == big, f"{len(big['cookie'])} chars")
A._kc_write(h)
check("a smaller session after it reads back (old slices dropped)", A._kc_read() == h)
check("the old slices are gone", subprocess.run(["security", "find-generic-password", "-s", "ytm-test-auth", "-a", "browser.2"],
                                                 capture_output=True).returncode != 0)
import json as _j
subprocess.run(["security", "add-generic-password", "-U", "-s", "ytm-test-auth", "-a", "browser", "-w", _j.dumps(h)], capture_output=True)
check("a 0.8.0 plain-json item still reads", A._kc_read() == h)
check("the write path never puts the blob in argv", "-w\", blob" not in LIB.read_text() and '"-i"' in LIB.read_text())

# 10. login auto: stale cookies in the first browser don't stop it trying the next; a login drops the account cache
A._kc_delete()
calls = []
A._candidates = lambda: iter(["vivaldi", "chrome"])
A._cookies = lambda src: calls.append(src) or FAKE
def fake_name(hd):
    if calls[-1] == "vivaldi": raise A.AuthError("stale")
    return "Second"
A._account_name = fake_name
(Path(SCRATCH) / "cache").mkdir(exist_ok=True); (Path(SCRATCH) / "cache/liked-200.json").write_text("{}")
st = A.login("auto")
check("auto login moves past a browser whose session is stale", calls == ["vivaldi", "chrome"] and st["source"] == "chrome", str(calls))
check("login drops the previous account's cache", not list((Path(SCRATCH) / "cache").glob("*.json")))
(Path(SCRATCH) / "cache/home.json").write_text("{}")
A.logout()
check("logout drops the account cache", not list((Path(SCRATCH) / "cache").glob("*.json")))
A._account_name = lambda hd: (_ for _ in ()).throw(A.AuthError("stale"))
try: A.login("auto"); check("all-stale auto login raises", False)
except A.AuthError as e: check("all-stale auto login raises with the last error", "stale" in str(e), str(e)[:60])

# make sure the test keychain item is never left behind
A._kc_delete()
left = subprocess.run(["security", "find-generic-password", "-s", "ytm-test-auth"], capture_output=True)   # any account, slices too
check("no ytm-test-auth item left in the keychain", left.returncode != 0)

print(f"\n{'all passed' if not fails else str(fails) + ' FAILED'}")
raise SystemExit(1 if fails else 0)
