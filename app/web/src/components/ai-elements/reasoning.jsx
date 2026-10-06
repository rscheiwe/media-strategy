import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, ChevronDown, CircleDashed } from "lucide-react";

import { cn } from "@/lib/utils";
import { Response } from "@/components/ai-elements/response";

function reasoningLabel(state) {
  if (state === "done") return "Reasoned";
  if (state === "error") return "Reasoning stopped";
  return "Reasoning";
}

export function Reasoning({ className, text, state = "streaming", defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen ?? state !== "done");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setOpen(state !== "done");
    setShowAll(false);
  }, [state]);

  const isComplete = state === "done";
  const hasText = Boolean(String(text || "").trim());
  const shouldLimit = isComplete && !showAll;

  if (!hasText && isComplete) return null;

  return (
    <div className={cn("my-4", className)} data-state={open ? "open" : "closed"}>
      <button
        type="button"
        className="group grid w-full grid-cols-[1.25rem_minmax(0,1fr)_1rem] items-center gap-3 text-left text-[12px]"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="flex justify-center">
          {isComplete ? (
            <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
          ) : (
            <CircleDashed className="size-4 shrink-0 animate-spin text-muted-foreground" />
          )}
        </span>
        <span className="min-w-0 font-medium text-muted-foreground">{reasoningLabel(state)}</span>
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground opacity-70 transition group-hover:opacity-100",
            open && "rotate-180",
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            key="reasoning-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-3">
              <div className="flex justify-center">
                <div className="mt-1 min-h-full w-px bg-border" />
              </div>
              <div className="mt-2">
                {hasText ? (
                  <div className={cn("relative overflow-hidden", shouldLimit && "max-h-[6.25rem]")}>
                    <Response className="text-[12px] text-muted-foreground/90">{text}</Response>
                    {shouldLimit ? (
                      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-b from-transparent to-background" />
                    ) : null}
                  </div>
                ) : (
                  <div className="h-4 w-24 animate-pulse rounded bg-muted" />
                )}
              </div>
            </div>
            {hasText && isComplete ? (
              <button
                type="button"
                className="mt-2 text-[12px] font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? "Show less" : "Show more"}
              </button>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
