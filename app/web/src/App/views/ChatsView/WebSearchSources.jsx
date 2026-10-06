import { ExternalLink } from "lucide-react";

import { cn } from "@/lib/utils";
import { Hint } from "@/components/ui/hint";

/**
 * Pull a source list out of a `web_search` tool result.
 *
 * Linkup returns `{answer, sources: [{name, url, snippet}]}` for `sourcedAnswer` and
 * `{results: [{name, url, content}]}` for `searchResults`. The output may arrive as a JSON
 * string when reloaded from history, so parse defensively and never throw.
 */
export function webSearchSourcesFromPart(part) {
  const name = part?.toolName ?? String(part?.type || "").replace(/^tool-/, "");
  if (name !== "web_search" || part?.state !== "output-available") return null;
  let output = part.output;
  if (typeof output === "string") {
    try {
      output = JSON.parse(output);
    } catch {
      return null;
    }
  }
  const items = Array.isArray(output?.sources)
    ? output.sources
    : Array.isArray(output?.results)
      ? output.results
      : [];
  const seen = new Set();
  const sources = [];
  for (const item of items) {
    const url = typeof item?.url === "string" ? item.url : "";
    if (!/^https?:\/\//i.test(url) || seen.has(url)) continue;
    seen.add(url);
    sources.push({ url, name: item.name || hostname(url) });
  }
  return sources.length ? sources : null;
}

function hostname(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Compact, full-width list of sources a web search came back with. */
export function WebSearchSources({ sources, className }) {
  if (!sources?.length) return null;
  return (
    <div className={cn("my-3 flex flex-wrap gap-1.5", className)}>
      {sources.slice(0, 8).map((source) => (
        <Hint key={source.url} label={source.name}>
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <span className="truncate">{source.name}</span>
            <span className="shrink-0 text-muted-foreground/70">{hostname(source.url)}</span>
            <ExternalLink className="h-3 w-3 shrink-0" />
          </a>
        </Hint>
      ))}
    </div>
  );
}
