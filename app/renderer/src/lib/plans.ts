import type { PlanFile, Repo } from "../../../electron/plans";
import type { PlanStatus } from "../../../electron/status";

export type { Plan, PlanFile, Repo } from "../../../electron/plans";
export type { BranchStatus, PlanStatus, PrStatus } from "../../../electron/status";

declare global {
  interface Window {
    dashboard: {
      plans: () => Promise<{ repos: Repo[] }>;
      planFile: (repo: string, plan: string, file: string) => Promise<PlanFile | null>;
      planStatus: (repo: string, plan: string) => Promise<PlanStatus>;
      zoom: () => number;
    };
  }
}
