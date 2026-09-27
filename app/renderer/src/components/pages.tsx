import { ChevronDown, TriangleAlert } from "lucide-react";
import { type ComponentProps, type ReactNode } from "react";
import { KeyValue, KeyValues } from "@/components/key-values";
import { Markdown } from "@/components/markdown";
import { PlanStatusBox } from "@/components/plan-status";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { usePlanFile } from "@/hooks/use-plan";
import { useStored } from "@/hooks/use-stored";
import { repoName, type Page } from "@/lib/page";
import type { Plan, PlanFile, Repo } from "@/lib/plans";
import { cn } from "@/lib/utils";

type PagesProps = { page: Page; repos: Repo[]; onNavigate: (page: Page) => void };

export function Pages({ page, repos, onNavigate }: PagesProps) {
  const repo = repos.find((r) => r.slug === page.repo);
  const plan = repo?.plans.find((p) => p.name === page.plan);
  if (repo && plan) return <PlanPage page={page} repo={repo} plan={plan} onNavigate={onNavigate} />;
  return <PlansPage repos={repos} onNavigate={onNavigate} />;
}

function Heading({ trail, title, sub }: { trail?: ReactNode; title: string; sub?: ReactNode }) {
  return (
    <header className="mb-6">
      {trail && <p className="mb-1 text-xs text-muted-foreground">{trail}</p>}
      <h1 className="text-2xl">{title}</h1>
      {sub && <p className="mt-1 text-muted-foreground">{sub}</p>}
    </header>
  );
}

type Filter = { repo: string | null; archived: boolean };

