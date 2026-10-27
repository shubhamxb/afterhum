# architecture

```
bin/ytm            the cli and the player's brain (one python file; the commands, the mpv socket, queue, radio, modes)
  lib/             what it loads on demand: auth, library, account, taste, ratings, offline, spotify, intent, autolook,
                   and the pure tables/maths: looks.py, modes.py, analysis.py
ytm-log.lua        runs inside mpv: writes the listening log, refills the queue, retries dead songs
tui.py             the terminal ui (textual), a sibling of the app
live/              optional: recolours a ghostty terminal to the cover (live.py + alive.glsl)
stage/             the native app (swift, core animation): stage/Sources/YtmStage
  Looks/<Name>.swift   one file per scene; Painter.swift + Layers.swift dispatch and share helpers
install.sh         tools + python env + the `ytm` command (+ the app with --app)
tests/             standalone scripts: `python tests/test_x.py`, or all of them with `tests/run.sh`
```

## how a song plays

1. `ytm <words>`: `lib/intent.py` reads the words (a mode? "songs like"? a plain search?). `bin/ytm` searches with ytmusicapi
   (signed in or not) and asks mpv to play the best match; mpv's own yt-dlp hook resolves the stream.
2. mpv runs headless as a daemon with a json ipc socket (`~/.local/share/ytm/mpv.sock`). every client (cli, terminal ui, the app)
   is a short-lived conversation on that socket; none of them plays audio.
3. `ytm-log.lua` writes one jsonl row per play (`plays/YYYY-MM.jsonl`): finished, skipped or partial. `lib/taste.py` and the
   radio ranking read it. a refill asks `bin/ytm` for a radio from what is playing.
4. `lib/offline.py` keeps about 1 GB of recent audio on disk; when the network drops, the cache plays.
5. `ytm analyze <id>` reads a song's audio once (`lib/analysis.py`: tempo, per-frame bands, drops and lifts) and caches it. the app
   schedules each upcoming moment from the playback position; it never polls.

## the app and the cli agree through files

the app shells out to `ytm` for anything that thinks (search, modes, likes). state both read is a file under the data dir:
`prefs.json` (look, accent, motion), `looks.json` (what `auto` chose per song), `analysis/`, `art/`, `ratings.json`. writers merge
and write atomically; a missing or garbled file reads as the default.

## the one rule about paths

nothing is hardcoded to a checkout. python finds the repo from `bin/ytm`'s own real path (a symlink is fine); the app reads it from
`Contents/Resources/root`, written by `stage/build.sh`. the data dir is `~/.local/share/ytm` (`YTM_DATA` overrides it, which is
how every test runs against scratch data).

## known seams (good first refactors)

- `bin/ytm` is still ~1,400 lines: the player core (ipc, queue, radio, output guard) shares module state, which the tests patch.
  split it by moving state behind a small class first, then the commands.
- the mode → scene table exists in three places: `lib/looks.py`, `Backdrop.swift` (five modes) and `AutoLook.swift`.
- the `auto` rules exist twice (swift and python) by design; `tests/autolook_cases.json` is the shared truth.
