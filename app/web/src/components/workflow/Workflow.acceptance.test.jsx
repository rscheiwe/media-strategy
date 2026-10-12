// Clarification form and recommendation card acceptance tests.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { BriefDetailsForm } from "./BriefDetailsForm";
import { RecommendationCard } from "./RecommendationCard";
import { WorkflowStages } from "./WorkflowStages";
import {
  artifactParts,
  formParts,
  openFormId,
  deliveryRows,
  recommendationMarkdown,
  stageParts,
  submissionText,
  submittedForms,
} from "@/lib/workflow";

const motionState = vi.hoisted(() => ({ reduced: false }));

vi.mock("framer-motion", () => {
  const Div = ({ initial, animate, variants, children, ...props }) => (
    <div {...props} data-motion-initial={initial ?? ""} data-motion-animate={animate ?? ""}>{children}</div>
  );
  const Paragraph = ({ initial, animate, variants, children, ...props }) => (
    <p {...props} data-motion-initial={initial ?? ""} data-motion-animate={animate ?? ""}>{children}</p>
  );
  return { motion: { div: Div, p: Paragraph }, useReducedMotion: () => motionState.reduced };
});

afterEach(() => cleanup());

const form = {
  status: "FORM_REQUIRED",
  form_id: "form-1",
  missing: ["vertical"],
  form_config: {
    title: "Confirm the brief",
    description: "I need the vertical.",
    submit_label: "Get recommendation",
    fields: [
      { name: "client_name", label: "Client", type: "text", value: "New mobile app", required: false },
      { name: "vertical", label: "Vertical", type: "choice", value: null, missing: true, required: true, options: [{ value: "Retail", label: "Retail" }, { value: "Entertainment", label: "Entertainment" }] },
      { name: "kpi", label: "Primary KPI", type: "choice", value: "in_view_rate", inferred: true, required: true, options: [{ value: "ctr", label: "CTR" }, { value: "in_view_rate", label: "In-view rate" }] },
      { name: "geo", label: "Geo", type: "choice", value: "US", required: true, options: [{ value: "US", label: "US" }] },
      { name: "budget", label: "Budget (USD)", type: "number", value: 25000, required: true, presets: [15000, 25000] },
      { name: "impression_goal", label: "Impression goal (optional)", type: "number", value: null, required: false },
    ],
  },
};

const rec = {
  requirements: { client_name: "Acme Shoes", vertical: "Retail", kpi: "ctr", geo: "US", budget: 30000 },
  candidates: [
    { product_id: "P003", product_name: "Commerce Connect", cpm: 15, kpi_value: 0.0123, kpi_rank: 1, affordable_imps: 2000000 },
    { product_id: "P005", product_name: "Premium Takeover", cpm: 32, kpi_value: 0.0091, kpi_rank: 3, affordable_imps: 937500 },
  ],
  benchmarks: [{ product_id: "P003", ctr: 0.0123, in_view_rate: 0.69 }],
  scale: [{ product_id: "P005", confidence: 0.91, capacity: 940540, absorbs_budget: true }],
  plan: { kind: "single", lines: [{ product_id: "P005", product_name: "Premium Takeover", spend: 30000, impressions: 937500, kpi_value: 0.0091, confidence: 0.91, low_confidence: false }], total_spend: 30000, total_impressions: 937500, blended_kpi: 0.0091, unspent: 0 },
  alternatives: [{ product_ids: ["P003"], kind: "rejected_leader", kpi_value: 0.0123, reasons: ["INVENTORY_BELOW_BUDGET"], delta: { kpi_points: 0.32, impression_shortfall: 573600 } }],
  tradeoffs: [{ code: "KPI_LEADER_NOT_VIABLE", facts: { product_id: "P003", reasons: ["INVENTORY_BELOW_BUDGET"] } }],
  policy: { note: "Capacity = forecast × confidence" },
  rationale: { text: "Premium Takeover fits the available Retail inventory." },
};

