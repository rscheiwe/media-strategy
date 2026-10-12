import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Check, ChevronRight, Copy, TriangleAlert } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  KPI_LABELS,
  OUTCOME_LABELS,
  REASON_TEXT,
  count,
  deliveryRows,
  money,
  pct,
  recommendationMarkdown,
  tradeoffText,
} from "@/lib/workflow";

/**
 * The structured recommendation (brief requirements 6 and 9): interpreted requirements, the
 * recommended product or bundle with forecasted impressions, the rationale, rejected
 * higher-ranked alternatives with reasons, tradeoffs, the benchmarks and the policy used.
 * Renders a loading state until the artifact's payload arrives.
 */
export function RecommendationCard({ artifact, compact = false, onOpen }) {
  const reveal = useReveal(artifact);
  // The plan is computed and shown at once; the two model-written sections (9.5, 9.6) arrive
  // ~1-2s later with the "complete" snapshot. Until then they show that they're being written,
  // rather than template text that would silently change.
  const writing = artifact?.status === "streaming";
  const rec = artifact?.payload;
  if (!rec) {
    return (
      <section
        aria-label="Recommendation loading"
        className="my-3 space-y-2 rounded-xl border border-border bg-card p-4"
      >
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-2/3" />
      </section>
    );
  }
  const req = rec.requirements || {};
  const kpiLabel = KPI_LABELS[req.kpi] || "KPI";
  const names = Object.fromEntries(
    (rec.candidates || []).map((c) => [c.product_id, c.product_name]),
  );
  const scale = Object.fromEntries((rec.scale || []).map((s) => [s.product_id, s]));
  const plan = rec.plan || {};
  const rationale = rec.rationale || {};
  // 9.6: the explained rejected top alternatives (older artifacts: the templated reasons).
  const rejectedNotes = rationale.rejected?.length
    ? rationale.rejected.map((n) => ({
        key: n.product_id,
        name: n.product_name,
        text: n.explanation,
      }))
    : (rec.alternatives || [])
        .filter((a) => a.kind === "rejected_leader")
        .map((alt) => ({
          key: alt.product_ids.join("+"),
          name: alt.product_ids.map((id) => names[id]).join(" + "),
          text: `(${kpiLabel} ${pct(alt.kpi_value)}): ${alt.reasons.map((r) => REASON_TEXT[r] || r).join("; ")}`,
        }));

  return (
    <section
      aria-label="Recommendation"
      className="my-3 overflow-hidden rounded-xl border border-border bg-card shadow-sm"
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Recommendation
          </p>
          <h3 className="mt-0.5 truncate text-sm font-semibold">{artifact.title}</h3>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <CopyMarkdown artifact={artifact} disabled={writing} />
          {onOpen ? (
            <button
              type="button"
              onClick={onOpen}
              className="text-[12px] font-medium text-primary hover:underline"
            >
              View report
            </button>
          ) : null}
        </div>
      </header>

      {/* The final answer, in the order of brief requirement 9 (9.1-9.6). */}
      <motion.div
        className="space-y-4 px-4 py-3"
        initial={reveal ? "hidden" : "show"}
        animate="show"
        variants={{ show: { transition: { staggerChildren: 0.08 } } }}
      >
        <Section title="Client requirements">
          <Requirements req={req} kpiLabel={kpiLabel} />
        </Section>

        <Section title={plan.kind === "bundle" ? "Recommended bundle" : "Recommended product"}>
          {plan.kind === "none" ? (
            <p className="flex items-center gap-2 text-sm text-destructive">
              <TriangleAlert className="size-4" /> No viable plan for this brief.
            </p>
          ) : (
            <PlanTable plan={plan} kpiLabel={kpiLabel} scale={scale} />
          )}
        </Section>

        {!compact ? <Delivery rec={rec} kpiLabel={kpiLabel} /> : null}

        {rationale.best_fit || rationale.text ? (
          <Section title="Why this is the best fit">
            {writing ? (
              <Writing lines={2} />
            ) : (
              <p className="text-sm leading-relaxed">{rationale.best_fit || rationale.text}</p>
            )}
            {rationale.best_fit && rationale.tradeoff ? (
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {rationale.tradeoff}
              </p>
            ) : null}
          </Section>
        ) : null}

        {!compact && plan.kind !== "none" ? (
          <Section title="Rejected top alternatives">
            {writing && rejectedNotes.length ? (
              <Writing lines={rejectedNotes.length} />
            ) : rejectedNotes.length ? (
              <ul className="space-y-1.5">
                {rejectedNotes.map((note) => (
                  <li key={note.key} className="text-[13px]">
                    {/* The explanation usually opens with the name; only label it when it doesn't. */}
                    {note.text.startsWith(note.name) ? null : (
                      <span className="font-medium">{note.name} </span>
                    )}
                    <span
                      className={note.text.startsWith(note.name) ? "" : "text-muted-foreground"}
                    >
                      {note.text}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted-foreground">
                None: nothing ranks above the pick on {kpiLabel}.
              </p>
            )}
          </Section>
        ) : null}

        {!compact && (rec.tradeoffs || []).length ? (
          <Section title="Tradeoffs">
            <ul className="list-disc space-y-1 pl-5 text-[13px]">
              {rec.tradeoffs.map((t, i) => (
                <li key={`${t.code}-${i}`}>{tradeoffText(t, names)}</li>
              ))}
            </ul>
          </Section>
        ) : null}

        {!compact ? (
          <motion.div variants={ITEM}>
            <Benchmarks rec={rec} kpi={req.kpi} kpiLabel={kpiLabel} scale={scale} />
          </motion.div>
        ) : null}

        {rec.policy?.note ? (
          <motion.p variants={ITEM} className="text-[11px] text-muted-foreground">
            Inventory policy: {rec.policy.note}.
          </motion.p>
        ) : null}
      </motion.div>
    </section>
  );
}

// One section of the card; reveals in turn with its siblings (see useReveal).
const ITEM = {
  hidden: { opacity: 0, y: 6 },
  show: { opacity: 1, y: 0, transition: { duration: 0.22, ease: [0.22, 1, 0.36, 1] } },
};

function Section({ title, children }) {
  return (
    <motion.div variants={ITEM}>
      <h4 className="mb-1.5 text-[12px] font-semibold text-muted-foreground">{title}</h4>
      {children}
    </motion.div>
  );
}

/**
 * A card that just arrived reveals section by section (~0.6s): the answer lands in ~1.5s and a
 * whole card appearing at once is jarring. Reloaded or older cards, and reduced motion, render
 * as is. Decided once, on mount.
 */
function useReveal(artifact) {
  const reduced = useReducedMotion();
  const [fresh] = useState(() => {
    const created = Number(artifact?.createdAt || 0);
    return Boolean(created) && Date.now() - created < 20_000;
  });
  return fresh && !reduced;
}

function Requirements({ req, kpiLabel }) {
  const chips = [
    req.client_name && ["Client", req.client_name],
    ["Vertical", req.vertical],
    ["KPI", kpiLabel],
    ["Geo", req.geo],
    ["Budget", money(req.budget)],
    req.impression_goal && ["Goal", `${count(req.impression_goal)} imps`],
  ].filter(Boolean);
  return (
    <ul aria-label="Requirements" className="flex flex-wrap gap-1.5">
      {chips.map(([label, value]) => (
        <li
          key={label}
          className="rounded-full border border-border bg-muted/40 px-2 py-0.5 text-[11.5px]"
        >
          <span className="text-muted-foreground">{label}:</span> {value}
        </li>
      ))}
    </ul>
  );
}

function PlanTable({ plan, kpiLabel, scale }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead className="border-b border-border text-left text-[11.5px] text-muted-foreground">
          <tr>
            <th className="py-1.5 pr-3 font-medium">Product</th>
            <th className="py-1.5 pr-3 text-right font-medium">Spend</th>
            <th className="py-1.5 pr-3 text-right font-medium">Forecasted impressions</th>
            <th className="py-1.5 pr-3 text-right font-medium">{kpiLabel}</th>
            <th className="py-1.5 text-right font-medium">Forecast confidence</th>
          </tr>
        </thead>
        <tbody>
          {plan.lines.map((line) => (
            <tr key={line.product_id} className="border-b border-border/60 last:border-0">
              <td className="py-1.5 pr-3 font-medium">{line.product_name}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{money(line.spend)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{count(line.impressions)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{pct(line.kpi_value)}</td>
              <td
                className={cn(
                  "py-1.5 text-right tabular-nums",
                  line.low_confidence && "text-amber-700 dark:text-amber-400",
                )}
              >
                {Number(scale[line.product_id]?.confidence ?? line.confidence).toFixed(2)}
                {line.low_confidence ? " (low)" : ""}
              </td>
            </tr>
          ))}
        </tbody>
        {plan.lines.length > 1 ? (
          <tfoot className="text-[12px] text-muted-foreground">
            <tr>
              <td className="pt-1.5 pr-3">
                Total{plan.unspent ? ` (${money(plan.unspent)} unspent)` : ""}
              </td>
              <td className="pt-1.5 pr-3 text-right tabular-nums">{money(plan.total_spend)}</td>
              <td className="pt-1.5 pr-3 text-right tabular-nums">
                {count(plan.total_impressions)}
              </td>
              <td className="pt-1.5 pr-3 text-right tabular-nums">{pct(plan.blended_kpi)}</td>
              <td />
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

/**
 * What the budget delivers: the pick and the bundle not chosen, by expected outcomes and cost per
 * outcome. The KPI rate decides the pick (brief 8.6); this shows what that choice means in clicks
 * or viewable impressions per dollar. Hidden for no plan and for older payloads.
 */
function Delivery({ rec, kpiLabel }) {
  const rows = deliveryRows(rec);
  if (!rows.length) return null;
  const labels = OUTCOME_LABELS[rec.requirements?.kpi] || OUTCOME_LABELS.ctr;
  return (
    <motion.div
      variants={ITEM}
      aria-label="What the budget delivers"
      role="group"
      className="rounded-lg border border-yellow-200 bg-yellow-50 p-3 dark:border-yellow-900/50 dark:bg-yellow-950/30"
    >
      <h4 className="mb-1.5 text-[12px] font-semibold text-yellow-900 dark:text-yellow-200">
        What {money(rec.requirements?.budget)} delivers
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead className="border-b border-yellow-200 text-left text-[11.5px] text-muted-foreground dark:border-yellow-900/50">
            <tr>
              <th className="py-1.5 pr-3 font-medium">Option</th>
              <th className="py-1.5 pr-3 text-right font-medium">{kpiLabel}</th>
              <th className="py-1.5 pr-3 text-right font-medium">{labels.outcomes}</th>
              <th className="py-1.5 text-right font-medium">{labels.cost}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-yellow-200/60 last:border-0 dark:border-yellow-900/40">
                <td className="py-1.5 pr-3">
                  <span className="font-medium">{r.label}</span>{" "}
                  <span className="text-muted-foreground">({r.note})</span>
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{pct(r.rate)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{count(r.outcomes)}</td>
                <td className="py-1.5 text-right tabular-nums">{money(r.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1.5 text-[11px] text-muted-foreground">
        Ranked by {kpiLabel}, as the brief asks; volume and cost are shown for comparison and
        don&apos;t change the pick.
      </p>
    </motion.div>
  );
}

function Benchmarks({ rec, kpi, kpiLabel, scale }) {
  const [open, setOpen] = useState(true); // 9.3: benchmark values are part of the final answer
  const rows = [...(rec.candidates || [])].sort((a, b) => a.kpi_rank - b.kpi_rank);
  const bench = Object.fromEntries((rec.benchmarks || []).map((b) => [b.product_id, b]));
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1 text-[12px] font-semibold text-muted-foreground hover:text-foreground"
      >
        <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
        Calculated benchmarks and inventory ({rec.requirements?.vertical}, {rec.requirements?.geo})
      </button>
      {open ? (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead className="border-b border-border text-left text-muted-foreground">
              <tr>
                <th className="py-1 pr-2 font-medium">#</th>
                <th className="py-1 pr-2 font-medium">Product</th>
                <th className="py-1 pr-2 text-right font-medium">
                  {kpi === "ctr" ? "CTR" : "In-view"}
                </th>
                <th className="py-1 pr-2 text-right font-medium">
                  {kpi === "ctr" ? "In-view" : "CTR"}
                </th>
                <th className="py-1 pr-2 text-right font-medium">CPM</th>
                <th className="py-1 pr-2 text-right font-medium">Budget buys</th>
                <th className="py-1 pr-2 text-right font-medium">Capacity</th>
                <th className="py-1 text-right font-medium">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const b = bench[c.product_id] || {};
                const s = scale[c.product_id] || {};
                return (
                  <tr key={c.product_id} className="border-b border-border/50 last:border-0">
                    <td className="py-1 pr-2 tabular-nums">{c.kpi_rank}</td>
                    <td className="py-1 pr-2">{c.product_name}</td>
                    <td className="py-1 pr-2 text-right tabular-nums">{pct(c.kpi_value)}</td>
                    <td className="py-1 pr-2 text-right tabular-nums">
                      {pct(kpi === "ctr" ? b.in_view_rate : b.ctr)}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums">{money(c.cpm)}</td>
                    <td className="py-1 pr-2 text-right tabular-nums">
                      {count(c.affordable_imps)}
                    </td>
                    <td
                      className={cn(
                        "py-1 pr-2 text-right tabular-nums",
                        s.absorbs_budget === false && "text-amber-700 dark:text-amber-400",
                      )}
                    >
                      {count(s.capacity)}
                    </td>
                    <td className="py-1 text-right tabular-nums">
                      {Number(s.confidence ?? 0).toFixed(2)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {kpiLabel} is ratio-of-sums over {rec.requirements?.vertical} line items. Capacity is
            per the inventory policy.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/** Copies the card as Markdown (requirement-9 order); a check mark confirms for 1.5s. */
function CopyMarkdown({ artifact, disabled = false }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(recommendationMarkdown(artifact));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      disabled={disabled} // while the rationale is still being written
      aria-label={copied ? "Copied as Markdown" : "Copy as Markdown"}
      title="Copy as Markdown"
      className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </button>
  );
}

/** Placeholder for a section the model is still writing. */
function Writing({ lines = 1 }) {
  return (
    <div role="status" aria-label="Writing the rationale…" className="space-y-1.5">
      <p className="text-[12px] text-muted-foreground">Writing the rationale…</p>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn("h-3", i === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}
