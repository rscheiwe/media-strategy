import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";

import { QuoteIcon } from "lucide-react";

import { BrandMark } from "@/components/icons/BrandMark";
import { LoaderIcon } from "@/components/icons/LoaderIcon";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { cn } from "@/lib/utils";

import { ChatErrorNotice } from "@/components/ai-elements/chat-error";
import { AssistantMessageParts } from "./AssistantMessageParts";
import { ChatMessagesSkeleton } from "./ChatMessagesSkeleton";
import { ChatMessageRail } from "./ChatMessageRail";
import { agentIntro } from "@/constants/chatAgents";
import { messageToText } from "./chatUtils";

const PIN_SPACER_OFFSET_PX = 40;

const StatusIcon = memo(function StatusIcon({ active }) {
  return (
    <span className="relative inline-flex h-5 w-5 items-center justify-center">
      <LoaderIcon
        className={cn(
          "absolute inset-0 h-5 w-5 transition-opacity duration-200",
          active ? "opacity-100" : "opacity-0",
        )}
        idleColor="#e5e7eb"
        activeColor="#7b828c"
      />
      <BrandMark
        className={cn(
          "absolute inset-0 h-5 w-5 opacity-70 transition-opacity duration-200",
          active ? "opacity-0" : "opacity-70",
        )}
      />
    </span>
  );
});

function rangeIntersectsElement(range, element) {
  try {
    return range.intersectsNode(element);
  } catch {
    return false;
  }
}

function selectionIntersectsAssistantMessage(range, container) {
  return Array.from(container.querySelectorAll('[data-role="assistant"]')).some((element) =>
    rangeIntersectsElement(range, element),
  );
}

const UserMessageParts = memo(function UserMessageParts({ message, bubbleClassName }) {
  const parts =
    Array.isArray(message.parts) && message.parts.length
      ? message.parts
      : [{ type: "text", text: message.content || "" }];
  const quoteParts = parts.filter((part) => part?.type === "quote" && part.text);
  const text = parts
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("");

  return (
    <div className="ml-auto flex max-w-[78%] flex-col items-end gap-2">
      {quoteParts.map((part, index) => (
        <div
          key={`quote-${index}`}
          className="flex max-w-xs items-start gap-2 rounded-lg border bg-card p-2.5 text-xs text-card-foreground shadow-sm"
        >
          <div className="mt-0.5 h-14 w-1 shrink-0 rounded-full bg-border" />
          <QuoteIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <p className="line-clamp-4 min-w-0 leading-snug text-muted-foreground">{part.text}</p>
        </div>
      ))}
      {text ? <MessageContent className={bubbleClassName}>{text}</MessageContent> : null}
    </div>
  );
});

