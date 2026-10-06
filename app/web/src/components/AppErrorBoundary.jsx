import React from "react";
import { AlertTriangle, Home, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Skeleton } from "./ui/skeleton";
import { AppShell } from "./AppShell";
import { PrimaryNav } from "./PrimaryNav";
import { MainHeader } from "./MainHeader";

function formatErrorMessage(error) {
  if (!error) return "Unexpected application error";
  if (typeof error === "string") return error;
  return error.message || "Unexpected application error";
}

export class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorMessage: "" };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      errorMessage: formatErrorMessage(error),
    };
  }

  componentDidCatch(error, errorInfo) {
    toast.error("Application error", {
      description: formatErrorMessage(error),
    });

    if (typeof this.props.onError === "function") {
      this.props.onError(error, errorInfo);
    }
  }

  handleReload = () => {
    window.location.reload();
  };

  handleHome = () => {
    window.location.assign("/");
  };

  render() {
    if (this.state.hasError) {
      return (
        <AppShell
          primaryNav={<PrimaryNav disabled />}
          header={<MainHeader title="Application Error" theme="light" onToggleTheme={() => {}} />}
          contentOverflow="hidden"
          contentPadding="p-0"
        >
          <div className="relative flex h-full min-h-0 flex-col overflow-hidden bg-[linear-gradient(180deg,hsl(var(--background)),hsl(var(--muted)/0.08))]">
            <div className="pointer-events-none absolute inset-0 p-6">
              <div className="mx-auto flex h-full max-w-3xl flex-col gap-4">
                <Skeleton className="ml-auto h-10 w-2/5 rounded-2xl bg-muted/50" />
                <Skeleton className="h-4 w-4/5 bg-muted/45" />
                <Skeleton className="h-4 w-3/5 bg-muted/45" />
                <Skeleton className="h-4 w-2/3 bg-muted/45" />
                <Skeleton className="mt-auto h-24 w-full rounded-2xl bg-muted/45" />
              </div>
            </div>

            <div className="relative z-10 flex h-full items-center justify-center px-6 py-10">
              <div className="w-full max-w-xl rounded-3xl border border-border/70 bg-background/96 p-8 shadow-[0_24px_80px_rgba(15,23,42,0.12)] backdrop-blur">
                <div className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-destructive/20 bg-destructive/10 text-destructive">
                  <AlertTriangle className="h-5 w-5" />
                </div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Application Error
                </p>
                <h1 className="mt-3 text-[2rem] font-semibold tracking-tight text-foreground">
                  This screen crashed.
                </h1>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">
                  The app shell is still available, but the current screen hit an unexpected runtime
                  error. Reload the page to recover.
                </p>

                <div className="mt-6 rounded-2xl border border-border bg-muted/25 p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Error
                  </div>
                  <div className="mt-2 font-mono text-sm text-foreground">
                    {this.state.errorMessage || "Unexpected application error"}
                  </div>
                </div>

                <div className="mt-6 flex flex-wrap gap-3">
                  <Button type="button" onClick={this.handleReload} className="gap-2">
                    <RotateCcw className="h-4 w-4" />
                    Reload app
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={this.handleHome}
                    className="gap-2"
                  >
                    <Home className="h-4 w-4" />
                    Start a new chat
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </AppShell>
      );
    }

    return this.props.children;
  }
}
