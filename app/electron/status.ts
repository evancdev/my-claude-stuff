import { execFile } from "node:child_process";
import { join } from "node:path";
import { commaList, frontmatter, landingBranches, planFolder, readText, repoPath } from "./plans.ts";

export type Checks = { passed: number; failed: number; running: number; cancelled: number; skipped: number };

export type PrStatus = {
  number: string;
  url: string;
  head: string;
  state: "open" | "draft" | "merged" | "closed";
  review: string;
  checks: Checks;
  merged: string;
};

export type BranchStatus = {
  name: string;
  commit: string | null;
  base: string;
  parent: string | null;
  ahead: number | null;
  behind: number | null;
  worktree: string | null;
  changes: number | null;
};

type Failed = { number?: string; name?: string; error: string };

export type Section = { pr: PrStatus | Failed | null; branch: BranchStatus | Failed | null };

export type PlanStatus = Section[];

const env = {
  ...process.env,
  // An app opened from Finder gets a PATH without Homebrew on it, where gh
  // lives. Homebrew first, as in a terminal, so git is the same one.
  PATH: ["/opt/homebrew/bin", "/usr/local/bin", process.env.PATH].join(":"),
  // So git status doesn't take index.lock from an agent working in the same
  // worktree.
  GIT_OPTIONAL_LOCKS: "0",
};

function run(cwd: string, command: string, ...args: string[]) {
  return new Promise<string | null>((resolve) => {
    execFile(command, args, { cwd, env, timeout: 15_000 }, (err, stdout) => resolve(err ? null : stdout.trim()));
  });
}

const FAILED = ["FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"];

// A check run has a conclusion once it finishes; a commit status only ever has
// a state. Anything without either is still running.
export function tallyChecks(rollup: { conclusion?: string; state?: string }[]): Checks {
  const checks = { passed: 0, failed: 0, running: 0, cancelled: 0, skipped: 0 };
  for (const check of rollup) {
    const result = (check.conclusion || check.state || "").toUpperCase();
    if (result === "SUCCESS") checks.passed++;
    else if (FAILED.includes(result)) checks.failed++;
    else if (result === "CANCELLED") checks.cancelled++;
    else if (["SKIPPED", "NEUTRAL", "STALE"].includes(result)) checks.skipped++;
    else checks.running++;
  }
  return checks;
}

async function prStatus(repo: string, number: string): Promise<PrStatus | Failed> {
  if (!/^\d+$/.test(number)) return { number, error: "not a PR number" };
  const fields = "state,isDraft,reviewDecision,statusCheckRollup,mergedAt,url,headRefName";
  const out = await run(repo, "gh", "pr", "view", number, "--json", fields);
  try {
    const pr = JSON.parse(out ?? "");
    return {
      number,
      url: pr.url,
      head: pr.headRefName,
      state: pr.state === "MERGED" ? "merged" : pr.state === "CLOSED" ? "closed" : pr.isDraft ? "draft" : "open",
      review: (pr.reviewDecision ?? "").toLowerCase().replaceAll("_", " "),
      checks: tallyChecks(pr.statusCheckRollup ?? []),
      merged: pr.mergedAt ?? "",
    };
  } catch {
    return { number, error: "gh could not read it" };
  }
}

export function worktreeFor(porcelain: string, branch: string) {
  for (const block of porcelain.split("\n\n")) {
    const lines = block.split("\n");
    if (lines.includes(`branch refs/heads/${branch}`)) {
      return lines.find((line) => line.startsWith("worktree "))?.slice("worktree ".length) ?? null;
    }
  }
  return null;
}

type Git = (...args: string[]) => Promise<string | null>;

function gitIn(repo: string): Git {
  return (...args) => run(repo, "git", ...args);
}

type RepoState = { base: string; worktrees: string };

