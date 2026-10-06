import { memo } from "react";

import { Response } from "@/components/ai-elements/response";
import { Activity } from "@/components/ai-elements/activity";
import {
  StepRow,
  ThoughtStep,
  latestStepSummary,
  toSteps,
} from "@/components/ai-elements/reasoning-steps";
import { CodeBlock } from "@/components/ai-elements/code-block";
import { WebSearchSources, webSearchSourcesFromPart } from "./WebSearchSources";
import { WorkflowStages } from "@/components/workflow/WorkflowStages";
import { WorkflowExtras } from "@/components/workflow/WorkflowExtras";
import { WORKFLOW_TOOL, stageParts } from "@/lib/workflow";
import { toolActivity, toolLabel } from "@/lib/toolLabels";

/**
 * Assistant rendering for the full-page chat.
 *
 * This is a second renderer, deliberately, rather than a change to
 * `AssistantMessageParts` — that one stays the simple per-part default for
 * `ChatConversation`; ChatsView opts into this one.
 *
 * What differs from `AssistantMessageParts`:
 *
 * - Consecutive reasoning and tool parts collapse into ONE container instead of
 *   a separate block per part. A response alternates think → call → think, so
 *   per-part blocks stack up and push the answer off screen.
 * - Reasoning and tool calls share one vertical rail. Previously reasoning was
 *   a collapsible and tools were separate cards beside it, which made a single
 *   continuous sequence read as disconnected pieces.
 *
 * What is deliberately unchanged: `part.output` is never normalised, reparsed or
 * written back onto the part.
 */

function isToolPart(part) {
  return typeof part?.type === "string" && part.type.startsWith("tool-");
}

function toolName(part) {
  return part.toolName ?? String(part.type || "").replace(/^tool-/, "");
}

/**
 * Whether a tool call has come back yet.
 *
 * Shared by the row and the container header so the two cannot disagree.
 * `!streaming` forces "done" so a call that never reported an output can't
 * leave a spinner running under a finished answer.
 */
function toolStatus(part, streaming) {
  if (part.state === "output-error") return "error";
  if (part.state === "output-available" || !streaming) return "done";
  return "running";
}

function isEmpty(value) {
  if (value === undefined || value === null || value === "") return true;
  return typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length;
}

/** A placeholder box, so every tool step shows both Input and Output, even when empty. */
function EmptyBox({ label, tone, text }) {
  return (
    <CodeBlock
      label={label}
      tone={tone}
      language="text"
      code={text}
      maxHeightClassName="max-h-none"
    />
  );
}

