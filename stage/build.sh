#!/bin/sh
# build ytm.app (no xcode needed: swiftpm + an ad-hoc signature). installs to ~/Applications; YTM_APP=/some/dir/ytm.app builds elsewhere.
set -e
cd "$(dirname "$0")"
swift build -c release 2>&1 | grep -E "error|Build complete" || true
ROOT="$(cd .. && pwd)"
APP="${YTM_APP:-$HOME/Applications/ytm.app}"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
printf '%s\n' "$ROOT" > "$APP/Contents/Resources/root"   # where bin/ytm lives: the app drives that cli
cp .build/release/YtmStage "$APP/Contents/MacOS/YtmStage"
[ -f icon.icns ] && cp icon.icns "$APP/Contents/Resources/icon.icns"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ytm</string>
  <key>CFBundleDisplayName</key><string>ytm</string>
  <key>CFBundleIdentifier</key><string>dev.afterhum.ytm</string>
  <key>CFBundleExecutable</key><string>YtmStage</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(cat ../VERSION)</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.music</string>
</dict></plist>
PL
codesign --force --deep -s - "$APP" 2>/dev/null
echo "built $APP"
