import { Skeleton } from "@/components/ui/skeleton";

/**
 * Placeholder shown while a chat's messages are fetched: a right-aligned user
 * bubble followed by a block of assistant lines, so the shape of a conversation
 * is there before the content is.
 *
 * Layout-only on purpose — the caller already supplies the centred `max-w-3xl`
 * column and its padding, so this must not re-apply them or the skeleton would
 * sit in a narrower column than the messages that replace it, and the view would
 * jump on load.
 *
 * Colours come from the `Skeleton` default (`bg-muted`) rather than a fixed
 * grey, so it follows the light/dark theme.
 */
export function ChatMessagesSkeleton() {
  return (
    <div className="w-full" role="status" aria-label="Loading chat messages">
      {/* User message bubble */}
      <div className="flex justify-end">
        <Skeleton className="h-10 w-2/5 rounded-2xl" />
      </div>

      {/* Assistant response lines — ragged widths so it reads as prose. */}
      <div className="mt-8 space-y-3">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-[93%]" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-[86%]" />
        <Skeleton className="h-4 w-[95%]" />
        <Skeleton className="h-4 w-[78%]" />
        <Skeleton className="h-4 w-1/2" />
      </div>

      <span className="sr-only">Loading chat…</span>
    </div>
  );
}

export default ChatMessagesSkeleton;
