/**
 * Helpers for the recommendation workflow's parts (see app/api chat/workflow_tool.py):
 *  - tool-run_recommendation: output FORM_REQUIRED (a form) | done | not_run | failed
 *  - data-workflow-stage: one row per DAG stage, updated in place by id
 *  - data-artifact-recommendation: the structured Recommendation, snapshots by id
 */

export const WORKFLOW_TOOL = "tool-run_recommendation";

export const KPI_LABELS = { ctr: "CTR", in_view_rate: "In-view rate" };

export const REASON_TEXT = {
  INVENTORY_BELOW_BUDGET: "Forecast capacity can't absorb the full budget",
  BUDGET_BELOW_GOAL: "The budget buys fewer impressions than the goal",
  INVENTORY_BELOW_GOAL: "Forecast capacity is below the impression goal",
  BELOW_MIN_CONFIDENCE: "Forecast confidence is below the policy minimum",
};

export function pct(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
}

export function money(value) {
  if (typeof value !== "number") return "—";
  return Number.isInteger(value)
    ? `$${value.toLocaleString("en-US")}`
    : `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function count(value) {
  return typeof value === "number" ? Math.round(value).toLocaleString("en-US") : "—";
}

/** Latest snapshot of each data part with an id, in first-seen order. */
function latestById(parts, type) {
  const order = [];
  const byId = new Map();
  for (const part of parts || []) {
    if (part?.type !== type) continue;
    const id = part.id || part.data?.id || `${type}-${order.length}`;
    if (!byId.has(id)) order.push(id);
    byId.set(id, part);
  }
  return order.map((id) => byId.get(id));
}

export function stageParts(message) {
  return latestById(message?.parts, "data-workflow-stage").map((part) => part.data || {});
}

export function artifactParts(message) {
  return latestById(message?.parts, "data-artifact-recommendation").map((part) => part.data || {});
}

/** Forms (FORM_REQUIRED tool outputs) in a message. */
export function formParts(message) {
  return (message?.parts || [])
    .filter((part) => part?.type === WORKFLOW_TOOL && part.output?.status === "FORM_REQUIRED")
    .map((part) => part.output);
}

/**
 * The form a user can still answer: the newest form, if no user message follows it.
 * Older forms, and a form already answered, render read-only.
 */
export function openFormId(messages) {
  for (let i = (messages || []).length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.role === "user") return null;
    const forms = formParts(message);
    if (forms.length) return forms[forms.length - 1].form_id;
  }
  return null;
}

/** Submitted values per form id, from user messages' metadata (persisted by the API). */
export function submittedForms(messages) {
  const out = {};
  for (const message of messages || []) {
    const submission = message?.metadata?.formSubmission;
    if (message.role === "user" && submission?.form_id)
      out[submission.form_id] = submission.parameters || {};
  }
  return out;
}

/** The user-visible line for a form submission (also what the model sees in history). */
export function submissionText(values) {
  const parts = [];
  if (values.client_name) parts.push(`Client: ${values.client_name}`);
  if (values.vertical) parts.push(`Vertical: ${values.vertical}`);
  if (values.kpi) parts.push(`KPI: ${KPI_LABELS[values.kpi] || values.kpi}`);
  if (values.geo) parts.push(`Geo: ${values.geo}`);
  if (values.budget) parts.push(`Budget: ${money(Number(values.budget))}`);
  if (values.impression_goal)
    parts.push(`Goal: ${count(Number(values.impression_goal))} impressions`);
  return parts.join(" · ");
}

export function tradeoffText(tradeoff, names) {
  const f = tradeoff.facts || {};
  const name = (id) => names[id] || id;
  switch (tradeoff.code) {
    case "KPI_LEADER_NOT_VIABLE":
      return `${name(f.product_id)} leads on the KPI but isn't viable: ${(f.reasons || [])
        .map((r) => REASON_TEXT[r]?.toLowerCase() || r)
        .join("; ")}.`;
    case "BUNDLE_UPLIFT":
      return `A ${(f.product_ids || []).map(name).join(" + ")} bundle would lift the blended KPI by ${f.uplift_pct}%.`;
    case "NEAR_TIE":
      return `${name(f.product_id)} is within ${Math.abs(f.kpi_points).toFixed(2)} points: a near-tie.`;
    case "LOW_CONFIDENCE_LINE":
      return `${name(f.product_id)}'s forecast confidence is low (${Number(f.confidence).toFixed(2)}).`;
    case "THIN_MARGIN":
      return `${name(f.product_id)}'s forecast covers the buy with only ${f.margin_pct}% to spare.`;
    case "UNSPENT_BUDGET": {
      const why =
        f.reason === "line_limit"
          ? `the bundle is at its ${f.max_lines || 3}-product limit, each product at its usable inventory`
          : f.reason === "no_inventory"
            ? "no other product has usable inventory left for it"
            : "it's below the minimum line spend";
      return `${money(f.unspent)} stays unspent: ${why}.`;
    }
    case "GOAL_MARGIN":
      return `The plan ${f.margin_pct >= 0 ? "exceeds" : "misses"} the impression goal by ${Math.abs(f.margin_pct)}%.`;
    case "NO_VIABLE_PLAN":
      return "No product or small bundle can deliver this brief under the current policy.";
    default:
      return tradeoff.code;
  }
}

