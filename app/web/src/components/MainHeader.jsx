import { MessageSquare, Moon, Sun } from "lucide-react";

import { Button } from "./ui/button";
import { SidebarTrigger } from "./ui/sidebar";
import { Hint } from "@/components/ui/hint";

/** Top bar: sidebar toggle, the active chat's title, and the theme switch. */
export function MainHeader({ title, statusContent = null, theme, onToggleTheme }) {
  const nextTheme = theme === "dark" ? "light" : "dark";
  return (
    <div className="pr-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <SidebarTrigger className="h-8 w-8 rounded-md border border-border bg-background text-foreground hover:bg-accent" />
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden text-sm text-foreground">
          <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{title || "New chat"}</span>
        </div>
        {statusContent ? (
          <div className="ml-auto">{statusContent}</div>
        ) : (
          <div className="ml-auto" />
        )}
        <Hint label={`Switch to ${nextTheme} mode`} side="bottom">
          <Button
            type="button"
            className="h-8 w-8 rounded-md border border-border bg-background p-0 text-foreground hover:bg-accent"
            onClick={onToggleTheme}
            aria-label={`Switch to ${nextTheme} mode`}
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </Hint>
      </div>
    </div>
  );
}
