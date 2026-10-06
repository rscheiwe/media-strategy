import { describe, expect, it } from "vitest";
import { extractChatMetadata, messageToText, toUiMessage } from "./chatUtils";

describe("chatUtils", () => {
  it("turns text and quote parts into the request content", () => {
    expect(
      messageToText({
        parts: [
          { type: "quote", text: "source" },
          { type: "text", text: "Answer this" },
        ],
      }),
    ).toContain("Answer this");
  });

  it("normalizes persisted messages and stream metadata", () => {
    expect(toUiMessage({ id: "1", role: "assistant", content: "Hi" }).parts).toEqual([
      { type: "text", text: "Hi" },
    ]);
    expect(extractChatMetadata({ type: "data-chat-metadata", data: { chatId: "abc" } })).toEqual({
      chatId: "abc",
    });
  });
});
