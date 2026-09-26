import { useCallback, useEffect, useState } from "react";
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
export function usePageHistory() {
  const [page, setPage] = useState<Page>(() => history.state ?? HOME);

  useEffect(() => {
    if (!history.state) history.replaceState(HOME, "");
    const pop = (e: PopStateEvent) => setPage(e.state ?? HOME);

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
      if (Math.abs(sum) < SWIPE || speed <= SLOW) return;
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
  }, []);

  const navigate = useCallback((next: Page) => {
    if (JSON.stringify(next) === JSON.stringify(history.state)) return;
    history.pushState(next, "");
    setPage(next);
  }, []);

  return [page, navigate] as const;
}