function PlansPage({ repos, onNavigate }: Omit<PagesProps, "page">) {
  const [filter, setFilter] = useStored<Filter>("plans-filter", { repo: null, archived: false });
  const inView = (p: Plan) => p.archived === filter.archived;
  const withPlans = repos.filter((r) => r.plans.length);
  const picked = withPlans.find((r) => r.slug === filter.repo);
  const cards = (picked ? [picked] : withPlans).flatMap((repo) =>
    repo.plans.filter(inView).map((plan) => ({ repo, plan })),
  );
  const count = (rs: Repo[]) => rs.reduce((n, r) => n + r.plans.filter(inView).length, 0);

  return (
    <>
      <Heading
        title="Plans"
        sub={`${cards.length} ${filter.archived ? "archived " : ""}${cards.length === 1 ? "plan" : "plans"}`}
      />
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <Chip on={!picked} onClick={() => setFilter({ ...filter, repo: null })}>
          All <Num n={count(withPlans)} />
        </Chip>
        {withPlans.map((r) => (
          <Chip key={r.slug} title={r.label} on={picked === r} onClick={() => setFilter({ ...filter, repo: r.slug })}>
            {repoName(r.label)} <Num n={count([r])} />
          </Chip>
        ))}
        <span className="mx-1 h-5 w-px bg-border" />
        <Chip on={filter.archived} onClick={() => setFilter({ ...filter, archived: !filter.archived })}>
          archive
        </Chip>
      </div>
      {cards.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
          {cards.map(({ repo, plan }) => (
            <button
              key={`${repo.slug}/${plan.name}`}
              onClick={() => onNavigate({ section: "plans", repo: repo.slug, plan: plan.name })}
              className="flex flex-col rounded-lg border p-4 text-left hover:bg-foreground/5"
            >
              <div className="flex items-baseline gap-3">
                <p className="min-w-0 flex-1 truncate">{plan.name}</p>
                <Status status={statusOf(plan)} />
              </div>
              <p className="mb-4 truncate text-xs text-muted-foreground">{repoName(repo.label)}</p>
              <Progress plan={plan} />
              {plan.problems.length > 0 && (
                <p
                  title={plan.problems.join("\n")}
                  className="mt-3 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-500"
                >
                  <TriangleAlert className="size-3.5" />
                  {plan.problems.length} {plan.problems.length === 1 ? "problem" : "problems"}
                </p>
              )}
            </button>
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">No plans here.</p>
      )}
    </>
  );
}

function Chip({ on, className, ...props }: ComponentProps<"button"> & { on: boolean }) {
  return (
    <button
      aria-pressed={on}
      className={cn(
        "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs text-muted-foreground hover:text-foreground",
        "aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background",
        className,
      )}
      {...props}
    />
  );
}

function Num({ n }: { n: number }) {
  return <span className="opacity-60">{n}</span>;
}

const DOTS: Record<string, string> = {
  active: "bg-emerald-500",
  reviewing: "bg-amber-500",
  archived: "bg-muted-foreground",
  abandoned: "bg-muted-foreground/40",
};

// master.md can lag behind a move into archive/.
function statusOf(plan: Plan) {
  return plan.archived && plan.status !== "abandoned" ? "archived" : plan.status;
}

function Status({ status }: { status: string }) {
  return (
    <span title={status} className="flex max-w-1/2 min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      <span className={cn("size-2 shrink-0 rounded-full", DOTS[status] ?? "border border-muted-foreground")} />
      <span className="truncate">{status}</span>
    </span>
  );
}

// The label is where the plan is now; the bar is how many milestones are done.
function Progress({ plan }: { plan: Plan }) {
  const done = plan.total ? (plan.done / plan.total) * 100 : 0;
  return (
    <>
      <p className="text-xs text-muted-foreground tabular-nums">
        {plan.milestone === null ? "no milestone recorded" : `${plan.milestone} of ${plan.total || "?"}`}
      </p>
      {/* Kept when empty, so the bars line up across a row. */}
      <p className="mt-0.5 truncate text-xs">{plan.milestoneTitle || "\u00a0"}</p>
      <div className="mt-2 h-1 rounded-full bg-foreground/10">
        <div className="h-full rounded-full bg-foreground" style={{ width: `${done}%` }} />
      </div>
    </>
  );
}

const MASTER = "master.md";
const MILESTONE_FILE = /^plan(\d+)\.md$/;

type PlanPageProps = { page: Page; repo: Repo; plan: Plan; onNavigate: (page: Page) => void };

function PlanPage({ page, repo, plan, onNavigate }: PlanPageProps) {
  const open = page.file && plan.files.includes(page.file) ? page.file : MASTER;
  const file = usePlanFile(repo.slug, plan.name, open);
  const milestoneFiles = plan.files.filter((f) => MILESTONE_FILE.test(f));
  const show = (name: string) =>
    onNavigate({
      section: "plans",
      repo: repo.slug,
      plan: plan.name,
      // A card opens master.md without naming it, so clicking master there is
      // the same page and adds no history entry.
      file: name === MASTER ? undefined : name,
    });
  const where =
    plan.milestone === null ? "no milestone recorded" : `milestone ${plan.milestone} of ${plan.total || "?"}`;
  return (
    <>
      <Heading
        trail={
          <>
            <button className="hover:text-foreground hover:underline" onClick={() => onNavigate({ section: "plans" })}>
              Plans
            </button>
            {` / ${repoName(repo.label)}`}
          </>
        }
        title={plan.name}
        sub={[statusOf(plan), where].filter(Boolean).join(" · ")}
      />
      <div className="mb-6 flex flex-wrap items-center gap-2">
        {plan.files.map((name) =>
          !milestoneFiles.includes(name) ? (
            <Chip key={name} on={name === open} onClick={() => show(name)}>
              {name.replace(/\.md$/, "")}
            </Chip>
          ) : name === milestoneFiles[0] ? (
            <MilestoneMenu key="plans" plan={plan} files={milestoneFiles} open={open} onPick={show} />
          ) : null,
        )}
      </div>
      {file === undefined ? (
        <div className="max-w-2xl space-y-3">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="mt-8 h-6 w-1/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : file === null ? (
        <p className="text-muted-foreground">Could not read {open}.</p>
      ) : open === MASTER ? (
        // The status box shows the PR.
        <FileView
          file={{ ...file, meta: file.meta.filter(([key]) => key !== "pr") }}
          beside={<PlanStatusBox repo={repo.slug} plan={plan} />}
        />
      ) : (
        <FileView file={file} />
      )}
    </>
  );
}

type MilestoneMenuProps = { plan: Plan; files: string[]; open: string; onPick: (file: string) => void };

// The milestone files share one tab, so a long plan doesn't fill the row.
function MilestoneMenu({ plan, files, open, onPick }: MilestoneMenuProps) {
  const on = files.includes(open);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Chip on={on}>
          {on ? open.replace(/\.md$/, "") : "plans"}
          <ChevronDown className="size-3" />
        </Chip>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-auto max-w-md">
        <DropdownMenuRadioGroup value={open} onValueChange={onPick}>
          {files.map((name) => {
            const number = Number(MILESTONE_FILE.exec(name)?.[1]);
            const milestone = plan.milestones.find((m) => m.number === number);
            return (
              <DropdownMenuRadioItem key={name} value={name} className="gap-3 text-xs">
                <span className="w-4 text-right text-muted-foreground tabular-nums">{number}</span>
                <span className="min-w-0 flex-1 truncate">{milestone?.title || name}</span>
                {milestone?.state && <span className="text-muted-foreground">{milestone.state}</span>}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function FileView({ file, beside }: { file: PlanFile; beside?: ReactNode }) {
  return (
    <>
      {(file.meta.length > 0 || beside) && (
        <div className="mb-6 grid grid-cols-[repeat(auto-fit,minmax(min(22rem,100%),1fr))] gap-4">
          {file.meta.length > 0 && (
            <KeyValues className="w-full">
              {file.meta.map(([key, value]) => (
                <KeyValue key={key} name={key} title={value}>
                  {/^https?:\/\//.test(value) ? (
                    <a href={value} target="_blank" rel="noreferrer" className="underline">
                      {value}
                    </a>
                  ) : (
                    value
                  )}
                </KeyValue>
              ))}
            </KeyValues>
          )}
          {beside}
        </div>
      )}
      <Markdown text={file.body} />
    </>
  );
}
