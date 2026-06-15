/* ── Agent progress panel rendering ───────────────── */
import { getState } from './state.js';
import { stateLabels } from './constants.js';
import { esc } from './helpers.js';

export function renderAgents() {
  const state = getState();
  const agents = state.agents;
  const ids = Object.keys(agents);

  console.log('[Agent Debug] renderAgents called, agents count:', ids.length);

  if (ids.length === 0) {
    const col1 = document.getElementById('agentColumn1');
    const col2 = document.getElementById('agentColumn2');
    if (col1) {
      col1.innerHTML = '<div class="card">' +
        '<div class="card-header"><span>🤖 Agent Progress</span><span id="agentCount" class="text-muted">0 agents</span></div>' +
        '<div class="agent-list"><div class="empty-state"><h3>No agent data</h3><p>Waiting for session to start...</p></div></div></div>';
    }
    if (col2) col2.innerHTML = '';
    document.querySelectorAll('.scribe-row-container').forEach(el => el.remove());
    return;
  }

  // Save existing agent output text before re-render
  const savedOutputs = {};
  for (const id of ids) {
    const existing = document.getElementById('agent-output-' + id);
    if (existing) {
      const pre = existing.querySelector('.agent-output-text');
      if (pre) savedOutputs[id] = pre.textContent;
    }
  }
  // Save scribe output before re-render
  let savedScribeOutput = '';
  const existingScribe = document.getElementById('agent-output-scribe');
  if (existingScribe) {
    const scribePre = existingScribe.querySelector('.agent-output-text');
    if (scribePre) savedScribeOutput = scribePre.textContent;
    if (existingScribe._outputTimer) {
      clearTimeout(existingScribe._outputTimer);
      existingScribe._outputTimer = null;
    }
    existingScribe._outputBuffer = '';
  }

  // Split agents into two columns
  const mid = Math.ceil(ids.length / 2);
  const col1Ids = ids.slice(0, mid);
  const col2Ids = ids.slice(mid);

  function renderAgentCard(agentIds) {
    let html = '<div class="card"><div class="card-header"><span>🤖 Agent Progress</span><span id="agentCount" class="text-muted">' + agentIds.length + ' agent' + (agentIds.length !== 1 ? 's' : '') + '</span></div><div class="agent-list">';
    for (const id of agentIds) {
      const info = agents[id] || {};
      const status = info.status || 'waiting';
      const stateClass = info.state || status || 'waiting';
      const label = stateLabels[stateClass] || stateClass;
      const name = state.agentNames[id] || id;
      const emoji = state.agentEmojis[id] || '🤖';

      html += '<div class="agent-row">' +
        '<span class="agent-emoji">' + emoji + '</span>' +
        '<span class="agent-name">' + esc(name) + '</span>' +
        '<span class="state-badge state-' + stateClass + '">' + label + '</span>' +
      '</div>';
    }
    // Output panels for each agent
    for (const id of agentIds) {
      html += '<div class="agent-output" id="agent-output-' + id + '" style="display:none;">' +
        '<pre class="agent-output-text"></pre>' +
      '</div>';
    }
    html += '</div></div>';
    return html;
  }

  // Render columns
  const col1El = document.getElementById('agentColumn1');
  const col2El = document.getElementById('agentColumn2');
  if (col1El) col1El.innerHTML = renderAgentCard(col1Ids);
  if (col2El) col2El.innerHTML = renderAgentCard(col2Ids);

  // Remove any previous scribe containers
  document.querySelectorAll('.scribe-row-container').forEach(el => el.remove());

  // ── Scribe row (spans full width below the grid) ──
  const sc = state.scribeInfo || { status: 'waiting' };
  const scStateClass = sc.state || sc.status || "waiting";
  const scLabel = stateLabels[scStateClass] || scStateClass;
  const scDisplay = (sc.status === 'running' || sc.status === 'done') ? 'block' : 'none';

  const scribeHtml = '<div class="scribe-row-container" style="max-width:1200px;margin:8px auto 0;padding:0 24px;">' +
    '<div class="card">' +
    '<div class="agent-list">' +
    '<div class="agent-row scribe-row">' +
    '<span class="agent-emoji">📝</span>' +
    '<span class="agent-name">Scribe</span>' +
    '<span class="state-badge state-' + scStateClass + '">' + scLabel + '</span>' +
    '</div>' +
    '<div class="agent-output" id="agent-output-scribe" style="display:' + scDisplay + ';">' +
    '<pre class="agent-output-text"></pre>' +
    '</div>' +
    '</div>' +
    '</div>' +
  '</div>';

  // Insert scribe row after the progress view
  const progressView = document.getElementById('progressView');
  if (progressView) {
    progressView.insertAdjacentHTML('afterend', scribeHtml);
  }

  // Restore saved output text after re-render
  for (const id of ids) {
    if (savedOutputs[id]) {
      const newPanel = document.getElementById('agent-output-' + id);
      if (newPanel) {
        const pre = newPanel.querySelector('.agent-output-text');
        if (pre) {
          pre.textContent = savedOutputs[id];
          newPanel.style.display = 'block';
        }
      }
    }
  }
  // Restore scribe output after re-render
  if (savedScribeOutput) {
    const newScribePanel = document.getElementById('agent-output-scribe');
    if (newScribePanel) {
      const scribePre = newScribePanel.querySelector('.agent-output-text');
      if (scribePre) {
        scribePre.textContent = savedScribeOutput;
        newScribePanel.style.display = 'block';
      }
    }
  }
}
