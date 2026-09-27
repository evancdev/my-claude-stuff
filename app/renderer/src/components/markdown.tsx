import MarkdownIt from "markdown-it";
import { type MouseEvent, useMemo } from "react";

// html stays off, so markdown-it escapes raw HTML in a file instead of passing
// it through. That is what makes the innerHTML below safe.
const md = new MarkdownIt({ linkify: true });

// Left alone, a relative link loads over the app from the dev server and a
// #fragment adds a history entry the page history can't read. The written
// href, since link.href resolves a relative one against the dev server too.
function openLink(e: MouseEvent) {
  const link = (e.target as Element).closest("a");
  if (!link) return;
  e.preventDefault();
  const href = link.getAttribute("href") ?? "";
  if (/^https?:\/\//i.test(href)) window.open(href);
}

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => md.render(text), [text]);
  return (
    <div
      // No bigger than the page title.
      className="prose prose-sm max-w-none prose-neutral dark:prose-invert prose-h1:text-2xl"
      onClick={openLink}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
