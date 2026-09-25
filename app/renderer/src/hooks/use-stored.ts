import { useEffect, useState } from "react";

export function useStored<T extends object>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) ?? "null");
      // A field missing from an older saved shape takes its initial value.
      return saved && typeof saved === "object" ? { ...initial, ...saved } : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {}
  }, [key, value]);
  return [value, setValue] as const;
}
