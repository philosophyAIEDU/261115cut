#!/usr/bin/env bash
# Builds build/installer/SophiCut-Mac-<AppleSilicon|Intel>.dmg for the CPU of the Mac it runs on.
#   PYTHON=python3.12 bash packaging/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
BUILD=$ROOT/build
mkdir -p "$BUILD/installer"
PY=${PYTHON:-python3}
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' sophicut/__init__.py)
if [ "$(uname -m)" = arm64 ]; then FFARCH=arm64; LABEL=AppleSilicon; else FFARCH=amd64; LABEL=Intel; fi
echo "== SophiCut $VERSION for Mac ($LABEL) =="

# 1) clean build environment
"$PY" -m venv "$BUILD/venv"
VPY=$BUILD/venv/bin/python
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r requirements.txt pyinstaller
"$VPY" packaging/make_icon.py "$BUILD/sophicut.png"

# 2) ffmpeg: static macOS builds by Martin Riedl (newest release, checksum verified)
FF=$BUILD/ffmpeg-mac
rm -rf "$FF" && mkdir -p "$FF"
DIR=$(curl -fsSL https://ffmpeg.martin-riedl.de/ | grep -o "download/macos/$FFARCH/[0-9]*_[0-9.]*/ffmpeg.zip" | head -1 | sed 's|/ffmpeg.zip$||')
[ -n "$DIR" ] || { echo "ffmpeg build for $FFARCH not found"; exit 1; }
echo "ffmpeg: $DIR"
for b in ffmpeg ffprobe; do
  curl -fsSL "https://ffmpeg.martin-riedl.de/$DIR/$b.zip" -o "$BUILD/$b.zip"
  want=$(curl -fsSL "https://ffmpeg.martin-riedl.de/$DIR/$b.zip.sha256" | awk '{print $1}')
  have=$(shasum -a 256 "$BUILD/$b.zip" | awk '{print $1}')
  [ "$want" = "$have" ] || { echo "checksum mismatch for $b"; exit 1; }
  unzip -o -q "$BUILD/$b.zip" -d "$FF"
done
chmod +x "$FF/ffmpeg" "$FF/ffprobe"
"$FF/ffmpeg" -hide_banner -encoders | grep -q libx264
curl -fsSL "https://ffmpeg.martin-riedl.de/$DIR/codecs.txt" -o "$FF/FFMPEG-CODECS.txt" || true

# 3) app bundle (ffmpeg goes inside the .app so the bundle signature stays valid)
"$VPY" -m PyInstaller --noconfirm --clean --windowed --name SophiCut \
  --icon "$BUILD/sophicut.png" --osx-bundle-identifier kr.philosophyai.sophicut \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" --specpath "$BUILD" --paths "$ROOT" \
  --add-data "$ROOT/sophicut/static:sophicut/static" \
  --add-data "$ROOT/sophicut/fonts:sophicut/fonts" \
  --add-binary "$FF/ffmpeg:ffmpeg" --add-binary "$FF/ffprobe:ffmpeg" \
  --hidden-import sophicut.app \
  --collect-all ctranslate2 --collect-all faster_whisper --collect-all onnxruntime --collect-all av \
  "$ROOT/packaging/entry.py"

APP=$BUILD/dist/SophiCut.app
PL=$APP/Contents/Info.plist
# runs in the background (no Dock icon); the UI is the browser tab
plutil -replace LSUIElement -bool true "$PL"
plutil -replace CFBundleDisplayName -string "소피컷" "$PL"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$PL"
plutil -replace CFBundleVersion -string "$VERSION" "$PL"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

# 4) disk image with an Applications shortcut (drag to install)
STAGE=$BUILD/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
OUT=$BUILD/installer/SophiCut-Mac-$LABEL.dmg
rm -f "$OUT"
for i in 1 2 3 4 5; do  # hdiutil sometimes fails with "Resource busy" on CI runners
  hdiutil create -volname "SophiCut" -srcfolder "$STAGE" -ov -format UDZO "$OUT" && break
  [ "$i" = 5 ] && exit 1
  echo "hdiutil failed, retrying ($i)"; sleep 10
done
echo "== done: $OUT ($(du -h "$OUT" | cut -f1)) =="
