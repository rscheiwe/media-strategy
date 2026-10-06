const DEFAULT_TIMEOUT_MS = 12000;

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

// The API's origin. Unset or empty means same-origin: nginx (docker) and the Vite dev
// server both proxy /app and /health to the API.
function baseUrl() {
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

export function getApiBaseUrl() {
  return baseUrl();
}

function apiErrorMessage(status, statusText, payload) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => item?.msg || item?.message || String(item))
      .filter(Boolean)
      .join("; ");
  }
  if (typeof payload?.message === "string" && payload.message.trim()) {
    return payload.message;
  }
  if (typeof payload?.error === "string" && payload.error.trim()) {
    return payload.error;
  }
  return `API ${status} ${statusText}`;
}

/**
 * Cap in-flight requests below the server's DB pool.
 *
 * Every request holds a Postgres connection for its whole duration, so firing more of them at once
 * than the pool has connections does not go faster — it goes dramatically slower. Measured against
 * this server (pool of 5): 8 requests at once took 26.6s and one 503'd, while the same 8 at a
 * concurrency of 3 finished in 1.9s.
 *
 * Queueing here rather than at each call site means every caller benefits and no view has to know
 * about the pool. Keep this below APP_DB_POOL_MAX.
 */
const MAX_IN_FLIGHT_REQUESTS = 4;
let inFlightRequests = 0;
const requestQueue = [];

function acquireRequestSlot() {
  if (inFlightRequests < MAX_IN_FLIGHT_REQUESTS) {
    inFlightRequests += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => requestQueue.push(resolve));
}

function releaseRequestSlot() {
  const next = requestQueue.shift();
  if (next) {
    next();
    return;
  }
  inFlightRequests = Math.max(0, inFlightRequests - 1);
}

async function fetchJson(path, options = {}) {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options;
  const method = String(options.method || "GET").toUpperCase();
  const hasBody = options.body !== undefined && options.body !== null;

  // Wait for a slot BEFORE starting the timeout clock — otherwise time spent queued counts
  // against the request's own budget and queueing would cause the very timeouts it prevents.
  await acquireRequestSlot();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${baseUrl()}${path}`, {
      ...fetchOptions,
      headers: {
        ...(hasBody && method !== "GET" && method !== "HEAD"
          ? { "content-type": "application/json" }
          : {}),
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });
    let payload = null;
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }
    if (!res.ok) {
      throw new ApiError(apiErrorMessage(res.status, res.statusText, payload), res.status, payload);
    }
    return payload;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new ApiError("Request timed out", 0, null);
    }
    if (err instanceof ApiError) {
      throw err;
    }
    throw new ApiError(err.message || "Network error", 0, null);
  } finally {
    clearTimeout(timeout);
    releaseRequestSlot();
  }
}

export async function listChats({ limit = 20, offset = 0 } = {}) {
  const query = new URLSearchParams();
  if (limit) query.set("limit", String(limit));
  if (offset) query.set("offset", String(offset));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return fetchJson(`/app/chats${suffix}`);
}

export async function createChat({ title, agentUuid } = {}) {
  return fetchJson("/app/chats", {
    method: "POST",
    body: JSON.stringify({
      title: title || "New chat",
      agent_uuid: agentUuid || null,
    }),
  });
}

export async function getChat(chatId) {
  return fetchJson(`/app/chats/${encodeURIComponent(chatId)}`);
}

export async function updateChat(chatId, patch = {}) {
  return fetchJson(`/app/chats/${encodeURIComponent(chatId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function deleteChat(chatId) {
  return fetchJson(`/app/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
}

export async function setChatPinned(chatId, pinned) {
  return updateChat(chatId, { pinned: Boolean(pinned) });
}

/** Thumbs up (1) / down (-1) on an assistant message, with an optional comment. */
export async function setMessageFeedback(chatId, messageId, { rating, comment } = {}) {
  return fetchJson(
    `/app/chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "PUT",
      body: JSON.stringify({ rating, comment: comment || null }),
    },
  );
}

export async function clearMessageFeedback(chatId, messageId) {
  return fetchJson(
    `/app/chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    { method: "DELETE" },
  );
}

/** The caller's preferences: `{settings: {theme, defaultThinking, thinkingEffort, ...}}`. */
