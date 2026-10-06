// Fallback only — the per-agent suggestions in constants/chatAgents.js are what the UI shows.
export const EMPTY_SUGGESTIONS = [
  "Which ad products are there, and what do they cost?",
  "Calculate the average CTR and in-view rate for each vertical",
  "Which product is cheapest per thousand impressions?",
  "What would suit a Retail brand optimizing for clicks?",
];

export function messageToText(message) {
  const parts = Array.isArray(message.parts) ? message.parts : [];
  const fromParts = parts
    .map((part) => {
      if (part?.type === "text" && typeof part.text === "string") return part.text;
      if (part?.type === "quote" && typeof part.text === "string") {
        const quotedLines = part.text
          .split(/\r?\n/)
          .map((line) => `> ${line}`)
          .join("\n");
        return `Replying to selected text:\n${quotedLines}`;
      }
      return "";
    })
    .join("");
  return fromParts || message.content || "";
}

export function createLocalId(prefix) {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}_${crypto.randomUUID()}`;
  }
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function isActiveToolPart(part) {
  return (
    part?.type?.startsWith?.("tool-") &&
    (part.state === "input-streaming" || part.state === "input-available")
  );
}

function markLatestActiveToolAsError(parts, errorText) {
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    const part = parts[index];
    if (!isActiveToolPart(part)) continue;
    part.state = "output-error";
    part.errorText = errorText;
    return true;
  }
  return false;
}

export function markActiveToolError(messages, error) {
  const errorText = error?.errorText || error?.message || String(error || "Tool failed");
  let patched = false;

  const nextMessages = [...(messages || [])];
  for (let messageIndex = nextMessages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = nextMessages[messageIndex];
    if (message?.role !== "assistant" || !Array.isArray(message.parts)) {
      continue;
    }
    const parts = message.parts.map((part) => ({ ...part }));
    if (!markLatestActiveToolAsError(parts, errorText)) continue;
    nextMessages[messageIndex] = { ...message, parts };
    patched = true;
    break;
  }

  return patched ? nextMessages : messages;
}

export function toUiMessage(message) {
  const content = message.content || "";
  const parts =
    Array.isArray(message.parts) && message.parts.length
      ? message.parts
      : [{ type: "text", text: content }];
  return {
    id: message.id,
    role: message.role,
    parts,
    content,
    // Kept for workflow UI: a user message's formSubmission shows the form it answered.
    metadata: message.metadata || {},
  };
}

export function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

export function extractChatMetadata(data) {
  if (!data) return null;
  if (data.type === "data-chat-metadata") return data.data || data;
  if (data.chatId) return data;
  if (data.data?.chatId) return data.data;
  return null;
}

export function hasAssistantTextForCurrentTurn(messages) {
  const latestUserMessageIndex = messages.reduce(
    (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
    -1,
  );
  return messages
    .slice(latestUserMessageIndex + 1)
    .some((message) => message.role === "assistant" && messageToText(message).trim().length > 0);
}
