/* ── Central shared state ──────────────────────────── */

const state = {
  agents: {},
  agentNames: {},
  agentEmojis: {},
  scribeInfo: { status: 'waiting', state: 'waiting' },
  currentState: 'IDLE',
  currentTopic: '',
  currentSessionId: null,
  eventCount: 0,
  qaLog: [],
  availableModels: [],
};

export function getState() {
  return state;
}

export function resetDetailState() {
  state.agents = {};
  state.scribeInfo = { status: 'waiting', state: 'waiting' };
  state.eventCount = 0;
  state.currentState = 'IDLE';
  state.currentTopic = '';
  state.qaLog = [];
}
