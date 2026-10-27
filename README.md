# afterhum

**youtube music, light.** a tiny always-on player for macOS: `mpv` plays, `yt-dlp` resolves, and a native stage app turns the
window into a living, song-matched wallpaper. it aims for 40–60 MB while playing (the electron apps idle at ~370 MB).

> the command is `ytm`. the project is called afterhum. unofficial: not affiliated with or endorsed by YouTube or Google.

```
ytm lofi beats          play the best match, then a radio of ~25 songs like it
ytm music for coding    the words are read first: this is a mode, not a search
ytm songs like low roar a radio of its neighbours, minus the artist
ytm stage               the native stage: scenes, cover art, the player on glass
ytm ui                  the same player in your terminal
```

## what it does

- **plays anything on youtube music** without a browser tab. media keys and the menu bar work through macOS now-playing.
- **19 modes** for what you're doing (`work focus coding design writing reading study cafe · gym run drive cook gaming party ·
  morning winddown · chill energy sleep`), or make your own: `ytm mode new tea "tea time, quiet piano" --instrumental --vol 30`.
- **search that reads the words** ("something for coding", "arijit singh new") and reranks plain searches away from hour-long
  uploads, slowed/reverb and covers.
- **never stops**: a 1 GB offline cache fills while you listen and plays when the network drops; the output re-opens itself when
  headphones come and go.
- **the stage**: eight scenes (dusk, rain window, neon drive, stargaze, paper, aurora, embers, tide) drawn once per song and moved by
  core animation, so there is no per-frame drawing. `auto` picks a scene from the song; beats, drops and lifts come from an analysis of the audio.
- **likes and dislikes kept on your mac** (a dislike is never queued again; signed in, the thumb also goes to your account).
- **a taste model** from your own listening log: what you finish, what you skip, by time of day and mode. explainable, offline.

## install

needs macOS 14+, [Homebrew](https://brew.sh), and for the app the swift toolchain (Xcode 15+ or `xcode-select --install`).

```sh
git clone <this repo> afterhum && cd afterhum
./install.sh --app        # tools (mpv, yt-dlp, uv), a private python env, the `ytm` command, the stage app
ytm lofi beats
```

`./install.sh` without `--app` skips the stage. it is safe to run again. `ytm update` (also run by itself every 3 days while a
player lives) upgrades yt-dlp, mpv and ytmusicapi, because yt-dlp breaks when youtube changes.

## signing in (optional)

signed out, search, radio, modes, the stage and the offline cache all work. `ytm login` lists your browser profiles and asks which
to use; macOS then asks once to let `ytm` read that browser's cookie key: choose **Allow**, not Always. with a Premium account you get
the 256 kbps formats (opus / aac) automatically, plus your likes, home mixes and taste.

what is stored, and where (nothing leaves your mac except the youtube music calls themselves):

| what | where |
|---|---|
| login cookies | a `0600` file under `~/.local/share/ytm/run/`, written when the player starts, removed when it stops |
| play log, likes, taste, account snapshot, offline audio | `~/.local/share/ytm/` |
| spotify tokens (if you use `ytm spotify`) | your keychain |

`ytm logout` forgets the login. delete `~/.local/share/ytm` to forget everything.

## audio quality

this is a lossy source: the best youtube music serves is ~256 kbps (Premium). there is no lossless tier. over Bluetooth, macOS
only speaks SBC and AAC, so a wireless headset tops out at AAC whatever the source. for the cleanest sound use wired or a USB DAC.

## troubleshooting

- **silence or stalls with a Bluetooth headset**: if macOS makes the headset's *microphone* the default input, the headset drops into
  call mode (16 kHz mono) and playback degrades. set **System Settings → Sound → Input** to the MacBook's microphone.
  `ytm out` lists outputs; `ytm out reload` reopens the player's output.
- **songs won't open**: `ytm update` (youtube changes often). the log is `~/.local/share/ytm/update.log`.
- **the app says it can't find `ytm`**: rebuild it from the checkout you want it to drive: `./install.sh --app`.

## development

```sh
tests/run.sh ~/.local/share/ytm/venv/bin/python    # 14 test files, ~800 checks; offline, scratch data only, never your real library
cd stage && swift build -c release && .build/release/YtmStage --checks
```

headless renders of every scene: `YtmStage --snapshot out.png --vid <id> --look aurora` (never opens the app).
see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the parts fit and [docs/SCENES.md](docs/SCENES.md) to add a scene.
the changelog is [CHANGELOG.md](CHANGELOG.md).

## credits

built on [mpv](https://mpv.io), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [ytmusicapi](https://github.com/sigma67/ytmusicapi) and
[textual](https://textual.textualize.io). prior art and what it taught this: [research/prior-art.md](research/prior-art.md).

licensed MIT, see [LICENSE](LICENSE). respect the terms of the services you use it with: this is a personal player, not a downloader.