function asJson(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function parseOutput(output) {
  if (!output || typeof output !== "string") return output;
  try {
    return JSON.parse(output);
  } catch {
    return output;
  }
}

/**
 * The request side of a tool call. Code a tool ran (run_python_analysis) is shown
 * as highlighted Python with line numbers; any other input as JSON.
 */
function ToolInput({ input }) {
  if (isEmpty(input)) return <EmptyBox label="Input" tone="input" text="No arguments" />;
  if (typeof input === "object" && typeof input.code === "string") {
    const datasets = Array.isArray(input.datasets) && input.datasets.length ? input.datasets : null;
    return (
      <CodeBlock
        label="Input"
        tone="input"
        language="python"
        code={input.code}
        meta={datasets ? `data: ${datasets.join(", ")}` : null}
        lineNumbers
        maxHeightClassName="max-h-none"
      />
    );
  }
  return (
    <CodeBlock
      label="Input"
      tone="input"
      language="json"
      code={asJson(input)}
      maxHeightClassName="max-h-none"
    />
  );
}

/**
 * The result side. Printed analysis output is shown as printed (a pandas table
 * reads best as text), with its one-line error on a red panel if it failed; any
 * other result is shown as JSON.
 */
function ToolOutput({ part }) {
  if (part.errorText) {
    return <CodeBlock label="Error" tone="error" language="text" code={part.errorText} />;
  }
  const output = parseOutput(part.output);
  if (isEmpty(output)) {
    const done = part.state === "output-available" || part.state === "output-error";
    return (
      <EmptyBox label="Output" tone="output" text={done ? "No output" : "Waiting for output…"} />
    );
  }
  if (typeof output === "object" && typeof output.stdout === "string") {
    const seconds =
      typeof output.duration_ms === "number" ? `${(output.duration_ms / 1000).toFixed(2)}s` : null;
    const status = output.timed_out ? "timed out" : output.success === false ? "failed" : "ok";
    return (
      <>
        {output.stdout.trim() || !output.error ? (
          <CodeBlock
            label="Output"
            tone="output"
            language="text"
            code={output.stdout.trimEnd() || "No printed output"}
            meta={[status, seconds, output.truncated ? "truncated" : null]
              .filter(Boolean)
              .join(" · ")}
            maxHeightClassName="max-h-none"
          />
        ) : null}
        {output.error ? (
          <CodeBlock label="Error" tone="error" language="text" code={output.error} />
        ) : null}
      </>
    );
  }
  const fetched =
    typeof output === "object" && output.dataset_id
      ? `${output.dataset_id} · ${output.row_count} rows`
      : null;
  const failed = typeof output === "object" && output.error && !output.dataset_id;
  return (
    <CodeBlock
      label={failed ? "Error" : "Output"}
      tone={failed ? "error" : "output"}
      language="json"
      code={asJson(output)}
      meta={fetched}
      maxHeightClassName="max-h-none"
    />
  );
}

/** A tool call as a row on the rail, expanding to request + result. */
function ToolStep({ part, isLast, streaming, defaultOpen, stages }) {
  const status = toolStatus(part, streaming);
  const summary = toolLabel(toolName(part), part.input ?? part.rawInput);

  return (
    <StepRow status={status} summary={summary} isLast={isLast} expandable defaultOpen={defaultOpen}>
      <div className="mt-2 space-y-2">
        {stages?.length ? <WorkflowStages stages={stages} /> : null}
        {/* The workflow step shows its stages; its form or card is the output, so no raw JSON. */}
        {part.type === WORKFLOW_TOOL ? null : (
          <>
            <ToolInput input={part.input ?? part.rawInput} />
            <ToolOutput part={part} />
          </>
        )}
      </div>
    </StepRow>
  );
}

/** Header for a container: what the assistant is doing, or last did. */
function groupLabel(lastRow, group, streaming) {
  if (lastRow.kind === "tool") {
    return toolActivity(
      toolName(lastRow.part),
      toolStatus(lastRow.part, streaming) === "running",
      lastRow.part.output,
    );
  }
  const trace = group.items.filter((entry) => entry.part?.type === "reasoning").at(-1)?.part.text;
  return latestStepSummary(trace, streaming ? "streaming" : "done") ?? "Working";
}

/**
 * What a part is, for layout.
 *
 * `hidden` matters as much as the other two. `step-start` is a real entry in
 * `message.parts` and the SDK emits one per tool round, so treating it as a
 * boundary splits what is one continuous think → call → think sequence into a
 * separate container per round — several boxes with nothing visible between
 * them.
 */
function classify(part) {
  if (part?.type === "reasoning" || isToolPart(part)) return "activity";
  if (part?.type === "text" && String(part.text || "").trim()) return "prose";
  return "hidden";
}

export const ChatsAssistantParts = memo(function ChatsAssistantParts({
  message,
  fallbackText,
  inFlight = true,
}) {
  const parts = Array.isArray(message.parts) ? message.parts : [];
  if (!parts.length) {
    return <Response className="text-sm leading-[1.75]">{fallbackText}</Response>;
  }

  const entries = parts.map((part, index) => ({ part, index }));
  const stages = stageParts(message);

  // Activity containers are INLINE and chronological: each consecutive run of
  // reasoning + tool calls becomes one container, sitting where it happened.
  // Text parts are messages to the reader and always render as prose between
  // them — hoisting all activity above all text would reorder the transcript
  // and demote narration to a step.
  const groups = [];
  for (const entry of entries) {
    const kind = classify(entry.part);
    if (kind === "hidden") continue;
    const tail = groups[groups.length - 1];
    if (tail && tail.kind === kind) tail.items.push(entry);
    else groups.push({ kind, items: [entry] });
  }

  // The form and card sit right after the workflow step that produced them, so text the turn
  // adds afterwards (the closing follow-up question) reads below them, in order.
  const extrasAt = groups.findIndex(
    (group) => group.kind === "activity" && group.items.some(({ part }) => part?.type === WORKFLOW_TOOL),
  );
  const extras = <WorkflowExtras key={`${message.id}-workflow`} message={message} />;

  const rendered = groups.map((group, gi) => {
    if (group.kind === "prose") {
      return group.items.map(({ part, index }) => (
        <Response key={`${message.id}-${index}`} className="text-sm leading-[1.75]">
          {part.text}
        </Response>
      ));
    }

    // A run is finished once user-facing text after it has started rendering.
    // `reasoning-end` arrives AFTER the answer begins, so waiting for the part
    // to close leaves a step spinning underneath a completed answer.
    const lastIndex = group.items[group.items.length - 1].index;
    // A message that's no longer in flight is done, even if no text follows its tools (a
    // workflow turn ends with its form or card, not prose).
    const streaming =
      inFlight &&
      !entries.some(
        ({ part, index }) =>
          index > lastIndex && part?.type === "text" && String(part.text || "").trim().length > 0,
      );

    // Flatten the run onto ONE rail: each paragraph of each reasoning block and
    // each tool call becomes a row, in order. Only the final row can spin.
    const rows = [];
    const artifacts = [];
    for (const { part, index } of group.items) {
      if (isToolPart(part)) {
        rows.push({ kind: "tool", part, key: part.toolCallId ?? `t${index}` });
        // Results the reader acts on (search sources) live OUTSIDE the rail, at
        // full width, rather than buried in an 18rem scroll region.
        const sources = webSearchSourcesFromPart(part);
        if (sources) artifacts.push({ sources, key: `${message.id}-sources-${index}` });
      } else if (part.text) {
        const last = part === group.items[group.items.length - 1].part;
        for (const [i, paragraph] of toSteps(part.text, streaming && last).entries()) {
          rows.push({ kind: "thought", paragraph, key: `r${index}-${i}` });
        }
      }
    }
    if (!rows.length && !artifacts.length) return gi === extrasAt ? extras : null;

    return (
      <div key={`${message.id}-activity-${gi}`}>
        {rows.length ? (
          <Activity
            label={groupLabel(rows.at(-1), group, streaming)}
            streaming={streaming}
            itemCount={rows.length}
          >
            <ul className="space-y-0">
              {rows.map((row, i) =>
                row.kind === "tool" ? (
                  <ToolStep
                    key={row.key}
                    part={row.part}
                    isLast={i === rows.length - 1}
                    streaming={streaming}
                    stages={row.part.type === WORKFLOW_TOOL ? stages : undefined}
                    // Steps always start closed, one call or many; the reader opens one.
                    defaultOpen={false}
                  />
                ) : (
                  <ThoughtStep
                    key={row.key}
                    paragraph={row.paragraph}
                    isLast={i === rows.length - 1}
                    streaming={streaming}
                  />
                ),
              )}
            </ul>
          </Activity>
        ) : null}
        {artifacts.map(({ sources, key }) => (
          <WebSearchSources key={key} sources={sources} />
        ))}
        {gi === extrasAt ? extras : null}
      </div>
    );
  });

  return (
    <>
      {rendered}
      {extrasAt === -1 ? extras : null}
    </>
  );
});
