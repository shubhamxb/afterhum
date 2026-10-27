"""ytm analyze: memory, correctness, one-at-a-time, parking. offline (no network), scratch data only.
run: python3 tests/test_analyze.py
born from an early panic: the old analyser used ~15 GB per hour-long song and the stage ran several at once."""
import fcntl, importlib.machinery, importlib.util, json, os, resource, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

SCRATCH = tempfile.mkdtemp(prefix="ytm-test-")
os.environ["YTM_DATA"] = SCRATCH; os.environ["YTM_NO_REEXEC"] = "1"
l = importlib.machinery.SourceFileLoader("ytmcli", str(ROOT / "bin/ytm"))
s = importlib.util.spec_from_loader("ytmcli", l); Y = importlib.util.module_from_spec(s); l.exec_module(Y)
import numpy as np

fails = 0
def check(name, ok, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}  {detail}")

def clicks(minutes, bpm=120):
    sr = Y.SR; n = int(minutes * 60 * sr); x = (np.random.default_rng(1).standard_normal(n) * 0.02).astype(np.float32)
    step = int(sr * 60 / bpm)
    for i in range(0, n - 400, step): x[i:i + 400] += np.hanning(400).astype(np.float32) * 0.9
    return x

def peak_mb(code):
    """run a snippet in a fresh python and report its peak resident memory"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ, timeout=300)
    return float(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else float("inf"), r.stderr[-300:]

PRE = f"""import importlib.machinery, importlib.util, resource, numpy as np
l = importlib.machinery.SourceFileLoader("y", {str(ROOT / 'bin/ytm')!r}); s = importlib.util.spec_from_loader("y", l)
Y = importlib.util.module_from_spec(s); l.exec_module(Y)
sr = Y.SR
"""
# 1. memory: the capped real path (12 min in) and an hour fed straight in, each in its own process
t0 = time.time()
mb12, err = peak_mb(PRE + "x = np.random.default_rng(1).standard_normal(12*60*sr, dtype=np.float32)\nout, bpm, secs = Y.analyze_pcm(x)\nprint(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6)")
check("12-minute song stays under 300 MB peak", mb12 < 300, f"{mb12:.0f} MB in {time.time() - t0:.1f} s {err}")
t0 = time.time()
mb60, err = peak_mb(PRE + "x = np.random.default_rng(1).standard_normal(60*60*sr, dtype=np.float32)\nout, bpm, secs = Y.analyze_pcm(x)\nassert secs <= Y.MAX_SECONDS\nprint(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6)")
check("an hour-long mix is capped and stays under 600 MB (old code: ~15 GB)", mb60 < 600, f"{mb60:.0f} MB in {time.time() - t0:.1f} s {err}")

# 2. correctness: a 120 bpm click track
out, bpm, secs = Y.analyze_pcm(clicks(2, 120))
check("tempo of a 120 bpm click track", abs(bpm - 120) < 4, f"{bpm:.1f} bpm")
beats = int((out[:, 28] > 0).sum()); check("beat count ≈ 2 min × 120", abs(beats - 240) <= 8, f"{beats} beats")
check("29 columns, uint8", out.shape[1] == 29 and out.dtype == np.uint8)

# 2b. moments: a quiet minute then a loud one → exactly one drop, near the jump; steady music → no moments
quiet = clicks(1, 120) * 0.15; loud = clicks(1, 120)
out2, _, _ = Y.analyze_pcm(np.concatenate([quiet, loud]))
ms = Y.moments(out2)
check("one drop where the song jumps", len(ms) == 1 and ms[0]["kind"] == "drop" and abs(ms[0]["t"] - 60) < 6, str(ms))
check("steady music has no moments", Y.moments(Y.analyze_pcm(clicks(2, 120))[0]) == [])

# 3. one at a time: while the lock is held, analyze() returns at once without touching the network
Y.ANALYSIS.mkdir(parents=True, exist_ok=True)
lk = open(Y.ANALYSIS / ".lock", "w"); fcntl.flock(lk, fcntl.LOCK_EX)
t0 = time.time(); r = Y.analyze("lockedvid00", quiet=True)
check("a second analysis never runs alongside the first", r is None and time.time() - t0 < 1, f"{time.time() - t0:.2f} s")
fcntl.flock(lk, fcntl.LOCK_UN); lk.close()

# 4. parking: a fresh failure marker means no retry (no network, instant)
(Y.ANALYSIS / "parkedvid00.failed").write_text("test")
t0 = time.time(); r = Y.analyze("parkedvid00", quiet=True)
check("a failed song is parked for an hour", r is None and time.time() - t0 < 1, f"{time.time() - t0:.2f} s")

print(f"\n{'all passed' if not fails else f'{fails} failed'} · scratch {SCRATCH}")
sys.exit(1 if fails else 0)
