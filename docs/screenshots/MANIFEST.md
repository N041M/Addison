# Screenshots — generated, not hand-taken

**Regenerate with one command. Do not edit these by hand.**

```bash
cd shell && npm run dev          # in one terminal
cd shell && npm run screenshots  # in another
```

Source: [`shell/scripts/capture-screenshots.mjs`](../../shell/scripts/capture-screenshots.mjs).

| Image | Shows | Captured against |
|---|---|---|
| `hero.png` | The three-column shell, the first-run block, a composed message. Dark. | `8768cab` |
| `hero-light.png` | The same, light theme. | `8768cab` |
| `settings.png` | Settings — providers, keys, local models. Dark. | `8768cab` |

## Why these are generated

Images are the **only** documentation this repo's drift tests cannot check — a
stale PNG is invisible to CI, and the UI has been redesigned wholesale four times
(cool-slate → terminal → Fern → dark v4). Generating them makes "is this current?"
answerable by re-running one command instead of by eye.

## What they are honest about

Every pixel is the real shipped frontend on the vite dev server. **The Agent Core
is not running**, so anything needing live data is out of scope and deliberately
not captured: a streamed reply, the widget rail's token meter, a permission card,
the restore-point list. Those need `npm run tauri dev` and a real key.

One piece of stagecraft, declared: without a core the composer placeholder reads
*"Addison's engine isn't connected yet."* The script types a real message into it
— what a user would do — rather than hiding the line.

## When to retake

Any change to the app shell, the empty state, the composer, Settings, the theme
tokens, or the scramble timing. `TESTING-CHECKLIST.md` §13 carries the reminder.
