// Capture the README screenshots from the real frontend.
//
// WHY THIS EXISTS: images are the only documentation this repo's drift tests
// cannot check — a stale PNG is invisible to CI. So the shots are *generated*
// from the app rather than taken by hand, which makes them cheap to redo and
// makes "is this current?" answerable by re-running one command.
//
// It drives the ACTUAL frontend on the vite dev server, so every pixel is real
// shipped UI. It does NOT fake a connected backend: the Agent Core is a separate
// process and is not running here, so surfaces that need live data (a streamed
// reply, the token meter, a permission card) are deliberately out of scope —
// those need `npm run tauri dev` and a real key. What IS captured is the
// first-run experience, which is exactly what a new reader wants to see.
//
// The one piece of stagecraft, declared: the composer placeholder reads
// "Addison's engine isn't connected yet" when there is no core, which is an
// artifact of this harness rather than something a user sees. We type a real
// message into it — the same thing a user would do — instead of hiding it.
//
// Usage:  cd shell && npm run screenshots     (expects vite already on :5173)
//
// Requires the `playwright` devDependency and a local Google Chrome; no browser
// is downloaded (channel: "chrome").

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const TARGET = process.env.ADDISON_URL ?? "http://localhost:5173";
const OUT = new URL("../../docs/screenshots/", import.meta.url).pathname;
const VIEWPORT = { width: 1440, height: 900 };

// One message, used by both theme shots so the light/dark pair in the README is
// actually comparable. A reload clears the composer, so it has to be re-typed
// after `setTheme` — the first light shot went out with an empty composer and
// the "engine isn't connected" placeholder showing.
const COMPOSED =
  "Rename the photos in my Downloads folder so they sort by date, and tell me " +
  "what you changed before you touch anything.";

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({
  viewport: VIEWPORT,
  deviceScaleFactor: 2, // retina, so the images stay crisp when scaled down
  colorScheme: "dark",
});
const page = await context.newPage();

await page.goto(TARGET, { waitUntil: "networkidle" });

// The greeting stack scrambles in on mount. `waitForTextToSettle` (below —
// declarations hoist) is what keeps the shot on resolved text rather than a frame
// of noise.
await waitForTextToSettle();

/**
 * Wait until the rendered text stops changing.
 *
 * A fixed timeout is not enough: the signature character-scramble resolves each
 * surface over ~1.1s plus a per-element stagger, and a shot taken mid-flight
 * captures glyph noise that reads as mojibake — the first run of this script
 * produced a Settings page titled "SettinOE". Sampling until two consecutive
 * reads match makes the wait proportional to whatever the animation actually
 * does, so a future motion change cannot silently corrupt the screenshots.
 */
async function waitForTextToSettle(timeoutMs = 8000) {
  const started = Date.now();
  let previous = null;
  while (Date.now() - started < timeoutMs) {
    const current = await page.evaluate(() => document.body.innerText);
    if (current === previous) return;
    previous = current;
    await page.waitForTimeout(250);
  }
  console.warn("  (text never settled — motion may be running indefinitely)");
}

async function shot(name, prepare) {
  if (prepare) await prepare();
  await waitForTextToSettle();
  await page.screenshot({ path: `${OUT}${name}.png` });
  console.log(`  ${name}.png`);
}

/**
 * Switch theme and reload.
 *
 * NOT an `addInitScript`: that runs on EVERY navigation, so pinning the theme
 * that way meant the reload below re-applied it and the "light" shot came out
 * dark — shipped that way for one commit (reported 2026-07-27). localStorage
 * survives a reload on its own, so setting it once and reloading is both simpler
 * and actually correct.
 */
async function setTheme(theme) {
  await page.evaluate((t) => localStorage.setItem("addison.theme", t), theme);
  await page.reload({ waitUntil: "networkidle" });
  await waitForTextToSettle();
}

async function composeMessage() {
  await page.getByRole("textbox", { name: /Message to Addison/i }).fill(COMPOSED);
}

async function openBothColumns() {
  for (const label of ["Show chats", "Show widgets"]) {
    const button = page.getByRole("button", { name: label });
    if (await button.count()) await button.first().click();
  }
  await page.waitForTimeout(400);
}

console.log("capturing:");

// Dark is the designed reference; light is a derived translation.
await setTheme("dark");

// 1. The hero: full three-column shell, first-run block, a composed message.
await openBothColumns();
await shot("hero", composeMessage);

// 2. Settings — the profile/mode surface, where the safety model is user-facing.
await shot("settings", async () => {
  await page.getByRole("button", { name: "Settings" }).first().click();
});

// Snapshots and Tools are deliberately NOT captured. Both list data the Agent
// Core supplies, so headless they render one honest sentence over an empty page
// ("Your restore points appear here once Addison's engine is connected") — true,
// and useless as an illustration. Capturing them would mean either shipping a
// mostly-blank image or faking the data. They need `npm run tauri dev`.

// 5. Light theme, since the theme is a three-way and light is a real surface.
await shot("hero-light", async () => {
  await setTheme("light");
  await openBothColumns();
  await composeMessage();
});

await browser.close();
console.log(`\nwrote to docs/screenshots/ at ${VIEWPORT.width}x${VIEWPORT.height} @2x`);
