# changelog

one version for the whole thing (cli, terminal ui, stage app). `VERSION` is the source of truth: `stage/build.sh` stamps it into
the app and `ytm --version` prints it. newest first.

## 0.10.0: first shareable release
- **the repo is self-contained**: no machine-specific paths. the cli finds its own root (`bin/ytm` may be symlinked anywhere), the
  app learns it from the bundle (`Resources/root`, written by `stage/build.sh`). `./install.sh` sets up tools, the python env and the
  `ytm` command; `./install.sh --app` also builds the stage app. tests run from a clean checkout: `tests/run.sh`.
- **three new scenes** for the stage: **aurora** (green and violet curtains over a still sky), **embers** (a banked fire, sparks
  rising) and **tide** (a night sea, a pale moon and its path of light). pick them in the app, in the terminal ui (`s`) or with
  `ytm look aurora`. `auto` still chooses among the first five from the song; the new ones are for choosing.
- **one file per scene**: `stage/Sources/YtmStage/Looks/<Name>.swift` holds what a scene paints and the layer tree that moves it,
  replacing two 160-line `switch` blocks. adding a scene is one file plus a name in `lib/looks.py` and `Look.swift`
  (see docs/SCENES.md). a test fails if the stage and the cli disagree about the list.
- **smaller cli**: the mode table (`lib/modes.py`), the audio analysis maths (`lib/analysis.py`) and the shared look tables
  (`lib/looks.py`) moved out of `bin/ytm`; the names are re-exported, so scripts and tests that import them keep working.
- the optional lights add-on (Bluetooth room lights following the music) is no longer part of this repo; it only read files from the data dir.
- app bundle id is now `dev.afterhum.ytm` (so stage settings stored by an older private build start from defaults).

## 0.1 to 0.9: private development
a listening log, search that reads the words, 19 modes, an offline cache, premium formats when signed in, likes and dislikes kept
locally, a taste model, the terminal ui, and the native stage with song-matched scenes. the history is not part of this repository.
