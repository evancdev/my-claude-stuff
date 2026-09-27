import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, sep } from "node:path";
import { after, before, describe, test } from "node:test";
import { collect, decodeSlug, frontmatter, loadPlan, parseMilestones, readPlanFile, slugify } from "./plans.ts";

const MASTER = `---
plan: demo
Status: active
milestone: 2
---

# Demo

### 1. Parse the plan folder (done)
- Done when: it parses.

### 2. Write the handoff command (active)
- Done when: it hands off.

## Out of scope

- Not a milestone.
`;

let root: string;
before(() => {
  root = mkdtempSync(join(tmpdir(), "grug-plans-"));
});
after(() => rmSync(root, { recursive: true, force: true }));

function write(path: string, text: string) {
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, text);
}

describe("frontmatter", () => {
  test("reads the block at the top, with keys lowercased", () => {
    // Fails if a capitalized key such as Status: is missed.
    assert.deepEqual([...frontmatter(MASTER)], [["plan", "demo"], ["status", "active"], ["milestone", "2"]]);
  });

  test("finds nothing without a closed block at the very top", () => {
    // Fails if a --- further down, or a block that never closes, is read as the header.
    assert.equal(frontmatter("# Title\n---\nstatus: active\n---\n").size, 0);
    assert.equal(frontmatter("---\nstatus: active\n\n# Never closed\n").size, 0);
  });
});

describe("parseMilestones", () => {
  test("counts ### N. headings and reads the state in parentheses", () => {
    const found = parseMilestones(MASTER + "### 3. No state yet\n### 4. Nested (a (b))\n### 5. Two (a) (b)  \n");
    assert.deepEqual(
      found.map((m) => [m.number, m.title, m.state]),
      [
        [1, "Parse the plan folder", "done"],
        [2, "Write the handoff command", "active"],
        [3, "No state yet", ""],
        // Only a last pair of parentheses with none inside is a state.
        [4, "Nested (a (b))", ""],
        [5, "Two (a)", "b"],
      ],
    );
  });

  test("a long heading line takes linear time", () => {
    // Fails if the pattern backtracks: 40,000 spaces took two seconds before,
    // with the main process frozen for all of it.
    const start = performance.now();
    parseMilestones("### 1. a" + " ".repeat(40000) + "(");
    assert.ok(performance.now() - start < 100);
  });
});

describe("loadPlan", () => {
  test("a well-formed plan has its progress and files, and no problems", async () => {
    const folder = join(root, "load", "demo");
    write(join(folder, "master.md"), MASTER);
    for (const name of ["plan10.md", "plan2.md", "plan1.md", "findings.md", "decisions.md", "notes.txt"]) {
      write(join(folder, name), "x");
    }
    const plan = await loadPlan(folder, false);
    assert.deepEqual(plan, {
      name: "demo",
      status: "active",
      archived: false,
      milestone: 2,
      milestoneTitle: "Write the handoff command",
      milestones: [
        { number: 1, title: "Parse the plan folder", state: "done" },
        { number: 2, title: "Write the handoff command", state: "active" },
      ],
      total: 2,
      done: 1,
      // Reading order, so plan10 comes after plan2 and a note an agent added
      // comes last.
      files: ["master.md", "plan1.md", "plan2.md", "plan10.md", "findings.md", "decisions.md"],
      problems: [],
    });
  });

  test("a plan that breaks the contract says how", async () => {
    // Fails if a bad status, a milestone past the end, or a half-done rename
    // goes unreported.
    const folder = join(root, "bad", "demo");
    write(join(folder, "master.md"), "---\nplan: other\nstatus: shipped\nmilestone: 9\n---\n### 1. Only one\n");
    const plan = await loadPlan(folder, false);
    assert.equal(plan.milestoneTitle, "");
    assert.deepEqual(plan.problems, [
      "status is 'shipped', not one of planning, active, reviewing, archived, abandoned",
      "milestone 9 is past the 1 in master.md",
      "master.md says plan: other, folder is named demo",
    ]);
  });

  test("a master.md with no header block has no status or milestone", async () => {
    const folder = join(root, "bare", "demo");
    write(join(folder, "master.md"), "# Just a title\n");
    const plan = await loadPlan(folder, false);
    assert.equal(plan.status, "unknown");
    assert.equal(plan.milestone, null);
    assert.deepEqual(plan.problems, [
      "master.md has no frontmatter block",
      "master.md frontmatter has no status",
      "master.md frontmatter has no milestone",
    ]);
  });

  test("reads a master.md the way Python does, whatever its line endings or byte-order mark", async () => {
    // Fails if Grug and the Python hook disagree about whether the header block exists.
    const cr = join(root, "cr", "demo");
    write(join(cr, "master.md"), "---\rstatus: active\rmilestone: 1\r---\r### 1. a (done)\r");
    assert.deepEqual((await loadPlan(cr, false)).problems, []);
    const bom = join(root, "bom", "demo");
    write(join(bom, "master.md"), "\ufeff---\nstatus: active\nmilestone: 1\n---\n");
    assert.equal((await loadPlan(bom, false)).problems[0], "master.md has no frontmatter block");
  });

  test("status: archived counts as archived even outside archive/", async () => {
    const folder = join(root, "done", "demo");
    write(join(folder, "master.md"), "---\nstatus: archived\nmilestone: 1\n---\n");
    assert.equal((await loadPlan(folder, false)).archived, true);
  });
});

