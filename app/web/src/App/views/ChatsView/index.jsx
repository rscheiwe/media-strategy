import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { toast } from "sonner";

import {
  clearMessageFeedback,
  createChat,
  getApiBaseUrl,
  getChat,
  setMessageFeedback,
} from "@/api";
import { DEFAULT_AGENT_UUID } from "@/constants/chatAgents";

import { ChatComposer } from "./ChatComposer";
import { createChatFetch } from "@/lib/chat-errors";
import { ChatConversation } from "./ChatConversation";
import { ChatsAssistantParts } from "./ChatsAssistantParts";
import { WorkflowContext } from "@/components/workflow/WorkflowContext";
import { openFormId, submissionText, submittedForms } from "@/lib/workflow";
import { MessageActions } from "./MessageActions";
import {
  createLocalId,
  extractChatMetadata,
  hasAssistantTextForCurrentTurn,
  markActiveToolError,
  messageToText,
  toUiMessage,
} from "./chatUtils";

// Server-side message ids are UUIDs; only those can carry feedback. (A message
// that never got its server id — e.g. a turn that failed before streaming —
// keeps the SDK's random id and is not rateable.)
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The browser's IANA time zone, so the assistant knows the user's local date and time. */
function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

export function ChatsView({ setChats, refreshChats, settings }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeChatId, setActiveChatId] = useState(() => searchParams.get("chat") || null);
  const [chatLoading, setChatLoading] = useState(false);
  const [selectedAgentUuid, setSelectedAgentUuid] = useState(DEFAULT_AGENT_UUID);
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [thinkingEffort, setThinkingEffort] = useState("high");
  // Form values submitted in this session (persisted ones come from message metadata).
  const [submittedLocal, setSubmittedLocal] = useState({});
  const [input, setInput] = useState("");
  const [quotedSelection, setQuotedSelection] = useState(null);
  const [pendingUserMessage, setPendingUserMessage] = useState(null);
  // message id → {rating, comment}; loaded with the chat, updated optimistically.
  const [feedbackByMessage, setFeedbackByMessage] = useState({});
  const activeChatIdRef = useRef(activeChatId);
  const selectedAgentUuidRef = useRef(selectedAgentUuid);
  const thinkingEnabledRef = useRef(thinkingEnabled);
  const thinkingEffortRef = useRef(thinkingEffort);
  const skipNextLoadChatIdRef = useRef(null);
  // One-request side channel (e.g. a form submission), sent with the next request then cleared.
  const metadataRef = useRef(null);

  useEffect(() => {
    const next = searchParams.get("chat") || null;
    activeChatIdRef.current = next;
    setActiveChatId(next);
    setQuotedSelection(null);
  }, [searchParams]);

  // A new chat starts from the user's saved defaults (user_settings). Within a
  // chat, the composer's controls are free to diverge for that conversation.
  const defaultThinking = Boolean(settings?.defaultThinking);
  const defaultEffort = settings?.thinkingEffort || "high";
  const defaultAgentUuid = settings?.defaultAgentUuid || DEFAULT_AGENT_UUID;
  useEffect(() => {
    if (activeChatId) return;
    thinkingEnabledRef.current = defaultThinking;
    setThinkingEnabled(defaultThinking);
    thinkingEffortRef.current = defaultEffort;
    setThinkingEffort(defaultEffort);
    selectedAgentUuidRef.current = defaultAgentUuid;
    setSelectedAgentUuid(defaultAgentUuid);
  }, [activeChatId, defaultThinking, defaultEffort, defaultAgentUuid]);

  useEffect(() => {
    selectedAgentUuidRef.current = selectedAgentUuid;
  }, [selectedAgentUuid]);

  useEffect(() => {
    thinkingEnabledRef.current = thinkingEnabled;
  }, [thinkingEnabled]);

  useEffect(() => {
    thinkingEffortRef.current = thinkingEffort;
  }, [thinkingEffort]);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        // Preserves the HTTP status, which the SDK's own error handling
        // discards — see lib/chat-errors.
        fetch: createChatFetch(),
        api: `${getApiBaseUrl()}/app/chat`,
        prepareSendMessagesRequest: ({ messages }) => {
          const velMessages = messages.map((message) => ({
            role: message.role,
            content: messageToText(message),
            parts: message.parts,
          }));
          const metadata = metadataRef.current;
          metadataRef.current = null;
          return {
            body: {
              chat_id: activeChatIdRef.current || null,
              agent_uuid: selectedAgentUuidRef.current,
              thinking_enabled: thinkingEnabledRef.current,
              thinking_effort: thinkingEffortRef.current,
              messages: velMessages,
              timezone: browserTimeZone(),
              ...(metadata ? { metadata } : {}),
            },
          };
        },
      }),
    [],
  );

  const { messages, sendMessage, setMessages, status, error, stop } = useChat({
    transport,
    experimental_throttle: 80,
    onData: (data) => {
      const metadata = extractChatMetadata(data);
      if (!metadata?.chatId) return;
      activeChatIdRef.current = metadata.chatId;
      setActiveChatId(metadata.chatId);
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("chat", metadata.chatId);
        return next;
      });
      setChats((prev) => {
        if (prev.some((chat) => chat.id === metadata.chatId)) return prev;
        return [
          {
            id: metadata.chatId,
            title: metadata.chatTitle || "New chat",
            agentUuid: metadata.agentUuid || selectedAgentUuidRef.current,
            updatedAt: new Date().toISOString(),
          },
          ...prev,
        ];
      });
    },
    onFinish: () => {
      void refreshChats?.();
    },
    onError: (streamError) => {
      setMessages((currentMessages) => markActiveToolError(currentMessages, streamError));
    },
  });

  useEffect(() => {
    let ignore = false;
    if (!activeChatId) {
      setChatLoading(false);
      setMessages([]);
      setPendingUserMessage(null);
      setFeedbackByMessage({});
      return;
    }
    if (skipNextLoadChatIdRef.current === activeChatId) {
      skipNextLoadChatIdRef.current = null;
      setChatLoading(false);
      return;
    }
    setChatLoading(true);
    getChat(activeChatId)
      .then((payload) => {
        if (ignore) return;
        const loaded = payload?.messages || [];
        setFeedbackByMessage(
          Object.fromEntries(
            loaded
              .filter((message) => message.feedback)
              .map((message) => [message.id, message.feedback]),
          ),
        );
        setMessages(loaded.map(toUiMessage));
      })
      .catch((err) => {
        if (ignore) return;
        toast.error(err?.payload?.detail || err?.message || "Failed to load chat.");
      })
      .finally(() => {
        if (!ignore) setChatLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [activeChatId, setMessages]);

  function handleAgentChange(agentUuid) {
    selectedAgentUuidRef.current = agentUuid;
    setSelectedAgentUuid(agentUuid);
  }

  function handleThinkingEnabledChange(enabled) {
    thinkingEnabledRef.current = enabled;
    setThinkingEnabled(enabled);
  }

  function handleThinkingEffortChange(effort) {
    thinkingEffortRef.current = effort;
    setThinkingEffort(effort);
  }

  const ensureChatForSend = useCallback(
    async (firstMessage) => {
      if (activeChatIdRef.current) return activeChatIdRef.current;
      const payload = await createChat({
        title: firstMessage,
        agentUuid: selectedAgentUuidRef.current,
      });
      const chat = payload?.chat;
      if (!chat?.id) {
        throw new Error("Failed to create chat.");
      }
      activeChatIdRef.current = chat.id;
      skipNextLoadChatIdRef.current = chat.id;
      setActiveChatId(chat.id);
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("chat", chat.id);
        return next;
      });
      setChats((prev) => {
        if (prev.some((item) => item.id === chat.id)) return prev;
        return [chat, ...prev];
      });
      return chat.id;
    },
    [setChats, setSearchParams],
  );

  const createUserMessageParts = useCallback((prompt, quote) => {
    const parts = [];
    if (quote?.text) {
      parts.push({ type: "quote", text: quote.text });
    }
    parts.push({ type: "text", text: prompt });
    return parts;
  }, []);

  const handleQuoteSelection = useCallback((text) => {
    setQuotedSelection({ text });
  }, []);

  const sendUserMessage = useCallback(
    async (rawText) => {
      const prompt = rawText.trim();
      if (!prompt || status === "submitted" || status === "streaming") return;
      const quote = quotedSelection;
      const messageParts = createUserMessageParts(prompt, quote);
      const messageText = messageToText({ parts: messageParts });
      setInput("");
      setQuotedSelection(null);
      const messageId = createLocalId("user");
      setPendingUserMessage({
        id: messageId,
        text: messageText,
        baseMessageCount: messages.length,
      });
      try {
        await ensureChatForSend(prompt);
        await sendMessage({
          id: messageId,
          role: "user",
          parts: messageParts,
        });
      } catch (err) {
        setInput(prompt);
        setQuotedSelection(quote);
        setPendingUserMessage(null);
        toast.error(err?.payload?.detail || err?.message || "Failed to send message.");
      }
    },
    [
      createUserMessageParts,
      ensureChatForSend,
      messages.length,
      quotedSelection,
      sendMessage,
      status,
    ],
  );

  // A clarification form's answers go to the workflow as structured metadata; the visible
  // user message is a one-line summary of them.
  const submitForm = useCallback(
    (formId, parameters) => {
      metadataRef.current = {
        type: "tool_form_submission",
        target_tool: "run_recommendation",
        form_id: formId,
        parameters,
      };
      setSubmittedLocal((prev) => ({ ...prev, [formId]: parameters }));
      void sendUserMessage(submissionText(parameters) || "Submitted the brief details");
    },
    [sendUserMessage],
  );

  const workflowValue = useMemo(
    () => ({
      openFormId: openFormId(messages),
      submitted: { ...submittedForms(messages), ...submittedLocal },
      busy: status === "submitted" || status === "streaming",
      submitForm,
    }),
    [messages, submittedLocal, status, submitForm],
  );

  const onSubmit = useCallback(
    (event) => {
      event.preventDefault();
      void sendUserMessage(input);
    },
    [input, sendUserMessage],
  );

  const handleRate = useCallback(async (message, rating) => {
    const chatId = activeChatIdRef.current;
    if (!chatId) return;
    let previous;
    setFeedbackByMessage((current) => {
      previous = current[message.id];
      const next = { ...current };
      if (rating === null) delete next[message.id];
      else next[message.id] = { rating, comment: null };
      return next;
    });
    try {
      if (rating === null) await clearMessageFeedback(chatId, message.id);
      else await setMessageFeedback(chatId, message.id, { rating });
    } catch (err) {
      setFeedbackByMessage((current) => {
        const next = { ...current };
        if (previous) next[message.id] = previous;
        else delete next[message.id];
        return next;
      });
      toast.error(err?.payload?.detail || err?.message || "Failed to save feedback.");
    }
  }, []);

  const renderAssistantFooter = useCallback(
    (message) => (
      <MessageActions
        message={message}
        rating={feedbackByMessage[message.id]?.rating ?? null}
        canRate={Boolean(activeChatId) && UUID_RE.test(String(message.id))}
        onRate={handleRate}
      />
    ),
    [activeChatId, feedbackByMessage, handleRate],
  );

  // Stop the in-flight stream. The server persists whatever was produced so far,
  // so the partial answer survives a reload.
  const onStop = useCallback(() => {
    void stop();
    void refreshChats?.();
  }, [refreshChats, stop]);

  const uiStatus = status === "ready" ? "idle" : status;
  const showThinking =
    status === "submitted" || (status === "streaming" && !hasAssistantTextForCurrentTurn(messages));

  const renderedMessages = useMemo(() => {
    if (!pendingUserMessage) return messages;
    const appendedMessages = messages.slice(pendingUserMessage.baseMessageCount);
    const matchingMessage =
      appendedMessages.find((message) => message.id === pendingUserMessage.id) ||
      appendedMessages.find(
        (message) => message.role === "user" && messageToText(message) === pendingUserMessage.text,
      );
    if (matchingMessage) return messages;
    return [
      ...messages,
      {
        id: pendingUserMessage.id,
        role: "user",
        parts: [{ type: "text", text: pendingUserMessage.text }],
      },
    ];
  }, [messages, pendingUserMessage]);

  useEffect(() => {
    if (!pendingUserMessage) return;
    const appendedMessages = messages.slice(pendingUserMessage.baseMessageCount);
    const matchingMessage =
      appendedMessages.find((message) => message.id === pendingUserMessage.id) ||
      appendedMessages.find(
        (message) => message.role === "user" && messageToText(message) === pendingUserMessage.text,
      );
    if (matchingMessage) setPendingUserMessage(null);
  }, [messages, pendingUserMessage]);

  return (
    <WorkflowContext.Provider value={workflowValue}>
      <div className="flex h-full min-h-0 overflow-hidden bg-background">
        <main className="flex min-w-0 flex-1 flex-col">
          <ChatConversation
            selectedAgentUuid={selectedAgentUuid}
            messages={renderedMessages}
            status={status}
            error={error}
            chatLoading={chatLoading}
            showThinking={showThinking}
            onSuggestion={sendUserMessage}
            onQuoteSelection={handleQuoteSelection}
            renderAssistantParts={ChatsAssistantParts}
            renderAssistantFooter={renderAssistantFooter}
            showMessageRail
            // Light-blue user bubble rather than the default solid `bg-primary`.
            userMessageClassName="group-[.is-user]:bg-blue-50 group-[.is-user]:text-blue-900 dark:group-[.is-user]:bg-blue-950/40 dark:group-[.is-user]:text-blue-400"
          />
          <ChatComposer
            input={input}
            status={status}
            uiStatus={uiStatus}
            selectedAgentUuid={selectedAgentUuid}
            thinkingEnabled={thinkingEnabled}
            thinkingEffort={thinkingEffort}
            quotedSelection={quotedSelection}
            onInputChange={setInput}
            onAgentChange={handleAgentChange}
            onThinkingEnabledChange={handleThinkingEnabledChange}
            onThinkingEffortChange={handleThinkingEffortChange}
            onClearQuote={() => setQuotedSelection(null)}
            onSubmit={onSubmit}
            onStop={onStop}
            submitOnEnter={settings?.sendOnEnter !== false}
          />
        </main>
      </div>
    </WorkflowContext.Provider>
  );
}
