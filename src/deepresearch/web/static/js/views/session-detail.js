/* ── Session detail view — progress, events, SSE processing ── */
import { getState, resetDetailState } from '../state.js';
import { STATE_ORDER, STATE_LABELS, STATE_BADGE_CLASSES, STATE_COLORS, stateLabels } from '../constants.js';
import { esc, showToast, $ } from '../helpers.js';
import { fetchSessionDetailAPI, fetchSessionStateAPI, startResearchAPI } from '../api.js';
import { connectSessionSSE, disconnectSSE } from '../sse.js';
import { addEvent } from '../event-log.js';
import { renderAgents } from '../agent-panels.js';
import { addQA, renderQA } from '../qa-log.js';
import { getModelPicker } from '../model-picker.js';

// ── Timer refs (local to this module) ───────────────
let sessionStartTime = null;
let elapsedTimer = null;

function startElapsedTimer(startTime) {
  if (startTime) {
    sessionStartTime = new Date(startTime).getTime();
  } else {
    sessionStartTime = Date.now();
  }
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(updateElapsed, 1000);
  updateElapsed();
}

function stopElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

function updateElapsed() {
  if (!sessionStartTime) return;
  const elapsed = Math.floor((Date.now() - sessionStartTime) / 1000);
  const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const secs = String(elapsed % 60).padStart(2, '0');
  const el = document.getElementById('elapsedDisplay');
  if (el) el.textContent = mins + ':' + secs;
}

// ── State badge update ──────────────────────────────
export function updateState(stateName) {
  const state = getState();
  state.currentState = stateName || 'IDLE';
  const label = STATE_LABELS[state.currentState] || state.currentState;
  const cls = STATE_BADGE_CLASSES[state.currentState] || 'badge-idle';
  const color = STATE_COLORS[state.currentState] || '#8b949e';
  const stateBadge = document.getElementById('stateBadge');
  if (stateBadge) {
    stateBadge.className = 'badge ' + cls;
    stateBadge.innerHTML = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:' + color + ';"></span> ' + label;
  }

  document.querySelectorAll('.phase-step').forEach(el => {
    const phase = el.dataset.phase;
    const idx = STATE_ORDER.indexOf(phase);
    const curIdx = STATE_ORDER.indexOf(state.currentState);
    el.classList.remove('active', 'done', 'current');
    if (idx < curIdx) el.classList.add('done');
    else if (idx === curIdx) el.classList.add('current');
    else el.classList.add('active');
  });
}

// ── Show detail ─────────────────────────────────────
export function showDetail(sessionId) {
  const state = getState();
  state.currentSessionId = sessionId;
  // Show detail view
  const detailView = document.getElementById('detailView');
  const sessionListView = document.getElementById('sessionListView');
  const newResearchView = document.getElementById('newResearchView');
  const settingsView = document.getElementById('settingsView');
  const systemLogView = document.getElementById('systemLogView');
  [sessionListView, newResearchView, settingsView, systemLogView].forEach(v => {
    if (v) v.classList.add('hidden');
  });
  if (detailView) detailView.classList.remove('hidden');

  const statusBar = document.getElementById('statusBar');
  if (statusBar) statusBar.classList.remove('hidden');

  // Stop system log auto-refresh
  if (state.systemLogInterval) {
    clearInterval(state.systemLogInterval);
    state.systemLogInterval = null;
  }

  resetDetailState();
  updateState('IDLE');

  const phaseDisplay = document.getElementById('phaseDisplay');
  const topicDisplay = document.getElementById('topicDisplay');
  const elapsedDisplay = document.getElementById('elapsedDisplay');
  if (phaseDisplay) phaseDisplay.textContent = 'Loading...';
  if (topicDisplay) topicDisplay.textContent = '—';
  if (elapsedDisplay) elapsedDisplay.textContent = '00:00';

  const eventLog = document.getElementById('eventLog');
  if (eventLog) {
    eventLog.innerHTML = '<div class="empty-state" id="emptyLog"><h3>No events yet</h3><p>Events will appear here as the research progresses.</p></div>';
  }
  const eventCountEl = document.getElementById('eventCount');
  if (eventCountEl) eventCountEl.textContent = '0 events';

  const resultView = document.getElementById('resultView');
  const errorView = document.getElementById('errorView');
  if (resultView) resultView.classList.add('hidden');
  if (errorView) errorView.classList.add('hidden');

  renderAgents();
  renderQA();
  stopElapsedTimer();

  // First fetch current state, then connect SSE (SSE replays event_history)
  fetchSessionDetail(sessionId).then(() => {
    connectSessionSSE(sessionId);
  });
}