// What every branch in a repo shares, so a plan with several reads it once.
function repoState(git: Git): Promise<RepoState> {
  return Promise.all([
    git("symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
    git("worktree", "list", "--porcelain"),
  ]).then(([base, worktrees]) => ({ base: base || "main", worktrees: worktrees ?? "" }));
}

// The branch this one was cut from or rebased onto: of the branches that share
// its history, the one it has the fewest commits beyond.
async function parentBranch(git: Git, ref: string, name: string, base: string) {
  const format = `%(refname) %(refname:short) %(ahead-behind:${ref})`;
  const out = await git("for-each-ref", `--format=${format}`, "refs/heads", "refs/remotes/origin");
  if (out === null) return null;
  const skip = [`refs/heads/${name}`, `refs/remotes/origin/${name}`, "refs/remotes/origin/HEAD"];
  const candidates = [];
  for (const line of out.split("\n")) {
    const [full, short, theirs, mine] = line.split(" ");
    if (!full || skip.includes(full)) continue;
    // Holds all of this one: a copy, a branch cut from it, or one it was
    // merged into. The base after a merge is still the answer.
    if (+mine === 0 && short !== base) continue;
    candidates.push({ short, mine: +mine, theirs: +theirs, other: short !== base, remote: full.startsWith("refs/remotes/") });
  }
  // The base wins a tie, so a branch cut from the same commit doesn't.
  candidates.sort((a, b) => a.mine - b.mine || +a.other - +b.other || a.theirs - b.theirs || +a.remote - +b.remote);
  return candidates[0]?.short ?? base;
}

export const MISSING = "not on this machine";

export async function branchStatus(
  repo: string,
  name: string,
  shared?: Promise<RepoState>,
): Promise<BranchStatus | Failed> {
  // git reads an argument with a leading "-" as an option.
  if (!/^(?!-)[\w./-]+$/.test(name)) return { name, error: "not a branch name" };
  const git = gitIn(repo);
  let ref = null;
  for (const candidate of [`refs/heads/${name}`, `refs/remotes/origin/${name}`]) {
    if ((await git("rev-parse", "--verify", "--quiet", candidate)) !== null) {
      ref = candidate;
      break;
    }
  }
  if (!ref) return { name, error: MISSING };
  const { base, worktrees } = await (shared ?? repoState(git));
  const [log, counts, parent] = await Promise.all([
    git("log", "-1", "--format=%s", ref),
    git("rev-list", "--left-right", "--count", `${base}...${ref}`),
    parentBranch(git, ref, name, base),
  ]);
  const [behind, ahead] = counts?.split(/\s+/).map(Number) ?? [null, null];
  const worktree = worktreeFor(worktrees, name);
  const changes = worktree ? await run(worktree, "git", "status", "--porcelain") : "";
  return {
    name,
    commit: log || null,
    base,
    parent,
    ahead,
    behind,
    worktree,
    changes: changes === null ? null : changes ? changes.split("\n").length : 0,
  };
}

// One per branch the plan lands on, with the PR opened from it. `pr:` lists
// PRs in the order they were opened, so a branch with two takes the later one,
// and a PR gh couldn't read, which has no branch to match, takes the next branch
// still without one. Any PR left over gets a section of its own.
export function sections(branches: (BranchStatus | Failed)[], prs: (PrStatus | Failed)[]): Section[] {
  const out: Section[] = branches.map((branch) => ({
    pr: prs.findLast((pr) => "head" in pr && pr.head === branch.name) ?? null,
    branch,
  }));
  for (const pr of prs) {
    const free = !("head" in pr) && out.find((section) => !section.pr);
    if (free) free.pr = pr;
  }
  for (const pr of prs) if (!out.some((section) => section.pr === pr)) out.push({ pr, branch: null });
  return out;
}

// A second branch is cut when its first milestone opens, so until then it
// isn't missing, just not made yet. main is the plan's own branch.
export function notCutYet(branches: (BranchStatus | Failed)[], main: string) {
  return branches.map((branch) =>
    "error" in branch && branch.error === MISSING && branch.name !== main
      ? { ...branch, error: "not created yet" }
      : branch,
  );
}

export async function planStatus(projects: string, slug: unknown, plan: unknown): Promise<PlanStatus> {
  const folder = await planFolder(projects, slug, plan);
  if (!folder || typeof slug !== "string") return [];
  const master = (await readText(join(folder, "master.md"))) ?? "";
  const meta = frontmatter(master);
  const numbers = [...new Set(commaList(meta.get("pr") ?? "").map((part) => part.replace(/^#/, "")))];
  const main = meta.get("branch") ?? "";
  const names = [...new Set([main, ...landingBranches(master)].filter(Boolean))];
  const repo = await repoPath(slug);
  if (!repo) {
    const error = "repo not found on this machine";
    return sections(
      names.map((name) => ({ name, error })),
      numbers.map((number) => ({ number, error })),
    );
  }
  const shared = repoState(gitIn(repo));
  const [prs, branches] = await Promise.all([
    Promise.all(numbers.map((number) => prStatus(repo, number))),
    Promise.all(names.map((name) => branchStatus(repo, name, shared))),
  ]);
  return sections(notCutYet(branches, main), prs);
}