describe("clarification form", () => {
  it("test_wf16_clarification_form", () => {
    const onSubmit = vi.fn();
    render(<BriefDetailsForm form={form} interactive busy={false} onSubmit={onSubmit} />);
    const dialog = screen.getByRole("form", { name: "Confirm the brief" });
    expect(within(dialog).getByText("needed")).toBeInTheDocument();
    expect(within(dialog).getByText("confirm")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Get recommendation" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "In-view rate" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(dialog).getByRole("button", { name: "Entertainment" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Get recommendation" }));
    expect(onSubmit).toHaveBeenCalledWith("form-1", expect.objectContaining({ vertical: "Entertainment", budget: 25000, impression_goal: null }));
  });

  it("renders older/submitted forms as disabled with the persisted selected values", () => {
    render(<BriefDetailsForm form={form} interactive={false} submittedValues={{ ...form.form_config.fields.reduce((a, f) => ({ ...a, [f.name]: f.value }), {}), vertical: "Entertainment" }} busy={false} />);
    expect(screen.getByText("Submitted")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Entertainment" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Entertainment" })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers brief advertisers in a keyboard-accessible combobox and accepts any client name", () => {
    const onSubmit = vi.fn();
    const suggestions = ["Acme Shoes", "Burger Bazaar", "CineVerse", "Stellar Bank", "Wanderlust Air"];
    const suggestedForm = {
      ...form,
      form_config: {
        ...form.form_config,
        fields: form.form_config.fields.map((field) =>
          field.name === "client_name"
            ? { ...field, value: "", suggestions, placeholder: "Type or select an advertiser…" }
            : field,
        ),
      },
    };
    render(<BriefDetailsForm form={suggestedForm} interactive busy={false} onSubmit={onSubmit} />);
    const client = screen.getByRole("combobox", { name: "Client" });
    expect(client).toHaveAttribute("aria-autocomplete", "list");
    expect(client).toHaveAttribute("placeholder", "Type or select an advertiser…");

    fireEvent.focus(client);
    const listId = client.getAttribute("aria-controls");
    expect(client).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("listbox")).toHaveAttribute("id", listId);
    expect(screen.getByRole("listbox")).toHaveClass("left-0", "right-0", "top-full", "mt-1");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(suggestions);
    fireEvent.keyDown(client, { key: "Escape" });
    expect(client).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    fireEvent.focus(client);
    fireEvent.change(client, { target: { value: "st" } });
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["Stellar Bank", "Wanderlust Air"]);
    fireEvent.keyDown(client, { key: "ArrowDown" });
    expect(screen.getByRole("option", { name: "Stellar Bank" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(client, { key: "Enter" });
    expect(client).toHaveValue("Stellar Bank");
    expect(client).toHaveAttribute("aria-expanded", "false");

    fireEvent.change(client, { target: { value: "" } });
    fireEvent.mouseDown(screen.getByRole("option", { name: "Wanderlust Air" }));
    expect(client).toHaveValue("Wanderlust Air");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    fireEvent.change(client, { target: { value: "Northstar Coffee" } });
    fireEvent.click(screen.getByRole("button", { name: "Entertainment" }));
    fireEvent.click(screen.getByRole("button", { name: "Get recommendation" }));
    expect(onSubmit).toHaveBeenCalledWith("form-1", expect.objectContaining({ client_name: "Northstar Coffee", vertical: "Entertainment" }));
  });
});

