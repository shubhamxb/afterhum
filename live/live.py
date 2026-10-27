"""ytm live: the window itself becomes the album.

on each track (and on pause/play) this writes the ytm window's own ghostty config: a wallpaper made from the cover
(blurred, darkened, pulled toward a night palette) + alive.glsl with the cover's three colours baked in,
then sends that one ghostty instance SIGUSR2 so it reloads. only the ytm window changes; every other terminal is untouched.
the window opts in through YTM_LIVE=1 (skhd ⌥⌘y launches it with --config-file pointing at LIVE/ghostty.conf).
"""
import colorsys, os, re, signal, subprocess
from pathlib import Path

HERE = Path(__file__).parent
LIVE = Path(os.environ.get("YTM_DATA", Path.home() / ".local/share/ytm")) / "live"
NIGHT = (11, 13, 26)   # the night background, #0b0d1a


def palette(cover):
    """three colours from the cover: the main one, a second distinct one, and the brightest spark. lifted to read on dark."""
    from PIL import Image
    img = Image.open(cover).convert("RGB").resize((64, 64))
    q = img.quantize(colors=8, method=Image.Quantize.MEDIANCUT).convert("RGB")
    counts = sorted(q.getcolors(64 * 64), reverse=True)
    def hsv(c): return colorsys.rgb_to_hsv(*[x / 255 for x in c])
    def lift(c, s_min=0.45, v_min=0.80):
        h, s, v = hsv(c); r, g, b = colorsys.hsv_to_rgb(h, max(s, s_min), max(v, v_min)); return (r, g, b)
    ranked = sorted(counts, key=lambda nc: -nc[0] * (0.25 + hsv(nc[1])[1]))   # common and colourful first
    main = ranked[0][1]
    second = next((c for _, c in ranked[1:] if abs(hsv(c)[0] - hsv(main)[0]) > 0.08), ranked[min(1, len(ranked) - 1)][1])
    spark = max((c for _, c in counts), key=lambda c: hsv(c)[2] * (0.4 + hsv(c)[1]))
    return [lift(main), lift(second), lift(spark, 0.35, 0.95)]


def wallpaper(cover, vid):
    """the cover as a room: cropped to the window, blurred to light, darkened, washed toward night, vignetted."""
    from PIL import Image, ImageFilter, ImageDraw
    W, H = 1680, 1050
    img = Image.open(cover).convert("RGB")
    s = max(W / img.width, H / img.height) * 1.15
    img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    img = img.crop(((img.width - W) // 2, (img.height - H) // 2, (img.width - W) // 2 + W, (img.height - H) // 2 + H))
    img = img.filter(ImageFilter.GaussianBlur(42))
    img = Image.blend(img, Image.new("RGB", (W, H), NIGHT), 0.62)
    mask = Image.new("L", (W, H), 0); ImageDraw.Draw(mask).ellipse((-W * 0.2, -H * 0.25, W * 1.2, H * 1.25), fill=255)
    img = Image.composite(img, Image.new("RGB", (W, H), NIGHT), mask.filter(ImageFilter.GaussianBlur(160)))
    LIVE.mkdir(parents=True, exist_ok=True)
    out = LIVE / f"wall-{vid}.jpg"; img.save(out, quality=88)   # (apply() skips this when the file already exists)
    for old in sorted(LIVE.glob("wall-*.jpg"), key=lambda p: p.stat().st_mtime)[:-6]: old.unlink()   # keep the last few
    return out


_GHOSTTY = []   # [pid or None] once looked up: the terminal a process runs in doesn't change under it


def ghostty_pid():
    """the ghostty instance this process runs in: walk up the parent chain (once per process, up to 12 `ps`)."""
    if not _GHOSTTY: _GHOSTTY.append(_find_ghostty())
    return _GHOSTTY[0]


def _find_ghostty():
    pid = os.getpid()
    for _ in range(12):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not out: return None
        ppid, comm = out.split(None, 1)
        if "ghostty" in comm.lower(): return pid
        pid = int(ppid)
        if pid <= 1: return None
    return None


# 0.8: the shared look (DATA/prefs.json) caps the energy: paper is the calmest room; aurora, tide, stars and rain stay calm; ember a little warmer; neon/dusk normal
LOOK_ENERGY = {"paper": 0.15, "aurora": 0.3, "tide": 0.3, "stars": 0.4, "rain": 0.4, "ember": 0.5, "neon": 0.7, "dusk": 0.7}


def apply(vid=None, cover=None, playing=True, energy=0.6, look=None):
    """write the window's config + shader and make its ghostty reload. returns the palette (or None if not live)."""
    if not os.environ.get("YTM_LIVE"): return None
    if look: energy = min(energy, LOOK_ENERGY.get(look, energy))
    LIVE.mkdir(parents=True, exist_ok=True)
    pal = palette(cover) if cover and Path(cover).exists() else [(0.278, 0.702, 0.847), (0.545, 0.424, 1.0), (0.91, 0.561, 0.706)]
    src = (HERE / "alive.glsl").read_text()
    for name, c in zip(("C1", "C2", "C3"), pal):
        src = re.sub(rf"const vec3 {name} = vec3\([^)]*\);", f"const vec3 {name} = vec3({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f});", src)
    src = re.sub(r"const float PLAYING = [\d.]+;", f"const float PLAYING = {1.0 if playing else 0.0:.1f};", src)
    src = re.sub(r"const float ENERGY = [\d.]+;", f"const float ENERGY = {energy:.2f};", src)
    (LIVE / "alive.glsl").write_text(src)
    lines = [f"custom-shader = {LIVE / 'alive.glsl'}", "custom-shader-animation = true"]
    if vid and cover and Path(cover).exists():
        wall = LIVE / f"wall-{vid}.jpg"
        lines += [f"background-image = {wall if wall.exists() else wallpaper(cover, vid)}", "background-image-opacity = 1.0",
                  "background-image-fit = cover", "background-image-position = center"]
    (LIVE / "ghostty.conf").write_text("# written by ytm live (live/live.py) on every track. don't edit.\n" + "\n".join(lines) + "\n")
    pid = ghostty_pid()
    if pid: os.kill(pid, signal.SIGUSR2)
    return pal
