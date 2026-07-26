#!/bin/bash
# Rebuild the browser favicons from favicon-master.svg (macOS only — uses
# QuickLook and sips, neither of which needs a third-party rasteriser).
#
# Each size is rendered DIRECTLY at that size rather than downscaled from one
# large raster. That is the whole point of this script: the previous single
# 64px favicon was left for the browser to squash to 16px, and a two-storey "a"
# does not survive that. Rendering at 16 lets the text rasteriser hint the
# strokes onto the pixel grid instead of averaging them into grey.
#
# Run from the repo root:  bash docs/design-brief-dark/brand/build-favicon.sh
set -euo pipefail
SRC="docs/design-brief-dark/brand/favicon-master.svg"
DEST="shell/public"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

render() { # size, outfile
  mkdir -p "$WORK/$1"
  qlmanage -t -s "$1" -o "$WORK/$1" "$SRC" >/dev/null 2>&1
  local made="$WORK/$1/$(basename "$SRC").png"
  [ -f "$made" ] || { echo "QuickLook produced no PNG at ${1}px" >&2; exit 1; }
  # QuickLook fits the longest edge; the master is square, so this is a no-op
  # except where it pads. Force the exact box so the browser gets what it asked.
  sips -z "$1" "$1" "$made" --out "$2" >/dev/null
}

render 16  "$DEST/favicon-16.png"
render 32  "$DEST/favicon-32.png"
render 180 "$DEST/apple-touch-icon.png"
# The unsized default any older client falls back to. 32 rather than 64: it is
# the size a retina tab actually asks for, so nothing has to squash it.
cp "$DEST/favicon-32.png" "$DEST/favicon.png"

echo "Favicons rebuilt in $DEST:"
for f in favicon-16.png favicon-32.png favicon.png apple-touch-icon.png; do
  echo "  $f  $(sips -g pixelWidth -g pixelHeight "$DEST/$f" | awk '/pixel/{printf "%s ", $2}')"
done
