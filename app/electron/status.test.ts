import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, test } from "node:test";
import { branchStatus, tallyChecks, worktreeFor } from "./status.ts";

describe("tallyChecks", () => {
  test("counts finished check runs by conclusion and commit statuses by state", () => {
    // Fails if a check still running, which has no conclusion yet, is counted
    // as passed or dropped.
    assert.deepEqual(
      tallyChecks([
        { conclusion: "SUCCESS" },
        { conclusion: "FAILURE" },
        { conclusion: "CANCELLED" },
        { conclusion: "SKIPPED" },
        { conclusion: "" },
        { state: "SUCCESS" },
        { state: "ERROR" },
        { state: "PENDING" },
        { conclusion: "STALE" },
      ]),
      { passed: 2, failed: 2, running: 2, cancelled: 1, skipped: 2 },
    );
  });
});

describe("worktreeFor", () => {
  test("finds the worktree that has the branch checked out", () => {
    const porcelain = [
      "worktree /repo\nHEAD 1\nbranch refs/heads/main",
      "worktree /repo/.worktrees/a b\nHEAD 2\nbranch refs/heads/feature",
      "worktree /repo/.worktrees/c\nHEAD 3\ndetached",
    ].join("\n\n");
    assert.equal(worktreeFor(porcelain, "feature"), "/repo/.worktrees/a b");
    // Fails if a branch whose name another one starts with matches.
    assert.equal(worktreeFor(porcelain, "feat"), null);
  });
});

describe("branchStatus", () => {
  let repo: string;
  const git = (cwd: string, ...args: string[]) =>
    execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd, stdio: "pipe" }).toString();
  const branchOff = (from: string, name: string) =>
    git(repo, "branch", name, git(repo, "commit-tree", "-p", from, "-m", name, `${from}^{tree}`).trim());

  before(() => {
    repo = realpathSync(mkdtempSync(join(tmpdir(), "grug-status-")));
    git(repo, "init", "-q", "-b", "main");
    git(repo, "commit", "-q", "--allow-empty", "-m", "start");
    branchOff("main", "old");
    branchOff("main", "shipped");
    git(repo, "merge", "-q", "--no-ff", "-m", "merge shipped", "shipped");
    branchOff("main", "aside");
    git(repo, "worktree", "add", "-q", "-b", "feature", join(repo, "wt"));
    git(join(repo, "wt"), "commit", "-q", "--allow-empty", "-m", "one");
    git(join(repo, "wt"), "commit", "-q", "--allow-empty", "-m", "two on the branch");
    git(repo, "commit", "-q", "--allow-empty", "-m", "main moves on");
    branchOff("feature", "stacked");
    git(repo, "branch", "stacked-copy", "stacked");
    writeFileSync(join(repo, "wt", "draft.txt"), "x");
  });
  after(() => rmSync(repo, { recursive: true, force: true }));

  test("says where a branch stands against main and what its worktree holds", async () => {
    const status = await branchStatus(repo, "feature");
    assert.ok(status && !("error" in status));
    assert.equal(status.commit, "two on the branch");
    assert.deepEqual([status.base, status.ahead, status.behind], ["main", 2, 1]);
    assert.deepEqual([status.worktree, status.changes], [join(repo, "wt"), 1]);
  });

  test("names the branch it was cut from or rebased onto", async () => {
    // Fails if a branch cut from the same commit as feature, one cut from
    // feature itself, a copy of stacked, or an older branch once shipped is
    // merged into main is named instead.
    for (const [name, parent] of [["feature", "main"], ["stacked", "feature"], ["shipped", "main"]]) {
      const status = await branchStatus(repo, name);
      assert.ok(!("error" in status));
      assert.equal(status.parent, parent);
    }
  });

  test("reports a branch that isn't here, and never hands git an option", async () => {
    assert.deepEqual(await branchStatus(repo, "gone"), { name: "gone", error: "not on this machine" });
    assert.deepEqual(await branchStatus(repo, "--output=x"), { name: "--output=x", error: "not a branch name" });
  });
});
