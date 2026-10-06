import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Highlight, Prism } from "prism-react-renderer";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A highlighted code panel: one look for every code surface (markdown answers and
 * tool Input / Output boxes).
 *
 * Colours come from CSS variables (`--code-*` in index.css), not from a Prism theme
 * object, so light and dark follow the app's `.dark` class with no JS theme
 * plumbing and no re-render on toggle.
 *
 * Highlighting is synchronous (prism-react-renderer), so code that is still
 * streaming in as tool arguments highlights as it arrives.
 */

const THEME = {
  plain: { color: "hsl(var(--code-fg))", backgroundColor: "transparent" },
  styles: [
    {
      types: ["comment", "prolog", "doctype", "cdata"],
      style: { color: "hsl(var(--code-comment))", fontStyle: "italic" },
    },
    {
      types: ["keyword", "selector", "important", "atrule"],
      style: { color: "hsl(var(--code-keyword))" },
    },
    {
      types: ["string", "char", "attr-value", "regex", "inserted"],
      style: { color: "hsl(var(--code-string))" },
    },
    {
      types: ["number", "boolean", "constant", "symbol"],
      style: { color: "hsl(var(--code-number))" },
    },
    { types: ["function", "class-name"], style: { color: "hsl(var(--code-function))" } },
    {
      types: ["builtin", "property", "attr-name", "tag", "variable"],
      style: { color: "hsl(var(--code-property))" },
    },
    {
      types: ["operator", "punctuation", "deleted"],
      style: { color: "hsl(var(--code-punctuation))" },
    },
  ],
};

const LANGUAGE_ALIASES = {
  py: "python",
  js: "javascript",
  ts: "typescript",
  yml: "yaml",
  md: "markdown",
};
const LANGUAGE_LABELS = {
  python: "Python",
  json: "JSON",
  sql: "SQL",
  javascript: "JavaScript",
  typescript: "TypeScript",
  jsx: "JSX",
  tsx: "TSX",
  yaml: "YAML",
  markdown: "Markdown",
  text: "Text",
};

const TONES = {
  input: { dot: "bg-sky-500", label: "text-sky-700 dark:text-sky-300" },
  output: { dot: "bg-emerald-500", label: "text-emerald-700 dark:text-emerald-300" },
  error: { dot: "bg-rose-500", label: "text-rose-700 dark:text-rose-300" },
  neutral: { dot: "bg-muted-foreground/50", label: "text-muted-foreground" },
};

// Scroll positions survive re-renders while a block streams in (keyed by content).
const SCROLL_POSITIONS = new Map();

function normalizeLanguage(language) {
  const key = String(language || "text").toLowerCase();
  const resolved = LANGUAGE_ALIASES[key] || key;
  return Prism.languages[resolved] ? resolved : "text";
}

function scrollKey(language, code) {
  const seed = `${language}:${code.slice(0, 320)}`;
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1)
    hash = (hash * 31 + seed.charCodeAt(index)) >>> 0;
  return `${language}:${hash}`;
}

export const CodeBlock = memo(function CodeBlock({
  code,
  language = "text",
  label,
  tone = "neutral",
  meta,
  lineNumbers = false,
  maxHeightClassName = "max-h-[520px]",
  className,
}) {
  const [copied, setCopied] = useState(false);
  const preRef = useRef(null);
  const text = String(code ?? "").replace(/\n$/, "");
  const lang = normalizeLanguage(language);
  const key = useMemo(() => scrollKey(lang, text), [lang, text]);
  const palette = TONES[tone] || TONES.neutral;
  const capped = maxHeightClassName !== "max-h-none";

  useLayoutEffect(() => {
    const pre = preRef.current;
    const saved = SCROLL_POSITIONS.get(key);
    if (!pre || !saved) return;
    const maxTop = Math.max(0, pre.scrollHeight - pre.clientHeight);
    pre.scrollTop =
      saved.bottomGap <= 4 ? Math.max(0, maxTop - saved.bottomGap) : Math.min(saved.top, maxTop);
    pre.scrollLeft = saved.left;
  });

  const rememberScroll = () => {
    const pre = preRef.current;
    if (!pre) return;
    SCROLL_POSITIONS.set(key, {
      top: pre.scrollTop,
      left: pre.scrollLeft,
      bottomGap: Math.max(0, pre.scrollHeight - pre.clientHeight - pre.scrollTop),
    });
  };

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard unavailable (insecure context): nothing to do
    }
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-[hsl(var(--code-border))] bg-[hsl(var(--code-bg))] shadow-[0_1px_2px_rgba(0,0,0,0.04)]",
        className,
      )}
    >
      <div className="flex items-center gap-2 border-b border-[hsl(var(--code-border))] bg-[hsl(var(--code-header))] px-3 py-1.5">
        <span className={cn("size-1.5 shrink-0 rounded-full", palette.dot)} aria-hidden="true" />
        {label ? (
          <span
            className={cn("text-[10.5px] font-semibold uppercase tracking-[0.08em]", palette.label)}
          >
            {label}
          </span>
        ) : null}
        <span className="rounded border border-[hsl(var(--code-border))] px-1.5 py-px font-mono text-[10px] text-muted-foreground">
          {LANGUAGE_LABELS[lang] || lang}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {meta ? (
            <span className="font-mono text-[10.5px] text-muted-foreground">{meta}</span>
          ) : null}
          <button
            type="button"
            onClick={copy}
            aria-label={label ? `Copy ${String(label).toLowerCase()}` : "Copy code"}
            className="rounded p-1 text-muted-foreground transition-colors hover:bg-background/70 hover:text-foreground"
          >
            {copied ? (
              <Check className="size-3.5 text-emerald-600" />
            ) : (
              <Copy className="size-3.5" />
            )}
          </button>
        </div>
      </div>
      <Highlight code={text} language={lang} theme={THEME}>
        {({ tokens, getLineProps, getTokenProps }) => (
          <pre
            ref={preRef}
            onScroll={rememberScroll}
            // Only a panel with its own height cap keeps the wheel to itself; an uncapped
            // one (inside the Activity rail) lets vertical scrolling reach the rail.
            onWheel={capped ? (event) => event.stopPropagation() : undefined}
            className={cn(
              "overflow-auto py-2.5 font-mono text-[12px] leading-[1.6]",
              capped && "overscroll-contain",
              maxHeightClassName,
            )}
          >
            <code className="block min-w-full">
              {tokens.map((line, index) => {
                const { key: _lineKey, ...lineProps } = getLineProps({ line });
                return (
                  <div key={index} {...lineProps} className="flex px-3">
                    {lineNumbers ? (
                      <span className="mr-4 w-6 shrink-0 select-none text-right text-[hsl(var(--code-gutter))]">
                        {index + 1}
                      </span>
                    ) : null}
                    <span className="whitespace-pre">
                      {line.map((token, tokenIndex) => {
                        const { key: _tokenKey, ...tokenProps } = getTokenProps({ token });
                        return <span key={tokenIndex} {...tokenProps} />;
                      })}
                    </span>
                  </div>
                );
              })}
            </code>
          </pre>
        )}
      </Highlight>
    </div>
  );
});
