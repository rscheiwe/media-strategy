import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import { messageToText } from "./chatUtils";
import { Hint } from "@/components/ui/hint";

// Below this many turns the rail is noise — a single-turn chat needs no map.
const MIN_TURNS = 2;
const LABEL_MAX = 90;

/**
 * A vertical index of the conversation, pinned to the right edge: one tick per
 * user turn, in order. Hovering expands the ticks into their text so you can see
 * what each turn asked; clicking scrolls to it.
 *
 * The ticks are derived from the same `messages` the transcript renders, and
 * scrolling targets the `data-msg-id` anchors already on each message wrapper —
 * so there is no second source of truth about what a turn is or where it lives.
 */
export function ChatMessageRail({ messages, containerRef, className }) {
  const [expanded, setExpanded] = useState(false);
  const [activeId, setActiveId] = useState(null);
  const listRef = useRef(null);

  const turns = useMemo(() => {
    return (messages || [])
      .filter((message) => message.role !== "assistant" && !message.pending)
      .map((message) => {
        const text = (messageToText(message) || "").replace(/\s+/g, " ").trim();
        return {
          id: message.id,
          label: text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX)}…` : text,
        };
      })
      .filter((turn) => turn.label);
  }, [messages]);

  const scrollToTurn = useCallback(
    (id) => {
      const container = containerRef?.current;
      const target = container?.querySelector(`[data-msg-id="${CSS.escape(id)}"]`);
      if (!container || !target) return;
      // Offset by a little so the turn lands below the top edge rather than
      // flush against it.
      const top = target.offsetTop - container.offsetTop - 12;
      container.scrollTo({ top, behavior: "smooth" });
      setActiveId(id);
    },
    [containerRef],
  );

  // Track which turn the reader is currently in, so the rail shows position
  // rather than just structure. Driven off the container's own scroll rather
  // than IntersectionObserver — the anchors are plain offsets in one scroller,
  // and this stays correct while messages stream in and resize.
  useEffect(() => {
    const container = containerRef?.current;
    if (!container || turns.length < MIN_TURNS) return undefined;

    let frame = 0;
    const measure = () => {
      frame = 0;
      const maxScroll = container.scrollHeight - container.clientHeight;
      const progress = maxScroll > 0 ? Math.min(1, container.scrollTop / maxScroll) : 1;
      // The marker eases down the viewport as you approach the end. With a fixed
      // marker near the top, the final turns can never reach it — they sit below
      // it even at maximum scroll (the trailing spacer makes this worse), so the
      // last tick would never light up no matter how far you scrolled. Sliding
      // it from a quarter down to near the bottom keeps every turn reachable.
      const markerRatio = 0.25 + 0.65 * progress;
      const marker = container.scrollTop + container.clientHeight * markerRatio;
      let current = null;
      for (const turn of turns) {
        const node = container.querySelector(`[data-msg-id="${CSS.escape(turn.id)}"]`);
        if (!node) continue;
        if (node.offsetTop - container.offsetTop <= marker) current = turn.id;
        else break;
      }
      // At the very end, the last turn is active by definition — there is
      // nothing further to scroll to.
      if (progress >= 0.999) current = turns[turns.length - 1]?.id ?? current;
      setActiveId(current ?? turns[0]?.id ?? null);
    };

    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(measure);
    };

    measure();
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [containerRef, turns]);

  // Keep the active row visible once the panel is open.
  useEffect(() => {
    if (!expanded || !activeId) return;
    listRef.current
      ?.querySelector(`[data-rail-id="${CSS.escape(activeId)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [expanded, activeId]);

  if (turns.length < MIN_TURNS) return null;

  return (
    <div
      className={cn(
        "pointer-events-none absolute right-0 top-1/2 z-20 -translate-y-1/2",
        className,
      )}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {/* Collapsed: tick marks. Hidden from assistive tech — the expanded list
          below is the accessible control, and duplicating it would announce
          every turn twice. */}
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-auto flex cursor-pointer flex-col items-end gap-[5px] py-3 pl-8 pr-4 transition-opacity duration-150",
          expanded ? "pointer-events-none opacity-0" : "opacity-100",
        )}
      >
        {turns.map((turn) => (
          <span
            key={turn.id}
            className={cn(
              "h-[2px] rounded-full transition-all duration-150",
              turn.id === activeId ? "w-6 bg-foreground/80" : "w-4 bg-foreground/40",
            )}
          />
        ))}
      </div>

      {/* Expanded: the same turns, readable and clickable. */}
      <div
        ref={listRef}
        className={cn(
          "absolute right-3 top-1/2 max-h-[60vh] w-72 -translate-y-1/2 overflow-y-auto rounded-lg border bg-popover p-1 shadow-md transition-all duration-150",
          expanded
            ? "pointer-events-auto translate-x-0 opacity-100"
            : "pointer-events-none translate-x-1 opacity-0",
        )}
        aria-label="Jump to a message"
      >
        {turns.map((turn) => (
          <Hint key={turn.id} label={turn.label} side="left">
            <button
              type="button"
              data-rail-id={turn.id}
              onClick={() => scrollToTurn(turn.id)}
              className={cn(
                "block w-full truncate rounded px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent",
                turn.id === activeId ? "bg-accent/60 text-foreground" : "text-muted-foreground",
              )}
            >
              {turn.label}
            </button>
          </Hint>
        ))}
      </div>
    </div>
  );
}

export default ChatMessageRail;
