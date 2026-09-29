import { readdir, readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, join, sep } from "node:path";

// landsOn is the branch its "- Lands on:" line names, "" for the plan's own.
export type Milestone = { number: number; title: string; state: string; landsOn: string };

export type Plan = {
  name: string;
  status: string;
  archived: boolean;
  open: number[];
  milestones: Milestone[];
  total: number;
  done: number;
  files: string[];
  problems: string[];
};

export type PlanFile = { meta: [string, string][]; body: string };

export type Repo = {
  slug: string;
  label: string;
  note: string;
  problems: string[];
  plans: Plan[];
};

// "### 2. Write the handoff command (active)". master.md is prose, so the
// trailing state is optional rather than a reason to drop the milestone. Two
// regexes, not one: a single pattern with an optional tail backtracks in
// quadratic time on a long line, and this runs in the main process.
const MILESTONE_HEAD = /^###\s+(\d+)\.(.*)$/;
const STATE = /\(([^()]*)\)$/;

const PLAN_FILE = /^plan(\d+)\.md$/;

// What conventions/plan.md allows. Anything else is reported as a problem.
const STATUSES = ["planning", "active", "reviewing", "archived", "abandoned"];

// ignoreBOM keeps a byte-order mark in the text, where Python keeps it too.
const utf8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

function compare(a: string, b: string) {
  return a < b ? -1 : a > b ? 1 : 0;
}

async function list(dir: string) {
  try {
    return (await readdir(dir, { withFileTypes: true })).sort((a, b) => compare(a.name, b.name));
  } catch {
    return [];
  }
}

async function isDir(path: string) {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}

async function isFile(path: string) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

// Invalid UTF-8 reads as null, the same as a missing file. Line endings
// become \n, as Python's read_text makes them.
export async function readText(path: string) {
  try {
    return utf8.decode(await readFile(path)).replace(/\r\n?/g, "\n");
  } catch {
    return null;
  }
}

// How Claude Code names ~/.claude/projects/<slug>/ after a repo's path.
export function slugify(text: string) {
  return text.replace(/[^a-zA-Z0-9]/g, "-");
}

// Loose reads any block between a leading --- and the next, the way
// frontmatter() in scripts/_lib.py does for the SessionStart hook, so a comment
// line in master.md's header can't make the two disagree. Another file can open
// with a --- rule instead, so there the block counts only if it is all keys.
function fence(text: string, loose: boolean) {
  if (!text.startsWith("---")) return null;
  const end = text.indexOf("\n---", 3);
  if (end === -1) return null;
  const lines = text.slice(3, end).split(/\r\n|\r|\n/);
  if (!loose && !lines.every((line) => !line.trim() || /^[\w-]+\s*:/.test(line.trim()))) return null;
  const after = text.indexOf("\n", end + 4);
  return { lines, bodyStart: after === -1 ? text.length : after + 1 };
}

// Flat `key: value` pairs from a leading `---` block, keys lowercased.
export function frontmatter(text: string, loose = true) {
  const out = new Map<string, string>();
  for (const line of fence(text, loose)?.lines ?? []) {
    const colon = line.indexOf(":");
    const key = line.slice(0, colon).trim();
    if (colon !== -1 && key) out.set(key.toLowerCase(), line.slice(colon + 1).trim());
  }
  return out;
}

const LANDS_ON = /^-\s+Lands on:\s*(.*)$/i;

export function parseMilestones(master: string): Milestone[] {
  const out: Milestone[] = [];
  let current: Milestone | null = null;
  for (const line of master.split(/\r\n|\r|\n/)) {
    const head = MILESTONE_HEAD.exec(line);
    if (head) {
      const title = head[2].trim();
      const state = STATE.exec(title);
      current = state
        ? { number: Number(head[1]), title: title.slice(0, state.index).trim(), state: state[1].trim(), landsOn: "" }
        : { number: Number(head[1]), title, state: "", landsOn: "" };
      out.push(current);
    } else if (line.startsWith("#")) {
      current = null;
    } else if (current && !current.landsOn) {
      current.landsOn = LANDS_ON.exec(line)?.[1].replaceAll("`", "").trim().split(/\s+/)[0] ?? "";
    }
  }
  return out;
}

export function landingBranches(master: string) {
  return [...new Set(parseMilestones(master).flatMap((m) => m.landsOn || []))];
}

