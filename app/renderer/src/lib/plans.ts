import type { Repo } from "../../../electron/plans";

export type { Plan, Repo } from "../../../electron/plans";

declare global {
  interface Window {
    dashboard: { plans: () => Promise<{ repos: Repo[] }> };
  }
}
