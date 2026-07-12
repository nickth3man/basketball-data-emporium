/**
 * MarkdownContent — renders the streamed `turn.answer` as rich markdown.
 *
 * Wrapped in `React.memo` so SETTLED bubbles (whose `content` prop is
 * stable) do not re-parse on every token-by-token delta of the live
 * in-flight bubble above them. The live bubble re-renders are unavoidable
 * (its content genuinely changes), but memoizing isolates the cost to it.
 *
 * Pipeline: react-markdown + remark-gfm (tables, strikethrough, task
 * lists) + rehype-raw (model-emitted inline HTML like <br>) +
 * rehype-highlight (auto-detects fenced code language and reuses the
 * already-shipped highlight.js — no double bundle). The `.hljs` token
 * colors come from globals.css (github-light + a scoped dark override).
 *
 * Safety note: rehype-raw renders raw HTML verbatim. The backend composes
 * `answer` from a controlled template path (not free model CoT), so the
 * HTML surface is bounded; flag for review if the composer ever surfaces
 * untrusted model output directly.
 */
import { memo, useMemo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeHighlight from "rehype-highlight";
import type { PluggableList } from "unified";

import { cn } from "@/lib/utils";

export interface MarkdownContentProps {
  content: string;
  className?: string;
}

function MarkdownContentImpl({ content, className }: MarkdownContentProps) {
  // Plugins are stable across renders (module-level would be even better,
  // but a memoized array avoids re-creating the pipeline each render).
  const remarkPlugins = useMemo(() => [remarkGfm], []);
  // `PluggableList` (from unified) is the contextual type react-markdown's
  // `rehypePlugins` prop expects; annotating it makes TS read the
  // `[plugin, options]` pair as a tuple rather than widening to an array.
  const rehypePlugins = useMemo<PluggableList>(
    () => [rehypeRaw, [rehypeHighlight, { ignoreMissing: true }]],
    [],
  );

  return (
    <div
      className={cn(
        // `prose-chat` (defined in globals.css) wires the prose tokens to
        // our design-system CSS vars so headings, links, code, and tables
        // read in both themes.
        `
          prose-chat prose prose-sm max-w-none
          dark:prose-invert
        `,
        `
          prose-headings:mt-3 prose-headings:mb-1
          prose-p:leading-relaxed
          prose-li:my-0.5
        `,
        `prose-pre:my-2 prose-pre:bg-muted prose-pre:px-3 prose-pre:py-2.5`,
        "prose-table:text-xs",
        className,
      )}
    >
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={{
          // External links open safely in a new tab; inline links keep the
          // accent underline treatment from prose.
          a(props) {
            const { children, href } = props;
            const isExternal = typeof href === "string" && /^https?:\/\//i.test(href);
            return (
              <a
                href={href}
                target={isExternal ? "_blank" : undefined}
                rel={isExternal ? "noopener noreferrer" : undefined}
              >
                {children}
              </a>
            );
          },
          // Wrap tables in a horizontal scroll container so wide result-like
          // tables don't blow out the bubble width.
          table(props) {
            return (
              <div className="
                my-2 overflow-x-auto rounded-md border border-border
              ">
                <table {...props} />
              </div>
            );
          },
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}

export const MarkdownContent = memo(MarkdownContentImpl);
