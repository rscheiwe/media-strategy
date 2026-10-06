import { describeChatError, isRequestRefused } from "@/lib/chat-errors";
import { cn } from "@/lib/utils";

/**
 * A failed chat turn, explained.
 *
 * Every chat surface here used to render `error.message` directly, which for a
 * non-2xx response is the raw API body — so a refused request showed the reader
 * `{"detail": ...}` where an answer should be. It reads as
 * though the assistant replied with a JSON fragment, and says nothing about what
 * to do.
 *
 * Shared by every chat surface so they all explain a failure the same way. A refused
 * request is the one case with a definite remedy (reload), so it gets an action; everything else is stated plainly,
 * since the composer still holds the message and retrying is already possible.
 */
export function ChatErrorNotice({ error, className }) {
  const message = describeChatError(error);
  if (!message) return null;

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive",
        className,
      )}
    >
      <span className="min-w-0">{message}</span>
      {isRequestRefused(error) ? (
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="shrink-0 rounded border border-destructive/40 px-2 py-0.5 text-xs font-medium transition-colors hover:bg-destructive/10"
        >
          Reload
        </button>
      ) : null}
    </div>
  );
}
