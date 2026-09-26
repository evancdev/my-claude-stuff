import { useCallback, useEffect, useRef, useState } from "react";
import type { PanelImperativeHandle } from "react-resizable-panels";
import { Pages } from "@/components/pages";
import { Sidebar } from "@/components/sidebar";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { usePageHistory } from "@/hooks/use-page-history";
import type { Repo } from "@/lib/plans";

const MIN = 48;
const ICONS = 140;
const MAX = 240;
const DEFAULT = 240;
const WIDTH_KEY = "sidebar-width";

function savedWidth() {
  try {
    const saved = Number(localStorage.getItem(WIDTH_KEY) || NaN);
    return Number.isFinite(saved) ? Math.min(MAX, Math.max(MIN, saved)) : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function App() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [page, navigate] = usePageHistory();
  const sidebar = useRef<PanelImperativeHandle>(null);
  // Read once. A defaultSize that changes mid-drag, as the rail switches back
  // to text, stops the drag dead.
  const [initialWidth] = useState(savedWidth);
  const [rail, setRail] = useState(initialWidth < ICONS);

  const load = useCallback(async () => {
    try {
      setRepos((await window.dashboard.plans()).repos);
      setError(null);
    } catch (err) {
      setError(`Could not load plans: ${err instanceof Error ? err.message : err}`);
    }
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [load]);

  return (
    <ResizablePanelGroup orientation="horizontal" className="h-screen! text-sm">
      <ResizablePanel
        id="sidebar"
        panelRef={sidebar}
        defaultSize={initialWidth}
        minSize={MIN}
        maxSize={MAX}
        groupResizeBehavior="preserve-pixel-size"
        onResize={(size) => {
          setRail(size.inPixels < ICONS);
          try {
            localStorage.setItem(WIDTH_KEY, String(Math.round(size.inPixels)));
          } catch {}
        }}
      >
        <Sidebar page={page} onNavigate={navigate} rail={rail} />
      </ResizablePanel>
      <ResizableHandle
        title="Drag to resize. Double-click to reset."
        className="hover:bg-ring data-[separator=active]:bg-ring"
        // The library's own reset goes to defaultSize, which is the saved width.
        disableDoubleClick
        onDoubleClick={() => sidebar.current?.resize(DEFAULT)}
      />
      <ResizablePanel id="main">
        <main className="h-full overflow-y-auto px-10 py-8">
          {error ?? <Pages page={page} repos={repos} onNavigate={navigate} />}
        </main>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
