import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function KeyValues({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <dl
      className={cn(
        "grid w-fit max-w-full grid-cols-[auto_minmax(0,1fr)] content-start gap-x-4 gap-y-1 rounded-lg border px-4 py-3 text-xs",
        className,
      )}
    >
      {children}
    </dl>
  );
}

export function KeyValue({ name, title, children }: { name: string; title?: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{name}</dt>
      <dd title={title} className="truncate">
        {children}
      </dd>
    </>
  );
}
