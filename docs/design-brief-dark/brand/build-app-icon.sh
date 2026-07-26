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

# QuickLook flattens onto WHITE, so everything outside the rounded tile comes
# back opaque white and the dock draws a white square behind the mark. Put the
# alpha back from app-icon.svg's own geometry: an 824x824 tile at a 100px margin
# with a 185px radius on the 1024 grid. Keep these three numbers in step with the
# master. Inside the tile pixels keep their colour; outside goes transparent,
# feathered across the last pixel so the corners are not stairs.
python3 - "$MASTER" <<'PY'
import struct, sys, zlib
path = sys.argv[1]
MARGIN, RADIUS, GRID = 100.0, 185.0, 1024.0

d = open(path, "rb").read()
pos, idat, w, h, bd, ct = 8, b"", None, None, None, None
while pos < len(d):
    ln = struct.unpack(">I", d[pos:pos+4])[0]; typ = d[pos+4:pos+8]; ch = d[pos+8:pos+8+ln]
    if typ == b"IHDR": w, h, bd, ct = struct.unpack(">IIBB", ch[:10])
    elif typ == b"IDAT": idat += ch
    pos += 12 + ln
if (bd, ct) != (8, 6):
    raise SystemExit(f"expected 8-bit RGBA from QuickLook, got depth {bd} type {ct}")

raw, stride, rows, prev, i = zlib.decompress(idat), w*4, [], bytearray(w*4), 0
for _ in range(h):                       # undo the per-scanline filters
    f = raw[i]; i += 1
    line = bytearray(raw[i:i+stride]); i += stride
    for x in range(stride):
        a = line[x-4] if x >= 4 else 0
        b = prev[x]; c = prev[x-4] if x >= 4 else 0
        if f == 1: line[x] = (line[x] + a) & 255
        elif f == 2: line[x] = (line[x] + b) & 255
        elif f == 3: line[x] = (line[x] + (a + b)//2) & 255
        elif f == 4:
            p = a + b - c; pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
            line[x] = (line[x] + (a if (pa <= pb and pa <= pc) else b if pb <= pc else c)) & 255
    rows.append(line); prev = line

s = w / GRID
x0, y0, x1, y1, r = MARGIN*s, MARGIN*s, w - MARGIN*s, h - MARGIN*s, RADIUS*s
def coverage(px, py):
    if px < x0 or px > x1 or py < y0 or py > y1: return 0.0
    dx = (x0 + r - px) if px < x0 + r else ((px - (x1 - r)) if px > x1 - r else 0.0)
    dy = (y0 + r - py) if py < y0 + r else ((py - (y1 - r)) if py > y1 - r else 0.0)
    if dx <= 0 or dy <= 0: return 1.0
    dist = (dx*dx + dy*dy) ** 0.5
    return 1.0 if dist <= r - 0.5 else (0.0 if dist >= r + 0.5 else r + 0.5 - dist)

for y in range(h):
    line = rows[y]
    for x in range(w):
        a = coverage(x + 0.5, y + 0.5)
        if a < 1.0:
            line[x*4 + 3] = int(round(line[x*4 + 3] * a))

def chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
open(path, "wb").write(
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + bytes(l) for l in rows), 9))
    + chunk(b"IEND", b""))
PY

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
