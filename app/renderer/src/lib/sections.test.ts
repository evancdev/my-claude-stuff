import assert from "node:assert/strict";
import { describe, test } from "node:test";
import type { BranchStatus, PrStatus } from "./plans";
import { namesBranches, readyToArchive } from "./sections.ts";

const branch = (name: string): BranchStatus => ({
  name,
  commit: null,
  base: "main",
  parent: null,
  ahead: 0,
  behind: 0,
  worktree: null,
  changes: null,
});
const pr = (number: string, state: PrStatus["state"]): PrStatus => ({
  number,
  url: "",
  head: "",
  state,
  review: "",
  checks: { passed: 0, failed: 0, running: 0, cancelled: 0, skipped: 0 },
  merged: "",
});

describe("readyToArchive", () => {
  test("waits for a merged PR on every branch", () => {
    const first = { pr: pr("1", "merged"), branch: branch("test/demo") };
    // Fails if a second branch with no PR yet, one with an open PR, or one gh
    // couldn't read lets the plan show as ready while its work hasn't landed.
    assert.equal(readyToArchive([first, { pr: null, branch: branch("test/demo-split") }], false), false);
    assert.equal(readyToArchive([first, { pr: pr("2", "open"), branch: null }], false), false);
    const unread = { pr: { number: "2", error: "gh could not read it" }, branch: null };
    assert.equal(readyToArchive([first, unread], false), false);
    // Fails if a plan whose PRs have all merged is never offered for archiving.
    assert.equal(readyToArchive([first, { pr: pr("2", "merged"), branch: null }], false), true);
    // Fails if a plan already archived is offered again.
    assert.equal(readyToArchive([first], true), false);
  });
});

describe("namesBranches", () => {
  test("names a branch only when the frontmatter box doesn't already", () => {
    // Fails if the plan's lone branch is named twice on the page.
    assert.equal(namesBranches([{ pr: null, branch: branch("test/demo") }], "test/demo"), false);
    // Fails if a lone branch that isn't the plan's own, from a Lands on line,
    // goes unnamed, or boxes that sit side by side can't be told apart.
    assert.equal(namesBranches([{ pr: null, branch: branch("test/other") }], "test/demo"), true);
    const [own, stray] = [{ pr: null, branch: branch("test/demo") }, { pr: pr("2", "open"), branch: null }];
    assert.equal(namesBranches([own, stray], "test/demo"), true);
  });
});
