// Accessibility + layout regressions in the app chrome (phase-3 review,
// 2026-07-26). Each of these was measured in the running app, and each one is
// silent: nothing throws, nothing looks obviously wrong, the app is simply
// unusable in a way that only shows up with a keyboard, a screen reader, a
// contrast meter, or a 820px window.
//
//   (1) A PENDING CONSENT CARD MUST OUTRANK THE FLOATING CHROME. On a surface
//       the card sat inside <main>, under the restore modal's and the drawer's
//       `fixed z-40` scrim: clicking "Allow" hit the scrim and closed the modal
//       instead of answering. A permission card blocks the turn, so that is a
//       dead end, not a cosmetic bug.
//   (2) HIDDEN COLUMNS MUST NOT HOLD FOCUS STOPS. A collapsed sidebar / rail is
//       0px wide, transparent and click-through, but its buttons still took Tab
//       — the ring rendered nowhere and Enter navigated.
//   (3) THE DRAWER IS A MODAL AND MUST SAY SO. No dialog role, no focus move, no
//       Tab trap meant a keyboard user tabbed through the page behind the scrim.
//   (4) THE LIGHT RAMP MUST CLEAR WCAG AA. Dark is the designed reference; light
//       was a translation by eye and landed at 1.53:1 for the composer's
//       microcopy.
//   (5) EFFORT MUST BE REACHABLE BY KEYBOARD. Tab closed the model menu, so the
//       three Effort buttons could never take focus — while the composer label
//       advertised the effort ("Claude Opus 4.8 · high").
//   (6) TWO SIDE COLUMNS DO NOT FIT UNDER 1024px. At 820px the reading column
//       measured 208px.
//   (7) A ROW NAME MUST NOT PAINT OUTSIDE ITS ROW.

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { App, SurfaceConsentLayer } from "../App";
import { MobileDrawer } from "../components/MobileDrawer";
import { ModelSelector } from "../components/ModelSelector";
import { PermissionCard } from "../components/PermissionCard";
import { RestorePointsModal } from "../components/RestorePointsModal";
import { SurfaceRow } from "../components/Surface";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { CloudModel } from "../types/ui";

afterEach(cleanup);

