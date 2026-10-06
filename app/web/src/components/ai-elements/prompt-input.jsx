import { Children, forwardRef } from "react";
import { SendIcon, SquareIcon, XIcon } from "lucide-react";
import { LoaderIcon } from "@/components/icons/LoaderIcon";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function PromptInput({ className, ...props }) {
  return (
    <form
      className={cn("w-full overflow-hidden rounded-xl border bg-background shadow-sm", className)}
      {...props}
    />
  );
}

export const PromptInputTextarea = forwardRef(function PromptInputTextarea(
  {
    className,
    placeholder = "Ask anything...",
    minHeight = 48,
    maxHeight = 164,
    // false: Enter inserts a newline and Cmd/Ctrl+Enter sends (user_settings.send_on_enter).
    submitOnEnter = true,
    onChange,
    ...props
  },
  ref,
) {
  return (
    <Textarea
      ref={ref}
      className={cn(
        "w-full resize-none rounded-none border-0 bg-transparent px-3 py-2 text-sm shadow-none focus-visible:ring-0",
        className,
      )}
      onChange={(event) => {
        const element = event.currentTarget;
        element.style.height = "auto";
        element.style.height = `${Math.min(maxHeight, Math.max(minHeight, element.scrollHeight))}px`;
        onChange?.(event);
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
        if (submitOnEnter || event.metaKey || event.ctrlKey) {
          event.preventDefault();
          event.currentTarget.form?.requestSubmit();
        }
      }}
      placeholder={placeholder}
      rows={1}
      {...props}
    />
  );
});

export function PromptInputToolbar({ className, ...props }) {
  return (
    <div className={cn("flex items-center justify-end border-t px-1 py-1", className)} {...props} />
  );
}

export function PromptInputTools({ className, ...props }) {
  return <div className={cn("flex items-center gap-1", className)} {...props} />;
}

export function PromptInputButton({ variant = "ghost", className, size, ...props }) {
  const resolvedSize = size ?? (Children.count(props.children) > 1 ? "default" : "icon");
  return (
    <Button
      className={cn("gap-1.5 rounded-lg", className)}
      size={resolvedSize}
      type="button"
      variant={variant}
      {...props}
    />
  );
}

export function PromptInputSubmit({
  className,
  variant = "default",
  size = "icon",
  status = "idle",
  children,
  ...props
}) {
  const icon =
    status === "submitted" ? (
      <LoaderIcon className="h-4 w-4" idleColor="#e5e7eb" activeColor="#7b828c" />
    ) : status === "streaming" ? (
      <SquareIcon className="h-4 w-4" />
    ) : status === "error" ? (
      <XIcon className="h-4 w-4" />
    ) : (
      <SendIcon className="h-4 w-4" />
    );

  return (
    <Button
      className={cn("gap-1.5 rounded-lg", className)}
      size={size}
      type="submit"
      variant={variant}
      {...props}
    >
      {children ?? icon}
    </Button>
  );
}