describe("recommendation UI", () => {
  it("test_wf17_recommendation_ui", () => {
    const { rerender } = render(<RecommendationCard artifact={{ status: "loading" }} />);
    expect(screen.getByRole("region", { name: "Recommendation loading" })).toBeInTheDocument();
    rerender(<RecommendationCard artifact={{ id: "artifact-rec-1", title: "Acme · Retail · US", payload: rec }} onOpen={vi.fn()} />);
    const card = screen.getByRole("region", { name: "Recommendation" });
    expect(within(card).getByRole("list", { name: "Requirements" })).toHaveTextContent("Acme Shoes");
    const [planTable, benchmarkTable] = within(card).getAllByRole("table");
    expect(planTable).toHaveTextContent("Premium Takeover");
    expect(planTable).toHaveTextContent("Forecasted impressions");
    expect(within(card).getByText("Why this is the best fit")).toBeInTheDocument();
    expect(within(card).getByText("Rejected top alternatives")).toBeInTheDocument();
    expect(within(card).getByText(/Forecast capacity can't absorb/)).toBeInTheDocument();
    expect(within(card).getByText("Tradeoffs")).toBeInTheDocument();
    expect(within(card).getByText(/Inventory policy:/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Calculated benchmarks and inventory/ })).toHaveAttribute("aria-expanded", "true");
    expect(within(benchmarkTable).getByText("Commerce Connect")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "View report" })).toBeInTheDocument();
    // A payload saved before the delivery fields existed shows no delivery comparison.
    expect(within(card).queryByRole("group", { name: "What the budget delivers" })).not.toBeInTheDocument();

    render(<WorkflowStages stages={[{ run_id: "run-1", stage: "benchmarks", label: "Benchmarks", status: "done", summary: "6 products", ms: 12 }]} />);
    expect(screen.getByRole("list", { name: "Workflow stages" })).toHaveTextContent(/Benchmarks\s*·\s*6 products/);
    expect(screen.getByText("12 ms")).toBeInTheDocument();
  });

  it("compares what the budget delivers in a pale-yellow inner card", () => {
    const withDelivery = {
      ...rec,
      plan: { ...rec.plan, expected_outcomes: 8493.02, cost_per_outcome: 3.5323 },
      alternatives: [
        ...rec.alternatives,
        {
          product_ids: ["P003", "P006"],
          kind: "bundle",
          kpi_value: 0.01156,
          delta: { kpi_points: 0.25, uplift_pct: 27.6 },
          plan: {
            kind: "bundle",
            lines: [
              { product_id: "P003", product_name: "Commerce Connect" },
              { product_id: "P006", product_name: "Attention Builder" },
            ],
            blended_kpi: 0.01156,
            expected_outcomes: 23592.4,
            cost_per_outcome: 1.2716,
          },
        },
      ],
    };
    render(<RecommendationCard artifact={{ id: "artifact-rec-2", title: "Acme · Retail · US", payload: withDelivery }} />);
    const delivery = screen.getByRole("group", { name: "What the budget delivers" });
    expect(delivery).toHaveClass("bg-yellow-50");
    expect(delivery).toHaveTextContent("What $30,000 delivers");
    expect(delivery).toHaveTextContent("Expected clicks");
    expect(delivery).toHaveTextContent("Cost per click");
    const rows = within(delivery).getAllByRole("row");
    expect(rows[1]).toHaveTextContent(/Premium Takeover\s*\(recommended\)\s*0\.91%\s*8,493\s*\$3\.53/);
    expect(rows[2]).toHaveTextContent(/Commerce Connect \+ Attention Builder\s*\(bundle, not chosen\)\s*1\.16%\s*23,592\s*\$1\.27/);
    expect(delivery).toHaveTextContent("don't change the pick");

    const markdown = recommendationMarkdown({ title: "Acme · Retail · US", payload: withDelivery });
    expect(markdown.indexOf("**What $30,000 delivers**")).toBeGreaterThan(markdown.indexOf("**Recommended product**"));
    expect(markdown.indexOf("**What $30,000 delivers**")).toBeLessThan(markdown.indexOf("**Why this is the best fit**"));
    expect(markdown).toContain("| Option | CTR | Expected clicks | Cost per click |");
    expect(markdown).toContain("| Premium Takeover (recommended) | 0.91% | 8,493 | $3.53 |");
    expect(markdown).toContain("| Commerce Connect + Attention Builder (bundle, not chosen) | 1.16% | 23,592 | $1.27 |");

    const inView = deliveryRows({ ...withDelivery, requirements: { ...rec.requirements, kpi: "in_view_rate" } });
    expect(inView).toHaveLength(2);
    cleanup();
    render(
      <RecommendationCard
        artifact={{ id: "artifact-rec-3", title: "Burger · QSR · US", payload: { ...withDelivery, requirements: { ...rec.requirements, kpi: "in_view_rate" } } }}
      />,
    );
    const inViewCard = screen.getByRole("group", { name: "What the budget delivers" });
    expect(inViewCard).toHaveTextContent("Expected viewable imps");
    expect(inViewCard).toHaveTextContent("Cost per 1,000 viewable");

    expect(deliveryRows({ ...withDelivery, plan: { kind: "none", lines: [] } })).toEqual([]);
    expect(deliveryRows(rec)).toEqual([]);
  });

  it("reveals a fresh card and copies it as markdown", async () => {
    const artifact = {
      id: "artifact-md-1",
      title: "Acme · Retail · US",
      createdAt: 999_999,
      payload: {
        ...rec,
        rationale: {
          ...rec.rationale,
          rejected: [{ product_id: "P003", product_name: "Commerce Connect", explanation: "Commerce Connect leads on the KPI but isn't viable." }],
        },
        plan: {
          ...rec.plan,
          kind: "bundle",
          lines: [
            rec.plan.lines[0],
            { product_id: "P003", product_name: "Commerce Connect", spend: 10_000, impressions: 666666, kpi_value: 0.0123, confidence: 0.87 },
          ],
          total_spend: 28_000,
          total_impressions: 1_604_166,
          blended_kpi: 0.0104,
          unspent: 2_000,
        },
      },
    };
    const markdown = recommendationMarkdown(artifact);
    const headings = [
      "**Client requirements**",
      "**Recommended bundle**",
      "**Why this is the best fit**",
      "**Rejected top alternatives**",
      "**Tradeoffs**",
      "**Calculated benchmarks and inventory (Retail, US)**",
      "_Inventory policy: Capacity = forecast × confidence._",
    ];
    for (let i = 1; i < headings.length; i += 1)
      expect(markdown.indexOf(headings[i - 1])).toBeLessThan(markdown.indexOf(headings[i]));
    expect(markdown).toContain("| Premium Takeover | $30,000 | 937,500 | 0.91% | 0.91 |");
    expect(markdown).toContain("| Total ($2,000 unspent) | $28,000 | 1,604,166 | 1.04% |  |");
    expect(markdown).toContain("- Commerce Connect leads on the KPI but isn't viable");
    const noRejectedMarkdown = recommendationMarkdown({
      ...artifact,
      payload: { ...artifact.payload, rationale: { ...rec.rationale, rejected: [] } },
    });
    expect(noRejectedMarkdown).toContain("None: nothing ranks above the pick on CTR.");

    const writeText = vi.fn().mockResolvedValue(undefined);
    const clipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
    try {
      const { unmount } = render(<RecommendationCard artifact={artifact} />);
      const freshBody = screen.getByRole("region", { name: "Recommendation" }).querySelector(".space-y-4");
      expect(freshBody).toHaveAttribute("data-motion-initial", "hidden");
      fireEvent.click(screen.getByRole("button", { name: "Copy as Markdown" }));
      await screen.findByRole("button", { name: "Copied as Markdown" });
      expect(writeText).toHaveBeenCalledWith(markdown);

      unmount();
      render(<RecommendationCard artifact={{ ...artifact, createdAt: 979_999 }} />);
      const staleBody = screen.getByRole("region", { name: "Recommendation" }).querySelector(".space-y-4");
      expect(staleBody).toHaveAttribute("data-motion-initial", "show");

      cleanup();
      motionState.reduced = true;
      render(<RecommendationCard artifact={artifact} />);
      const reducedBody = screen.getByRole("region", { name: "Recommendation" }).querySelector(".space-y-4");
      expect(reducedBody).toHaveAttribute("data-motion-initial", "show");
    } finally {
      now.mockRestore();
      motionState.reduced = false;
      if (clipboard) Object.defineProperty(navigator, "clipboard", clipboard);
      else delete navigator.clipboard;
    }
  });

  it("shows writing placeholders while streaming", () => {
    const rationale = {
      best_fit: "Template rationale that must not appear while the model is writing.",
      rejected: [
        { product_id: "P003", product_name: "Commerce Connect", explanation: "Commerce Connect cannot absorb this buy." },
        { product_id: "P004", product_name: "Social Amplifier", explanation: "Social Amplifier misses the goal." },
      ],
    };
    const artifact = {
      id: "artifact-writing-1",
      title: "Acme · Retail · US",
      status: "streaming",
      createdAt: 0,
      payload: { ...rec, rationale },
    };
    const { rerender } = render(<RecommendationCard artifact={artifact} />);
    const writing = screen.getAllByRole("status", { name: "Writing the rationale…" });
    expect(writing).toHaveLength(2);
    expect(writing[0].querySelectorAll('[data-slot="skeleton"]')).toHaveLength(2);
    expect(writing[1].querySelectorAll('[data-slot="skeleton"]')).toHaveLength(2);
    expect(screen.queryByText(rationale.best_fit)).not.toBeInTheDocument();
    expect(screen.queryByText(rationale.rejected[0].explanation)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy as Markdown" })).toBeDisabled();
    expect(screen.getByText("Recommended product")).toBeInTheDocument();

    rerender(<RecommendationCard artifact={{ ...artifact, status: "complete" }} />);
    expect(screen.queryByRole("status", { name: "Writing the rationale…" })).not.toBeInTheDocument();
    expect(screen.getByText(rationale.best_fit)).toBeInTheDocument();
    expect(screen.getByText(rationale.rejected[0].explanation)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy as Markdown" })).toBeEnabled();

    rerender(<RecommendationCard artifact={{
      ...artifact,
      payload: { ...artifact.payload, alternatives: [], rationale: { ...rationale, rejected: [] } },
    }} />);
    expect(screen.getAllByRole("status", { name: "Writing the rationale…" })).toHaveLength(1);
    expect(screen.getByText("None: nothing ranks above the pick on CTR.")).toBeInTheDocument();
  });
});

describe("workflow persisted-part helpers", () => {
  it("keeps latest snapshots, makes only newest unanswered form interactive, and restores submitted values", () => {
    const assistant = { role: "assistant", parts: [
      { type: "tool-run_recommendation", output: form },
      { type: "data-workflow-stage", id: "wf-r-validate", data: { stage: "validate", status: "running" } },
      { type: "data-workflow-stage", id: "wf-r-validate", data: { stage: "validate", status: "done" } },
      { type: "data-artifact-recommendation", id: "artifact-rec-r", data: { status: "loading" } },
      { type: "data-artifact-recommendation", id: "artifact-rec-r", data: { status: "complete", payload: rec } },
    ] };
    const submitted = { role: "user", metadata: { formSubmission: { form_id: "form-1", parameters: { vertical: "Entertainment", budget: 25000 } } } };
    expect(formParts(assistant)).toEqual([form]);
    expect(stageParts(assistant)).toEqual([{ stage: "validate", status: "done" }]);
    expect(artifactParts(assistant)).toEqual([{ status: "complete", payload: rec }]);
    expect(openFormId([assistant])).toBe("form-1");
    expect(openFormId([assistant, submitted])).toBeNull();
    expect(submittedForms([assistant, submitted])).toEqual({ "form-1": { vertical: "Entertainment", budget: 25000 } });
    expect(submissionText({ client_name: "New mobile app", vertical: "Entertainment", kpi: "in_view_rate", geo: "US", budget: 25000 })).toBe("Client: New mobile app · Vertical: Entertainment · KPI: In-view rate · Geo: US · Budget: $25,000");
  });
});
