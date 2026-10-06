// Mirrors api/src/chat_api/chat/agents.py — keep the UUIDs in sync.
export const ASSISTANT_AGENT_UUID = "3b9c6f0e-6a3e-4f43-9a51-2f7e6d1c8a10";
export const DEFAULT_AGENT_UUID = ASSISTANT_AGENT_UUID;

/**
 * The agents a user can pick, in one place.
 *
 * The composer hides the agent picker while there is only one; adding an entry
 * here (plus the matching config server-side) is all it takes to offer another.
 *
 * `reason` is why you would choose one; `blurb` and `suggestions` are what an
 * empty conversation offers.
 */
export const CHAT_AGENTS = [
  {
    uuid: ASSISTANT_AGENT_UUID,
    label: "Media Planner",
    reason: "Media-planning assistant: products, CPMs and campaign planning.",
    blurb: "Ask about the ad products and pricing, or describe a client brief to plan for.",
    suggestions: [
      "Which ad products are there, and what do they cost?",
      "Calculate the average CTR and in-view rate for each vertical",
      "Which product is cheapest per thousand impressions?",
      "What would suit a Retail brand optimizing for clicks?",
    ],
    // The API's thinking modes (reasoning model / reflection) are off by default.
    supportsThinking: false,
  },
];

const AGENT_BY_UUID = new Map(CHAT_AGENTS.map((agent) => [agent.uuid, agent]));

/** Display name for an agent uuid; falls back to the server-side default. */
export function agentLabel(uuid) {
  return AGENT_BY_UUID.get(uuid)?.label || AGENT_BY_UUID.get(DEFAULT_AGENT_UUID).label;
}

/**
 * The empty-conversation intro for an agent.
 *
 * Falls back to the default because that is what the server resolves an
 * unknown uuid to — so an unrecognised agent shows the intro for the agent that
 * will actually answer.
 */
export function agentIntro(uuid) {
  return AGENT_BY_UUID.get(uuid) || AGENT_BY_UUID.get(DEFAULT_AGENT_UUID);
}

/**
 * Whether this agent can actually think. Showing the controls for an agent
 * that ignores them would be a lie in the UI.
 */
export function agentSupportsThinking(uuid) {
  return Boolean(agentIntro(uuid)?.supportsThinking);
}