export const ChatConversation = memo(function ChatConversation({
  messages,
  status,
  error,
  chatLoading,
  showThinking,
  onSuggestion,
  onQuoteSelection,
  contentClassName = "px-4 pb-6 pt-3",
  userMessageClassName,
  // Opt-in: the turn rail needs horizontal room to sit beside the transcript,
  // so only full-width layouts should enable it.
  showMessageRail = false,
  // Which component renders assistant parts. Defaults to
  // the simple per-part renderer.
  // ChatsView passes its own to opt into the grouped activity rail.
  renderAssistantParts: AssistantParts = AssistantMessageParts,
  // Which agent is answering; drives the empty-state intro and suggestions.
  selectedAgentUuid,
  // Optional per-message actions (copy, feedback) rendered under a finished
  // assistant message: (message) => ReactNode.
  renderAssistantFooter,
}) {
  const intro = agentIntro(selectedAgentUuid);
  const containerRef = useRef(null);
  const spacerRef = useRef(null);
  const selectionToolbarRef = useRef(null);
  const selectionHideTimerRef = useRef(null);
  const selectionUpdateTimerRef = useRef(null);
  const selectedTextRef = useRef("");
  const lastPinnedUserIdRef = useRef(null);
  const [spacerHeight, setSpacerHeight] = useState(0);

  const submittedUserMessageId = useMemo(() => {
    const lastMessage = messages.at(-1);
    return lastMessage?.role === "user" ? lastMessage.id : null;
  }, [messages]);

  const displayMessages = useMemo(() => {
    if (!showThinking) return messages;
    return [
      ...messages,
      {
        id: "assistant-pending",
        role: "assistant",
        pending: true,
        parts: [{ type: "text", text: "Thinking..." }],
      },
    ];
  }, [messages, showThinking]);

  const latestAssistantId = useMemo(
    () => [...displayMessages].reverse().find((item) => item.role === "assistant")?.id,
    [displayMessages],
  );

  // Assistant messages of the turn in flight: everything after the last user message. While
  // waiting for text, the "pending" placeholder is the latest one, but the real (still empty)
  // streaming message sits before it and must not get its action bar yet.
  const currentTurnIds = useMemo(() => {
    const lastUser = displayMessages.findLastIndex((item) => item.role === "user");
    return new Set(displayMessages.slice(lastUser + 1).map((item) => item.id));
  }, [displayMessages]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    const spacer = spacerRef.current;
    if (!container) return;
    if (!lastPinnedUserIdRef.current || !spacer || displayMessages.length === 0) {
      setSpacerHeight(0);
      return;
    }

    function topInContent(element) {
      const containerRect = container.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      return elementRect.top - containerRect.top + container.scrollTop;
    }

    function updateSpacerHeight() {
      const pinnedElement = container.querySelector(
        `[data-msg-id="${lastPinnedUserIdRef.current}"]`,
      );
      if (!pinnedElement) {
        setSpacerHeight(0);
        return;
      }

      const pinnedTop = topInContent(pinnedElement);
      const contentEnd = topInContent(spacer);
      const nextSpacerHeight = Math.max(
        0,
        pinnedTop + container.clientHeight - contentEnd - PIN_SPACER_OFFSET_PX,
      );
      setSpacerHeight(nextSpacerHeight);
    }

    updateSpacerHeight();
    const resizeObserver = new ResizeObserver(updateSpacerHeight);
    resizeObserver.observe(container);
    Array.from(container.querySelectorAll("[data-msg-id]")).forEach((element) => {
      resizeObserver.observe(element);
    });
    return () => {
      resizeObserver.disconnect();
    };
  }, [displayMessages, error]);

  useLayoutEffect(() => {
    if (!submittedUserMessageId || submittedUserMessageId === lastPinnedUserIdRef.current) return;
    lastPinnedUserIdRef.current = submittedUserMessageId;

    requestAnimationFrame(() => {
      const container = containerRef.current;
      const spacer = spacerRef.current;
      const submittedUserElement = container?.querySelector(
        `[data-msg-id="${submittedUserMessageId}"]`,
      );
      if (!container || !spacer || !submittedUserElement) return;

      const containerRect = container.getBoundingClientRect();
      const pinnedTop =
        submittedUserElement.getBoundingClientRect().top - containerRect.top + container.scrollTop;
      const contentEnd =
        spacer.getBoundingClientRect().top - containerRect.top + container.scrollTop;
      const nextSpacerHeight = Math.max(
        0,
        pinnedTop + container.clientHeight - contentEnd - PIN_SPACER_OFFSET_PX,
      );

      spacer.style.minHeight = `${nextSpacerHeight}px`;
      setSpacerHeight(nextSpacerHeight);

      requestAnimationFrame(() => {
        submittedUserElement.scrollIntoView({
          block: "start",
          behavior: "smooth",
        });
      });
    });
  }, [submittedUserMessageId]);

  function updateSelectionReply() {
    const container = containerRef.current;
    const toolbar = selectionToolbarRef.current;
    const selection = window.getSelection?.();
    if (!container || !selection || selection.rangeCount === 0 || selection.isCollapsed) {
      scheduleSelectionReplyHide();
      return;
    }

    const selectedText = selection.toString().replace(/\s+/g, " ").trim();
    if (!selectedText) {
      scheduleSelectionReplyHide();
      return;
    }

    const range = selection.getRangeAt(0);
    if (!selectionIntersectsAssistantMessage(range, container)) {
      scheduleSelectionReplyHide();
      return;
    }

    const rect = range.getBoundingClientRect();
    if (!rect || (rect.width === 0 && rect.height === 0)) {
      scheduleSelectionReplyHide();
      return;
    }

    if (selectionHideTimerRef.current) {
      window.clearTimeout(selectionHideTimerRef.current);
      selectionHideTimerRef.current = null;
    }
    selectedTextRef.current = selectedText;
    if (toolbar) {
      toolbar.style.left = `${rect.left + rect.width / 2}px`;
      toolbar.style.top = `${rect.top - 8}px`;
      toolbar.style.display = "inline-flex";
    }
  }

  function hideSelectionReply() {
    if (selectionHideTimerRef.current) {
      window.clearTimeout(selectionHideTimerRef.current);
      selectionHideTimerRef.current = null;
    }
    if (selectionUpdateTimerRef.current) {
      window.clearTimeout(selectionUpdateTimerRef.current);
      selectionUpdateTimerRef.current = null;
    }
    selectedTextRef.current = "";
    if (selectionToolbarRef.current) {
      selectionToolbarRef.current.style.display = "none";
    }
  }

  function scheduleSelectionReplyHide(delay = 180) {
    if (selectionHideTimerRef.current) {
      window.clearTimeout(selectionHideTimerRef.current);
    }
    selectionHideTimerRef.current = window.setTimeout(() => {
      selectionHideTimerRef.current = null;
      hideSelectionReply();
    }, delay);
  }

  function scheduleSelectionReplyUpdate(delay = 90) {
    if (selectionUpdateTimerRef.current) {
      window.clearTimeout(selectionUpdateTimerRef.current);
    }
    selectionUpdateTimerRef.current = window.setTimeout(() => {
      selectionUpdateTimerRef.current = null;
      updateSelectionReply();
    }, delay);
  }

  useEffect(() => {
    function handlePointerDown(event) {
      if (event.target?.closest?.("[data-selection-reply-toolbar]")) return;
      hideSelectionReply();
    }

    function handleSelectionSettled(event) {
      if (event.target?.closest?.("[data-selection-reply-toolbar]")) return;
      scheduleSelectionReplyUpdate();
    }

    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("selectionchange", handleSelectionSettled);
    document.addEventListener("pointerup", handleSelectionSettled);
    document.addEventListener("keyup", handleSelectionSettled);
    return () => {
      if (selectionHideTimerRef.current) {
        window.clearTimeout(selectionHideTimerRef.current);
      }
      if (selectionUpdateTimerRef.current) {
        window.clearTimeout(selectionUpdateTimerRef.current);
      }
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("selectionchange", handleSelectionSettled);
      document.removeEventListener("pointerup", handleSelectionSettled);
      document.removeEventListener("keyup", handleSelectionSettled);
    };
  }, []);

  function handleQuoteSelection(event) {
    event.preventDefault();
    event.stopPropagation();
    if (!selectedTextRef.current) return;
    onQuoteSelection?.(selectedTextRef.current);
    window.getSelection?.()?.removeAllRanges();
    hideSelectionReply();
  }

  return (
    <div className="relative flex min-h-0 flex-1">
      {/* The rail sits OUTSIDE the scroller: absolutely positioned inside it, it
          would scroll away with the transcript instead of staying pinned. */}
      {showMessageRail ? (
        <ChatMessageRail messages={displayMessages} containerRef={containerRef} />
      ) : null}
      <div
        ref={containerRef}
        className="min-h-0 flex-1 overflow-y-auto"
        style={{ overscrollBehavior: "contain", scrollPaddingTop: 0 }}
        role="log"
        onScroll={hideSelectionReply}
      >
        <button
          ref={selectionToolbarRef}
          type="button"
          data-selection-reply-toolbar
          className="fixed z-50 hidden h-8 items-center rounded-md border bg-popover px-3 text-xs font-medium text-popover-foreground shadow-md transition-colors hover:bg-accent"
          style={{ transform: "translate(-50%, -100%)" }}
          onPointerDown={handleQuoteSelection}
        >
          Reply
        </button>
        <div
          className={cn(
            "mx-auto w-full max-w-3xl",
            contentClassName,
            // Only the empty state is centred. The skeleton mimics real messages,
            // which flow from the top — centring it would drop the placeholder
            // mid-pane and then snap it upward once the messages arrive.
            !chatLoading && messages.length === 0 && "flex min-h-full items-center justify-center",
          )}
        >
          {chatLoading ? (
            <ChatMessagesSkeleton />
          ) : messages.length === 0 ? (
            <div className="w-full max-w-xl space-y-5 text-center">
              <div>
                <h2 className="text-2xl font-semibold tracking-tight">{intro.label}</h2>
                <p className="mt-2 text-sm text-muted-foreground">{intro.blurb}</p>
              </div>
              <Suggestions className="!flex-wrap !justify-center !gap-2">
                {intro.suggestions.map((suggestion) => (
                  <Suggestion
                    key={suggestion}
                    suggestion={suggestion}
                    className="rounded-md border bg-card px-3 py-2 text-sm"
                    onClick={onSuggestion}
                  />
                ))}
              </Suggestions>
            </div>
          ) : (
            displayMessages.map((message) => {
              const role = message.role === "assistant" ? "assistant" : "user";
              const text = messageToText(message);
              const isLatestAssistant = message.id === latestAssistantId;
              const showAnimatedLoader =
                message.pending || (status === "streaming" && isLatestAssistant);
              const isStreamingThis =
                currentTurnIds.has(message.id) &&
                (status === "streaming" || status === "submitted");
              const footer =
                role === "assistant" &&
                !message.pending &&
                !isStreamingThis &&
                renderAssistantFooter
                  ? renderAssistantFooter(message)
                  : null;
              return (
                <div key={message.id} data-msg-id={message.id} data-role={role}>
                  <Message from={role} className={role === "user" ? "!py-2" : "!py-4"}>
                    {role === "assistant" ? (
                      <div className="w-full max-w-none space-y-3">
                        {!message.pending ? (
                          <AssistantParts
                            message={message}
                            fallbackText={text}
                            inFlight={isStreamingThis}
                          />
                        ) : null}
                        {isLatestAssistant ? (
                          // Layout motion: smooth the loader row when streamed text pushes it down.
                          <motion.div
                            layout="position"
                            transition={{
                              layout: {
                                duration: 0.18,
                                ease: [0.22, 1, 0.36, 1],
                              },
                            }}
                            className="flex min-h-[1.5rem] items-center"
                            data-loader-layout-motion="true"
                          >
                            <StatusIcon active={showAnimatedLoader} />
                            {footer ? <div className="ml-2">{footer}</div> : null}
                          </motion.div>
                        ) : (
                          footer
                        )}
                      </div>
                    ) : (
                      <UserMessageParts message={message} bubbleClassName={userMessageClassName} />
                    )}
                  </Message>
                </div>
              );
            })
          )}
          {error ? <ChatErrorNotice error={error} className="mt-3" /> : null}
          <div ref={spacerRef} aria-hidden="true" style={{ minHeight: spacerHeight }} />
        </div>
      </div>
    </div>
  );
});