// ── Fetch session detail ────────────────────────────
async function fetchSessionDetail(sessionId) {
  const [data, stateData] = await Promise.all([
    fetchSessionDetailAPI(sessionId),
    fetchSessionStateAPI(sessionId),
  ]);

  if (data) {
    const detailTopic = document.getElementById('detailTopic');
    const detailSessionId = document.getElementById('detailSessionId');
    const detailStatusBadge = document.getElementById('detailStatusBadge');
    const topicDisplay = document.getElementById('topicDisplay');

    if (detailTopic) detailTopic.textContent = esc(data.topic);
    if (detailSessionId) detailSessionId.textContent = 'Session ' + data.session_id;
    if (detailStatusBadge) {
      detailStatusBadge.textContent = data.status;
      detailStatusBadge.className = 'session-status-badge ' + data.status;
    }

    const state = getState();
    state.currentTopic = data.topic || '';
    if (topicDisplay) {
      const t = state.currentTopic;
      topicDisplay.textContent = t.length > 60 ? t.slice(0, 60) + '…' : t;
    }

    if (data.status === 'complete' && data.result) {
      showResult(data.result, data.topic, sessionId);
      return; // Don't restore state for completed sessions
    } else if (data.status === 'error') {
      showError(data.error);
      return;
    }
  }

  // Restore live state from event history for running sessions
  if (stateData && stateData.status === 'running') {
    const state = getState();

    // Restore agent states
    if (stateData.agent_states) {
      Object.entries(stateData.agent_states).forEach(([id, agentState]) => {
        state.agents[id] = {
          status: agentState.status || 'waiting',
          state: agentState.state || 'waiting',
        };
      });
    }

    // Restore scribe info
    if (stateData.scribe_info) {
      state.scribeInfo = stateData.scribe_info;
    }

    // Restore current pipeline state
    if (stateData.current_state) {
      updateState(stateData.current_state);
      const phaseDisplay = document.getElementById('phaseDisplay');
      if (phaseDisplay) {
        phaseDisplay.textContent = STATE_LABELS[stateData.current_state] || stateData.current_state;
      }
    }

    // Restore event count
    if (stateData.event_count !== undefined) {
      state.eventCount = stateData.event_count;
      const eventCountEl = document.getElementById('eventCount');
      if (eventCountEl) {
        eventCountEl.textContent = stateData.event_count + ' event' + (stateData.event_count !== 1 ? 's' : '');
      }
    }

    // Restore elapsed timer from session creation time
    if (stateData.elapsed_start) {
      startElapsedTimer(stateData.elapsed_start);
    }

    // Re-render agents with restored state
    renderAgents();
  }
}

// ── Show result / error ─────────────────────────────
function showResult(result, topic, sessionId) {
  const resultView = document.getElementById('resultView');
  const errorView = document.getElementById('errorView');
  const resultTopic = document.getElementById('resultTopic');
  const resultFilename = document.getElementById('resultFilename');
  const resultStatus = document.getElementById('resultStatus');
  const downloadPdfBtn = document.getElementById('downloadPdfBtn');
  const downloadHtmlBtn = document.getElementById('downloadHtmlBtn');

  if (resultView) resultView.classList.remove('hidden');
  if (errorView) errorView.classList.add('hidden');
  if (resultTopic) resultTopic.textContent = topic || '—';
  if (resultFilename) resultFilename.textContent = result.pdf_filename || '—';
  if (resultStatus) resultStatus.textContent = 'Complete';

  const pdfFile = result.pdf_filename || 'research.pdf';
  const sid = sessionId || getState().currentSessionId;
  if (downloadPdfBtn) {
    downloadPdfBtn.onclick = function() {
      window.open('/api/download/' + sid + '/' + encodeURIComponent(pdfFile), '_blank');
    };
  }

  if (result.html_path && downloadHtmlBtn) {
    const htmlFile = result.html_path.split('/').pop();
    downloadHtmlBtn.onclick = function() {
      window.open('/api/download/' + sid + '/' + encodeURIComponent(htmlFile), '_blank');
    };
    downloadHtmlBtn.classList.remove('hidden');
  } else if (downloadHtmlBtn) {
    downloadHtmlBtn.classList.add('hidden');
  }

  updateState('COMPLETE');
  const phaseDisplay = document.getElementById('phaseDisplay');
  if (phaseDisplay) phaseDisplay.textContent = 'Complete';
  const statusBar = document.getElementById('statusBar');
  if (statusBar) statusBar.classList.remove('hidden');
}

