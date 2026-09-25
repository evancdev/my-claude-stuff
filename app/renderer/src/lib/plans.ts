// Mirrors as_json() in scripts/plans.py.

export type Plan = {
  name: string;
  status: string;
  archived: boolean;
  milestone: number | null;
  total: number;
  done: number;
  files: string[];
  problems: string[];
};

export type Repo = {
  slug: string;
  label: string;
  note: string;
  problems: string[];
  plans: Plan[];
};

declare global {
  interface Window {
    dashboard: { plans: () => Promise<{ repos: Repo[] }> };
  }
}
