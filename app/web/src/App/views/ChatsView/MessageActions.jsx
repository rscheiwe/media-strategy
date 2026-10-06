import { memo, useState } from "react";
import { Check, Copy, ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { messageToText } from "./chatUtils";
import { Hint } from "@/components/ui/hint";

/**
 * Copy + thumbs up/down under a finished assistant message.
 *
 * `rating` is the saved value (1, -1, or null). Clicking the active thumb again
 * clears it. Feedback needs the message to exist server-side, which is true for
 * history and for a just-streamed turn (the server stamps its row id on the
 * stream's `start` event); the parent hides feedback until a chat id exists.
 */
export const MessageActions = memo(function MessageActions({
  message,
  rating = null,
  canRate = true,
  onRate,
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(messageToText(message));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Clipboard unavailable");
    }
  }

  const iconButton = "h-7 w-7 text-muted-foreground hover:text-foreground";
  return (
    <div className="flex items-center gap-0.5" data-message-actions="">
      <Hint label={copied ? "Copied" : "Copy"}>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={iconButton}
          onClick={() => void handleCopy()}
          aria-label={copied ? "Copied" : "Copy message"}
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </Button>
      </Hint>
      {canRate ? (
        <>
          <Hint label={"Good response"}>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={cn(iconButton, rating === 1 && "text-foreground")}
              onClick={() => onRate?.(message, rating === 1 ? null : 1)}
              aria-label="Good response"
              aria-pressed={rating === 1}
            >
              <ThumbsUp className="h-3.5 w-3.5" fill={rating === 1 ? "currentColor" : "none"} />
            </Button>
          </Hint>
          <Hint label={"Bad response"}>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className={cn(iconButton, rating === -1 && "text-foreground")}
              onClick={() => onRate?.(message, rating === -1 ? null : -1)}
              aria-label="Bad response"
              aria-pressed={rating === -1}
            >
              <ThumbsDown className="h-3.5 w-3.5" fill={rating === -1 ? "currentColor" : "none"} />
            </Button>
          </Hint>
        </>
      ) : null}
    </div>
  );
});
