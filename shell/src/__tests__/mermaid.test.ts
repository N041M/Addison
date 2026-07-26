// Every mermaid block in the repo's markdown must parse.
//
// The docs lean on mermaid heavily — architecture, classes, data-model and flows
// are 21–64% diagram — and a diagram that fails to parse renders as a raw code
// block or an error box wherever the markdown is viewed. Nothing else in the
// gates looks at them, so a typo in a diagram ships silently.
//
// This runs in vitest rather than as a standalone script because mermaid needs a
// DOM (`DOMPurify.addHook`), and vitest already provides jsdom.
//
// Parsing is NOT the same as rendering well. It cannot tell you a graph is an
// unreadable tangle of crossing edges — that is what the 2026-07-27 report was
// about, and it took a human looking at it. This only catches the syntax half.

import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import mermaid from "mermaid";

// NOT `import.meta.url`: under Vite that resolves to a `/@fs/…` virtual path,
// which `readdirSync` cannot open. vitest runs with `shell/` as cwd.
const REPO = join(process.cwd(), "..");
const SKIP = new Set(["node_modules", ".git", ".venv", ".claude", "dist", ".pytest_cache"]);

function markdownFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) markdownFiles(path, out);
    else if (entry.endsWith(".md")) out.push(path);
  }
  return out;
}

interface Block {
  file: string;
  index: number;
  source: string;
}

function mermaidBlocks(): Block[] {
  const blocks: Block[] = [];
  for (const file of markdownFiles(REPO)) {
    const text = readFileSync(file, "utf8");
    const matches = text.match(/```mermaid\n([\s\S]*?)```/g) ?? [];
    matches.forEach((raw, index) => {
      blocks.push({
        file: relative(REPO, file),
        index,
        source: raw.replace(/```mermaid\n/, "").replace(/```$/, ""),
      });
    });
  }
  return blocks;
}

describe("mermaid diagrams in the docs", () => {
  const blocks = mermaidBlocks();

  it("finds the diagrams at all", () => {
    // Guards against the walk silently matching nothing and the suite going
    // green because it checked zero blocks.
    expect(blocks.length).toBeGreaterThan(10);
  });

  it.each(blocks.map((b) => [`${b.file} #${b.index + 1}`, b] as const))(
    "%s parses",
    async (_label, block) => {
      await expect(mermaid.parse(block.source)).resolves.toBeTruthy();
    },
  );
});
