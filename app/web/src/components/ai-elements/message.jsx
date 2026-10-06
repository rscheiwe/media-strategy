import { cn } from "@/lib/utils";

export function Message({ className, from, ...props }) {
  return (
    <div
      className={cn(
        "group flex w-full gap-2 py-2",
        from === "user"
          ? "is-user justify-end [&>div]:max-w-[84%]"
          : "is-assistant justify-start [&>div]:max-w-[100%]",
        className,
      )}
      {...props}
    />
  );
}

export function MessageContent({ children, className, ...props }) {
  return (
    <div
      className={cn(
        "rounded-lg px-3 text-sm",
        "group-[.is-user]:bg-primary group-[.is-user]:py-2 group-[.is-user]:text-primary-foreground",
        "group-[.is-assistant]:bg-transparent group-[.is-assistant]:pb-2 group-[.is-assistant]:pt-3 group-[.is-assistant]:text-foreground",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
