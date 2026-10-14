/**
 * User-facing names for tool calls on the activity rail: a plain label plus the call's key
 * arguments, so two calls of one tool read differently ("Retrieve data: Campaign history ·
 * QSR" vs "Retrieve data: Inventory · APAC"). Unknown tools fall back to their raw name.
 */

const DATASETS = {
  products: "Products",
  campaign_history: "Campaign history",
  inventory: "Inventory",
  client_briefs: "Client briefs",
};
const KPIS = { ctr: "CTR", in_view_rate: "In-view rate" };
const TOPICS = {
  overview: "Overview",
  workflow: "Workflow stages",
  benchmarks: "Benchmarks",
  inventory: "Inventory rule",
  selection: "How the plan is chosen",
  card: "Recommendation card",
  glossary: "Glossary",
};

function money(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? `$${n.toLocaleString("en-US")}` : null;
}

function join(...parts) {
  return parts
    .flat()
    .filter((p) => p !== null && p !== undefined && p !== "")
    .join(" · ");
}

function filterValues(filters) {
  return Object.values(filters || {}).map((v) => (Array.isArray(v) ? v.join(", ") : String(v)));
}

const TOOLS = {
  get_case_data: {
    label: "Retrieve data",
    running: "Retrieving data",
    done: "Retrieved data",
    detail: (i) => join(DATASETS[i.dataset] || i.dataset, filterValues(i.filters)),
  },
  run_python_analysis: {
    label: "Run analysis",
    running: "Running analysis",
    done: "Ran analysis",
    detail: () => "", // the code and question are in the expanded step, not the label
  },
  list_products: {
    label: "List products",
    running: "Listing products",
    done: "Listed products",
    detail: () => "",
  },
  get_benchmarks: {
    label: "Get benchmarks",
    running: "Getting benchmarks",
    done: "Got benchmarks",
    detail: (i) => i.vertical,
  },
  select_products: {
    label: "Rank products",
    running: "Ranking products",
    done: "Ranked products",
    detail: (i) => join(i.vertical, KPIS[i.kpi] || i.kpi, money(i.budget)),
  },
  check_scale: {
    label: "Check scale",
    running: "Checking scale",
    done: "Checked scale",
    detail: (i) => join(i.vertical, i.geo, money(i.budget)),
  },
  run_recommendation: {
    label: "Run recommendation",
    running: "Running recommendation",
    done: "Ran recommendation",
    detail: (i) => join(i.client_name, i.vertical, KPIS[i.kpi] || i.kpi, i.geo, money(i.budget)),
  },
  read_knowledge: {
    label: "Read the guide",
    running: "Reading the guide",
    done: "Read the guide",
    detail: (i) => TOPICS[i.topic] || i.topic,
  },
  web_search: {
    label: "Search the web",
    running: "Searching the web",
    done: "Searched the web",
    detail: (i) => i.query,
  },
};

const MAX_DETAIL = 80;

function clip(text) {
  const t = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  return t.length > MAX_DETAIL ? `${t.slice(0, MAX_DETAIL - 1)}…` : t;
}

/** "Retrieve data: Campaign history · QSR" (or the raw name for an unknown tool). */
export function toolLabel(name, input) {
  const tool = TOOLS[name];
  if (!tool) return name;
  const detail = clip(tool.detail(input && typeof input === "object" ? input : {}));
  return detail ? `${tool.label}: ${detail}` : tool.label;
}

/** The container header: "Retrieving data…" while running, "Retrieved data" when done. */
export function toolActivity(name, running, output) {
  const tool = TOOLS[name];
  if (!tool) return running ? `Running ${name}…` : `Used ${name}`;
  if (running) return `${tool.running}…`;
  // The workflow that stopped at its form didn't run a recommendation: it checked the brief.
  if (name === "run_recommendation" && output?.status === "FORM_REQUIRED")
    return "Checked the brief";
  return tool.done;
}
