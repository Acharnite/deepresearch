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
    if (col1) {
      col1.innerHTML = '<div class="card">' +
        '<div class="card-header"><span>🤖 Agent Progress</span><span id="agentCount" class="text-muted">0 agents</span></div>' +
        '<div class="agent-list"><div class="empty-state"><h3>No agent data</h3><p>Waiting for session to start...</p></div></div></div>';
    }
    return;
  }

  // Save existing agent output text and scroll positions before re-render
  const savedOutputs = {};
  const savedScrollTops = {};
  for (const id of ids) {
    const existing = document.getElementById('agent-output-' + id);
    if (existing) {
      const pre = existing.querySelector('.agent-output-text');
      if (pre) savedOutputs[id] = pre.textContent;
      savedScrollTops[id] = existing.scrollTop;
    }
  }

  // Render all agents in a single column
  function renderAgentCard(agentIds) {
    let html = '<div class="card"><div class="card-header"><span>🤖 Agent Progress</span><span id="agentCount" class="text-muted">' + agentIds.length + ' agent' + (agentIds.length !== 1 ? 's' : '') + '</span></div><div class="agent-list">';
    for (const id of agentIds) {
      const info = agents[id] || {};
      const status = info.status || 'waiting';
      const stateClass = info.state || status || 'waiting';
      const label = stateLabels[stateClass] || stateClass;
      const name = state.agentNames[id] || id;
      const emoji = state.agentEmojis[id] || '🤖';

      // Agent header row + output panel together (no interleaving)
      html += '<div class="agent-section" id="agent-section-' + id + '">';
      html += '<div class="agent-row">' +
        '<span class="agent-emoji">' + emoji + '</span>' +
        '<span class="agent-name">' + esc(name) + '</span>' +
        '<span class="state-badge state-' + stateClass + '">' + label + '</span>' +
        '<button class="agent-toggle" onclick="toggleAgentOutput(\'' + id + '\')" title="Toggle log">▾</button>' +
      '</div>';
      // Output panel immediately after this agent's header
      html += '<div class="agent-output" id="agent-output-' + id + '" data-agent="' + id + '" style="display:none;">' +
        '<pre class="agent-output-text"></pre>' +
      '</div>';
      html += '</div>'; // close agent-section
    }
    html += '</div></div>';
    return html;
  }

  // Render all agents in a single column
  const col1El = document.getElementById('agentColumn1');
  if (col1El) col1El.innerHTML = renderAgentCard(ids);

  // Update scribe state badge in sidebar (static HTML)
  const sc = state.scribeInfo || { status: 'waiting', state: 'waiting' };
  const scStateClass = sc.state || sc.status || "waiting";
  const scLabel = stateLabels[scStateClass] || scStateClass;
  const scDisplay = (sc.status === 'running' || sc.status === 'done') ? 'block' : 'none';

  const scribeBadge = document.getElementById('scribeStateBadge');
  if (scribeBadge) {
    scribeBadge.className = 'state-badge state-' + scStateClass;
    scribeBadge.textContent = scLabel;
  }

  // Show/hide scribe output panel
  const scribePanel = document.getElementById('agent-output-scribe');
  if (scribePanel) {
    scribePanel.style.display = scDisplay;
  }

  // Restore saved output text and scroll positions after re-render
  for (const id of ids) {
    if (savedOutputs[id]) {
      const newPanel = document.getElementById('agent-output-' + id);
      if (newPanel) {
        const pre = newPanel.querySelector('.agent-output-text');
        if (pre) {
          pre.textContent = savedOutputs[id];
          newPanel.style.display = 'block';
          // Restore scroll position (scroll to bottom if was at bottom)
          if (savedScrollTops[id] !== undefined) {
            newPanel.scrollTop = savedScrollTops[id];
          } else {
            newPanel.scrollTop = newPanel.scrollHeight;
          }
        }
      }
    }
  }
}

/* ── Collapsible agent output toggle ───────────────── */
export function toggleAgentOutput(agentId) {
  const panel = document.getElementById('agent-output-' + agentId);
  const section = document.getElementById('agent-section-' + agentId);
  if (!panel) return;
  const btn = section ? section.querySelector('.agent-toggle') : null;
  if (panel.style.display === 'none' || !panel.style.display) {
    panel.style.display = 'block';
    if (btn) btn.textContent = '▴';
    panel.scrollTop = panel.scrollHeight;
  } else {
    panel.style.display = 'none';
    if (btn) btn.textContent = '▾';
  }
}

// Expose toggle for onclick handlers (ES module → global)
window.toggleAgentOutput = toggleAgentOutput;
