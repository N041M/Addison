// The pointer glow is decoration, so the things worth pinning are the ones that
// stop decoration from costing anything: it must not re-render the app, it must
// not run when motion is off, and it must not leave a listener behind.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import { useRef } from "react";
import { PointerGlow } from "../components/PointerGlow";
import { setMotionEnabled } from "../lib/scramble";

afterEach(() => {
  cleanup();
  setMotionEnabled(true);
});
beforeEach(() => setMotionEnabled(true));

// jsdom ships no PointerEvent constructor. A MouseEvent of the same type is
// what the listener actually reads (clientX/clientY), so it exercises the real
// handler rather than a stand-in.
function move(x: number, y: number) {
  act(() => {
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: x, clientY: y }));
  });
}

/** jsdom has no rAF scheduling we can await, so drain it by hand. */
function flushFrames() {
  act(() => {
    vi.advanceTimersByTime(32);
  });
}

describe("the pointer glow", () => {
  it("follows the pointer, and sits a little below it", () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<PointerGlow />);
      const el = container.querySelector(".pointer-glow") as HTMLElement;
      expect(el).toBeTruthy();
      expect(el.getAttribute("aria-hidden")).toBe("true");
      expect(el.style.transform).toBe("");

      move(400, 200);
      flushFrames();

      // 10px of drop is what makes it read as *under* the cursor.
      expect(el.style.transform).toBe("translate3d(400px, 210px, 0)");
      expect(el.style.opacity).toBe("1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("never re-renders its parent while the pointer moves", () => {
    vi.useFakeTimers();
    try {
      let renders = 0;
      function Host() {
        renders += 1;
        const seen = useRef(0);
        seen.current += 1;
        return <PointerGlow />;
      }
      render(<Host />);
      const before = renders;

      for (let i = 0; i < 20; i++) move(100 + i, 100 + i);
      flushFrames();

      expect(renders).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does nothing at all when motion is off", () => {
    vi.useFakeTimers();
    try {
      setMotionEnabled(false);
      const { container } = render(<PointerGlow />);
      const el = container.querySelector(".pointer-glow") as HTMLElement;

      move(400, 200);
      flushFrames();

      expect(el.style.transform).toBe("");
      expect(el.style.opacity).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("takes the light away when the pointer leaves the window", () => {
    vi.useFakeTimers();
    try {
      const { container } = render(<PointerGlow />);
      const el = container.querySelector(".pointer-glow") as HTMLElement;
      move(400, 200);
      flushFrames();
      expect(el.style.opacity).toBe("1");

      act(() => {
        document.dispatchEvent(new MouseEvent("pointerleave"));
      });

      expect(el.style.opacity).toBe("0");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops listening once it is gone", () => {
    vi.useFakeTimers();
    try {
      const { container, unmount } = render(<PointerGlow />);
      const el = container.querySelector(".pointer-glow") as HTMLElement;
      move(400, 200);
      flushFrames();
      const parked = el.style.transform;

      unmount();
      move(900, 900);
      flushFrames();

      expect(el.style.transform).toBe(parked); // the detached node is left alone
    } finally {
      vi.useRealTimers();
    }
  });
});
