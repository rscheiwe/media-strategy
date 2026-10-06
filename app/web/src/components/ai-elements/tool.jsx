import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  CheckCircle2,
  ChevronDownIcon,
  CircleDashed,
  ClockIcon,
  SquareFunction,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

const STATUS_LABELS = {
  "approval-requested": "Awaiting Approval",
  "approval-responded": "Responded",
  "input-available": "Running",
  "input-streaming": "Pending",
  "output-available": "Completed",
  "output-denied": "Denied",
  "output-error": "Error",
};

const STATUS_ICONS = {
  "approval-requested": <ClockIcon className="size-4 text-yellow-600" />,
  "approval-responded": <CheckCircle2 className="size-4 text-blue-600" />,
  "input-available": <CircleDashed className="size-4 animate-spin text-muted-foreground" />,
  "input-streaming": <CircleDashed className="size-4 animate-spin text-muted-foreground" />,
  "output-available": <CheckCircle2 className="size-4 text-emerald-600" />,
  "output-denied": <XCircleIcon className="size-4 text-orange-600" />,
  "output-error": <XCircleIcon className="size-4 text-red-600" />,
};

export function Tool({ className, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={cn("group my-4 w-full", className)} data-state={open ? "open" : "closed"}>
      {typeof children === "function" ? children({ open, setOpen }) : children}
    </div>
  );
}

export function ToolHeader({ className, title, type, state, toolName, open = false, onToggle }) {
  const derivedName =
    type === "dynamic-tool" ? toolName : (type || "").split("-").slice(1).join("-");
  const name = title ?? derivedName;

  return (
    <button
      type="button"
      className={cn(
        "group/header grid w-full grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-3 text-left",
        className,
      )}
      onClick={(e) => {
        if (onToggle) {
          onToggle();
          return;
        }
        const parent = e.currentTarget.closest("[data-state]");
        if (parent) {
          const next = parent.dataset.state === "open" ? "closed" : "open";
          parent.dataset.state = next;
        }
      }}
    >
      <span className="flex justify-center">
        {state && STATUS_ICONS[state] ? (
          STATUS_ICONS[state]
        ) : (
          <WrenchIcon className="size-4 text-muted-foreground" />
        )}
      </span>
      <span className="min-w-0 truncate text-[12px] font-medium text-muted-foreground">
        Used {name}
      </span>
      <ChevronDownIcon
        className={cn(
          "size-4 shrink-0 text-muted-foreground opacity-70 transition group-hover/header:opacity-100",
          open && "rotate-180",
        )}
      />
    </button>
  );
}

export function ToolContent({
  className,
  open = false,
  type,
  title,
  toolName,
  state,
  input,
  output,
  errorText,
}) {
  const [detailOpen, setDetailOpen] = useState(false);
  const derivedName =
    type === "dynamic-tool" ? toolName : (type || "").split("-").slice(1).join("-");
  const name = title ?? derivedName;
  const showingRequest = state === "input-streaming" || state === "input-available";
  const detailLabel = showingRequest ? "Request" : "Result";
  const hasInput = input !== undefined && input !== null && input !== "";
  const hasOutput = output !== undefined && output !== null && output !== "";

  function renderPayload(value, { error = false } = {}) {
    const hasPayload = value !== undefined && value !== null && value !== "";
    if (!hasPayload) {
      return <div className="text-[11px] text-muted-foreground">No payload.</div>;
    }
    return (
      <pre
        className={cn(
          "max-w-full whitespace-pre-wrap break-words rounded-lg bg-muted/35 p-3 font-mono text-[11px] leading-relaxed [overflow-wrap:anywhere]",
          error ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
      </pre>
    );
  }

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          key="tool-content"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
          className={cn("overflow-hidden", className)}
        >
          <div className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-3">
            <div className="flex justify-center">
              <div className="mt-1 min-h-full w-px bg-border" />
            </div>
            <div className="mt-3 space-y-3 text-popover-foreground">
              <div className="flex items-center gap-3">
                <span className="flex size-5 shrink-0 items-center justify-center rounded-sm border bg-background text-muted-foreground">
                  <SquareFunction className="size-3.5" />
                </span>
                <span className="min-w-0 truncate text-[12px] font-medium text-muted-foreground">
                  {name}
                </span>
                {state ? (
                  <span className="ml-auto shrink-0 text-[11px] text-muted-foreground/75">
                    {STATUS_LABELS[state]}
                  </span>
                ) : null}
              </div>
              <div className="pl-8">
                <button
                  type="button"
                  className="mb-2 inline-flex rounded-md bg-muted/45 px-2 py-0.5 font-mono text-[11px] text-muted-foreground transition hover:bg-muted hover:text-foreground"
                  onClick={() => setDetailOpen((value) => !value)}
                >
                  {detailLabel}
                </button>
                <AnimatePresence initial={false}>
                  {detailOpen ? (
                    <motion.div
                      key="tool-detail"
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
                      className="overflow-hidden"
                    >
                      <div className="max-h-80 overflow-y-auto overflow-x-hidden rounded-xl border bg-background p-3">
                        {showingRequest ? (
                          <div className="space-y-2">
                            <div className="text-[12px] font-semibold text-foreground">Request</div>
                            {renderPayload(input)}
                          </div>
                        ) : (
                          <div className="space-y-4">
                            <div className="space-y-2">
                              <div className="text-[12px] font-semibold text-foreground">
                                Request
                              </div>
                              {hasInput ? (
                                renderPayload(input)
                              ) : (
                                <div className="text-[11px] text-muted-foreground">
                                  No request payload.
                                </div>
                              )}
                            </div>
                            <div className="space-y-2">
                              <div className="text-[12px] font-semibold text-foreground">
                                {errorText ? "Error" : "Response"}
                              </div>
                              {errorText ? (
                                renderPayload(errorText, { error: true })
                              ) : hasOutput ? (
                                renderPayload(output)
                              ) : (
                                <div className="text-[11px] text-muted-foreground">
                                  No response payload.
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </div>
            </div>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

export function ToolInput({ className, input }) {
  return (
    <div className={cn("space-y-2 overflow-hidden", className)}>
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Parameters
      </h4>
      <pre className="max-h-52 max-w-full overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
        {JSON.stringify(input, null, 2)}
      </pre>
    </div>
  );
}

export function ToolOutput({ className, output, errorText }) {
  if (!output && !errorText) return null;

  const displayValue =
    typeof output === "object" ? JSON.stringify(output, null, 2) : String(output ?? "");

  return (
    <div className={cn("space-y-2", className)}>
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {errorText ? "Error" : "Result"}
      </h4>
      <pre
        className={cn(
          "max-w-full overflow-y-auto overflow-x-hidden whitespace-pre-wrap break-words rounded-md p-2 font-mono text-[11px] leading-relaxed [overflow-wrap:anywhere]",
          errorText
            ? "bg-destructive/10 text-destructive"
            : "max-h-72 bg-muted/40 text-muted-foreground",
        )}
      >
        {errorText || displayValue}
      </pre>
    </div>
  );
}

export function toToolState(value) {
  const valid = [
    "approval-requested",
    "approval-responded",
    "input-available",
    "input-streaming",
    "output-available",
    "output-error",
    "output-denied",
  ];
  return valid.includes(value) ? value : "input-available";
}
