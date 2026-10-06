// Acceptance: a message's form and recommendation card render right after the workflow
// step that produced them, so text the turn adds afterwards (the follow-up question) reads
// below the card. Uses the real Acme example chat the API seeds (storage/example_chats.json).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import examples from "../../../../../api/src/chat_api/storage/example_chats.json";
import { installDomShims } from "@/test/acceptance";
import { ChatsAssistantParts } from "./ChatsAssistantParts";

beforeEach(() => installDomShims());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function acmeAssistant() {
  const acme = examples.find((e) => e.chat.title.startsWith("Acme Shoes"));
  const row = acme.messages.find((m) => m.role === "assistant");
  return { id: row.id, role: "assistant", parts: JSON.parse(row.parts_json) };
}

const precedes = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("workflow output order", () => {
  it("renders the card after the workflow step and before the follow-up question", () => {
    const message = acmeAssistant();
    const texts = message.parts.filter((p) => p.type === "text").map((p) => p.text);
    expect(texts.length).toBeGreaterThanOrEqual(2);
    const [lead, followUp] = [texts[0], texts.at(-1)];
    expect(followUp).toMatch(/^Would you like/);

    render(<ChatsAssistantParts message={message} inFlight={false} />);
    const card = screen.getByRole("region", { name: "Recommendation" });
    const leadNode = screen.getByText(lead);
    const followNode = screen.getByText(followUp);
    expect(precedes(leadNode, card)).toBe(true);
    expect(precedes(card, followNode)).toBe(true);
  });

  it("renders a card at the end when the message has no workflow step", () => {
    const message = acmeAssistant();
    const parts = message.parts.filter((p) => p.type !== "tool-run_recommendation");
    render(<ChatsAssistantParts message={{ ...message, parts }} inFlight={false} />);
    const card = screen.getByRole("region", { name: "Recommendation" });
    const lastText = screen.getByText(parts.filter((p) => p.type === "text").at(-1).text);
    expect(precedes(lastText, card)).toBe(true);
  });
});
