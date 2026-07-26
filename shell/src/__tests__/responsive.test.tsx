// The layout at every width that means something, checked as a matrix.
//
// App makes three structural decisions from media queries, and each one is a
// different shape of window:
//
//   < 768px    the sidebar becomes a slide-over drawer, and the widgets ride
//              inline at the foot of the thread.
//   768-1024   the sidebar is a real column again, but there is only room for
//              ONE side column, so the rail sheds and its widgets stay inline.
//   >= 1024    both side columns.
//
// The thing this file exists to prevent is the failure those bands invite:
// rendering the widgets TWICE, once inline and once in the rail, or losing them
// at some width where neither branch fires. That is invisible to a test that
// only ever renders at one size, and invisible in the app until someone happens
// to drag a window to the wrong width (which is exactly how the duplicate rail
// was found, 2026-07-26).
//
// The widths are the real edges and one either side of each, because an
// off-by-one in a breakpoint is the whole bug: 767.98 and 1023.98 are the
// complements Tailwind's `md:` and the rail query are written against.

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, within } from "@testing-library/react";
import { App } from "../App";

afterEach(cleanup);

/** Answer the media queries App asks, as a browser at `width` would. */
function stubWidth(width: number) {
  window.matchMedia = ((query: string) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    return {
      matches: max ? width <= Number(max[1]) : false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    };
  }) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  localStorage.clear();
  Element.prototype.scrollIntoView = () => {};
});

/** Every width worth checking, with what each one should produce. */
const WIDTHS: { w: number; label: string; drawer: boolean; railColumn: boolean }[] = [
  { w: 320, label: "smallest phone", drawer: true, railColumn: false },
  { w: 375, label: "phone", drawer: true, railColumn: false },
  { w: 430, label: "large phone", drawer: true, railColumn: false },
  { w: 600, label: "small tablet", drawer: true, railColumn: false },
  { w: 767, label: "one below the md edge", drawer: true, railColumn: false },
  { w: 768, label: "the md edge itself", drawer: false, railColumn: false },
  { w: 820, label: "the measured squeeze case", drawer: false, railColumn: false },
  { w: 1000, label: "one band below the rail", drawer: false, railColumn: false },
  { w: 1023, label: "one below the rail edge", drawer: false, railColumn: false },
  { w: 1024, label: "the rail edge itself", drawer: false, railColumn: true },
  { w: 1280, label: "laptop", drawer: false, railColumn: true },
  { w: 1440, label: "large laptop", drawer: false, railColumn: true },
  { w: 1920, label: "desktop", drawer: false, railColumn: true },
  { w: 2560, label: "wide desktop", drawer: false, railColumn: true },
];

describe("the layout at every width that means something", () => {
  for (const { w, label, drawer, railColumn } of WIDTHS) {
    it(`${w}px (${label}) puts the widgets in exactly one place`, () => {
      stubWidth(w);
      render(<App />);

      // THE INVARIANT. One rail, wherever it lives. Two means the widgets, the
      // token meter and the consent block are all on screen twice; zero means
      // they are unreachable at this width.
      const rails = screen.getAllByLabelText("Your widgets");
      expect(rails, `expected exactly one widget rail at ${w}px`).toHaveLength(1);

      const rail = rails[0];
      // The column form is fixed-width; the inline form fills the thread.
      const isColumn = rail.className.includes("w-[232px]");
      expect(isColumn, `rail form is wrong at ${w}px`).toBe(railColumn);
    });

    it(`${w}px (${label}) reaches the chats exactly one way`, () => {
      stubWidth(w);
      render(<App />);

      // Below the md edge the sidebar is a closed drawer, so the only way to the
      // chats is the ☰; above it the sidebar is a column with its own collapse
      // chevron and no ☰. Exactly one of the two, never both and never neither —
      // a window with neither cannot reach Tools, Snapshots or its own history.
      const burger = screen.queryByRole("button", { name: "Menu" });
      const chevron = screen.queryByRole("button", { name: /show chats|hide chats/i });
      expect(Boolean(burger), `☰ at ${w}px`).toBe(drawer);
      expect(Boolean(chevron), `collapse chevron at ${w}px`).toBe(!drawer);

      // The sidebar itself is rendered once above the edge and not at all below
      // (the drawer holds it, and the drawer starts closed).
      const workspaces = screen.queryAllByText("Workspace");
      expect(workspaces, `"Workspace" heading count at ${w}px`).toHaveLength(drawer ? 0 : 1);
    });
  }

  it("never renders the same widget rail inline and in the column at once", () => {
    // The two branches are written as an if/else, and this is the assertion that
    // keeps them that way: walk every width in one go and count.
    for (const { w } of WIDTHS) {
      stubWidth(w);
      const { unmount } = render(<App />);
      expect(screen.queryAllByLabelText("Your widgets"), `${w}px`).toHaveLength(1);
      unmount();
    }
  });

  it("keeps the composer reachable at every width", () => {
    for (const { w } of WIDTHS) {
      stubWidth(w);
      const { unmount } = render(<App />);
      const composer = screen.getByPlaceholderText(/write to addison|engine isn't connected/i);
      expect(composer, `no composer at ${w}px`).toBeTruthy();
      unmount();
    }
  });

  it("keeps the header title and the way home at every width", () => {
    for (const { w } of WIDTHS) {
      stubWidth(w);
      const { unmount } = render(<App />);
      const header = screen.getByRole("banner");
      expect(within(header).getByRole("button", { name: /new chat/i }), `${w}px`).toBeTruthy();
      unmount();
    }
  });
});
