import { useCallback } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Suggestions({ className, children, ...props }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)} {...props}>
      {children}
    </div>
  );
}

export function Suggestion({
  suggestion,
  onClick,
  className,
  variant = "outline",
  size = "sm",
  children,
  ...props
}) {
  const handleClick = useCallback(() => {
    onClick?.(suggestion);
  }, [onClick, suggestion]);

  return (
    <Button
      // `font-normal` overrides the Button base's `font-medium`: a suggestion is
      // a prompt to read, not an action to emphasise, and at this size the extra
      // weight made a stack of them read as headings.
      className={cn("cursor-pointer rounded-full px-4 font-normal", className)}
      onClick={handleClick}
      size={size}
      type="button"
      variant={variant}
      {...props}
    >
      {children || suggestion}
    </Button>
  );
}
