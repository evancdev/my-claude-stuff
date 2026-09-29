import { Minus, Plus } from "lucide-react";
import MarkdownIt from "markdown-it";
import { type MouseEvent, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

// html stays off, so markdown-it escapes raw HTML in a file instead of passing
// it through. That is what makes the innerHTML below safe.
const md = new MarkdownIt({ linkify: true });

// A mermaid block stays a code block until its diagram is drawn over it, and
// stays one if it doesn't draw.
const fence = md.renderer.rules.fence!;
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const html = fence(tokens, idx, options, env, self);
  return tokens[idx].info.trim().split(/\s+/)[0] === "mermaid" ? `<div data-mermaid>${html}</div>` : html;
};

// lucide's maximize-2 as markup, since the button goes in with the diagram.
const EXPAND_ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="m21 3-7 7"/><path d="m3 21 7-7"/><path d="M9 21H3v-6"/></svg>';

// Loaded on the first diagram, since it is most of the bundle.
let mermaid: Promise<typeof import("mermaid").default> | undefined;
let drawn = 0;

async function draw(blocks: HTMLElement[], dark: boolean, font: string, stale: () => boolean) {
  // Forgotten if it fails, so the next page with a diagram tries again.
  mermaid ??= import("mermaid")
    .then((module) => module.default)
    .catch((error) => {
      mermaid = undefined;
      throw error;
    });
  const m = await mermaid;
  // strict strips script from labels and links and ignores click callbacks,
  // which is what makes the innerHTML below safe.
  m.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    suppressErrorRendering: true,
    theme: dark ? "dark" : "neutral",
    fontFamily: font,
  });
  for (const block of blocks) {
    // Kept for the redraw when the theme changes, since the diagram replaces the code.
    const source = (block.dataset.source ??= block.textContent ?? "");
    try {
      const { svg } = await m.render(`mermaid-${++drawn}`, source);
      if (stale()) return;
      block.innerHTML =
        '<div class="flex justify-end">' +
        '<button data-expand title="Expand" aria-label="Expand diagram"' +
        ' class="rounded-md p-1 text-muted-foreground hover:bg-foreground/5 hover:text-foreground">' +
        `${EXPAND_ICON}</button>` +
        "</div>" +
        `<div data-diagram class="cursor-zoom-in overflow-x-auto [scrollbar-width:none]">${svg}</div>`;
      block.classList.add("not-prose", "my-6");
      // Mermaid shrinks a wide diagram to fit, past where it can be read.
      // This one stops at 75% and scrolls sideways instead.
      const drawing = block.querySelector<SVGSVGElement>("[data-diagram] svg");
      if (drawing) drawing.style.width = `max(100%, ${drawing.viewBox.baseVal.width * 0.75}px)`;
    } catch {}
  }
}

const darkQuery = matchMedia("(prefers-color-scheme: dark)");
function onDarkChange(listener: () => void) {
  darkQuery.addEventListener("change", listener);
  return () => darkQuery.removeEventListener("change", listener);
}

// Left alone, a relative link loads over the app from the dev server and a
// #fragment adds a history entry the page history can't read. The written
// href, since link.href resolves a relative one against the dev server too.
function openLink(link: Element, e: MouseEvent) {
  e.preventDefault();
  const href = link.getAttribute("href") ?? "";
  if (/^https?:\/\//i.test(href)) window.open(href);
}

type Drawing = { svg: string; width: number; height: number };

// Without the width that fits it to the page, so the dialog sets its size.
function copyOf(drawing: SVGSVGElement): Drawing {
  const copy = drawing.cloneNode(true) as SVGSVGElement;
  copy.removeAttribute("style");
  copy.setAttribute("width", "100%");
  const { width, height } = drawing.viewBox.baseVal;
  return { svg: copy.outerHTML, width, height };
}

const ZOOMS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3];

function Expanded({ drawing, onClose }: { drawing: Drawing; onClose: () => void }) {
  const view = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const smaller = ZOOMS.filter((z) => z < zoom - 0.001).at(-1);
  const larger = ZOOMS.find((z) => z > zoom + 0.001);
  const fit = () => {
    const box = view.current;
    if (box) setZoom(Math.min(1, (box.clientWidth - 32) / drawing.width, (box.clientHeight - 32) / drawing.height));
  };
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        className="flex h-[90vh] w-[95vw] max-w-none flex-col gap-0 p-0 sm:max-w-none"
      >
        <DialogTitle className="sr-only">Diagram</DialogTitle>
        <div className="flex items-center gap-1 border-b px-2 py-1.5 text-xs">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Smaller"
            disabled={!smaller}
            onClick={() => smaller && setZoom(smaller)}
          >
            <Minus />
          </Button>
          <span className="w-10 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Larger"
            disabled={!larger}
            onClick={() => larger && setZoom(larger)}
          >
            <Plus />
          </Button>
          <Button variant="ghost" size="sm" onClick={fit}>
            Fit
          </Button>
        </div>
        <div ref={view} className="flex-1 overflow-auto [scrollbar-width:none]">
          <div
            className="mx-auto p-4"
            style={{ width: drawing.width * zoom + 32 }}
            dangerouslySetInnerHTML={{ __html: drawing.svg }}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => md.render(text), [text]);
  const ref = useRef<HTMLDivElement>(null);
  const dark = useSyncExternalStore(onDarkChange, () => darkQuery.matches);
  const [expanded, setExpanded] = useState<Drawing | null>(null);
  useEffect(() => {
    const blocks = [...ref.current!.querySelectorAll<HTMLElement>("[data-mermaid]")];
    if (!blocks.length) return;
    let stale = false;
    draw(blocks, dark, getComputedStyle(ref.current!).fontFamily, () => stale).catch(() => {});
    return () => {
      stale = true;
    };
  }, [html, dark]);
  const onClick = (e: MouseEvent) => {
    const target = e.target as Element;
    const link = target.closest("a");
    if (link) return openLink(link, e);
    const drawing = target.closest("[data-mermaid]")?.querySelector<SVGSVGElement>("[data-diagram] svg");
    if (drawing && target.closest("[data-expand], [data-diagram]")) setExpanded(copyOf(drawing));
  };
  return (
    <>
      <div
        ref={ref}
        // No bigger than the page title.
        className="prose prose-sm max-w-none prose-neutral dark:prose-invert prose-h1:text-2xl"
        onClick={onClick}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {expanded && <Expanded drawing={expanded} onClose={() => setExpanded(null)} />}
    </>
  );
}
