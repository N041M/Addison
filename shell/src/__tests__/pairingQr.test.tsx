// The pairing QR code (Settings → Your phone → Pair a phone). A phone camera has to
// read it off the screen, so these tests check what a scanner depends on: the
// drawing matches the library's module grid cell for cell and the right way up,
// dark modules sit on white, and the white border is four modules wide. They also
// check the library import itself, which is what proves `qrcode-generator` loads
// under vitest and jsdom.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import qrcode from "qrcode-generator";
import {
  PAIRING_QR_LABEL,
  PairingQr,
  QUIET_ZONE_MODULES,
  pairingQrModules,
} from "../components/PairingQr";

afterEach(cleanup);

/** A start link of the length a real one has: a 17-character bot name and a
 * seven-character code, 44 characters in all. */
const LINK = "https://t.me/addison_karel_bot?start=ABC-DEF";

function modulesFor(text: string): boolean[][] {
  const grid = pairingQrModules(text);
  if (!grid) throw new Error("pairingQrModules refused a link it should encode");
  return grid;
}

/** Rebuild the module grid from the rendered rects, so a test can compare what is
 * on screen with what the library encoded. */
function gridFromSvg(svg: SVGSVGElement, count: number): boolean[][] {
  const grid = Array.from({ length: count }, () => Array<boolean>(count).fill(false));
  for (const rect of Array.from(svg.querySelectorAll("g rect"))) {
    const x = Number(rect.getAttribute("x")) - QUIET_ZONE_MODULES;
    const y = Number(rect.getAttribute("y")) - QUIET_ZONE_MODULES;
    const width = Number(rect.getAttribute("width"));
    expect(Number(rect.getAttribute("height"))).toBe(1);
    for (let col = x; col < x + width; col++) grid[y][col] = true;
  }
  return grid;
}

describe("PairingQr", () => {
  it("draws a real start link as an image with a plain name", () => {
    const modules = modulesFor(LINK);
    // A QR code is 17 + 4 × version modules across. At level M a 44-byte link
    // needs version 4, which is 33 modules.
    expect(modules).toHaveLength(33);
    for (const row of modules) expect(row).toHaveLength(33);
    render(<PairingQr modules={modules} />);
    const image = screen.getByRole("img", { name: PAIRING_QR_LABEL });
    expect(image.tagName.toLowerCase()).toBe("svg");
    expect(PAIRING_QR_LABEL).toBe("QR code for pairing your phone");
  });

  it("encodes at error-correction level M, as the library does for the same link", () => {
    // The library's own answer for level M, read straight from it. A drift to
    // another level, or to a hand-rolled encoder, changes this grid.
    const reference = qrcode(0, "M");
    reference.addData(LINK, "Byte");
    reference.make();
    const count = reference.getModuleCount();
    const expected = Array.from({ length: count }, (_, row) =>
      Array.from({ length: count }, (_, col) => reference.isDark(row, col)),
    );
    expect(modulesFor(LINK)).toEqual(expected);
  });

  it("draws exactly the library's modules, the right way up", () => {
    const modules = modulesFor(LINK);
    render(<PairingQr modules={modules} />);
    const svg = screen.getByRole("img") as unknown as SVGSVGElement;
    expect(gridFromSvg(svg, modules.length)).toEqual(modules);
  });

  it("draws dark modules on white inside a four-module white border", () => {
    const modules = modulesFor(LINK);
    const extent = modules.length + 2 * 4;
    render(<PairingQr modules={modules} />);
    const svg = screen.getByRole("img");
    expect(svg.getAttribute("viewBox")).toBe(`0 0 ${extent} ${extent}`);
    expect(svg.getAttribute("shape-rendering")).toBe("crispEdges");
    // The first child is the white ground, covering the whole drawing.
    const ground = svg.firstElementChild;
    expect(ground?.tagName.toLowerCase()).toBe("rect");
    expect(ground?.getAttribute("fill")).toBe("#FFFFFF");
    expect(ground?.getAttribute("width")).toBe(String(extent));
    expect(ground?.getAttribute("height")).toBe(String(extent));
    expect(svg.querySelector("g")?.getAttribute("fill")).toBe("#000000");
    // No dark module strays into the border.
    for (const rect of Array.from(svg.querySelectorAll("g rect"))) {
      const x = Number(rect.getAttribute("x"));
      const y = Number(rect.getAttribute("y"));
      const width = Number(rect.getAttribute("width"));
      expect(x).toBeGreaterThanOrEqual(4);
      expect(y).toBeGreaterThanOrEqual(4);
      expect(x + width).toBeLessThanOrEqual(extent - 4);
      expect(y + 1).toBeLessThanOrEqual(extent - 4);
    }
  });

  it("is a size a phone camera reads from a laptop screen, with whole pixels per module", () => {
    const modules = modulesFor(LINK);
    const extent = modules.length + 2 * QUIET_ZONE_MODULES;
    render(<PairingQr modules={modules} />);
    const svg = screen.getByRole("img");
    const width = Number(svg.getAttribute("width"));
    expect(svg.getAttribute("height")).toBe(String(width));
    expect(width).toBeGreaterThanOrEqual(176);
    expect(width).toBeLessThanOrEqual(210);
    expect(Number.isInteger(width / extent)).toBe(true);
  });

  it("returns nothing for text the library cannot encode", () => {
    // Far past the largest QR version at level M. The panel then shows the code on
    // its own instead of a broken drawing.
    expect(pairingQrModules("x".repeat(3000))).toBeNull();
  });
});
