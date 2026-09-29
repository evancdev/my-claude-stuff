import { Fragment, type ReactNode, useRef, useState } from "react";
import { KeyValue, KeyValues } from "@/components/key-values";
import { Skeleton } from "@/components/ui/skeleton";
import { usePlanStatus } from "@/hooks/use-plan";
import type { BranchStatus, Plan, PrStatus } from "@/lib/plans";
import { namesBranches, readyToArchive } from "@/lib/sections";
import { cn } from "@/lib/utils";

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const UNITS = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["week", 604_800],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
] as const;

function ago(ms: number) {
  const seconds = (ms - Date.now()) / 1000;
  const unit = UNITS.find(([, size]) => Math.abs(seconds) >= size);
  return unit ? relative.format(Math.round(seconds / unit[1]), unit[0]) : "just now";
}

const PR_DOTS: Record<PrStatus["state"], string> = {
  open: "bg-emerald-500",
  draft: "bg-muted-foreground",
  merged: "bg-violet-500",
  closed: "bg-red-500",
};

const REVIEW_COLORS: Record<string, string> = {
  approved: "text-emerald-600 dark:text-emerald-500",
  "changes requested": "text-amber-600 dark:text-amber-500",
};

// branch is the plan's own, from master.md's frontmatter.
export function PlanStatusBox({ repo, plan, branch }: { repo: string; plan: Plan; branch?: string }) {
  const status = usePlanStatus(repo, plan.name);
  if (status === undefined) return <Skeleton className="min-h-28 rounded-lg" />;
  if (!status.length) return null;
  const archivable = readyToArchive(status, plan.archived);
  const named = namesBranches(status, branch);
  const boxes = status.map(({ pr, branch }) => (
    <KeyValues key={`${pr?.number}/${branch?.name}`} className="h-full w-full">
      {pr &&
        ("error" in pr ? (
          <KeyValue name="pr">{`#${pr.number} · ${pr.error}`}</KeyValue>
        ) : (
          <PrRows pr={pr} archivable={archivable} />
        ))}
      {branch &&
        ("error" in branch ? (
          <KeyValue name="branch">{named ? `${branch.name} · ${branch.error}` : branch.error}</KeyValue>
        ) : (
          <BranchRows branch={branch} named={named} />
        ))}
    </KeyValues>
  ));
  if (boxes.length === 1) return boxes[0];
  const labels = status.map(({ pr, branch }) => (pr ? `#${pr.number}` : (branch?.name ?? "")));
  // Keyed, so another plan's page opens on its first box.
  return (
    <Slides key={plan.name} labels={labels}>
      {boxes}
    </Slides>
  );
}

// One box in view at a time. A two-finger swipe slides them, and only swiping
// past either end goes back or forward a page.
function Slides({ labels, children }: { labels: string[]; children: ReactNode[] }) {
  const strip = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(0);
  // A refresh can drop a box from under the one in view.
  const shown = Math.min(scrolled, labels.length - 1);
  const slideTo = (i: number) => strip.current?.scrollTo({ left: i * strip.current.clientWidth, behavior: "smooth" });
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div
        ref={strip}
        onScroll={(e) => setScrolled(Math.round(e.currentTarget.scrollLeft / e.currentTarget.clientWidth))}
        className="flex flex-1 snap-x snap-mandatory overflow-x-auto [scrollbar-width:none]"
      >
        {children.map((box, i) => (
          // Inset, so a scroll that stops a fraction short can't clip the border.
          <div key={labels[i]} className="w-full shrink-0 snap-start px-px">
            {box}
          </div>
        ))}
      </div>
      <div className="flex justify-center">
        {labels.map((label, i) => (
          <button key={label} title={label} aria-label={`Show ${label}`} className="p-1" onClick={() => slideTo(i)}>
            <span className={cn("block size-1.5 rounded-full", i === shown ? "bg-foreground" : "bg-foreground/20")} />
          </button>
        ))}
      </div>
    </div>
  );
}

function PrRows({ pr, archivable }: { pr: PrStatus; archivable: boolean }) {
  const checks = [
    ["passed", pr.checks.passed, "text-emerald-600 dark:text-emerald-500"],
    ["failed", pr.checks.failed, "text-red-600 dark:text-red-500"],
    ["running", pr.checks.running, "text-amber-600 dark:text-amber-500"],
    ["cancelled", pr.checks.cancelled, "text-orange-600 dark:text-orange-400"],
    ["skipped", pr.checks.skipped, "text-muted-foreground"],
  ] as const;
  const ran = checks.filter(([, n]) => n > 0);
  return (
    <>
      <KeyValue name="pr">
        <span className="inline-flex items-center gap-1.5">
          <span className={cn("size-2 rounded-full", PR_DOTS[pr.state])} />
          <a href={pr.url} target="_blank" rel="noreferrer" className="underline">
            #{pr.number}
          </a>
          {pr.state}
          {pr.merged && ` ${ago(Date.parse(pr.merged))}`}
          {pr.state === "merged" && archivable && (
            <span className="text-amber-600 dark:text-amber-500">· ready to archive</span>
          )}
        </span>
      </KeyValue>
      <KeyValue name="checks">
        {ran.length
          ? ran.map(([label, n, color], i) => (
              <Fragment key={label}>
                {i > 0 && " · "}
                <span className={color}>{`${n} ${label}`}</span>
              </Fragment>
            ))
          : "none"}
      </KeyValue>
      {pr.review && (
        <KeyValue name="review">
          <span className={REVIEW_COLORS[pr.review]}>{pr.review}</span>
        </KeyValue>
      )}
    </>
  );
}

function BranchRows({ branch, named }: { branch: BranchStatus; named: boolean }) {
  const parent = branch.parent ?? "unknown";
  const changes =
    branch.worktree === null
      ? "not checked out"
      : branch.changes === null
        ? "couldn't read the worktree"
        : branch.changes
          ? `${branch.changes} uncommitted ${branch.changes === 1 ? "change" : "changes"}`
          : "no uncommitted changes";
  return (
    <>
      {named && (
        <KeyValue name="branch" title={branch.name}>
          {branch.name}
        </KeyValue>
      )}
      {branch.commit && (
        <KeyValue name="last commit" title={branch.commit}>
          {branch.commit}
        </KeyValue>
      )}
      {branch.ahead !== null && (
        <KeyValue name={`vs ${branch.base}`}>{`${branch.ahead} ahead · ${branch.behind} behind`}</KeyValue>
      )}
      <KeyValue name="rebased on" title={`${parent} (${changes})`}>
        <span className="flex">
          <span className="truncate">{parent}</span>
          <span className="ml-1 shrink-0">({changes})</span>
        </span>
      </KeyValue>
    </>
  );
}
