// Acceptance-test helpers. Everything is faked at the HTTP
// boundary: a fetch stub that serves the chat API contract, including a controllable
// AI SDK UI message stream for POST /app/chat. The app under test is real.
import { vi } from "vitest";
import { render } from "@testing-library/react";

export const CHAT_A = {
  id: "chat-a",
  userId: "u1",
  title: "Acme Shoes plan",
  agentUuid: "3b9c6f0e-6a3e-4f43-9a51-2f7e6d1c8a10",
  isArchived: false,
  pinnedAt: null,
  createdAt: "2026-10-04T10:00:00Z",
  updatedAt: "2026-10-04T10:00:00Z",
};
export const CHAT_B = {
  id: "chat-b",
  userId: "u1",
  title: "Stellar Bank plan",
  agentUuid: "3b9c6f0e-6a3e-4f43-9a51-2f7e6d1c8a10",
  isArchived: false,
  pinnedAt: null,
  createdAt: "2026-10-03T10:00:00Z",
  updatedAt: "2026-10-03T10:00:00Z",
};

/** jsdom gaps Radix/Sidebar/theme code relies on. */
export function installDomShims() {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  window.matchMedia ??= (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() {
      return false;
    },
  });
  Element.prototype.scrollIntoView ??= function () {};
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.releasePointerCapture ??= () => {};
  if (!window.crypto?.randomUUID) {
    let n = 0;
    Object.defineProperty(window, "crypto", {
      value: { ...(window.crypto || {}), randomUUID: () => `uuid-${++n}` },
    });
  }
}

/** A stream the test feeds frame by frame: push(chunk) / end(). */
export function controllableStream() {
  let controller;
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(c) {
      controller = c;
    },
  });
  return {
    body,
    push(chunk) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`));
    },
    end() {
      controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      controller.close();
    },
  };
}

const json = (data, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });

/**
 * Fake chat API. `state.chats` and `state.messages[chatId]` back the CRUD routes;
 * `onChatStream(body, init)` returns a controllableStream for POST /app/chat.
 * Every request is recorded in `calls` as {method, path, body, signal}.
 */
export function installApi({ chats = [], messages = {}, onChatStream } = {}) {
  const state = { chats: chats.map((c) => ({ ...c })), messages: { ...messages } };
  const calls = [];
  const fetchStub = vi.fn(async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, "http://localhost");
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method, path: url.pathname, body, signal: init.signal });
    const chatMatch = url.pathname.match(/^\/app\/chats\/([^/]+)$/);
    if (url.pathname === "/app/chats" && method === "GET") return json({ chats: state.chats });
    if (url.pathname === "/app/chats" && method === "POST") {
      const chat = {
        ...CHAT_A,
        id: `chat-new-${state.chats.length + 1}`,
        title: body?.title || "New chat",
      };
      state.chats.unshift(chat);
      state.messages[chat.id] = [];
      return json({ chat });
    }
    if (chatMatch && method === "GET") {
      const chat = state.chats.find((c) => c.id === chatMatch[1]);
      return chat
        ? json({ chat, messages: state.messages[chat.id] || [] })
        : json({ detail: "Chat not found" }, 404);
    }
    if (chatMatch && method === "PATCH") {
      const chat = state.chats.find((c) => c.id === chatMatch[1]);
      if (body.title !== undefined) chat.title = body.title;
      if (body.pinned !== undefined) chat.pinnedAt = body.pinned ? "2026-10-04T12:00:00Z" : null;
      return json({ chat: { ...chat } });
    }
    if (chatMatch && method === "DELETE") {
      const chat = state.chats.find((c) => c.id === chatMatch[1]);
      state.chats = state.chats.filter((c) => c.id !== chatMatch[1]);
      return json({ deleted: true, chat });
    }
    if (url.pathname === "/app/chat" && method === "POST") {
      const stream = onChatStream?.(body, init);
      if (!stream) throw new Error("test did not expect POST /app/chat");
      return new Response(stream.body, {
        status: 200,
        headers: { "content-type": "text/event-stream", "x-vercel-ai-ui-message-stream": "v1" },
      });
    }
    if (/\/feedback$/.test(url.pathname))
      return json({ feedback: body ?? null }, method === "DELETE" ? 204 : 200);
    if (url.pathname === "/app/settings") return json({ settings: {} });
    throw new Error(`unexpected request ${method} ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchStub);
  return { state, calls, fetch: fetchStub };
}

/** Render the real app the way main.jsx does, at `path` (e.g. "/?chat=chat-a"). */
export async function renderApp(path = "/") {
  window.history.replaceState({}, "", path);
  vi.resetModules();
  const { AppRouter } = await import("@/router");
  return render(<AppRouter />);
}

/** Assistant UI-stream frames for a plain text reply. */
export function textReply(stream, messageId, text) {
  stream.push({ type: "start", messageId });
  stream.push({ type: "start-step" });
  stream.push({ type: "text-start", id: "t1" });
  stream.push({ type: "text-delta", id: "t1", delta: text });
  stream.push({ type: "text-end", id: "t1" });
  stream.push({ type: "finish-step" });
  stream.push({ type: "finish" });
}
