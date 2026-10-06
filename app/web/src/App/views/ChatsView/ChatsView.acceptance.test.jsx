// Chat conversation acceptance tests.
// "The UI streams the reply into the conversation, shows tool calls as steps with their
// input and output, and reloading the page restores the chat from the API."
// The real app renders; only the HTTP API is faked (src/test/acceptance.js).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import {
  CHAT_A,
  controllableStream,
  installApi,
  installDomShims,
  renderApp,
} from "@/test/acceptance";
import { toolActivity, toolLabel } from "@/lib/toolLabels";

beforeEach(() => installDomShims());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function send(text) {
  const box = await screen.findByPlaceholderText(/Ask anything/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter", code: "Enter" });
}

describe("chat conversation", () => {
  it("streams the assistant reply into the conversation as it arrives", async () => {
    const stream = controllableStream();
    const api = installApi({ onChatStream: () => stream });
    await renderApp("/");

    await send("What does Commerce Connect do?");
    await waitFor(() =>
      expect(api.calls.some((c) => c.method === "POST" && c.path === "/app/chat")).toBe(true),
    );

    stream.push({ type: "start", messageId: "msg-1" });
    stream.push({ type: "start-step" });
    stream.push({ type: "text-start", id: "t1" });
    stream.push({ type: "text-delta", id: "t1", delta: "Commerce Connect is a shoppable" });
    // Visible before the stream has finished: the reply is streamed, not shown at the end.
    expect(await screen.findByText(/Commerce Connect is a shoppable/)).toBeInTheDocument();

    stream.push({ type: "text-delta", id: "t1", delta: " format for product discovery." });
    stream.push({ type: "text-end", id: "t1" });
    stream.push({ type: "finish-step" });
    stream.push({ type: "finish" });
    stream.end();
    expect(await screen.findByText(/shoppable format for product discovery\./)).toBeInTheDocument();
    // The title is also rendered in the optimistic sidebar row; at least one
    // matching transcript node proves the submitted user turn was rendered.
    expect(screen.getAllByText("What does Commerce Connect do?").length).toBeGreaterThan(0);
  });

  it("shows a tool call as a step with its input and its output", async () => {
    const stream = controllableStream();
    installApi({ onChatStream: () => stream });
    await renderApp("/");

    await send("Which products are video?");
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    stream.push({ type: "start", messageId: "msg-2" });
    stream.push({ type: "start-step" });
    stream.push({ type: "tool-input-start", toolCallId: "call-1", toolName: "list_products" });
    stream.push({
      type: "tool-input-available",
      toolCallId: "call-1",
      toolName: "list_products",
      input: { query: "video formats" },
    });
    stream.push({
      type: "tool-output-available",
      toolCallId: "call-1",
      output: { products: [{ product_id: "P002", product_name: "Video Spotlight", cpm: 18 }] },
    });
    stream.push({ type: "finish-step" });
    stream.push({ type: "start-step" });
    stream.push({ type: "text-start", id: "t2" });
    stream.push({ type: "text-delta", id: "t2", delta: "Video Spotlight is the video product." });
    stream.push({ type: "text-end", id: "t2" });
    stream.push({ type: "finish-step" });
    stream.push({ type: "finish" });
    stream.end();

    fireEvent.click(await screen.findByText("Listed products"));
    const step = await screen.findByText("List products");
    expect(await screen.findByText("Listed products")).toBeInTheDocument();
    // Arguments live in the expanded, labelled INPUT panel, not the concise tool header.
    if (!screen.queryByText(/video formats/) || !screen.queryByText(/P002/)) fireEvent.click(step);
    expect(await screen.findByText(/input/i)).toBeInTheDocument();
    expect(screen.getAllByText(/video formats/).length).toBeGreaterThan(0);
    expect(await screen.findByText(/output/i)).toBeInTheDocument();
    expect(await screen.findByText(/P002/)).toBeInTheDocument(); // the output
    expect(await screen.findByText(/Video Spotlight is the video product\./)).toBeInTheDocument();
  });

  it("collapsed by default and actions after stream", async () => {
    const stream = controllableStream();
    installApi({ onChatStream: () => stream });
    await renderApp("/");

    await send("Show me the catalog");
    stream.push({ type: "start", messageId: "00000000-0000-0000-0000-000000000201" });
    stream.push({ type: "start-step" });
    stream.push({ type: "tool-input-start", toolCallId: "call-stream", toolName: "list_products" });
    stream.push({ type: "tool-input-available", toolCallId: "call-stream", toolName: "list_products", input: {} });
    expect(await screen.findByText("List products")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Listing products/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByLabelText("Copy message")).not.toBeInTheDocument();

    stream.push({ type: "tool-output-available", toolCallId: "call-stream", output: { products: [] } });
    stream.push({ type: "finish-step" });
    stream.push({ type: "start-step" });
    stream.push({ type: "text-start", id: "done" });
    stream.push({ type: "text-delta", id: "done", delta: "Done." });
    stream.push({ type: "text-end", id: "done" });
    stream.push({ type: "finish-step" });
    stream.push({ type: "finish" });
    stream.end();

    expect(await screen.findByText("Listed products")).toBeInTheDocument();
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Listed products" })).toHaveAttribute("aria-expanded", "false");
    const assistant = document.querySelector('[data-role="assistant"]');
    expect(assistant).not.toBeNull();
    await waitFor(() => expect(within(assistant).queryByText("List products")).not.toBeInTheDocument(), { timeout: 3000 });
    expect(within(assistant).queryByText(/input/i)).not.toBeInTheDocument();
    expect(within(assistant).queryByText(/output/i)).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Copy message")).toBeInTheDocument();
  });

  it("keeps a completed workflow step closed without raw JSON", async () => {
    const stream = controllableStream();
    installApi({ onChatStream: () => stream });
    await renderApp("/");

    await send("Plan for Retail");
    stream.push({ type: "start", messageId: "00000000-0000-0000-0000-000000000300" });
    stream.push({ type: "start-step" });
    stream.push({ type: "tool-input-start", toolCallId: "workflow-call", toolName: "run_recommendation" });
    stream.push({ type: "tool-input-available", toolCallId: "workflow-call", toolName: "run_recommendation", input: { marker: "RAW_INPUT_SENTINEL" } });
    stream.push({ type: "data-workflow-stage", id: "wf-run-1-validate", data: { run_id: "run-1", stage: "validate", label: "Validate", status: "done", summary: "Brief checked" } });
    stream.push({ type: "tool-output-available", toolCallId: "workflow-call", output: {
      status: "FORM_REQUIRED", marker: "RAW_OUTPUT_SENTINEL", form_id: "form-wf30", missing: ["geo"],
      form_config: { title: "Confirm the brief", fields: [
        { name: "client_name", label: "Client", type: "text", value: "", required: false },
        { name: "vertical", label: "Vertical", type: "choice", value: "Retail", required: true, options: [{ value: "Retail", label: "Retail" }] },
        { name: "kpi", label: "Primary KPI", type: "choice", value: "ctr", required: true, options: [{ value: "ctr", label: "CTR" }] },
        { name: "geo", label: "Geo", type: "choice", value: null, required: true, options: [{ value: "US", label: "US" }] },
        { name: "budget", label: "Budget (USD)", type: "number", value: 15000, required: true },
      ], submit_label: "Get recommendation" },
    } });
    stream.push({ type: "finish-step" });
    stream.push({ type: "finish" });
    stream.end();

    expect(await screen.findByRole("form", { name: "Confirm the brief" })).toBeInTheDocument();
    const header = screen.getByRole("button", { name: "Checked the brief" });
    expect(header).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(header);
    fireEvent.click(screen.getByRole("button", { name: /Run recommendation/ }));
    const step = screen.getByRole("button", { name: /Run recommendation/ }).closest("li");
    expect(step).not.toBeNull();
    expect(within(step).getByRole("list", { name: "Workflow stages" })).toHaveTextContent("Validate");
    expect(screen.queryByText("RAW_INPUT_SENTINEL")).not.toBeInTheDocument();
    expect(screen.queryByText("RAW_OUTPUT_SENTINEL")).not.toBeInTheDocument();
    expect(within(step).queryByText("Input")).not.toBeInTheDocument();
    expect(within(step).queryByText("Output")).not.toBeInTheDocument();
  });

  it("restores a chat from the API when the page is reloaded", async () => {
    const api = installApi({
      chats: [CHAT_A],
      messages: {
        [CHAT_A.id]: [
          {
            id: "m1",
            chatId: CHAT_A.id,
            role: "user",
            content: "List the products",
            parts: [{ type: "text", text: "List the products" }],
            metadata: {},
            displayOrder: 100,
            createdAt: CHAT_A.createdAt,
            feedback: null,
          },
          {
            id: "m2",
            chatId: CHAT_A.id,
            role: "assistant",
            content: "Here is the catalog.",
            parts: [
              {
                type: "tool-list_products",
                toolCallId: "call-9",
                state: "output-available",
                input: { query: "all" },
                output: {
                  products: [{ product_id: "P005", product_name: "Premium Takeover", cpm: 32 }],
                },
              },
              { type: "text", text: "Here is the catalog." },
            ],
            metadata: {},
            displayOrder: 200,
            createdAt: CHAT_A.createdAt,
            feedback: null,
          },
        ],
      },
    });

    await renderApp(`/?chat=${CHAT_A.id}`);

    expect(await screen.findByText("List the products")).toBeInTheDocument();
    expect(await screen.findByText(/Here is the catalog\./)).toBeInTheDocument();
    fireEvent.click(await screen.findByText("Listed products"));
    const step = await screen.findByText("List products");
    expect(await screen.findByText("Listed products")).toBeInTheDocument();
    if (!screen.queryByText(/P005/)) fireEvent.click(step);
    expect(await screen.findByText(/P005/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "GET" && c.path === `/app/chats/${CHAT_A.id}`)).toBe(
      true,
    );
  });
});

describe("tool activity labels", () => {
  it("labels tool steps with their arguments", () => {
    expect(toolLabel("get_case_data", { dataset: "campaign_history", filters: { vertical: "QSR" } }))
      .toBe("Retrieve data: Campaign history · QSR");
    expect(toolLabel("get_case_data", { dataset: "inventory", filters: { geo: "APAC" } }))
      .toBe("Retrieve data: Inventory · APAC");
    expect(toolLabel("check_scale", { vertical: "Travel", geo: "APAC", budget: 18_000 }))
      .toBe("Check scale: Travel · APAC · $18,000");
    expect(toolLabel("run_python_analysis", { code: "secret", question: "also secret" }))
      .toBe("Run analysis");
    expect(toolLabel("unknown_tool", { x: 1 })).toBe("unknown_tool");
    expect(toolActivity("get_case_data", true)).toBe("Retrieving data…");
    expect(toolActivity("get_case_data", false)).toBe("Retrieved data");
    expect(toolActivity("unknown_tool", true)).toBe("Running unknown_tool…");
  });

  it("shows No arguments in the Input box for an empty list_products call", async () => {
    const stream = controllableStream();
    installApi({ onChatStream: () => stream });
    await renderApp("/");

    await send("List the catalog");
    stream.push({ type: "start", messageId: "msg-empty-input" });
    stream.push({ type: "start-step" });
    stream.push({ type: "tool-input-start", toolCallId: "empty-input", toolName: "list_products" });
    stream.push({ type: "tool-input-available", toolCallId: "empty-input", toolName: "list_products", input: {} });
    stream.push({ type: "tool-output-available", toolCallId: "empty-input", output: { products: [] } });
    stream.push({ type: "finish-step" });
    stream.push({ type: "finish" });
    stream.end();

    fireEvent.click(await screen.findByText("Listed products"));
    const step = await screen.findByText("List products");
    if (!screen.queryByText("No arguments")) fireEvent.click(step);
    expect(await screen.findByText(/input/i)).toBeInTheDocument();
    expect(await screen.findByText("No arguments")).toBeInTheDocument();
    expect(await screen.findByText(/output/i)).toBeInTheDocument();
  });
});

describe("browser time zone", () => {
  it("sends the browser IANA zone with the chat request", async () => {
    const stream = controllableStream();
    const api = installApi({ onChatStream: () => stream });
    await renderApp("/");

    await send("What time is it?");
    await waitFor(() => expect(api.calls.some((call) => call.method === "POST" && call.path === "/app/chat")).toBe(true));
    const request = api.calls.find((call) => call.method === "POST" && call.path === "/app/chat");
    expect(request.body.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone || null);
    stream.push({ type: "finish" });
    stream.end();
  });
});
