/**
 * Turning a failed chat request into something a reader can act on.
 *
 * The AI SDK"s transport does this on a non-2xx response (ai/dist/index.mjs):
 *
 *     if (!response.ok) throw new Error(await response.text() ?? "Failed to fetch…");
 *
 * The body becomes the message and the STATUS IS DISCARDED. So a refused
 * request arrived as a bare Error whose message was the literal API body, and
 * the conversation rendered `{"detail": ...}` at the reader — which says
 * nothing about what happened or what to do, and looks like the assistant said it.
 *
 * `createChatFetch` intercepts the response before the SDK sees it, so the
 * status survives and the UI can distinguish "the request was refused" from "the server
 * broke" from "you're offline". Nothing here parses the body for meaning beyond
 * pulling out FastAPI"s `detail`, because a body is not a contract.
 */

// There is no login in this app, so a 401/403 means a proxy or gateway refused the
// request; reloading is still the useful next step.
export const REQUEST_REFUSED_MESSAGE =
  "The chat service refused this request. Reload the page and try again.";

export class ChatRequestError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = "ChatRequestError";
    this.status = status;
    this.body = body;
  }
}

/**
 * A `fetch` for `DefaultChatTransport` that preserves the HTTP status.
 *
 * Throwing here rather than letting the SDK throw is deliberate: the SDK"s own
 * error carries no status, and `onError` is the only place the failure surfaces.
 */
export function createChatFetch(fetchImpl) {
  return async (input, init) => {
    const doFetch = fetchImpl || globalThis.fetch;
    const response = await doFetch(input, init);
    if (response.ok) return response;

    // Read the body for diagnostics, but never let a failure to read it mask
    // the status we actually care about.
    let body = "";
    try {
      body = await response.text();
    } catch {
      body = "";
    }

    throw new ChatRequestError(messageForStatus(response.status, body), {
      status: response.status,
      body,
    });
  };
}

/** FastAPI puts the human-readable reason in `detail`. */
function detailFromBody(body) {
  if (!body || typeof body !== "string") return null;
  const trimmed = body.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    // Plain text, but guard against a whole HTML error page landing in the UI.
    return trimmed.length > 0 && trimmed.length <= 200 && !/^</.test(trimmed) ? trimmed : null;
  }
  try {
    const parsed = JSON.parse(trimmed);
    const detail = parsed?.detail ?? parsed?.message ?? parsed?.error;
    return typeof detail === "string" && detail.trim() ? detail.trim() : null;
  } catch {
    return null;
  }
}

function messageForStatus(status, body) {
  if (status === 401 || status === 403) return REQUEST_REFUSED_MESSAGE;
  if (status === 429) {
    return "Too many requests right now. Wait a moment and try again.";
  }
  if (status >= 500) {
    return "The server hit an error handling that. Try again in a moment.";
  }

  const detail = detailFromBody(body);
  if (detail) return detail;
  return `That request failed (HTTP ${status}).`;
}

/**
 * Whether a proxy or gateway refused the request (401/403).
 *
 * Status only: there is no login here, so there is no session message to match.
 */
export function isRequestRefused(error) {
  const status = error?.status;
  return status === 401 || status === 403;
}

/** What to show the reader for a failed chat turn. Never a raw response body. */
export function describeChatError(error) {
  if (!error) return null;
  if (isRequestRefused(error)) return REQUEST_REFUSED_MESSAGE;

  if (error instanceof ChatRequestError || typeof error?.status === "number") {
    return messageForStatus(error.status, error.body);
  }

  const message = (typeof error === "string" ? error : error?.message) || "Something went wrong.";

  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return "Couldn’t reach the server. Check your connection and try again.";
  }

  // A message that is really a response body — the pre-`createChatFetch` shape,
  // and what put `{"detail":"…"}` on screen.
  const detail = detailFromBody(message);
  if (detail) return detail;

  return message;
}
