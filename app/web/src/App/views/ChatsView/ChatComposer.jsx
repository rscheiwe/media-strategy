import { useEffect, useRef } from "react";
import { QuoteIcon, XIcon } from "lucide-react";

import {
  PromptInput,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CHAT_AGENTS, agentSupportsThinking } from "@/constants/chatAgents";
import { cn } from "@/lib/utils";

const THINKING_EFFORTS = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
  { value: "extra", label: "Extra" },
  { value: "max", label: "Max" },
];

export function ChatComposer({
  input,
  status,
  uiStatus,
  selectedAgentUuid,
  thinkingEnabled,
  thinkingEffort,
  quotedSelection,
  onInputChange,
  onAgentChange,
  onThinkingEnabledChange,
  onThinkingEffortChange,
  onClearQuote,
  onSubmit,
  onStop,
  canSend = true,
  submitOnEnter = true,
}) {
  const busy = status === "submitted" || status === "streaming";
  const showThinkingControls = agentSupportsThinking(selectedAgentUuid);
  const textareaRef = useRef(null);

  useEffect(() => {
    if (quotedSelection) textareaRef.current?.focus();
  }, [quotedSelection]);

  return (
    <div className="shrink-0 bg-background p-4">
      <div className="mx-auto max-w-3xl">
        <PromptInput onSubmit={onSubmit}>
          {quotedSelection ? (
            <div className="px-3 pt-3">
              <div className="group flex max-w-[15rem] items-start gap-2 rounded-lg border bg-card p-2.5 text-xs text-card-foreground shadow-sm">
                <div className="mt-0.5 h-16 w-1 shrink-0 rounded-full bg-border" />
                <QuoteIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <p className="line-clamp-5 min-w-0 flex-1 leading-snug text-muted-foreground">
                  {quotedSelection.text}
                </p>
                <button
                  type="button"
                  className="rounded-sm p-0.5 text-muted-foreground opacity-70 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100"
                  aria-label="Remove quoted text"
                  onClick={onClearQuote}
                >
                  <XIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ) : null}
          <PromptInputTextarea
            ref={textareaRef}
            value={input}
            onChange={(event) => onInputChange(event.currentTarget.value)}
            submitOnEnter={submitOnEnter}
            placeholder={
              submitOnEnter ? "Ask anything..." : "Ask anything... (⌘/Ctrl+Enter to send)"
            }
            disabled={busy}
          />
          <PromptInputToolbar>
            <PromptInputTools className="mr-auto">
              {CHAT_AGENTS.length > 1 ? (
                <Select value={selectedAgentUuid} onValueChange={onAgentChange} disabled={busy}>
                  <SelectTrigger className="h-8 w-[150px] text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CHAT_AGENTS.map((agent) => (
                      <SelectItem key={agent.uuid} value={agent.uuid}>
                        {agent.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
              {showThinkingControls ? (
                <>
                  <Select
                    value={thinkingEffort}
                    onValueChange={onThinkingEffortChange}
                    disabled={busy || !thinkingEnabled}
                  >
                    <SelectTrigger
                      className={cn(
                        "h-8 w-[104px] text-xs",
                        !thinkingEnabled && "text-muted-foreground",
                      )}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent align="start">
                      <div className="px-2 py-2 text-xs leading-snug text-muted-foreground">
                        Higher effort can produce more thorough responses.
                      </div>
                      {THINKING_EFFORTS.map((effort) => (
                        <SelectItem key={effort.value} value={effort.value}>
                          {effort.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={thinkingEnabled}
                    disabled={busy}
                    className={cn(
                      "inline-flex h-8 items-center gap-2 rounded-md border bg-background px-2.5 text-xs text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50",
                      thinkingEnabled && "border-primary/40",
                    )}
                    onClick={() => onThinkingEnabledChange(!thinkingEnabled)}
                  >
                    <span>Thinking</span>
                    <span
                      className={cn(
                        "relative h-4 w-7 rounded-full bg-muted transition-colors",
                        thinkingEnabled && "bg-primary",
                      )}
                    >
                      <span
                        className={cn(
                          "absolute left-0.5 top-0.5 h-3 w-3 rounded-full bg-background shadow-sm transition-transform",
                          thinkingEnabled && "translate-x-3",
                        )}
                      />
                    </span>
                  </button>
                </>
              ) : null}
            </PromptInputTools>
            {busy && onStop ? (
              <PromptInputSubmit
                type="button"
                status={uiStatus}
                aria-label="Stop generating"
                onClick={onStop}
              />
            ) : (
              <PromptInputSubmit status={uiStatus} disabled={!input.trim() || busy || !canSend} />
            )}
          </PromptInputToolbar>
        </PromptInput>
      </div>
    </div>
  );
}