export function commaList(value: string) {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

// A slug replaces every character that isn't a letter or digit with "-", so
// it can't be reversed, only searched for. This walks down from / one segment
// at a time. A repo that has since moved is not found, and the budget stops a
// walk that wanders into a wide tree.
export async function decodeSlug(slug: string, budget = 6000) {
  const found: string[] = [];
  let scanned = 0;
  async function walk(here: string, rest: string): Promise<void> {
    if (!rest || scanned > budget || found.length >= 4) return;
    const children: string[] = [];
    for (const entry of await list(here)) {
      const child = join(here, entry.name);
      if (entry.isDirectory() || (entry.isSymbolicLink() && (await isDir(child)))) children.push(child);
    }
    scanned += children.length;
    for (const child of children) {
      // Each segment adds its own "/" as "-", which is why "/x/.claude" slugs
      // to "-x--claude".
      const piece = "-" + slugify(basename(child));
      if (rest === piece) found.push(child);
      else if (rest.startsWith(piece)) await walk(child, rest.slice(piece.length));
    }
  }
  await walk("/", slug);
  return found;
}

function tilde(path: string) {
  const home = homedir();
  return path.startsWith(home + sep) ? "~" + path.slice(home.length) : path;
}

// The walk is the slow part and repos don't move, so each slug is walked once
// per launch.
const walked = new Map<string, Promise<string[]>>();

function repoPaths(slug: string) {
  let paths = walked.get(slug);
  if (!paths) walked.set(slug, (paths = decodeSlug(slug)));
  return paths;
}

export async function repoPath(slug: string) {
  return (await repoPaths(slug))[0] ?? null;
}

async function repoLabel(slug: string) {
  const paths = await repoPaths(slug);
  if (!paths.length) return { label: slug, note: "no directory on this machine slugs to this" };
  const note = paths.length > 1 ? `ambiguous slug: ${paths.length - 1} other path(s) also match` : "";
  return { label: tilde(paths[0]), note };
}

const LOGS = ["findings.md", "changelog.md"];

// master.md, the milestone files by number, the logs, then any other notes an
// agent left in the folder.
function rank(name: string) {
  if (name === "master.md") return [0, 0];
  const match = PLAN_FILE.exec(name);
  if (match) return [1, Number(match[1])];
  return LOGS.includes(name) ? [2, LOGS.indexOf(name)] : [3, 0];
}

async function planFiles(folder: string) {
  const names: string[] = [];
  for (const entry of await list(folder)) {
    // Not a symlink, which could point readPlanFile anywhere on disk.
    if (entry.name.endsWith(".md") && entry.isFile()) names.push(entry.name);
  }
  return names.sort((a, b) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || x[1] - y[1];
  });
}

export async function loadPlan(folder: string, archived: boolean): Promise<Plan> {
  const name = basename(folder);
  const master = (await readText(join(folder, "master.md"))) ?? "";
  const meta = frontmatter(master);
  const milestones = parseMilestones(master);
  const problems: string[] = [];

  if (!master.trim()) problems.push("master.md is empty or unreadable");
  else if (!meta.size) problems.push("master.md has no frontmatter block");

  let status = (meta.get("status") ?? "").toLowerCase();
  if (!status) {
    problems.push("master.md frontmatter has no status");
    status = "unknown";
  } else if (!STATUSES.includes(status)) {
    problems.push(`status is '${status}', not one of ${STATUSES.join(", ")}`);
  }

  // Every milestone open right now. Several run at once, and none while
  // everything left waits on something outside the plan.
  const declared = meta.get("milestone");
  if (declared === undefined) problems.push("master.md frontmatter has no milestone");
  const open: number[] = [];
  for (const part of commaList(declared ?? "")) {
    if (!/^\d+$/.test(part)) {
      problems.push(`milestone lists '${part}', not a number`);
      continue;
    }
    open.push(Number(part));
    const listed = milestones.find((m) => m.number === Number(part));
    if (milestones.length && !listed) problems.push(`milestone ${part} is not in master.md`);
    else if (listed?.state.toLowerCase() === "done") problems.push(`milestone ${part} is marked done`);
  }

  // Everything addresses a plan by its folder name, so a `plan:` that says
  // otherwise is a rename that only half happened.
  const titled = meta.get("plan") ?? "";
  if (titled && titled !== name) problems.push(`master.md says plan: ${titled}, folder is named ${name}`);

  return {
    name,
    status,
    archived: archived || status === "archived",
    open,
    milestones,
    total: milestones.length,
    done: milestones.filter((m) => m.state.toLowerCase() === "done").length,
    files: await planFiles(folder),
    problems,
  };
}

// A plan is a folder holding a master.md, under plans/ or, once handoff has
// moved it, plans/archive/.
async function readRepo(slug: string, plansDir: string): Promise<Repo> {
  const repo: Repo = { slug, ...(await repoLabel(slug)), problems: [], plans: [] };
  for (const [base, archived] of [[plansDir, false], [join(plansDir, "archive"), true]] as const) {
    for (const entry of await list(base)) {
      const folder = join(base, entry.name);
      if ((entry.name === "archive" && !archived) || !(await isDir(folder))) continue;
      if (await isFile(join(folder, "master.md"))) repo.plans.push(await loadPlan(folder, archived));
      else repo.problems.push(`${archived ? "archive/" : ""}${entry.name}/ has no master.md, so it is not a plan`);
    }
  }
  return repo;
}

function segment(name: unknown): name is string {
  return typeof name === "string" && !["", ".", ".."].includes(name) && !name.includes(sep);
}

// readRepo's order, so a name in both places resolves to the one the page shows.
export async function planFolder(projects: string, slug: unknown, plan: unknown) {
  if (!segment(slug) || !segment(plan)) return null;
  const plansDir = join(projects, slug, "plans");
  for (const folder of [join(plansDir, plan), join(plansDir, "archive", plan)]) {
    if (await isFile(join(folder, "master.md"))) return folder;
  }
  return null;
}

// The renderer names the file, so this reads only one the scan would list.
export async function readPlanFile(
  projects: string,
  slug: unknown,
  plan: unknown,
  file: unknown,
): Promise<PlanFile | null> {
  const folder = await planFolder(projects, slug, plan);
  if (!folder || typeof file !== "string" || !(await planFiles(folder)).includes(file)) return null;
  const text = await readText(join(folder, file));
  if (text === null) return null;
  const loose = file === "master.md";
  return { meta: [...frontmatter(text, loose)], body: text.slice(fence(text, loose)?.bodyStart ?? 0) };
}

export async function collect(projects: string) {
  const repos: Repo[] = [];
  for (const entry of await list(projects)) {
    const plansDir = join(projects, entry.name, "plans");
    if (!(await isDir(plansDir))) continue;
    const repo = await readRepo(entry.name, plansDir);
    if (repo.plans.length || repo.problems.length) repos.push(repo);
  }
  return repos.sort((a, b) => compare(a.label.toLowerCase(), b.label.toLowerCase()));
}