function showError(err) {
  const errorView = document.getElementById('errorView');
  const resultView = document.getElementById('resultView');
  const errorMessage = document.getElementById('errorMessage');
  if (errorView) errorView.classList.remove('hidden');
  if (resultView) resultView.classList.add('hidden');
  if (errorMessage) errorMessage.textContent = err || 'Unknown error';
  updateState('ERROR');
  const phaseDisplay = document.getElementById('phaseDisplay');
  if (phaseDisplay) phaseDisplay.textContent = 'Error';
}

// ── Process event (called from SSE) ─────────────────
export function processEvent(data) {
  const state = getState();
  const eventType = data.event_type || 'unknown';
  const stateName = data.state || state.currentState;
  const topic = data.topic || state.currentTopic;

  if (topic) state.currentTopic = topic;
  updateState(stateName);

  const topicDisplay = document.getElementById('topicDisplay');
  if (topicDisplay && state.currentTopic) {
    const t = state.currentTopic;
    topicDisplay.textContent = t.length > 60 ? t.slice(0, 60) + '…' : t;
  }

  const phaseDisplay = document.getElementById('phaseDisplay');
  if (phaseDisplay) {
    phaseDisplay.textContent = STATE_LABELS[state.currentState] || state.currentState;
  }

  if (eventType === 'session_start') {
    const cancelBtn = document.getElementById('cancelBtn');
    if (cancelBtn) cancelBtn.classList.remove('hidden');
    state.agents = {};
    startElapsedTimer();
  }

  if (eventType === 'session_end') {
    const cancelBtn = document.getElementById('cancelBtn');
    if (cancelBtn) cancelBtn.classList.add('hidden');
    stopElapsedTimer();
    fetchSessionDetail(state.currentSessionId);
    refreshSessionList();
  }

  if (eventType === 'session_error') {
    const cancelBtn = document.getElementById('cancelBtn');
    if (cancelBtn) cancelBtn.classList.add('hidden');
    stopElapsedTimer();
    showError(data.error || 'Unknown error');
    refreshSessionList();
  }

  if (eventType === 'models_assigned' && data.assignments) {
    console.log('[Agent Debug] models_assigned:', data.assignments ? Object.keys(data.assignments).length + ' agents' : 'NO ASSIGNMENTS');
    Object.entries(data.assignments).forEach(([id]) => {
      if (!state.agents[id]) state.agents[id] = { status: 'waiting', state: 'waiting' };
    });
    renderAgents();
  }

  if (eventType === 'round_start') {
    Object.keys(state.agents).forEach(id => {
      state.agents[id] = { status: 'waiting', state: 'waiting' };
    });
    renderAgents();
  }

  if (eventType === 'agent_start') {
    console.log('[Agent Debug] agent_start:', data.agent_id);
    const aid = data.agent_id;
    if (!state.agents[aid]) state.agents[aid] = {};
    state.agents[aid].status = 'running';
    state.agents[aid].state = data.agent_state || 'researching';
    renderAgents();
  }

  if (eventType === 'agent_complete') {
    console.log('[Agent Debug] agent_complete:', data.agent_id);
    const aid = data.agent_id;
    if (state.agents[aid]) { state.agents[aid].status = 'done'; }  // DON'T set state to 'done' — agent may still refine/answer
    renderAgents();
  }

  if (eventType === 'agent_failed') {
    const aid = data.agent_id;
    if (state.agents[aid]) { state.agents[aid].status = 'failed'; state.agents[aid].state = 'failed'; }
    renderAgents();
  }

  if (eventType === 'agent_output' && data.agent_state) {
    const aid = data.agent_id;
    if (aid && state.agents[aid]) {
      state.agents[aid].state = data.agent_state;
      renderAgents();
    }
  }

  if (eventType === 'followup_start') {
    state.qaLog = [];
    renderQA();
  }

  if (eventType === 'followup_complete' && data.questions) {
    Object.entries(data.questions).forEach(function([agentId, questions]) {
      if (Array.isArray(questions)) {
        questions.forEach(function(q) {
          addQA(agentId, q, 'All Agents');
        });
      }
    });
  }

  if (eventType === 'agent_output') {
    const aid = data.agent_id;
    const text = data.text || '';
    if (!aid || !text) return;
    const panel = document.getElementById('agent-output-' + aid);
    const pre = panel ? panel.querySelector('.agent-output-text') : null;
    if (pre) {
      // Only auto-show panel if not manually collapsed (user toggled it closed)
      // Check if user has explicitly collapsed this panel by checking toggle button text
      const section = document.getElementById('agent-section-' + aid);
      const btn = section ? section.querySelector('.agent-toggle') : null;
      const isCollapsed = btn && btn.textContent === '▸';
      if (!isCollapsed) {
        panel.style.display = 'block';
      }
      // Buffer text and batch DOM updates (throttle to ~20fps)
      if (!panel._outputBuffer) panel._outputBuffer = '';
      panel._outputBuffer += text;
      if (!panel._outputTimer) {
        panel._outputTimer = setTimeout(() => {
          pre.textContent += panel._outputBuffer || '';
          panel._outputBuffer = '';
          panel._outputTimer = null;
          // Auto-scroll to bottom (not top!)
          if (!isCollapsed) {
            panel.scrollTop = panel.scrollHeight;
          }
        }, 50);
      }
    }
  }

  if (eventType === 'collaboration_phase') {
    Object.keys(state.agents).forEach(id => {
      if (state.agents[id].status !== 'done') { state.agents[id].status = 'done'; state.agents[id].state = 'done'; }
    });
    renderAgents();
  }

  if (eventType === 'scribe_start') {
    Object.keys(state.agents).forEach(id => {
      state.agents[id].status = 'done'; state.agents[id].state = 'done';
    });
    state.scribeInfo = { status: 'running', state: 'writing' };
    renderAgents();
  }

  if (eventType === 'scribe_end') {
    state.scribeInfo = { status: 'done', state: 'done' };
    renderAgents();
  }

  if (eventType === 'refinement_start') {
    updateState('REFINING');
    const phaseDisplay = document.getElementById('phaseDisplay');
    if (phaseDisplay) phaseDisplay.textContent = STATE_LABELS['REFINING'] || 'Refining';
  }

  if (eventType === 'refinement_complete') {
    // Refinement complete, will transition to ROUND2 or COMPILING
  }

  addEvent(eventType, data);
}

