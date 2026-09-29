import { ListChecks } from "lucide-react";
import type { ComponentType } from "react";
import type { Page } from "@/lib/page";
import { cn } from "@/lib/utils";

const SECTIONS: { id: string; title: string; Icon: ComponentType<{ className?: string }> }[] = [
  { id: "plans", title: "Plans", Icon: ListChecks },
];

type SidebarProps = {
  page: Page;
  onNavigate: (page: Page) => void;
  rail: boolean;
};

const row =
  "block w-full border-b border-sidebar-border text-left text-muted-foreground hover:bg-foreground/5 hover:text-foreground aria-[current=true]:bg-foreground/10 aria-[current=true]:text-foreground";

export function Sidebar({ page, onNavigate, rail }: SidebarProps) {
  return (
    <nav className="h-full overflow-y-auto bg-sidebar text-sidebar-foreground select-none">
      {SECTIONS.map(({ id, title, Icon }) => (
        <button
          key={id}
          title={title}
          aria-current={page.section === id}
          onClick={() => onNavigate({ section: id })}
          // One height for text and icon, so the row doesn't jump between them.
          className={cn(
            row,
            "flex h-8 items-center",
            rail
              ? "justify-center"
              : "px-4 text-[11px] font-bold tracking-[0.06em] uppercase",
          )}
        >
          {rail ? <Icon className="size-4" /> : title}
        </button>
      ))}
    </nav>
  );
}
