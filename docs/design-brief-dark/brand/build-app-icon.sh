#!/bin/bash
# Rebuild the Tauri OS icon set from app-icon.svg (macOS only — uses QuickLook,
# sips and iconutil, none of which need a third-party rasteriser installed).
# The .ico is written directly as a PNG-embedded container for the same reason.
#
# Run from the repo root:  bash docs/design-brief-dark/brand/build-app-icon.sh
set -euo pipefail
SRC="docs/design-brief-dark/brand/app-icon.svg"
DEST="shell/src-tauri/icons"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

qlmanage -t -s 1024 -o "$WORK" "$SRC" >/dev/null 2>&1
MASTER="$WORK/$(basename "$SRC").png"
[ -f "$MASTER" ] || { echo "QuickLook produced no PNG for $SRC" >&2; exit 1; }

mkdir -p "$WORK/out" "$WORK/Addison.iconset"
for s in 16 32 48 64 128 256 512 1024; do
  sips -z $s $s "$MASTER" --out "$WORK/out/$s.png" >/dev/null
done
cp "$WORK/out/16.png"   "$WORK/Addison.iconset/icon_16x16.png"
cp "$WORK/out/32.png"   "$WORK/Addison.iconset/icon_16x16@2x.png"
cp "$WORK/out/32.png"   "$WORK/Addison.iconset/icon_32x32.png"
cp "$WORK/out/64.png"   "$WORK/Addison.iconset/icon_32x32@2x.png"
cp "$WORK/out/128.png"  "$WORK/Addison.iconset/icon_128x128.png"
cp "$WORK/out/256.png"  "$WORK/Addison.iconset/icon_128x128@2x.png"
cp "$WORK/out/256.png"  "$WORK/Addison.iconset/icon_256x256.png"
cp "$WORK/out/512.png"  "$WORK/Addison.iconset/icon_256x256@2x.png"
cp "$WORK/out/512.png"  "$WORK/Addison.iconset/icon_512x512.png"
cp "$WORK/out/1024.png" "$WORK/Addison.iconset/icon_512x512@2x.png"
iconutil -c icns "$WORK/Addison.iconset" -o "$DEST/icon.icns"

python3 - "$WORK" "$DEST" <<'PY'
import struct, sys
work, dest = sys.argv[1], sys.argv[2]
sizes = [16, 32, 48, 64, 128, 256]
imgs = [(s, open(f"{work}/out/{s}.png", "rb").read()) for s in sizes]
off = 6 + 16 * len(imgs)
entries = b""; data = b""
for s, d in imgs:
    side = 0 if s >= 256 else s  # 0 means 256 in the ICO directory format
    entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(d), off)
    data += d; off += len(d)
open(f"{dest}/icon.ico", "wb").write(struct.pack("<HHH", 0, 1, len(imgs)) + entries + data)
PY

cp "$WORK/out/32.png"   "$DEST/32x32.png"
cp "$WORK/out/64.png"   "$DEST/64x64.png"
cp "$WORK/out/128.png"  "$DEST/128x128.png"
cp "$WORK/out/256.png"  "$DEST/128x128@2x.png"
cp "$WORK/out/1024.png" "$DEST/icon.png"
echo "Icon set rebuilt in $DEST"
