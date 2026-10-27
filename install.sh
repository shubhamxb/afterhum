#!/bin/sh
# install ytm from this checkout: tools (brew), a private python env, the `ytm` command, and (optionally) the stage app.
#   ./install.sh            tools + env + command
#   ./install.sh --app      also build the native stage app (needs the swift toolchain: xcode-select --install)
#   ./install.sh --no-brew  skip homebrew (you already have mpv, yt-dlp and uv)
# safe to run again. nothing leaves this mac; it reads no account until you run `ytm login`.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="${YTM_DATA:-$HOME/.local/share/ytm}"
BIN="${YTM_BIN_DIR:-$HOME/.local/bin}"
APP=0; BREW=1
for a in "$@"; do case "$a" in --app) APP=1;; --no-brew) BREW=0;; -h|--help) sed -n 2,7p "$0"; exit 0;; *) echo "unknown option: $a" >&2; exit 2;; esac; done

[ "$(uname -s)" = Darwin ] || { echo "ytm is macOS only (it uses CoreAudio, now-playing and a native stage)." >&2; exit 1; }

if [ "$BREW" = 1 ]; then
  command -v brew >/dev/null || { echo "homebrew is needed first: https://brew.sh  (or re-run with --no-brew if you have mpv, yt-dlp and uv)" >&2; exit 1; }
  echo "→ tools"; HOMEBREW_NO_AUTO_UPDATE=1 brew bundle --file="$ROOT/Brewfile"
fi
for t in mpv yt-dlp uv; do command -v "$t" >/dev/null || { echo "missing: $t" >&2; exit 1; }; done

echo "→ python env"
VENV="$HOME/.local/share/ytm/venv"   # fixed: bin/ytm looks for its python here whatever YTM_DATA says
mkdir -p "$DATA" "$(dirname "$VENV")"
[ -x "$VENV/bin/python" ] || uv venv -q --python 3.12 "$VENV" 2>/dev/null || uv venv -q "$VENV"
uv pip install -q --python "$VENV/bin/python" -r "$ROOT/requirements.txt"

echo "→ the ytm command ($BIN/ytm)"
mkdir -p "$BIN"; ln -sf "$ROOT/bin/ytm" "$BIN/ytm"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  add this to your shell profile:  export PATH=\"$BIN:\$PATH\"";; esac

if [ "$APP" = 1 ]; then
  command -v swift >/dev/null && xcode-select -p >/dev/null 2>&1 || { echo "the app needs the swift toolchain: run  xcode-select --install  (Xcode 15+ / Command Line Tools), then ./install.sh --app" >&2; exit 1; }
  echo "→ the stage app"; "$ROOT/stage/build.sh"
fi
echo "done. try:  ytm lofi beats     ·  ytm --help"