function mdRow(cells) {
  return `| ${cells.map((c) => String(c).replace(/\|/g, "\\|")).join(" | ")} |`;
}

/**
 * The recommendation card as Markdown (for copy/paste into a doc or email), in the order of
 * brief requirement 9: requirements, plan with forecasted impressions, why it's the best fit,
 * rejected top alternatives, tradeoffs, calculated benchmarks, inventory policy.
 */
export function recommendationMarkdown(artifact) {
  const rec = artifact?.payload;
  if (!rec) return "";
  const req = rec.requirements || {};
  const kpi = KPI_LABELS[req.kpi] || "KPI";
  const names = Object.fromEntries(
    (rec.candidates || []).map((c) => [c.product_id, c.product_name]),
  );
  const plan = rec.plan || {};
  const scale = Object.fromEntries((rec.scale || []).map((s) => [s.product_id, s]));
  const out = [`## Recommendation: ${artifact.title}`, ""];

  out.push("**Client requirements**", "");
  if (req.client_name) out.push(`- Client: ${req.client_name}`);
  out.push(
    `- Vertical: ${req.vertical}`,
    `- Primary KPI: ${kpi}`,
    `- Geo: ${req.geo}`,
    `- Budget: ${money(req.budget)}`,
  );
  if (req.impression_goal) out.push(`- Impression goal: ${count(req.impression_goal)}`);

  out.push("", `**${plan.kind === "bundle" ? "Recommended bundle" : "Recommended product"}**`, "");
  if (plan.kind === "none") {
    out.push("No viable plan for this brief.");
  } else {
    out.push(mdRow(["Product", "Spend", "Forecasted impressions", kpi, "Forecast confidence"]));
    out.push(mdRow(["---", "---:", "---:", "---:", "---:"]));
    for (const line of plan.lines || []) {
      out.push(
        mdRow([
          line.product_name,
          money(line.spend),
          count(line.impressions),
          pct(line.kpi_value),
          Number(line.confidence).toFixed(2),
        ]),
      );
    }
    if ((plan.lines || []).length > 1) {
      out.push(
        mdRow([
          `Total${plan.unspent ? ` (${money(plan.unspent)} unspent)` : ""}`,
          money(plan.total_spend),
          count(plan.total_impressions),
          pct(plan.blended_kpi),
          "",
        ]),
      );
    }
  }

  const rationale = rec.rationale || {};
  if (rationale.best_fit || rationale.text)
    out.push("", "**Why this is the best fit**", "", rationale.best_fit || rationale.text);

  if (plan.kind !== "none") {
    out.push("", "**Rejected top alternatives**", "");
    const notes = rationale.rejected || [];
    if (notes.length) for (const n of notes) out.push(`- ${n.explanation}`);
    else out.push(`None: nothing ranks above the pick on ${kpi}.`);
  }

  if ((rec.tradeoffs || []).length) {
    out.push("", "**Tradeoffs**", "", ...rec.tradeoffs.map((t) => `- ${tradeoffText(t, names)}`));
  }

  const bench = Object.fromEntries((rec.benchmarks || []).map((b) => [b.product_id, b]));
  const other = req.kpi === "ctr" ? "In-view rate" : "CTR";
  out.push("", `**Calculated benchmarks and inventory (${req.vertical}, ${req.geo})**`, "");
  out.push(
    mdRow(["#", "Product", kpi, other, "CPM", "Budget buys", "Usable inventory", "Confidence"]),
  );
  out.push(mdRow(["---:", "---", "---:", "---:", "---:", "---:", "---:", "---:"]));
  for (const c of [...(rec.candidates || [])].sort((a, b) => a.kpi_rank - b.kpi_rank)) {
    const b = bench[c.product_id] || {};
    const s = scale[c.product_id] || {};
    out.push(
      mdRow([
        c.kpi_rank,
        c.product_name,
        pct(c.kpi_value),
        pct(req.kpi === "ctr" ? b.in_view_rate : b.ctr),
        money(c.cpm),
        count(c.affordable_imps),
        count(s.capacity),
        Number(s.confidence ?? 0).toFixed(2),
      ]),
    );
  }
  if (rec.policy?.note) out.push("", `_Inventory policy: ${rec.policy.note}._`);
  return out.join("\n") + "\n";
}
