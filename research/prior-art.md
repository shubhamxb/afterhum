# ytm: prior art (lens, 2026-10-23)

Two MIT-licensed Python projects already ship most of the decided stack (ytmusicapi + yt-dlp + mpv). Borrow their patterns.
Don't rebuild them. The `ytm` name is already taken on PyPI. That's fine for a local tool, but don't publish under it.

## steal this (ranked)
1. **MaheshBhushan/yt-music-cli** (PyPI `ytm`, MIT, v0.11.2 on 2026-10-03). Same architecture: mpv starts idle with a JSON IPC socket,
   and `mpv/autoplay.lua` refills a radio queue *inside mpv*, so playback continues when the CLI exits. Credentials live in
   `session.json` with mode 0600 in a 0700 directory (allowlisted headers only). Take the autoplay.lua idea and the auth-file hygiene.
2. **peternaame-boop/ytm-player** (MIT, v2.1.0 on 2026-09-08, ~534 stars). Files worth reading: `services/macos_media.py`
   (pyobjc MPNowPlayingInfoCenter + MPRemoteCommandCenter), `services/history.py` (aiosqlite play/search history),
   `ytm stats --json`, `sync_history_to_ytmusic` (plays are reported back via `add_history_item`, so YTM's own recommendations
   learn from them), Unix-socket CLI to player, URL prefetch for instant skips, and an optional Last.fm scrobble.
3. **yt-dlp hygiene, the biggest reliability lever.** Install deno. Track yt-dlp releases (latest 2026.10.21). Don't hard-pin
   `player_client`: the defaults keep moving. Add a `ytm doctor` / self-update path.
4. **ytmusicapi browser-header auth** over OAuth. Since Nov 2024, OAuth needs your own Google Cloud client.
5. **Radio from YTM's own graph**, with Claude only reranking or filtering: `get_watch_playlist(radio=True)` + `get_song_related`.
6. **Local listen log, Maloja-style** (append-only plays table). Reports are SQL over it, and troi-style "flashback" and "exploration" ideas become queries.
7. Skip for now: TTS "voice DJ" talk breaks (see section 4).