describe("collect", () => {
  test("lists each repo's plans, archived ones included, and folders that aren't plans", async () => {
    const projects = join(root, "projects");
    const repo = join(root, "some-repo");
    mkdirSync(repo, { recursive: true });
    const plans = join(projects, slugify(repo), "plans");
    write(join(plans, "live", "master.md"), "---\nstatus: active\nmilestone: 1\n---\n");
    write(join(plans, "archive", "old", "master.md"), "---\nstatus: archived\nmilestone: 1\n---\n");
    mkdirSync(join(plans, "scratch"));
    // A project with no plans/ folder is left out.
    mkdirSync(join(projects, "-no-plans"), { recursive: true });

    const repos = await collect(projects);
    assert.equal(repos.length, 1);
    const [found] = repos;
    // The label is the repo's real path, recovered from the slug.
    assert.equal(found.label, repo.startsWith(homedir() + sep) ? "~" + repo.slice(homedir().length) : repo);
    assert.deepEqual(
      found.plans.map((p) => [p.name, p.archived]),
      [
        ["live", false],
        ["old", true],
      ],
    );
    // Fails if plans/archive/ is itself listed as a plan or a problem.
    assert.deepEqual(found.problems, ["scratch/ has no master.md, so it is not a plan"]);
  });
});

describe("readPlanFile", () => {
  const projects = () => join(root, "files");

  before(() => {
    const plans = join(projects(), "-repo", "plans");
    write(join(plans, "live", "master.md"), MASTER);
    write(join(plans, "live", "plan1.md"), "# One\n");
    write(join(plans, "live", "notes.txt"), "x");
    write(join(plans, "live", "findings.md"), "---\n\n## 1. Parse\nNote: short\n\n---\n\n## 2. Next\n");
    writeFileSync(join(plans, "live", "latin1.md"), Buffer.from([0xff, 0xfe, 0x41]));
    symlinkSync(join(projects(), "-repo", "secret.md"), join(plans, "live", "link.md"));
    write(join(plans, "archive", "old", "master.md"), "# Old\n");
    write(join(plans, "archive", "live", "master.md"), "# Archived twin\n");
    write(join(projects(), "-repo", "secret.md"), "x");
  });

  test("splits a file's header block from the text under it", async () => {
    assert.deepEqual(await readPlanFile(projects(), "-repo", "live", "master.md"), {
      meta: [...frontmatter(MASTER)],
      body: MASTER.slice(MASTER.indexOf("\n---\n") + 5),
    });
    assert.deepEqual(await readPlanFile(projects(), "-repo", "live", "plan1.md"), { meta: [], body: "# One\n" });
  });

  test("keeps a file that opens with a --- rule whole", async () => {
    // Fails if the text between two rules is taken for a header block and
    // dropped from the page.
    const findings = await readPlanFile(projects(), "-repo", "live", "findings.md");
    assert.deepEqual(findings?.meta, []);
    assert.match(findings?.body ?? "", /## 1\. Parse/);
  });

  test("says it couldn't read a file that isn't UTF-8, rather than showing a blank page", async () => {
    assert.equal(await readPlanFile(projects(), "-repo", "live", "latin1.md"), null);
  });

  test("finds an archived plan, and prefers the live one when both have the name", async () => {
    assert.equal((await readPlanFile(projects(), "-repo", "old", "master.md"))?.body, "# Old\n");
    assert.equal((await readPlanFile(projects(), "-repo", "live", "master.md"))?.meta.length, 3);
  });

  test("reads nothing the plan's file list doesn't hold", async () => {
    // Fails if the renderer can name its way to any other file on disk.
    for (const [slug, plan, file] of [
      ["-repo", "live", "notes.txt"],
      ["-repo", "live", "../../secret.md"],
      ["-repo", "live", "link.md"],
      ["-repo", "..", "secret.md"],
      ["..", "files", "master.md"],
      ["-repo", "live/../live", "master.md"],
      ["-repo", "gone", "master.md"],
      ["-repo", "live", 1],
    ]) {
      assert.equal(await readPlanFile(projects(), slug, plan, file), null, `${slug} ${plan} ${file}`);
    }
  });
});

describe("decodeSlug", () => {
  test("finds the directory a slug came from, and nothing for a slug with no directory", async () => {
    const dir = join(root, "a.dotted_name");
    mkdirSync(dir);
    assert.deepEqual(await decodeSlug(slugify(dir)), [dir]);
    assert.deepEqual(await decodeSlug(slugify(join(root, "gone"))), []);
  });
});
