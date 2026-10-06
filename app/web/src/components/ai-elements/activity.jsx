import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One collapsible container for everything the assistant did before answering —
 * every reasoning block and tool call, in order.
 *
 * Two reasons this is a single wrapper rather than one box per reasoning block:
 * a response alternates reason → tool → reason → tool, so per-block containers
 * stack up and push the answer off screen; and the header doubles as a live
 * status line, which only works if there is exactly one of them.
 *
 * The header always renders, whatever the step count. While streaming it stays
 * open and follows the newest item; once the run settles it collapses to the
 * header alone, which the reader can reopen.
 */
export function Activity({ children, label, streaming, itemCount, className }) {
  // A finished run mounts closed (no open-then-collapse flash on reload); a live one open.
  const [open, setOpen] = useState(Boolean(streaming));
  const [touched, setTouched] = useState(false);
  const scrollRef = useRef(null);

  // Collapse when the activity finishes — unless the reader has taken control.
  useEffect(() => {
    if (!touched) setOpen(streaming);
  }, [streaming, touched]);

  // Follow the newest item while it's still happening.
  //
  // Depends on `itemCount`, never on `children`: children is a new array on
  // every render, so using it here fired the scroll on every text delta and
  // made the box flicker for the whole answer.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !streaming || !open) return;
    el.scrollTop = el.scrollHeight;
  }, [itemCount, streaming, open]);

  return (
    <div className={cn("my-3", className)}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          setTouched(true);
          setOpen((v) => !v);
        }}
        className="group flex w-full items-center gap-1.5 text-left"
      >
        <span
          className={cn(
            "min-w-0 truncate text-[13px] text-muted-foreground/65",
            "group-hover:text-muted-foreground",
          )}
        >
          {label}
        </span>
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground/45 transition-transform",
            open && "rotate-90",
          )}
        />
      </button>

      <AnimatePresence initial={false}>
        {open ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            {/* A fixed-height window onto the steps, so opening them doesn't push the
                answer off screen. The 24px right padding keeps the scrollbar clear of
                the code panels: macOS overlay scrollbars take no layout space, so
                `scrollbar-gutter` alone doesn't reserve any. */}
            <div
              ref={scrollRef}
              data-activity-scroll=""
              className="mt-2 max-h-[24rem] overflow-y-auto overscroll-contain pr-6 [scrollbar-gutter:stable]"
            >
              {children}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