// Helper import for refreshSessionList to avoid circular dep
function refreshSessionList() {
  import('./session-list.js').then(mod => mod.refreshSessionList()).catch(() => {});
}

// ── Start Research (from New Research form) ─────────
window.startResearch = async function() {
  const topicInput = document.getElementById('topicInput');
  const startBtn = document.getElementById('startBtn');
  const cancelBtn = document.getElementById('cancelBtn');
  const customMinutesInput = document.getElementById('customMinutesInput');

  if (!topicInput) return;
  const topic = topicInput.value.trim();
  if (!topic) { showToast('Please enter a research topic.', 'error'); return; }

  const timeBudget = document.querySelector('input[name="time_budget"]:checked').value;
  const modelMode = document.querySelector('input[name="model_mode"]:checked').value;

  if (startBtn) {
    startBtn.disabled = true;
    startBtn.textContent = '⏳ Starting...';
  }

  const body = { topic: topic, time_budget: timeBudget, model_mode: modelMode };

  // Add model selection
  if (modelMode === 'same') {
    const picker = getModelPicker();
    body.selected_model = picker ? picker.getValue() : '';
  } else if (modelMode === 'manual') {
    const agentModels = {};
    document.querySelectorAll('.mini-picker-input[data-model-id]').forEach(input => {
      agentModels[input.dataset.agent] = input.dataset.modelId;
    });
    if (Object.keys(agentModels).length > 0) {
      body.agent_models = agentModels;
    }
  }

  // Add custom minutes if custom selected
  if (timeBudget === 'custom') {
    const minutes = parseInt(customMinutesInput?.value) || 15;
    body.time_budget_seconds = Math.max(60, Math.min(minutes * 60, 3600));
  }

  try {
    const response = await startResearchAPI(body);

    if (!response.ok) {
      const err = await response.json();
      showToast('Error: ' + (err.error || 'Unknown error'), 'error');
      if (startBtn) { startBtn.disabled = false; startBtn.innerHTML = '🚀 Start Research'; }
      return;
    }

    const data = await response.json();
    showToast('Session started!', 'success');

    // Navigate to session detail
    showDetail(data.session_id);

    if (startBtn) { startBtn.disabled = false; startBtn.innerHTML = '🚀 Start Research'; }
    if (cancelBtn) cancelBtn.classList.remove('hidden');

  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
    if (startBtn) { startBtn.disabled = false; startBtn.innerHTML = '🚀 Start Research'; }
  }
};

// ── Cancel research ─────────────────────────────────
window.cancelResearch = async function() {
  const state = getState();
  if (!state.currentSessionId) return;
  try {
    await import('../api.js').then(mod => mod.cancelSessionAPI(state.currentSessionId));
    const cancelBtn = document.getElementById('cancelBtn');
    if (cancelBtn) cancelBtn.classList.add('hidden');
    showToast('Session cancelled', 'success');
    import('./session-list.js').then(mod => mod.refreshSessionList()).catch(() => {});
  } catch (err) {
    console.warn('Cancel failed:', err);
  }
};
