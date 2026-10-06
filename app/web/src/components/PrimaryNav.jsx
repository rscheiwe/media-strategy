import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { MessageSquarePlus, MoreHorizontal, Pencil, Pin, PinOff, Trash2 } from "lucide-react";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "./ui/sidebar";
import { cn } from "../lib/utils";
import { formatDate } from "../App/views/ChatsView/chatUtils";
import { Hint } from "@/components/ui/hint";

const COLLAPSE_TRANSITION = {
  duration: 0.22,
  ease: [0.22, 1, 0.36, 1],
};

function CollapsedChatsPrompt({ onExpand }) {
  return (
    <button
      type="button"
      className="group mx-2 mt-3 flex min-h-[150px] w-[calc(100%-1rem)] flex-col items-center justify-center rounded-md px-3 py-4 text-center text-muted-foreground transition-colors hover:bg-card/60 hover:text-foreground"
      onClick={onExpand}
    >
      <div className="relative h-24 w-40 opacity-65 transition-opacity group-hover:opacity-85">
        <div className="absolute left-5 top-1 h-20 w-28 -rotate-6 rounded-md border border-border bg-background/70 shadow-sm">
          <div className="absolute left-5 top-5 h-2 w-16 rounded-full bg-muted-foreground/20" />
          <div className="absolute left-5 top-10 h-2 w-10 rounded-full bg-muted-foreground/15" />
          <div className="absolute bottom-4 left-5 h-2 w-14 rounded-full bg-emerald-400/25" />
        </div>
        <div className="absolute left-10 top-8 h-14 w-24 rounded-md border border-border bg-background/90 shadow-sm">
          <div className="absolute left-4 top-4 h-2 w-12 rounded-full bg-muted-foreground/25" />
          <div className="absolute left-4 top-8 h-2 w-16 rounded-full bg-muted-foreground/15" />
        </div>
        <div className="absolute right-7 top-2 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-background text-[11px] font-semibold text-muted-foreground shadow-sm">
          3
        </div>
        <div className="absolute bottom-2 left-8 h-px w-24 bg-gradient-to-r from-transparent via-border to-transparent" />
      </div>
      <span className="mt-1 text-xs font-medium text-foreground/75">Expand to see chats</span>
    </button>
  );
}

