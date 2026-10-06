// Chat list and controls acceptance tests.
// "In the UI, the user can start a new chat, see the chat list, rename, pin and delete a
// chat, and stop a streaming reply."
// The real app renders; only the HTTP API is faked (src/test/acceptance.js).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import {
  CHAT_A,
  CHAT_B,
  controllableStream,
  installApi,
  installDomShims,
  renderApp,
} from "@/test/acceptance";

beforeEach(() => installDomShims());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Open the Radix dropdown menu on a chat row and click one of its items. */
async function chooseFromChatMenu(title, item) {
  const label = await screen.findByText(title);
  const row = label.closest("li") || label.closest("button") || label.parentElement;
  const trigger = row.querySelector('[aria-haspopup="menu"]');
  expect(trigger, `no menu trigger on the "${title}" row`).not.toBeNull();
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: "mouse" });
  if (!screen.queryByRole("menu")) fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

describe("chat list and controls", () => {
  it("shows the user's chats from the API in the sidebar", async () => {
    installApi({ chats: [CHAT_A, CHAT_B] });
    await renderApp("/");

    expect(await screen.findByText(CHAT_A.title)).toBeInTheDocument();
    expect(screen.getByText(CHAT_B.title)).toBeInTheDocument();
  });

  it("starts a new chat and clears the open conversation", async () => {
    installApi({
      chats: [CHAT_A],
      messages: {
        [CHAT_A.id]: [
          {
            id: "m1",
            chatId: CHAT_A.id,
            role: "user",
            content: "Old question",
            parts: [{ type: "text", text: "Old question" }],
            metadata: {},
            displayOrder: 100,
            createdAt: CHAT_A.createdAt,
            feedback: null,
          },
        ],
      },
    });
    await renderApp(`/?chat=${CHAT_A.id}`);
    expect(await screen.findByText("Old question")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: /new chat/i })[0]);

    await waitFor(() => expect(screen.queryByText("Old question")).not.toBeInTheDocument());
    expect(new URLSearchParams(window.location.search).get("chat")).toBeNull();
  });

  it("renames a chat", async () => {
    const api = installApi({ chats: [CHAT_A, CHAT_B] });
    await renderApp("/");

    await chooseFromChatMenu(CHAT_A.title, /rename/i);
    const input = await screen.findByLabelText(/chat title/i);
    fireEvent.change(input, { target: { value: "Q3 Retail plan" } });
    const dialog = input.closest('[role="dialog"]') || document.body;
    fireEvent.click(within(dialog).getByRole("button", { name: /^(save|rename)$/i }));

    await waitFor(() =>
      expect(api.calls).toContainEqual(
        expect.objectContaining({
          method: "PATCH",
          path: `/app/chats/${CHAT_A.id}`,
          body: { title: "Q3 Retail plan" },
        }),
      ),
    );
    expect(await screen.findByText("Q3 Retail plan")).toBeInTheDocument();
  });

  it("pins a chat", async () => {
    const api = installApi({ chats: [CHAT_A, CHAT_B] });
    await renderApp("/");

    await chooseFromChatMenu(CHAT_B.title, /^pin$/i);

    await waitFor(() =>
      expect(api.calls).toContainEqual(
        expect.objectContaining({
          method: "PATCH",
          path: `/app/chats/${CHAT_B.id}`,
          body: { pinned: true },
        }),
      ),
    );
  });

  it("deletes a chat after confirmation", async () => {
    const api = installApi({ chats: [CHAT_A, CHAT_B] });
    await renderApp("/");

    await chooseFromChatMenu(CHAT_A.title, /delete/i);
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false); // nothing deleted before confirming
    const confirm = (await screen.findAllByRole("button", { name: /^delete$/i })).at(-1);
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(api.calls).toContainEqual(
        expect.objectContaining({ method: "DELETE", path: `/app/chats/${CHAT_A.id}` }),
      ),
    );
    await waitFor(() => expect(screen.queryByText(CHAT_A.title)).not.toBeInTheDocument());
    expect(screen.getByText(CHAT_B.title)).toBeInTheDocument();
  });

  it("stops a streaming reply", async () => {
    const stream = controllableStream();
    const api = installApi({ onChatStream: () => stream });
    await renderApp("/");

    const box = await screen.findByPlaceholderText(/Ask anything/);
    fireEvent.change(box, { target: { value: "Write a long plan" } });
    fireEvent.keyDown(box, { key: "Enter", code: "Enter" });
    stream.push({ type: "start", messageId: "msg-3" });
    stream.push({ type: "start-step" });
    stream.push({ type: "text-start", id: "t1" });
    stream.push({ type: "text-delta", id: "t1", delta: "Step one of the plan" });
    expect(await screen.findByText(/Step one of the plan/)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /stop generating/i }));

    const chatRequest = api.calls.find((c) => c.method === "POST" && c.path === "/app/chat");
    await waitFor(() => expect(chatRequest.signal?.aborted).toBe(true));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /stop generating/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByText(/Step one of the plan/)).toBeInTheDocument(); // the partial answer stays
  });
});
