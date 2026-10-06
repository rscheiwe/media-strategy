import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

export function SidebarInput({ className, ...props }) {
  return (
    <Input
      data-slot="sidebar-input"
      data-sidebar="input"
      className={cn("h-8 w-full bg-background shadow-none", className)}
      {...props}
    />
  );
}