export function PrimaryNav({
  disabled = false,
  chats = [],
  activeChatId = null,
  chatsLoading = false,
  chatsError = "",
  onNewChat,
  onSelectChat,
  onRenameChat,
  onDeleteChat,
  onTogglePin,
  // Top-level destinations: [{key, label, icon, active, onSelect}].
  navItems = [],
}) {
  const [chatsOpen, setChatsOpen] = useState(true);
  const [showCollapsedChatsPrompt, setShowCollapsedChatsPrompt] = useState(false);

  const pinnedChats = chats.filter((chat) => chat.pinnedAt);
  const otherChats = chats.filter((chat) => !chat.pinnedAt);

  function renderChatRow(chat) {
    const active = activeChatId === chat.id;
    return (
      <button
        key={chat.id}
        type="button"
        className={cn(
          "group/chat flex w-full items-start gap-2 px-2 py-2 text-left text-sm transition-colors",
          "rounded-md hover:bg-card hover:text-foreground",
          active ? "bg-card/65 text-foreground dark:bg-card/45" : "text-muted-foreground",
          disabled &&
            "cursor-not-allowed opacity-55 hover:bg-transparent hover:text-muted-foreground",
        )}
        onClick={() => {
          if (disabled) return;
          onSelectChat?.(chat.id);
        }}
        disabled={disabled}
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate font-normal text-inherit">
            {chat.title || "New chat"}
          </span>
          <span className="mt-0.5 block text-[11px] text-muted-foreground">
            {formatDate(chat.updatedAt)}
          </span>
        </span>
        {onDeleteChat || onRenameChat || onTogglePin ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <span
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-0 hover:bg-muted group-hover/chat:opacity-100"
                onClick={(event) => event.stopPropagation()}
              >
                <MoreHorizontal className="h-4 w-4" />
              </span>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {onTogglePin ? (
                <DropdownMenuItem
                  onClick={(event) => {
                    event.stopPropagation();
                    onTogglePin(chat);
                  }}
                >
                  {chat.pinnedAt ? (
                    <PinOff className="mr-2 h-4 w-4" />
                  ) : (
                    <Pin className="mr-2 h-4 w-4" />
                  )}
                  {chat.pinnedAt ? "Unpin" : "Pin"}
                </DropdownMenuItem>
              ) : null}
              {onRenameChat ? (
                <DropdownMenuItem
                  onClick={(event) => {
                    event.stopPropagation();
                    onRenameChat(chat);
                  }}
                >
                  <Pencil className="mr-2 h-4 w-4" />
                  Rename
                </DropdownMenuItem>
              ) : null}
              {onDeleteChat ? (
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteChat(chat);
                  }}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </button>
    );
  }

  return (
    <Sidebar collapsible="icon" className="border-r-0 text-foreground">
      <SidebarHeader className="px-3 py-3">
        <div className="flex items-center gap-2">
          <img src="/brand-mark.svg" alt="" className="h-8 w-8 shrink-0 dark:invert" />
          <div className="overflow-hidden transition-[max-width,opacity] duration-200 ease-linear group-data-[collapsible=icon]:max-w-0 group-data-[collapsible=icon]:opacity-0">
            <span className="text-lg font-[500] tracking-tight text-foreground">Media Planner</span>
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent className="overflow-hidden px-2 pb-0 pt-3">
        <SidebarGroup className="mb-2 px-0">
          <SidebarMenu>
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <SidebarMenuItem key={item.key}>
                  <SidebarMenuButton
                    onClick={() => {
                      if (disabled) return;
                      item.onSelect?.();
                    }}
                    isActive={item.active}
                    tooltip={item.label}
                    className={cn(
                      "border-l-2 rounded-r-md rounded-l-none px-2 py-2 text-left text-sm group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:rounded-md group-data-[collapsible=icon]:border-l-0 group-data-[collapsible=icon]:px-0",
                      item.active
                        ? "border-emerald-500 bg-card text-foreground"
                        : "border-transparent text-muted-foreground hover:bg-card hover:text-foreground",
                      disabled &&
                        "cursor-not-allowed opacity-55 hover:bg-transparent hover:text-muted-foreground",
                    )}
                    aria-disabled={disabled}
                  >
                    <Icon className="h-4 w-4" />
                    <span className="max-w-[120px] truncate whitespace-nowrap transition-[max-width,opacity,margin] duration-200 ease-linear group-data-[collapsible=icon]:ml-0 group-data-[collapsible=icon]:max-w-0 group-data-[collapsible=icon]:opacity-0">
                      {item.label}
                    </span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup className="flex min-h-0 flex-1 flex-col px-0 group-data-[collapsible=icon]:hidden">
          <div className="mb-1 flex items-center justify-between px-2">
            <button
              type="button"
              className="flex h-8 flex-1 items-center rounded-md pr-1 text-left text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground transition-colors hover:text-foreground"
              onClick={() => {
                setShowCollapsedChatsPrompt(false);
                setChatsOpen((value) => !value);
              }}
              aria-expanded={chatsOpen}
              aria-controls="primary-nav-chats"
            >
              <span>Chats</span>
            </button>
            {chatsOpen ? (
              <Hint label={"New chat"} wrap side="right">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-foreground"
                  onClick={() => {
                    if (disabled) return;
                    onNewChat?.();
                  }}
                  aria-label="New chat"
                  disabled={disabled}
                >
                  <MessageSquarePlus className="h-4 w-4" />
                </Button>
              </Hint>
            ) : null}
          </div>
          <AnimatePresence
            initial={false}
            onExitComplete={() => {
              if (!chatsOpen) setShowCollapsedChatsPrompt(true);
            }}
          >
            {chatsOpen ? (
              <motion.div
                key="chats-content"
                initial={{ height: 0, opacity: 0, y: -4 }}
                animate={{ height: "auto", opacity: 1, y: 0 }}
                exit={{ height: 0, opacity: 0, y: -4 }}
                transition={COLLAPSE_TRANSITION}
                className="min-h-0 flex-1 overflow-hidden"
              >
                <SidebarGroupContent id="primary-nav-chats" className="relative h-full min-h-0">
                  <div className="h-full space-y-1 overflow-y-auto pb-8 pr-1">
                    {chatsError ? (
                      <div className="mx-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                        {chatsError}
                      </div>
                    ) : null}
                    {chatsLoading && chats.length === 0 ? (
                      <div className="px-2 py-2 text-xs text-muted-foreground">
                        Loading chats...
                      </div>
                    ) : null}
                    {!chatsLoading && chats.length === 0 ? (
                      <div className="px-2 py-2 text-xs text-muted-foreground">No chats yet.</div>
                    ) : null}
                    {pinnedChats.length ? (
                      <>
                        <div className="flex items-center gap-1.5 px-2 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                          <Pin className="h-3 w-3" />
                          Pinned
                        </div>
                        {pinnedChats.map(renderChatRow)}
                        {otherChats.length ? <div className="mx-2 my-2 h-px bg-border" /> : null}
                      </>
                    ) : null}
                    {otherChats.map(renderChatRow)}
                  </div>
                  <div
                    aria-hidden="true"
                    className="pointer-events-none absolute bottom-0 left-0 right-3 z-10 h-10"
                    style={{
                      background:
                        "linear-gradient(to bottom, hsl(var(--muted) / 0), hsl(var(--muted) / 0.5) 72%, hsl(var(--muted) / 0.5))",
                    }}
                  />
                </SidebarGroupContent>
              </motion.div>
            ) : null}
          </AnimatePresence>
          {!chatsOpen && showCollapsedChatsPrompt ? (
            <motion.div
              key="chats-collapsed-prompt"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.16, ease: [0.4, 0, 0.2, 1] }}
              className="overflow-hidden"
            >
              <CollapsedChatsPrompt
                onExpand={() => {
                  setShowCollapsedChatsPrompt(false);
                  setChatsOpen(true);
                }}
              />
            </motion.div>
          ) : null}
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