## 1. existing clients and daemons
| project | lang / license | status (as seen) | worth borrowing |
|---|---|---|---|
| [yt-music-cli / `ytm`](https://github.com/MaheshBhushan/yt-music-cli) ([pypi](https://pypi.org/project/ytm/)) | Py / MIT | active: 0.2.0 on 2026-09-03 to 0.11.2 on 2026-10-03; 6 stars | idle-mpv IPC, autoplay.lua radio, session.json perms, 2 GB offline cache cap. No history or stats. macOS sign-in "not certified" |
| [ytm-player](https://github.com/peternaame-boop/ytm-player) ([site](https://ytm-player.com/)) | Py / MIT | active: v2.1.0 on 2026-09-08 | macOS Now Playing, sqlite history, stats, YTM history sync, last.fm, prefetch. Uses libmpv (python-mpv), not the mpv CLI |
| [ytermusic](https://github.com/ccgauche/ytermusic) | Rust / Apache-2.0 | last push 2026-03-04, 52 open issues | `headers.txt` cookie auth, ~20 MB RAM claim, Supermix, offline cache. No macOS media keys mentioned |
| [youtube-tui](https://crates.io/crates/youtube-tui) (Siriusmart) | Rust / license unverified | v0.9.4; docs.rs build failing | general YouTube TUI with embedded mpv; YAML config. Low relevance |
| [ytui-music](https://github.com/sudipghimire533/ytui-music) | Rust / GPL-2.0 | probably dormant (unverified); youtube-dl era | nothing to take. GPL anyway |
| [mopidy-ytmusic](https://mopidy.com/ext/ytmusic/) | Py / Apache-2.0 (unverified) | upstream lagging; distros use the jmcdo29 fork 0.3.9 | the mpd-style daemon path. Too heavy for 8 GB / light |
| [Youtube-Music-TUI](https://github.com/BrownNpc/Youtube-Music-TUI) | Py | not checked | not checked |

## 2. mpv on macOS
**Now Playing / media keys**
- mpv master has `osdep/mac/remote_command_center.swift`. It sets `MPNowPlayingInfoCenter.nowPlayingInfo` (title, artist,
  artwork, duration) and maps play/pause/next/prev/seek ([source](https://github.com/mpv-player/mpv/blob/master/osdep/mac/remote_command_center.swift)).
  It's compiled only when the build has `HAVE_MACOS_MEDIA_PLAYER`.
- **`--media-controls` is documented as Windows-only** ([manual](https://mpv.io/manual/master/), [arch man](https://man.archlinux.org/man/mpv.1)).
  On macOS the relevant switch is `--input-media-keys` (default yes, **except libmpv**). That exception is probably why ytm-player
  rolls its own pyobjc Now Playing. ytm uses the mpv CLI, so it should get Now Playing for free.
- **Unverified:** whether a backgrounded mpv started with `--no-video --idle` and no window actually registers with Now Playing.
  Test it in 2 minutes: start mpv that way, run `media-control get`, press F8.
- 0.41.0 (2025-12-21) release notes have no macOS media change ([notes](https://sources.debian.org/src/mpv/0.41.0-2/RELEASE_NOTES)).
- `media-control` (ungive) reads Now Playing through system perl because MediaRemote has been entitlement-gated since macOS 15.4
  ([repo](https://github.com/ungive/mediaremote-adapter)). A future macOS release could break that hack. For the status bar it's
  cheaper and sturdier to read ytm's own state (the mpv IPC socket or a state file) and keep media-control as a fallback.
- Gotchas from ytm-player's troubleshooting doc: Music.app steals the media keys (quit it), and the terminal may need Accessibility/Input Monitoring permission.

**Low-RAM audio-only config** (defaults are sourced; the values are my suggestion)
- Demuxer cache defaults are ~150 MiB forward / 50 MiB back (one source says 400 MiB). Check with `mpv --list-options | grep demuxer-max`.
  Audio runs at ~20 KB/s, so a few MiB is minutes of buffer.
- Suggested: `--idle=yes --no-video --audio-display=no --no-terminal --input-ipc-server=<sock> --cache=yes`
  `--demuxer-max-bytes=8MiB --demuxer-max-back-bytes=2MiB --demuxer-readahead-secs=60 --ytdl-format=bestaudio[acodec=opus]/bestaudio`.
- Design choice: resolve stream URLs in the Python side (ytm-player does this in `services/stream.py`) and give mpv direct URLs.
  Otherwise ytdl_hook starts a fresh yt-dlp process for every track (more latency and RAM spikes; my inference, not benchmarked).
  Resolved URLs expire, so re-resolve when a play fails (ytm-player retries once after clearing its cache).

**yt-dlp + YouTube, late 2026**
- The `web` client is SABR-only. `web_music` and `mweb` need a GVS PO token. `tv` serves DRM'd formats without cookies.
  Premium accounts don't need GVS tokens ([PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide), edited 2026-07-12;
  [#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)).
- A JS runtime is effectively required (EJS challenge solver). Deno is preferred and on by default; Bun is deprecated after 1.3.14
  ([EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS)). On macOS: `brew install deno`. A pip install needs `yt-dlp[default]`.
- Default clients keep changing. On 2026-08-17 YouTube broke the old default and every track returned 403. Fixed in 2026.08.19
  (android_vr dropped, visionos and web_embedded added), and 2026.10.21 made `tv_samsung` a default
  ([releases](https://github.com/yt-dlp/yt-dlp/releases)). Lesson: don't pin clients, and auto-update yt-dlp.
- Fallback if formats disappear: the [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) plugin,
  per the PO Token Guide. The guide says tokens can last as little as 12 h.
- Premium cookies unlock ~256 kbps opus on YTM. Premium is optional.

## 3. ytmusicapi: auth matrix
- **Hard auth check (`_check_auth`)** in the library mixin: `get_history`, `add_history_item`, `remove_history_items`,
  `get_library_*`, `rate_song`, `rate_playlist`, `edit_song_library_status`, `subscribe_artist(s)`, `get_account_info`, `get_tasteprofile`
  ([library.py](https://github.com/sigma67/ytmusicapi/blob/main/ytmusicapi/mixins/library.py)). `get_liked_songs` should need auth too (unverified).
- **No auth check:** `search`, `get_watch_playlist` (`radio=True` gives a fresh radio each call), `get_song_related`,
  `get_lyrics` (`timestamps=True` gives synced lyrics via the mobile client), `get_home`, `get_mood_categories`, `get_charts`.
  Without auth, `get_home` is presumably generic rather than personalised (inferred; source doesn't say).
  yt-music-cli confirms that search, playback, radio, lyrics and public playlists work signed out.
- **Browser auth** (`ytmusicapi browser` writes browser.json): valid "as long as the browser session is valid", about 2 years, and ends on logout
  ([docs](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html)). **OAuth** needs your own GCP client of type
  "TVs and Limited Input devices" since Nov 2024 ([docs](https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html)).
- **Rate limits:** the FAQ says one "most certainly" exists but normal use won't hit it, and gives no numbers ([faq](https://ytmusicapi.readthedocs.io/en/stable/faq.html)).
  Cache `get_home` and related results, and back off on 429.
- **Storing auth:** others use a plain 0600 file (yt-music-cli: allowlisted headers, "not encrypted", treat it like a browser session).
  ytermusic uses a plain `headers.txt`. Nobody I saw uses the Keychain. Something like `security add-generic-password` would be an
  upgrade, but it's optional.
- `get_history` returns only YTM's recent server-side history with no limit parameter. Keep your own local log as the source of truth.

## 4. "intelligent" features: good vs gimmick
**Good (cheap and grounded in data)**
- A local append-only play log (start, end, skip, seconds played, source = radio/search/claude). Skips and completion rate are the
  strongest taste signal, and they're free. Maloja is the model: "no social, radios, recommendations or any other gimmicks"
  ([maloja](https://github.com/krateng/maloja)).
- Wrapped-style reports as SQL over that log: top artists and tracks per week or month, new-vs-repeat ratio, hour-of-day heatmap,
  and "forgotten favourites" (ListenBrainz/troi's `weekly-flashback-jams`, [troi](https://github.com/metabrainz/troi-recommendation-playground)).
  Claude writes the prose; the numbers come from SQL.
- Smart radio = YTM radio/related as the candidate pool, reranked by the local log (recent skips down, unplayed artists up)
  plus time-of-day. ytm-player's `add_history_item` sync keeps YTM's own recommender learning too.
- Claude as a chooser, not an oracle: give it a candidate list of real videoIds and ask it to pick or order for a stated mood.
  Asking an LLM for song titles risks made-up tracks (my judgement; DJMate does the same split, ML picks and LLM fronts
  [DJMate](https://github.com/benjyb1/DJMate)).

**Gimmick for this use (background terminal player)**
- Spoken DJ breaks with TTS ([subwave](https://www.sourcepulse.org/projects/31620413), [WRIT-FM uses Claude CLI + Kokoro](https://www.roborhythms.com/how-to-build-ai-radio-station-with-claude-2026/)).
  Reviews of Spotify's AI DJ: fun, but it mostly replays known songs, discovery is weak, and the voice "feels like ads"
  ([Tom's Guide](https://www.tomsguide.com/opinion/ive-been-using-spotify-dj-for-a-month-heres-5-things-i-love-and-hate),
  [Gizmodo](https://gizmodo.com/spotify-ai-x-streaming-playlist-1850140153)). A one-line text "why this track" in the status bar gets 90% of the charm.
- Mood detection from audio. It's heavy, and YTM's mood categories (`get_mood_categories`) already exist.

## open / unverified
- mpv Now Playing when headless (test above). Whether the Homebrew mpv build has `HAVE_MACOS_MEDIA_PLAYER`.
- Exact mpv 0.41 demuxer defaults on this build. youtube-tui and mopidy-ytmusic licenses. ytui-music last activity.
