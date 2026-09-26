import { readdir, readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, join, sep } from "node:path";

export type Plan = {
  name: string;
  status: string;
  archived: boolean;
  milestone: number | null;
  milestoneTitle: string;
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

// Invalid UTF-8 reads as absent, the same as a missing file. Line endings
// become \n, as Python's read_text makes them.
async function readText(path: string) {
  try {
    return utf8.decode(await readFile(path)).replace(/\r\n?/g, "\n");
  } catch {
    return "";
  }
}

// How Claude Code names ~/.claude/projects/<slug>/ after a repo's path.
export function slugify(text: string) {
  return text.replace(/[^a-zA-Z0-9]/g, "-");
}

// Flat `key: value` pairs from a leading `---` block, keys lowercased. Must
// agree with frontmatter() in scripts/_lib.py, which the SessionStart hook uses.
export function frontmatter(text: string) {
  const out = new Map<string, string>();
  if (!text.startsWith("---")) return out;
  const end = text.indexOf("\n---", 3);
  if (end === -1) return out;
  for (const line of text.slice(3, end).split(/\r\n|\r|\n/)) {
    const colon = line.indexOf(":");
    const key = line.slice(0, colon).trim();
    if (colon !== -1 && key) out.set(key.toLowerCase(), line.slice(colon + 1).trim());
  }
  return out;
}

export function parseMilestones(master: string) {
  return master.split(/\r\n|\r|\n/).flatMap((line) => {
    const head = MILESTONE_HEAD.exec(line);
    if (!head) return [];
    const title = head[2].trim();
    const state = STATE.exec(title);
    return [
      state
        ? { number: Number(head[1]), title: title.slice(0, state.index).trim(), state: state[1].trim() }
        : { number: Number(head[1]), title, state: "" },
    ];
  });
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
const labels = new Map<string, Promise<{ label: string; note: string }>>();

function repoLabel(slug: string) {
  let label = labels.get(slug);
  if (!label) {
    label = decodeSlug(slug).then((paths) => {
      if (!paths.length) return { label: slug, note: "no directory on this machine slugs to this" };
      const note = paths.length > 1 ? `ambiguous slug: ${paths.length - 1} other path(s) also match` : "";
      return { label: tilde(paths[0]), note };
    });
    labels.set(slug, label);
  }
  return label;
}

// master.md, the numbered milestone files in order, then the logs.
async function planFiles(folder: string) {
  const numbered: [number, string][] = [];
  for (const entry of await list(folder)) {
    const match = PLAN_FILE.exec(entry.name);
    if (match && (await isFile(join(folder, entry.name)))) numbered.push([Number(match[1]), entry.name]);
  }
  numbered.sort((a, b) => a[0] - b[0]);
  const names = (await isFile(join(folder, "master.md"))) ? ["master.md"] : [];
  names.push(...numbered.map(([, name]) => name));
  for (const name of ["findings.md", "changelog.md"]) {
    if (await isFile(join(folder, name))) names.push(name);
  }
  return names;
}

export async function loadPlan(folder: string, archived: boolean): Promise<Plan> {
  const name = basename(folder);
  const master = await readText(join(folder, "master.md"));
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

  const declared = meta.get("milestone") ?? "";
  const milestone = /^\d+$/.test(declared) ? Number(declared) : null;
  if (milestone === null) {
    problems.push(declared ? `milestone is '${declared}', not a number` : "master.md frontmatter has no milestone");
  } else if (milestones.length && milestone > milestones.length) {
    problems.push(`milestone ${milestone} is past the ${milestones.length} in master.md`);
  }

  // Everything addresses a plan by its folder name, so a `plan:` that says
  // otherwise is a rename that only half happened.
  const titled = meta.get("plan") ?? "";
  if (titled && titled !== name) problems.push(`master.md says plan: ${titled}, folder is named ${name}`);

  return {
    name,
    status,
    archived: archived || status === "archived",
    milestone,
    milestoneTitle: milestones.find((m) => m.number === milestone)?.title ?? "",
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
