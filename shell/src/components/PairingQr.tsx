// The pairing link drawn as a QR code, for a phone camera to read off this screen
// (Settings → Your phone → Pair a phone).
//
// `qrcode-generator` encodes the link, and this file draws the library's module grid
// as SVG rects. It does not use the library's HTML output, because nothing in this
// window injects markup.
//
// The code is black on white in both themes. Phone cameras look for dark squares on
// a light ground and many of them fail on the inverted version, so this is the one
// element on the dark surface that keeps a white background on purpose. The white
// border is the four-module quiet zone the QR standard asks for.

import type { ReactElement } from "react";
import qrcode from "qrcode-generator";

/** The accessible name of the drawing. */
export const PAIRING_QR_LABEL = "QR code for pairing your phone";

/** The white border around the code, in modules. Four is the standard's minimum. */
export const QUIET_ZONE_MODULES = 4;

/** Roughly how wide the drawing is, in CSS pixels. A phone camera held a normal
 * distance from a laptop screen reads a code this size comfortably. The exact width
 * is the nearest whole number of pixels per module, so every module is drawn the
 * same size and none comes out a pixel wider than its neighbours. */
const TARGET_SIZE_PX = 188;

/** Encode `text` at error-correction level M and return the module grid, with
 * `true` for a dark module. Returns null when the library refuses the text, for
 * example when it is too long for the largest QR version. The panel then shows the
 * pairing code on its own. */
export function pairingQrModules(text: string): boolean[][] | null {
  try {
    const qr = qrcode(0, "M");
    qr.addData(text, "Byte");
    qr.make();
    const count = qr.getModuleCount();
    const grid: boolean[][] = [];
    for (let row = 0; row < count; row++) {
      const cells: boolean[] = [];
      for (let col = 0; col < count; col++) cells.push(qr.isDark(row, col));
      grid.push(cells);
    }
    return grid;
  } catch {
    return null;
  }
}

/** Draw a module grid from `pairingQrModules` as an SVG. Each horizontal run of
 * dark modules in a row is one rect, which keeps the element count to a few hundred
 * for a pairing link. */
export function PairingQr({ modules }: { modules: boolean[][] }) {
  const count = modules.length;
  const extent = count + QUIET_ZONE_MODULES * 2;
  const modulePx = Math.max(2, Math.round(TARGET_SIZE_PX / extent));
  const sizePx = modulePx * extent;

  const runs: ReactElement[] = [];
  modules.forEach((cells, row) => {
    let col = 0;
    while (col < count) {
      if (!cells[col]) {
        col++;
        continue;
      }
      const start = col;
      while (col < count && cells[col]) col++;
      runs.push(
        <rect
          key={`${row}:${start}`}
          x={start + QUIET_ZONE_MODULES}
          y={row + QUIET_ZONE_MODULES}
          width={col - start}
          height={1}
        />,
      );
    }
  });

  return (
    <svg
      role="img"
      aria-label={PAIRING_QR_LABEL}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${extent} ${extent}`}
      width={sizePx}
      height={sizePx}
      shapeRendering="crispEdges"
      className="block shrink-0"
    >
      <rect width={extent} height={extent} fill="#FFFFFF" />
      <g fill="#000000">{runs}</g>
    </svg>
  );
}
