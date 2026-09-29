import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, test } from "node:test";
import { branchStatus, MISSING, notCutYet, sections, tallyChecks, worktreeFor } from "./status.ts";

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

describe("sections", () => {
  test("puts each PR beside the branch it was opened from", () => {
    // Fails if a PR shows under the wrong branch, or one gh couldn't read, or
    // one opened from a branch the plan doesn't name, is dropped.
    const branch = (name: string) => ({
      name,
      commit: null,
      base: "main",
      parent: null,
      ahead: 0,
      behind: 0,
      worktree: null,
      changes: null,
    });
    const pr = (number: string, head: string) => ({
      number,
      url: "",
      head,
      state: "draft" as const,
      review: "",
      checks: tallyChecks([]),
      merged: "",
    });
    const [plan, split] = [branch("test/demo"), branch("test/demo-split")];
    const [first, second, stray] = [pr("1", "test/demo"), pr("2", "test/demo-split"), pr("3", "elsewhere")];
    const unread = { number: "4", error: "gh could not read it" };
    assert.deepEqual(sections([plan, split], [second, unread, first, stray]), [
      { pr: first, branch: plan },
      { pr: second, branch: split },
      { pr: unread, branch: null },
      { pr: stray, branch: null },
    ]);
  });

  test("a PR gh couldn't read still sits beside its branch", () => {
    // Fails if gh being logged out, or the repo missing, splits a plan's one
    // branch and one PR into two boxes.
    const branch = { name: "test/demo", error: "repo not found on this machine" };
    const pr = { number: "1", error: "repo not found on this machine" };
    assert.deepEqual(sections([branch], [pr]), [{ pr, branch }]);
  });

  test("a branch with a closed PR and a newer one shows the newer one", () => {
    // Fails if the closed PR, listed first in pr:, takes the branch and the
    // live one is left in a box of its own with no branch.
    const branch = { name: "test/demo", error: "x" };
    const pr = (number: string, state: "closed" | "open") => ({
      number,
      url: "",
      head: "test/demo",
      state,
      review: "",
      checks: tallyChecks([]),
      merged: "",
    });
    const [closed, reopened] = [pr("1", "closed"), pr("2", "open")];
    assert.deepEqual(sections([branch], [closed, reopened]), [
      { pr: reopened, branch },
      { pr: closed, branch: null },
    ]);
  });
});

describe("notCutYet", () => {
  test("a second branch not made yet says so, and the plan's own branch still reads as missing", () => {
    // Fails if a branch whose first milestone hasn't opened reads like a sync
    // problem, or if the plan's own missing branch is excused the same way.
    const [main, second] = [
      { name: "test/demo", error: MISSING },
      { name: "test/demo-split", error: MISSING },
    ];
    assert.deepEqual(notCutYet([main, second], "test/demo"), [
      main,
      { name: "test/demo-split", error: "not created yet" },
    ]);
  });
});

describe("worktreeFor", () => {
  test("finds the worktree that has the branch checked out", () => {
    // Fails if a branch's worktree isn't found, or its path is cut at a space.
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
    // Fails if the last commit, the counts against main, or the uncommitted file
    // in the branch's worktree comes out wrong.
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
    // Fails if a missing branch doesn't say so, or a name starting with - reaches
    // git, which would read it as an option.
    assert.deepEqual(await branchStatus(repo, "gone"), { name: "gone", error: "not on this machine" });
    assert.deepEqual(await branchStatus(repo, "--output=x"), { name: "--output=x", error: "not a branch name" });
  });
});
