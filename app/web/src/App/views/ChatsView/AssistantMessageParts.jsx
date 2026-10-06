import { memo } from "react";

import { Response } from "@/components/ai-elements/response";
import { Reasoning } from "@/components/ai-elements/reasoning";
import { WebSearchSources, webSearchSourcesFromPart } from "./WebSearchSources";
import { Tool, ToolContent, ToolHeader, toToolState } from "@/components/ai-elements/tool";

function isToolPart(part) {
  return typeof part?.type === "string" && part.type.startsWith("tool-");
}

const ToolPart = memo(function ToolPart({ part }) {
  const state = toToolState(part.state);
  const sources = webSearchSourcesFromPart(part);
  return (
    <>
      <div className="my-3 w-full">
        <Tool defaultOpen={false}>
          {({ open, setOpen }) => (
            <>
              <ToolHeader
                state={state}
                type={part.type}
                title={part.title}
                toolName={part.toolName}
                open={open}
                onToggle={() => setOpen((value) => !value)}
              />
              <ToolContent
                open={open}
                type={part.type}
                title={part.title}
                toolName={part.toolName}
                state={state}
                input={part.input ?? part.rawInput ?? {}}
                output={part.output}
                errorText={part.errorText}
              />
            </>
          )}
        </Tool>
      </div>
      {sources ? <WebSearchSources sources={sources} /> : null}
    </>
  );
});

const AssistantPart = memo(function AssistantPart({ part }) {
  if (part?.type === "text") {
    const text = part.text || "";
    if (!text.trim()) return null;
    return <Response className="text-sm">{text}</Response>;
  }
  if (isToolPart(part)) {
    return <ToolPart part={part} />;
  }
  if (part?.type === "reasoning" && part.text) {
    return <Reasoning text={part.text} state={part.state} defaultOpen={part.state !== "done"} />;
  }
  return null;
});

export const AssistantMessageParts = memo(function AssistantMessageParts({
  message,
  fallbackText,
}) {
  const parts = Array.isArray(message.parts) ? message.parts : [];
  if (!parts.length) {
    return <Response className="text-sm">{fallbackText}</Response>;
  }
  return parts.map((part, index) => <AssistantPart key={`${message.id}-${index}`} part={part} />);
});
