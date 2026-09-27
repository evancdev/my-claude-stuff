import { useEffect, useSyncExternalStore } from "react";

// Kept for the session, so going back to a page draws what it loaded on the
// first render and the saved scroll position has something to land on.
const loaded = new Map<string, unknown>();
const listeners = new Set<() => void>();
// One read per key at a time, so an older read can't land over a newer one.
const reading = new Set<string>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

// undefined until the first read, then read again each time the window regains
// focus.
function useLoaded<T>(key: string, read: () => Promise<T>) {
  const value = useSyncExternalStore(subscribe, () => loaded.get(key) as T | undefined);

  useEffect(() => {
    const load = async () => {
      if (reading.has(key)) return;
      reading.add(key);
      try {
        loaded.set(key, await read());
        for (const listener of listeners) listener();
      } finally {
        reading.delete(key);
      }
    };
    load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
    // read is a new function every render, but reads the same thing per key.
  }, [key]);

  return value;
}

// null if the file can't be read.
export function usePlanFile(repo: string, plan: string, file: string) {
  return useLoaded(JSON.stringify(["file", repo, plan, file]), () => window.dashboard.planFile(repo, plan, file));
}

export function usePlanStatus(repo: string, plan: string) {
  return useLoaded(JSON.stringify(["status", repo, plan]), () => window.dashboard.planStatus(repo, plan));
}
