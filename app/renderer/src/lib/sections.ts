import type { PlanStatus } from "./plans";

// A branch with no PR yet still has work to land, so it holds the plan open.
export function readyToArchive(status: PlanStatus, archived: boolean) {
  return (
    !archived && status.length > 0 && status.every(({ pr }) => pr !== null && "state" in pr && pr.state === "merged")
  );
}

// Whether each box names its branch. The plan's own branch, main, is already
// named in the frontmatter box beside a lone one.
export function namesBranches(status: PlanStatus, main?: string) {
  return status.length > 1 || status.some(({ branch }) => branch !== null && branch.name !== main);
}
