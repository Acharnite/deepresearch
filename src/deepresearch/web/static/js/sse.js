/* ── SSE connection manager ────────────────────────── */
import { getState } from './state.js';
import { setConnection } from './helpers.js';

export function connectSessionSSE(sessionId) {
  const state = getState();

  if (state.sseSource) {
    state.sseSource.close();
    state.sseSource = null;
  }

  setConnection('Connecting…', 'disconnected');

  const source = new EventSource('/api/sessions/' + sessionId + '/events');
  state.sseSource = source;

  source.onopen = function() {
    setConnection('Live', 'connected');
    state.usePolling = false;
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  };

  // Import processEvent lazily to avoid circular deps
  source.addEventListener('message', function(e) {
    try {
      const data = JSON.parse(e.data);
      // Dynamic import to break circular dependency
      import('./views/session-detail.js').then(mod => {
        mod.processEvent(data);
      });
    } catch (err) {
      console.warn('SSE parse error:', err);
    }
  });

  source.onerror = function() {
    setConnection('Disconnected', 'disconnected');
    source.close();
    state.sseSource = null;
  };
}

export function disconnectSSE() {
  const state = getState();
  if (state.sseSource) {
    state.sseSource.close();
    state.sseSource = null;
  }
}
