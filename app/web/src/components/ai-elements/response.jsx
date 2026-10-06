import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { memo, useRef, useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import { cn } from "@/lib/utils";
import { CodeBlock } from "@/components/ai-elements/code-block";

function languageFromClassName(className) {
  const match = /language-([\w-]+)/.exec(className || "");
  return match?.[1] || "text";
}

const MARKDOWN_COMPONENTS = {
  // ---------------------------------------------------------------------
  // Only p/ul/ol/li/code/pre/a were overridden here, so headings, tables,
  // blockquotes and rules fell through to Tailwind's preflight — which strips
  // heading sizes and sets border-width to 0. The result was that `# Heading`
  // rendered as bold body text and a markdown table had no visible lines.
  // `@tailwindcss/typography` is installed but never registered (plugins: []),
  // so there was no `prose` class picking up the slack either.
  //
  // Every class below resolves through this app's own tokens — no literal
  // colours — so the navy theme is untouched.
  // ---------------------------------------------------------------------
  // Body text is 14px, so the ladder is 19 / 16 / 14 / 13. The previous
  // 16/15/14/14 put every level within a pixel of the next and of the body,
  // which is why headings read as bold paragraphs rather than as structure.
  //
  // Margins are asymmetric on purpose — roughly 3:1 above vs below — so a
  // heading binds to the text it introduces instead of floating between two
  // sections. Adjacent vertical margins collapse, so `mt-6` after a paragraph's
  // `mb-2` yields 24px, not 32.
  h1: ({ children: content }) => (
    <h1 className="mb-2 mt-6 text-[19px] font-semibold leading-snug tracking-tight first:mt-0">
      {content}
    </h1>
  ),
  h2: ({ children: content }) => (
    <h2 className="mb-2 mt-6 text-[16px] font-semibold leading-snug tracking-tight first:mt-0">
      {content}
    </h2>
  ),
  h3: ({ children: content }) => (
    <h3 className="mb-1.5 mt-5 text-sm font-semibold leading-snug first:mt-0">{content}</h3>
  ),
  h4: ({ children: content }) => (
    <h4 className="mb-1 mt-4 text-[13px] font-semibold leading-snug first:mt-0">{content}</h4>
  ),
  // Below h4 there is no size left to give, so the levels separate by treatment
  // instead: h5 muted, h6 muted and lettercased.
  h5: ({ children: content }) => (
    <h5 className="mb-1 mt-4 text-[13px] font-semibold leading-snug text-muted-foreground first:mt-0">
      {content}
    </h5>
  ),
  h6: ({ children: content }) => (
    <h6 className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground first:mt-0">
      {content}
    </h6>
  ),
  // Tables use Streamdown's table treatment (as ai-elements' <Response> renders it), on
  // bg-card (white) rather than Streamdown's sidebar/background, which are grey in this theme:
  // a bordered, scrollable table with a shaded header row and row dividers, plus copy / download
  // controls that appear on hover.
  table: ({ children: content }) => <MarkdownTable>{content}</MarkdownTable>,
  thead: ({ children: content }) => <thead className="bg-muted/80">{content}</thead>,
  tbody: ({ children: content }) => <tbody className="divide-y divide-border">{content}</tbody>,
  tr: ({ children: content }) => <tr className="border-border">{content}</tr>,
  th: ({ children: content, style }) => (
    <th style={style} className="whitespace-nowrap px-4 py-2 text-left text-sm font-semibold">
      {content}
    </th>
  ),
  td: ({ children: content, style }) => (
    <td style={style} className="px-4 py-2 text-sm">
      {content}
    </td>
  ),
  blockquote: ({ children: content }) => (
    <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground">
      {content}
    </blockquote>
  ),
  // Models emit `---` before most `##` sections. The rule plus the heading
  // directly under it reads as a boxed band, and the rule adds nothing the
  // heading doesn't already say — so a rule that immediately precedes a heading
  // is dropped. A rule between two paragraphs still shows, lightly.
  hr: () => (
    <hr className="my-5 border-t border-border/60 [&:has(+h1)]:hidden [&:has(+h2)]:hidden [&:has(+h3)]:hidden [&:has(+h4)]:hidden" />
  ),

  p: ({ children: content }) => <p className="mb-2 last:mb-0">{content}</p>,
  ul: ({ children: content }) => <ul className="mb-2 ml-5 list-disc">{content}</ul>,
  ol: ({ children: content }) => <ol className="mb-2 ml-5 list-decimal">{content}</ol>,
  li: ({ children: content }) => <li className="mb-1">{content}</li>,
  code: ({ children: content }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px]">{content}</code>
  ),
  pre: ({ children: content }) => {
    const codeElement = Array.isArray(content) ? content[0] : content;
    const codeProps = codeElement?.props || {};
    return (
      <CodeBlock
        className="my-4"
        code={codeProps.children}
        language={languageFromClassName(codeProps.className)}
      />
    );
  },
  a: ({ href, children: content }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-primary underline underline-offset-2"
    >
      {content}
    </a>
  ),
};

export const Response = memo(function Response({ className, children, ...props }) {
  const text = typeof children === "string" ? children : null;

  return (
    <div className={cn("break-words leading-relaxed", className)} {...props}>
      {text != null ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
          {text}
        </ReactMarkdown>
      ) : (
        <div className="whitespace-pre-wrap">{children}</div>
      )}
    </div>
  );
});

/** The rendered table's cells, row by row (header first), read from the DOM. */
function tableRows(table) {
  return Array.from(table?.rows || []).map((row) =>
    Array.from(row.cells).map((cell) => cell.innerText.trim()),
  );
}

function toMarkdown(rows) {
  if (!rows.length) return "";
  const line = (cells) => `| ${cells.map((c) => c.replace(/\|/g, "\\|")).join(" | ")} |`;
  return [line(rows[0]), line(rows[0].map(() => "---")), ...rows.slice(1).map(line)].join("\n");
}

function toCsv(rows) {
  const cell = (c) => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c);
  return rows.map((r) => r.map(cell).join(",")).join("\n");
}

/** A markdown table in Streamdown's style, with hover controls: copy as Markdown, download CSV. */
function MarkdownTable({ children }) {
  const ref = useRef(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(toMarkdown(tableRows(ref.current)));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  const download = () => {
    const blob = new Blob([toCsv(tableRows(ref.current))], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "table.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  const control =
    "rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground";
  return (
    // One bordered panel; the controls overlay its top-right corner (as in ChatGPT) and show on
    // hover of THIS table (a named group: the message wrapper is a `group` too, so plain
    // group-hover fired anywhere in the message) or on keyboard focus.
    <div data-markdown-table className="group/table relative my-4">
      <div className="overflow-x-auto overflow-y-auto rounded-md border border-border bg-card">
        <table ref={ref} className="w-full divide-y divide-border border-collapse">
          {children}
        </table>
      </div>
      <div className="absolute right-1.5 top-1.5 z-10 flex items-center gap-0.5 rounded-md border border-border bg-card/95 p-0.5 opacity-0 shadow-sm transition-opacity group-hover/table:opacity-100 has-[:focus-visible]:opacity-100">
        <button
          type="button"
          onClick={copy}
          className={control}
          aria-label={copied ? "Copied table" : "Copy table as Markdown"}
          title="Copy table"
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        </button>
        <button
          type="button"
          onClick={download}
          className={control}
          aria-label="Download table as CSV"
          title="Download CSV"
        >
          <Download className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
