import { CheckCircle2, CircleDashed, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/** The DAG's stage rows (data-workflow-stage), in order, with their summaries. */
export function WorkflowStages({ stages }) {
  if (!stages?.length) return null;
  return (
    <ol aria-label="Workflow stages" className="space-y-1">
      {stages.map((stage) => {
        const Icon =
          stage.status === "error"
            ? XCircle
            : stage.status === "running"
              ? CircleDashed
              : CheckCircle2;
        return (
          <li key={`${stage.run_id}-${stage.stage}`} className="flex items-start gap-2 text-[12px]">
            <Icon
              className={cn(
                "mt-0.5 size-3.5 shrink-0",
                stage.status === "running" && "animate-spin text-muted-foreground",
                stage.status === "done" && "text-emerald-600 dark:text-emerald-400",
                stage.status === "error" && "text-destructive",
              )}
            />
            <span className="font-medium text-foreground">{stage.label}</span>
            {stage.summary ? (
              <span className="text-muted-foreground">· {stage.summary}</span>
            ) : null}
            {typeof stage.ms === "number" && stage.status === "done" ? (
              <span className="ml-auto font-mono text-[10.5px] text-muted-foreground/70">
                {stage.ms} ms
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