// jsdom ships no matchMedia, and every layout decision in App keys off one.
// `wide` is the ordinary desktop case: neither the mobile nor the 1024 query
// matches, so both side columns are up.
function stubMatchMedia(matches: (query: string) => boolean = () => false) {
  window.matchMedia = ((query: string) => ({
    matches: matches(query),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  stubMatchMedia();
  // App persists the rail/sidebar toggles, so a test that collapses one would
  // otherwise hand the next test a pre-collapsed shell.
  localStorage.clear();
  // jsdom has no layout, so no scrollIntoView; the model menu keeps the
  // highlighted row in view with it.
  Element.prototype.scrollIntoView = () => {};
});

// ---------------------------------------------------------------------------
// (1) the consent card outranks the floating chrome
// ---------------------------------------------------------------------------
/** The `z-<n>` Tailwind token on an element, as a number. */
function zLayer(el: Element): number {
  const token = Array.from(el.classList).find((c) => /^z-\d+$/.test(c));
  expect(token, `no z-<n> class on <${el.tagName.toLowerCase()}>`).toBeTruthy();
  return Number(token!.slice(2));
}

const EMPTY_SNAPSHOTS = {
  snapshots: [],
  snapshotsLoaded: true,
  warning: null,
  notice: null,
  busy: false,
} as unknown as SnapshotsState;

describe("the consent card on a surface", () => {
  it("sits on a layer strictly above the restore modal and the drawer", () => {
    const { container } = render(
      <>
        <RestorePointsModal connected snapshots={EMPTY_SNAPSHOTS} onClose={vi.fn()} />
        <MobileDrawer open onClose={vi.fn()}>
          <button type="button">A drawer control</button>
        </MobileDrawer>
        <SurfaceConsentLayer>
          <PermissionCard
            request={{
              toolId: "t",
              label: "Addison would like to read a page",
              description: "It will fetch example.com.",
              riskTier: "low",
            }}
            onRespond={vi.fn()}
          />
        </SurfaceConsentLayer>
      </>,
    );

    const consent = container.querySelector("[data-consent-layer]")!;
    // Both scrims are `fixed inset-0 z-40`. The card has to beat BOTH of them —
    // this is the whole fix: clicking Allow used to land on the scrim.
    const overlays = Array.from(container.querySelectorAll<HTMLElement>(".fixed.inset-0"));
    expect(overlays.length).toBeGreaterThanOrEqual(2);
    for (const overlay of overlays) {
      expect(zLayer(consent)).toBeGreaterThan(zLayer(overlay));
    }
    expect(consent.classList.contains("fixed")).toBe(true);
    // The layer spans the window, so it must be click-through; only the card
    // itself takes presses, or it would swallow every click on the page below.
    expect(consent.classList.contains("pointer-events-none")).toBe(true);
    expect(consent.firstElementChild!.classList.contains("pointer-events-auto")).toBe(true);
    expect(screen.getByRole("button", { name: "Allow" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (2) + (6) the app-shell columns
// ---------------------------------------------------------------------------
/** `<main>`'s direct children: [sidebar column, centre column, rail column?]. */
function columns(): HTMLElement[] {
  return Array.from(document.querySelector("main")!.children) as HTMLElement[];
}

describe("the collapsing side columns", () => {
  it("takes a collapsed sidebar out of the tab order entirely", () => {
    render(<App />);
    const [sidebar] = columns();
    // Open: a real column, reachable.
    expect(sidebar.hasAttribute("inert")).toBe(false);
    expect(within(sidebar).getByText("Settings")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide chats" }));
    // Collapsed: 0px wide and transparent, so every stop inside it would put the
    // focus ring nowhere. `inert` is what actually removes them — aria-hidden
    // and pointer-events:none never did.
    expect(sidebar.hasAttribute("inert")).toBe(true);
    expect(sidebar.getAttribute("aria-hidden")).toBe("true");
  });

  it("takes a hidden widget rail out of the tab order entirely", () => {
    render(<App />);
    const rail = columns()[2];
    expect(rail.hasAttribute("inert")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Hide widgets" }));
    expect(rail.hasAttribute("inert")).toBe(true);
    expect(rail.getAttribute("aria-hidden")).toBe("true");
  });

  it("sheds the widget rail below 1024px, before the drawer takes over", () => {
    // The 768–1024 band: too narrow for two side columns (the reading column
    // measured 208px at 820px), too wide for the mobile drawer.
    stubMatchMedia((q) => q.includes("1023.98"));
    render(<App />);
    expect(columns()).toHaveLength(2); // sidebar + centre, no rail COLUMN
    // The control stays, and this is the correction of 2026-07-27. It used to be
    // asserted ABSENT here, on the reasoning that it would "toggle a column that
    // cannot appear" — but the widgets do not disappear below 1024, they move
    // inline into the thread. So the button was hidden at exactly the widths where
    // the rail sat in the reading column and putting it away mattered most, and
    // this test called that correct. Below 1024 it drives the inline form, which
    // now starts closed.
    expect(screen.getByRole("button", { name: "Show widgets" })).toBeTruthy();
    // The sidebar survives — it is navigation; the rail is ambient.
    expect(screen.getByRole("button", { name: "Hide chats" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (3) the mobile drawer is a modal
// ---------------------------------------------------------------------------
describe("the mobile drawer", () => {
  function renderDrawer() {
    const onClose = vi.fn();
    const view = render(
      <MobileDrawer open onClose={onClose}>
        <div>
          <button type="button">First</button>
          <button type="button">Last</button>
        </div>
      </MobileDrawer>,
    );
    return { ...view, onClose };
  }

  it("scrims with the shared token, never with 25% of the text color", () => {
    const { container } = renderDrawer();
    const scrim = container.querySelector(".absolute.inset-0")!;
    // bg-ink/25 is 25% of the TEXT color: in dark that is near-white over
    // near-black, which BRIGHTENS the app behind the drawer (measured composite
    // rgb(67,67,68)) instead of pushing it back.
    expect(scrim.className).toContain("bg-scrim");
    expect(scrim.className).not.toContain("bg-ink/");
  });

  it("announces itself as a modal dialog and moves focus into it", () => {
    renderDrawer();
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "First" }));
  });

  it("keeps Tab inside the panel instead of walking the page behind the scrim", () => {
    renderDrawer();
    const dialog = screen.getByRole("dialog");
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });

    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(dialog, { key: "Tab" }); // wraps, never leaves
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});

// ---------------------------------------------------------------------------
// (5) Effort is reachable by keyboard
// ---------------------------------------------------------------------------
const CLOUD_WITH_EFFORT: CloudModel[] = [
  {
    id: "opus",
    label: "Claude Opus 4.8",
    effortLevels: [
      { id: "low", label: "low" },
      { id: "high", label: "high" },
    ],
    default: true,
  },
];
const CLOUD_NO_EFFORT: CloudModel[] = [
  { id: "haiku", label: "Claude Haiku 4.5", effortLevels: [], default: true },
];

function openModelMenu(cloudModels: CloudModel[], onSelectEffort = vi.fn()) {
  render(
    <ModelSelector
      roles={[]}
      cloudModels={cloudModels}
      selectedRole="primary"
      selectedCloudModel={cloudModels[0].id}
      onSelectModel={vi.fn()}
      onSelectEffort={onSelectEffort}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /^Model:/ }));
  return { list: screen.getByRole("tree"), onSelectEffort };
}

describe("the composer's model menu", () => {
  it("lets Tab reach the Effort buttons instead of closing the menu", () => {
    const { list, onSelectEffort } = openModelMenu(CLOUD_WITH_EFFORT);
    expect(document.activeElement).toBe(list);

    fireEvent.keyDown(list, { key: "Tab" });
    const low = screen.getByRole("button", { name: "low" });
    expect(document.activeElement).toBe(low);
    // Still open — Tab used to dismiss the whole panel here, which is why the
    // Effort buttons were unreachable by keyboard at all.
    expect(screen.getByRole("tree")).toBeTruthy();

    // "high ✓" — the active level carries its tick into the accessible name.
    fireEvent.keyDown(low, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /^high/ }));
    fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(low);

    fireEvent.click(low);
    expect(onSelectEffort).toHaveBeenCalledWith("low");
  });

  it("still closes on Escape and hands focus back to the label", () => {
    const { list } = openModelMenu(CLOUD_WITH_EFFORT);
    fireEvent.keyDown(list, { key: "Tab" }); // focus on an Effort button
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("tree")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Model:/ }));
  });

  it("lets Tab leave when there is no Effort section to reach", () => {
    const { list } = openModelMenu(CLOUD_NO_EFFORT);
    fireEvent.keyDown(list, { key: "Tab" });
    expect(screen.queryByRole("tree")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (7) a row name stays inside its row
// ---------------------------------------------------------------------------
describe("a surface row", () => {
  it("clips a long name and keeps the whole string in the title", () => {
    const name = "A restore point from before a very long-winded configuration change";
    render(<SurfaceRow name={name} value="today" />);
    const label = screen.getByText(name);
    expect(label.className).toContain("truncate");
    expect(label.getAttribute("title")).toBe(name);
  });
});

// ---------------------------------------------------------------------------
// (4) the light ramp clears WCAG AA
// ---------------------------------------------------------------------------
// Read as TEXT, not through the bundler: the point is to check the declared
// values, and a jsdom render never applies a Tailwind stylesheet anyway.
// (`import.meta.url` is an http URL under vitest's transform, hence the cwd.)
const CSS_PATH = ["src/styles.css", "shell/src/styles.css"]
  .map((p) => resolve(process.cwd(), p))
  .find(existsSync)!;
const CSS = readFileSync(CSS_PATH, "utf8");

/** The `--c-*` channel triples declared in the FIRST `:root {}` block (light).
 * `.dark` is the designed reference and is deliberately not checked here. */
function lightTokens(): Record<string, [number, number, number]> {
  const block = CSS.slice(CSS.indexOf(":root {"), CSS.indexOf(".dark {"));
  const out: Record<string, [number, number, number]> = {};
  for (const m of block.matchAll(/--c-([a-z-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

function relativeLuminance([r, g, b]: [number, number, number]): number {
  const chan = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
}

function contrast(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("the light palette", () => {
  // Every one of these was measured below its floor in the running app, and
  // every one is a value somebody could "tidy" back toward the dark ramp by eye.
  // 4.5 is AA for text (all of this copy is 10–13px, so the large-text exception
  // never applies); 3.0 is the non-text floor for a control's own boundary.
  const TEXT_ON_PAPER = ["ink", "ink-soft", "muted", "faint", "disabled", "ghost", "accent"];
  const UI_ON_PAPER = ["track", "track-hi", "scrollbar", "scrollbar-hi"];

  it("keeps every text token at or above 4.5:1 on the paper background", () => {
    const t = lightTokens();
    for (const name of TEXT_ON_PAPER) {
      expect(t[name], `--c-${name} missing from :root`).toBeTruthy();
      expect(
        Number(contrast(t[name], t.paper).toFixed(2)),
        `--c-${name} on --c-paper`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps the visible control affordances at or above 3:1", () => {
    const t = lightTokens();
    for (const name of UI_ON_PAPER) {
      expect(t[name], `--c-${name} missing from :root`).toBeTruthy();
      expect(
        Number(contrast(t[name], t.paper).toFixed(2)),
        `--c-${name} on --c-paper`,
      ).toBeGreaterThanOrEqual(3);
    }
  });

  it("keeps the ramp descending, so the hierarchy still reads", () => {
    const t = lightTokens();
    const ramp = ["ink", "ink-soft", "muted", "faint", "disabled", "ghost"];
    const ratios = ramp.map((n) => contrast(t[n], t.paper));
    for (let i = 1; i < ratios.length; i += 1) {
      expect(ratios[i], `--c-${ramp[i]} must be lighter than --c-${ramp[i - 1]}`).toBeLessThan(
        ratios[i - 1],
      );
    }
  });
});
