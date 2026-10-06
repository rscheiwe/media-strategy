import { cn } from "../../lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip";

/**
 * A real tooltip (Radix) for hover and keyboard focus — use this instead of a native `title=`.
 * Icon-only buttons still keep their `aria-label`; the tooltip is the visible twin of it.
 *
 * `wrap` puts the trigger on a span: needed for disabled buttons (they don't fire pointer events)
 * and components that don't forward refs (e.g. Badge).
 */
export function Hint({ label, side = "top", align = "center", wrap = false, className, children }) {
  if (label === null || label === undefined || label === "") return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {wrap ? <span className={cn("inline-flex", className)}>{children}</span> : children}
      </TooltipTrigger>
      <TooltipContent side={side} align={align} className="max-w-xs whitespace-pre-line">
        {label}
      </TooltipContent>
    </Tooltip>
  );
}
