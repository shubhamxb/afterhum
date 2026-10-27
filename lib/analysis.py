"""song analysis: pcm in, per-frame band energies + tempo + the song's real moments (drops and lifts) out. numpy only; no files,
no network. bin/ytm owns fetching the audio and caching the result; the stage reads the cache and lands visuals on the beat.

lesson (10-23): the first version held whole songs as one frames×2048 array. an hour-long mix needed ~15 GB per run and the
stage started several. now: chunked (flat memory), capped at MAX_SECONDS, 11 kHz. tests/test_analyze.py guards the memory."""
FPS, BANDS = 60, 24
SR = 11025; HOP = SR // FPS; NFFT = 1024
MAX_SECONDS = 720            # visuals don't need more; long mixes get their first 12 minutes
CHUNK = 2048                 # frames per block: ~8 MB of working memory, whatever the song's length
FAIL_HOLD = 3600             # a song that failed waits an hour before anyone tries again


def analyze_pcm(x):
    """pcm (float32 mono, SR) → frames × 29 uint8 + bpm. memory stays flat: frames are built one CHUNK at a time."""
    import numpy as np
    x = np.asarray(x, dtype=np.float32)[: MAX_SECONDS * SR]
    if len(x) < SR: raise RuntimeError("audio too short")
    n = 1 + (len(x) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    edges = np.geomspace(40, 5000, BANDS + 1)
    masks = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
    masks = [m if m.any() else (np.abs(freqs - lo) == np.abs(freqs - lo).min()) for m, lo in zip(masks, edges[:-1])]
    db = np.empty((n, BANDS), dtype=np.float32); rms = np.empty(n, dtype=np.float32)
    off = np.arange(NFFT)
    for a in range(0, n, CHUNK):
        b = min(n, a + CHUNK)
        fr = x[(HOP * np.arange(a, b))[:, None] + off[None, :]]                  # CHUNK × NFFT, freed each loop
        sp = np.abs(np.fft.rfft(fr * win, axis=1)).astype(np.float32)
        db[a:b] = 20 * np.log10(np.stack([sp[:, m].mean(axis=1) for m in masks], axis=1) + 1e-6)
        rms[a:b] = np.sqrt((fr * fr).mean(axis=1))
        del fr, sp
    lo_, hi_ = np.percentile(db, 5), np.percentile(db, 99)
    bnorm = np.clip((db - lo_) / (hi_ - lo_ + 1e-6), 0, 1)
    energy = np.clip(rms / (np.percentile(rms, 98) + 1e-9), 0, 1)
    flux = np.maximum(np.diff(db, axis=0, prepend=db[:1]), 0)[:, :10].mean(axis=1)
    onset = np.clip(flux / (np.percentile(flux, 99) + 1e-9), 0, 1)
    # tempo: autocorrelation only at the lags that matter (70-180 bpm), not the full o(n²) correlate
    env = (onset - onset.mean()).astype(np.float32)
    lags = np.arange(int(FPS * 60 / 180), int(FPS * 60 / 70))
    ac = np.array([float(np.dot(env[:-L], env[L:])) for L in lags])
    lag = int(lags[int(np.argmax(ac))]); bpm = 60 * FPS / lag
    phase = max(range(lag), key=lambda p: onset[p::lag].sum())
    beat = np.zeros(n, dtype=np.float32)
    for c in range(phase, n, lag):
        k = c + int(np.argmax(onset[max(0, c - 3):c + 4])) - min(3, c)
        if 0 <= k < n: beat[k] = 1.0
    k2 = FPS * 2; ker = np.ones(k2, dtype=np.float32) / k2
    intensity = np.convolve(energy, ker, mode="same"); intensity /= intensity.max() + 1e-9
    ahead = np.clip(np.concatenate([intensity[k2:], np.repeat(intensity[-1:], k2)]) - intensity, 0, 1) * 2
    cols = [bnorm, energy[:, None], onset[:, None], intensity[:, None], np.clip(ahead, 0, 1)[:, None], beat[:, None]]
    return (np.concatenate(cols, axis=1) * 255).astype(np.uint8), float(bpm), len(x) / SR


def moments(frames, max_n=8, gap_s=12):
    """the few places a song really moves: drops (energy jumps after a build) and lifts (smaller rises). the stage spends its
    big gestures only here, never on every beat (10-23: "it gives a sonic boom no matter what happens")."""
    import numpy as np
    inten = frames[:, 26].astype(np.float32) / 255
    k = FPS * 4                                                             # compare the next 4 s with the last 4 s
    if len(inten) < 3 * k: return []
    c = np.cumsum(np.concatenate([[0.0], inten]))
    n = len(inten)                                                          # i in [k, n-k]: mean(inten[i-k:i]) vs mean(inten[i:i+k])
    before = (c[k:n - k + 1] - c[0:n - 2 * k + 1]) / k; after = (c[2 * k:n + 1] - c[k:n - k + 1]) / k
    rise = np.zeros(n, dtype=np.float32); rise[k:n - k + 1] = after - before
    out, taken = [], np.zeros(len(inten), dtype=bool)
    for i in np.argsort(-rise):
        if rise[i] < 0.12 or len(out) >= max_n: break
        if taken[max(0, i - gap_s * FPS):i + gap_s * FPS].any(): continue
        taken[i] = True
        # land it on the strongest onset nearby (within 0.5 s), so the gesture hits with the music
        lo, hi = max(0, i - FPS // 2), min(len(inten), i + FPS // 2)
        j = lo + int(np.argmax(frames[lo:hi, 25]))
        out.append({"t": round(j / FPS, 2), "kind": "drop" if rise[i] >= 0.25 else "lift", "s": round(float(min(1.0, rise[i] * 2)), 2)})
    return sorted(out, key=lambda m: m["t"])
