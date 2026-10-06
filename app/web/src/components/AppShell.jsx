import { useState } from "react";
import { SidebarInset, SidebarProvider } from "./ui/sidebar";
import { TooltipProvider } from "./ui/tooltip";
import { cn } from "../lib/utils";

export function AppShell({
  primaryNav,
  secondaryNav,
  header,
  children,
  contentOverflow = "auto",
  contentPadding = "pr-4 py-4",
}) {
  const [sidebarOpen, setSidebarOpen] = useState(true);

  return (
    <TooltipProvider>
      <SidebarProvider
        defaultOpen={true}
        open={sidebarOpen}
        onOpenChange={setSidebarOpen}
        style={{
          "--sidebar-width": "16rem",
          "--sidebar-width-icon": "4rem",
          "--sidebar": "var(--muted) / 0.5",
        }}
      >
        {primaryNav}
        <SidebarInset className="flex h-screen flex-col overflow-hidden bg-muted/50">
          <div className="shrink-0">{header}</div>
          <div className="min-h-0 flex-1 pr-2 pb-2 pt-1">
            <div className="flex h-full overflow-hidden rounded-xl border border-border/50 bg-background shadow-sm">
              {secondaryNav}
              {/* `data-app-content` marks the page body as distinct from the
                  app chrome around it. */}
              <main
                data-app-content=""
                className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
              >
                <div
                  className={cn(
                    "min-h-0 flex-1",
                    contentPadding,
                    contentOverflow === "hidden" ? "overflow-hidden" : "overflow-auto",
                  )}
                >
                  {children}
                </div>
              </main>
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  );
}
