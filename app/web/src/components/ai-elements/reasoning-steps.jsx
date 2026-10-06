import { useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, CircleDashed, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { Response } from "@/components/ai-elements/response";

/**
 * Stepped view of a reasoning trace.
 *
 * Providers emit ONE continuous thinking block per model turn — they do not
 * split it into phases (only some providers' summarized thinking does that). But the
 * trace is naturally paragraphed, and those paragraphs do map to phases:
 * assessing the request, drafting, checking constraints. So we split on blank
 * lines and show one collapsed line per paragraph, each independently
 * expandable.
 *
 * This is a presentation of a single trace, not a claim that the model produced
 * discrete steps. Multiple genuine reasoning blocks (one per turn, e.g. either
 * side of a tool call) still render as separate `Reasoning*` components.
 */

/**
 * Prefer the model's own paragraph breaks; fall back to grouping sentences.
 *
 * Some providers emit the trace as ONE unbroken block with no newlines at all,
 * so paragraph splitting alone yields a single step. Sentence grouping is what
 * actually produces phases for them.
 */
const SENTENCES_PER_STEP = 2;

export function toSteps(text, streaming) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return [];

  const paragraphs = trimmed
    .split(/\n\s*\n+/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (paragraphs.length > 1) return paragraphs;

  // Split after sentence-ending punctuation followed by whitespace + a capital
  // or digit, so "e.g." and decimals don't fracture the trace.
  const sentences = trimmed
    .split(/(?<=[.!?])\s+(?=[A-Z0-9"'“])/)
    .map((x) => x.trim())
    .filter(Boolean);
  if (sentences.length <= 1) return [trimmed];

  // While streaming, drop a trailing fragment that has no sentence terminator
  // yet — it is mid-word. Everything already terminated is a finished step and
  // must render immediately.
  const usable =
    streaming && sentences.length > 1 && !/[.!?]["'“”]?$/.test(sentences.at(-1))
      ? sentences.slice(0, -1)
      : sentences;

  const steps = [];
  for (let i = 0; i < usable.length; i += SENTENCES_PER_STEP) {
    steps.push(usable.slice(i, i + SENTENCES_PER_STEP).join(" "));
  }
  return steps;
}

const MAX = 96;

/** Sentence head of a paragraph, untruncated. Skips bare list markers. */
function head(paragraph) {
  const oneLine = paragraph.replace(/\s+/g, " ").trim();
  const re = /(?<=[.!?])\s/g;
  let m;
  while ((m = re.exec(oneLine)) !== null) {
    const candidate = oneLine.slice(0, m.index);
    if (candidate.split(/\s+/).length >= 4 && !/(^|\s)[\w]{1,3}[.)]$/.test(candidate)) {
      return candidate;
    }
  }
  return oneLine;
}

/** Strip markdown emphasis so the one-line summary doesn't show raw `**`. */
function plain(text) {
  return text
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|\W)\*([^*]+)\*(?=\W|$)/g, "$1$2")
    .replace(/^#{1,6}\s+/gm, "")
    .trim();
}

/**
 * What a step shows collapsed, and what expanding reveals.
 *
 * Two ways content can be hidden: the sentence head is only part of the
 * paragraph, OR the head itself exceeds the character cap. Both must make the
 * step expandable — checking only the first is what made a visibly truncated
 * step unclickable.
 */
function stepView(paragraph) {
  const oneLine = paragraph.replace(/\s+/g, " ").trim();
  const sentence = head(paragraph);
  const clipped = sentence.length > MAX;
  const summary = plain(clipped ? `${sentence.slice(0, MAX).trimEnd()}…` : sentence);

  // If the visible line was clipped mid-sentence, the expansion needs the whole
  // paragraph; otherwise just the part the summary left out.
  const rest = oneLine.startsWith(sentence) ? oneLine.slice(sentence.length).trim() : "";
  const body = clipped ? oneLine : rest;

  return { summary, body, expandable: body.length > 0 };
}

/**
 * One row on the step rail: status dot, connector, one-line summary, and an
 * optional expansion.
 *
 * Shared with tool calls so a reasoning block and the tool it invoked sit on the
 * SAME rail. A response alternates think → call → think, and rendering the calls
 * as separate cards alongside the rail made one continuous thought read as
 * several disconnected blocks.
 */
export function StepRow({
  status = "done",
  summary,
  isLast,
  expandable,
  defaultOpen = false,
  children,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const rowRef = useRef(null);
  const scrollWhenOpen = useRef(false);

  // Opening a step brings it to the top of the Activity window, so the content
  // that just unfolded is in view rather than below the fold. Only the window
  // scrolls (never the page), and only on open. It runs once the expand
  // animation ends: before that the content has no height yet, so near the
  // bottom of the window there would be nothing to scroll into.
  const bringToTop = () => {
    const row = rowRef.current;
    const scroller = row?.closest("[data-activity-scroll]");
    if (!row || !scroller) return;
    const offset = row.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    scroller.scrollTo({ top: scroller.scrollTop + offset - 4, behavior: "smooth" });
  };

  const toggle = () => {
    scrollWhenOpen.current = !open;
    setOpen((value) => !value);
  };

  const Icon = status === "error" ? XCircle : status === "running" ? CircleDashed : CheckCircle2;

  return (
    <li ref={rowRef} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-3">
      <div className="flex flex-col items-center">
        <Icon
          className={cn(
            "mt-0.5 size-3.5 shrink-0",
            status === "running" && "animate-spin text-muted-foreground",
            status === "done" && "text-muted-foreground/50",
            status === "error" && "text-destructive/70",
          )}
        />
        {!isLast ? <div className="mt-1 w-px flex-1 bg-border" /> : null}
      </div>

      <div className={cn("min-w-0", isLast ? "pb-0" : "pb-3")}>
        <button
          type="button"
          disabled={!expandable}
          onClick={toggle}
          className={cn(
            // Lighter than body prose — the rail is context for the answer, not
            // part of it — but no lighter than the header that opened it. At
            // 12px the same opacity renders visibly thinner than the 13px
            // header, so matching them means giving the rows more, not equal.
            "block w-full text-left text-[12px] leading-snug text-muted-foreground",
            expandable && "hover:text-foreground",
            !expandable && "cursor-default",
          )}
        >
          {summary}
        </button>

        <AnimatePresence initial={false}>
          {open && expandable ? (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
              onAnimationComplete={() => {
                if (!scrollWhenOpen.current) return;
                scrollWhenOpen.current = false;
                bringToTop();
              }}
              className="overflow-hidden"
            >
              {children}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </li>
  );
}

/** A single paragraph of a reasoning trace, as a step row. */
export function ThoughtStep({ paragraph, isLast, streaming }) {
  const { summary, body, expandable } = useMemo(() => stepView(paragraph), [paragraph]);

  return (
    <StepRow
      status={streaming && isLast ? "running" : "done"}
      summary={summary}
      isLast={isLast}
      expandable={expandable}
    >
      <Response className="mt-1 text-[12px] text-muted-foreground">{body}</Response>
    </StepRow>
  );
}

export function ReasoningSteps({ className, text, state = "streaming", settled = false }) {
  // `state` only becomes "done" at `reasoning-end`, which providers send AFTER
  // the answer has started streaming. So a step would keep spinning underneath
  // a completed answer. `settled` lets the caller say "the answer began, this
  // reasoning is over" without waiting for the block to formally close.
  const streaming = !settled && state !== "done";
  const steps = useMemo(() => toSteps(text, streaming), [text, streaming]);

  if (!steps.length) {
    return streaming ? (
      <div className={cn("grid grid-cols-[1.25rem_minmax(0,1fr)] gap-3 py-1", className)}>
        <div className="flex justify-center">
          <CircleDashed className="mt-0.5 size-3.5 animate-spin text-muted-foreground" />
        </div>
        <div className="h-4 w-28 animate-pulse rounded bg-muted" />
      </div>
    ) : null;
  }

  // No scroll container here: consecutive reasoning blocks and tool calls are
  // wrapped together by `Activity`, so each block must render at natural height.
  return (
    <ul className={cn("space-y-0", className)}>
      {steps.map((paragraph, i) => (
        <ThoughtStep
          key={i}
          paragraph={paragraph}
          isLast={i === steps.length - 1}
          streaming={streaming}
        />
      ))}
    </ul>
  );
}

/** Step count, for callers that want to size or label a group. */
export function countReasoningSteps(text, state) {
  return toSteps(text, state !== "done").length;
}

/**
 * Summary of the newest step, for the group header.
 *
 * Prefers the newest step that reads as prose. A trace often ends mid-list
 * ("1. Strategic shifts — … 2. …"), and using that as a status line produces a
 * truncated list fragment rather than a description of what is happening.
 */
export function latestStepSummary(text, state) {
  const steps = toSteps(text, state !== "done");
  if (!steps.length) return null;

  const isListy = (step) => /^\s*(\d+[.)]|[-*•])\s/.test(step);
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    if (!isListy(steps[i])) return stepView(steps[i]).summary;
  }
  return stepView(steps[steps.length - 1]).summary;
}
