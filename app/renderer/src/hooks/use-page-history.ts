import { type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import type { Page } from "@/lib/page";

const HOME: Page = { section: "plans" };

const SWIPE = 100;
const QUIET = 250;
// A swipe coasts on after the fingers lift, fading a few percent per event,
// and the next swipe can start before it stops. Fingers landing cut the speed
// to under this share of the last two events in one step.
const DROP = 1 / 3;
// Nobody lands a second swipe this soon, so a dip this close to a fire is the
// same swipe.
const SETTLE = 200;
// So the faint tail of a long coast never adds up to a swipe.
const SLOW = 6;

// macOS reports trackpad scrolling in whole pixels and a notched mouse wheel in
// fractions, so only a trackpad swipe navigates. Blink divides the deltas by
// page zoom, which devicePixelRatio undoes.
function fromTrackpad(dx: number) {
  const px = dx * devicePixelRatio;
  return Math.abs(px - Math.round(px)) < 1e-6;
}

type Entry = { page: Page; key: string };

function entry(page: Page): Entry {
  return { page, key: crypto.randomUUID() };
}

// An entry without a key was saved by an older build, or by a #fragment jump.
function current(): Entry {
  if (!history.state?.key) history.replaceState(entry(HOME), "");
  return history.state;
}

function scrollsX(target: EventTarget | null, dx: number) {
  for (let el = target instanceof Element ? target : null; el; el = el.parentElement) {
    const room = dx < 0 ? el.scrollLeft : el.scrollWidth - el.clientWidth - el.scrollLeft;
    // At the edge, scrollLeft can be a fraction short of the end.
    if (room > 1 && /auto|scroll/.test(getComputedStyle(el).overflowX)) return true;
  }
  return false;
}

// macOS sends a two-finger swipe as horizontal wheel events, and Electron,
// unlike Chrome, doesn't turn them into back and forward, so this does.
export function usePageHistory(scroller: RefObject<HTMLElement | null>) {
  const [page, setPage] = useState<Page>(() => history.state?.page ?? HOME);
  const [scrolls] = useState(() => new Map<string, number>());
  const shown = useRef("");

  // <main> scrolls, not the document, so the browser can't restore it.
  const show = useCallback(
    (next: Entry, top: number) => {
      scrolls.set(shown.current, scroller.current?.scrollTop ?? 0);
      shown.current = next.key;
      flushSync(() => setPage(next.page));
      if (scroller.current) scroller.current.scrollTop = top;
    },
    [scrolls, scroller],
  );

  useEffect(() => {
    shown.current = current().key;
    const pop = () => {
      const next = current();
      show(next, scrolls.get(next.key) ?? 0);
    };

    let sum = 0;
    let last = -Infinity;
    let firedAt = -Infinity;
    let fired = false;
    let blocked = false;
    let direction = 0;
    let recent = [0, 0];
    const wheel = (e: WheelEvent) => {
      if (e.ctrlKey || !fromTrackpad(e.deltaX)) return;
      const speed = Math.abs(e.deltaX);
      const paused = e.timeStamp - last > QUIET;
      const settled = e.timeStamp - firedAt > SETTLE;
      const turned = speed > 0 && Math.sign(e.deltaX) !== direction;
      const landed = speed > 0 && speed < Math.min(...recent) * DROP;
      last = e.timeStamp;
      if (speed > 0) recent = [recent[1], speed];
      if (paused || (settled && (turned || landed))) {
        sum = 0;
        fired = false;
        if (speed > 0) direction = Math.sign(e.deltaX);
        // Once per swipe, so scrolling something to its edge doesn't carry on
        // into a navigation.
        blocked = scrollsX(e.target, direction);
      }
      if (fired || blocked) return;
      if (Math.abs(e.deltaY) > speed) {
        sum = 0;
        return;
      }
      sum += e.deltaX;
      // In screen points, so a swipe is as long at any page zoom.
      const zoom = window.dashboard.zoom();
      if (Math.abs(sum) * zoom < SWIPE || speed * zoom <= SLOW) return;
      fired = true;
      firedAt = e.timeStamp;
      // With natural scrolling, fingers moving right scroll left.
      if (sum < 0) history.back();
      else history.forward();
    };

    window.addEventListener("popstate", pop);
    window.addEventListener("wheel", wheel, { passive: true });
    return () => {
      window.removeEventListener("popstate", pop);
      window.removeEventListener("wheel", wheel);
    };
  }, [scrolls, show]);

  const navigate = useCallback(
    (target: Page) => {
      if (JSON.stringify(target) === JSON.stringify(current().page)) return;
      const next = entry(target);
      history.pushState(next, "");
      show(next, 0);
    },
    [show],
  );

  return [page, navigate] as const;
}
