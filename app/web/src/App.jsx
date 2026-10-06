import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { MessageSquarePlus } from "lucide-react";

import { AppShell } from "./components/AppShell";
import { PrimaryNav } from "./components/PrimaryNav";
import { MainHeader } from "./components/MainHeader";
import { ChatsView } from "./App/views/ChatsView";
import { DeleteChatDialog } from "./App/views/ChatsView/DeleteChatDialog";
import { RenameChatDialog } from "./App/views/ChatsView/RenameChatDialog";
import { deleteChat, listChats, setChatPinned, updateChat } from "./api";
import { useTheme } from "./hooks/use-theme";

const CHAT_PAGE_SIZE = 50;

/** Chat preferences. There is no settings page; these are the fixed defaults. */
const CHAT_SETTINGS = {
  sendOnEnter: true,
  defaultThinking: false,
  thinkingEffort: "high",
  defaultAgentUuid: null,
};

/**
 * The chat workspace shell: the chat list and its CRUD (incl. pinning), and the
 * AppShell (PrimaryNav + MainHeader) around ChatsView. There is no login.
 * The active thread lives in the `?chat=<id>` query param, which ChatsView
 * reads and writes.
 */
export default function App() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeChatId = searchParams.get("chat") || null;
  const { resolvedTheme, toggleTheme } = useTheme();

  const [chats, setChats] = useState([]);
  const [chatsLoading, setChatsLoading] = useState(false);
  const [chatsError, setChatsError] = useState("");
  const [deleteChatTarget, setDeleteChatTarget] = useState(null);
  const [deletingChat, setDeletingChat] = useState(false);
  const [renameChatTarget, setRenameChatTarget] = useState(null);
  const [renamingChat, setRenamingChat] = useState(false);

  const refreshChats = useCallback(async () => {
    setChatsLoading(true);
    setChatsError("");
    try {
      const payload = await listChats({ limit: CHAT_PAGE_SIZE });
      setChats(Array.isArray(payload?.chats) ? payload.chats : []);
    } catch (err) {
      setChatsError(err?.payload?.detail || err?.message || "Failed to load chats.");
    } finally {
      setChatsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshChats();
  }, [refreshChats]);

  const setActiveChat = useCallback(
    (chatId) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (chatId) next.set("chat", chatId);
        else next.delete("chat");
        return next;
      });
    },
    [setSearchParams],
  );

  function handleNewChat() {
    setActiveChat(null);
  }

  function handleSelectChat(chatId) {
    setActiveChat(chatId);
  }

  async function handleConfirmDeleteChat() {
    if (!deleteChatTarget) return;
    const chat = deleteChatTarget;
    setDeletingChat(true);
    try {
      await deleteChat(chat.id);
      setChats((prev) => prev.filter((item) => item.id !== chat.id));
      if (activeChatId === chat.id) {
        handleNewChat();
      }
      setDeleteChatTarget(null);
      toast.success("Chat deleted");
      void refreshChats();
    } catch (err) {
      toast.error(err?.payload?.detail || err?.message || "Failed to delete chat.");
    } finally {
      setDeletingChat(false);
    }
  }

  async function handleConfirmRenameChat(title) {
    if (!renameChatTarget) return;
    const chat = renameChatTarget;
    setRenamingChat(true);
    try {
      const payload = await updateChat(chat.id, { title });
      const nextTitle = payload?.chat?.title || title;
      setChats((prev) =>
        prev.map((item) => (item.id === chat.id ? { ...item, title: nextTitle } : item)),
      );
      setRenameChatTarget(null);
    } catch (err) {
      toast.error(err?.payload?.detail || err?.message || "Failed to rename chat.");
    } finally {
      setRenamingChat(false);
    }
  }

  async function handleTogglePin(chat) {
    const pinned = !chat.pinnedAt;
    // Optimistic: move it now, reconcile with the server's ordering after.
    setChats((prev) =>
      prev.map((item) =>
        item.id === chat.id
          ? { ...item, pinnedAt: pinned ? new Date().toISOString() : null }
          : item,
      ),
    );
    try {
      await setChatPinned(chat.id, pinned);
    } catch (err) {
      toast.error(err?.payload?.detail || err?.message || "Failed to update chat.");
    } finally {
      void refreshChats();
    }
  }

  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const headerTitle = activeChatId ? activeChat?.title : "New chat";

  return (
    <AppShell
      contentOverflow="hidden"
      contentPadding="p-0"
      primaryNav={
        <PrimaryNav
          chats={chats}
          activeChatId={activeChatId}
          chatsLoading={chatsLoading}
          chatsError={chatsError}
          onNewChat={handleNewChat}
          onSelectChat={handleSelectChat}
          onRenameChat={setRenameChatTarget}
          onDeleteChat={setDeleteChatTarget}
          onTogglePin={(chat) => void handleTogglePin(chat)}
          navItems={[
            {
              key: "new-chat",
              label: "New chat",
              icon: MessageSquarePlus,
              active: !activeChatId,
              onSelect: handleNewChat,
            },
          ]}
        />
      }
      header={<MainHeader title={headerTitle} theme={resolvedTheme} onToggleTheme={toggleTheme} />}
    >
      <ChatsView setChats={setChats} refreshChats={refreshChats} settings={CHAT_SETTINGS} />
      <DeleteChatDialog
        chat={deleteChatTarget}
        deleting={deletingChat}
        onCancel={() => setDeleteChatTarget(null)}
        onConfirm={() => void handleConfirmDeleteChat()}
      />
      <RenameChatDialog
        chat={renameChatTarget}
        saving={renamingChat}
        onCancel={() => setRenameChatTarget(null)}
        onConfirm={(title) => void handleConfirmRenameChat(title)}
      />
    </AppShell>
  );
}
