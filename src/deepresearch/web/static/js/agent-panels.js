/* ── Agent progress panel rendering ───────────────── */
import { getState } from './state.js';
import { stateLabels } from './constants.js';
import { esc } from './helpers.js';

export function renderAgents() {
  const state = getState();
  const agents = state.agents;
  const ids = Object.keys(agents);

  console.log('[Agent Debug] renderAgents called, agents count:', ids.length);

  // Save scroll position of the agent column container (FIX 4)
  const col1El = document.getElementById('agentColumn1');
  const savedColumnScrollTop = col1El ? col1El.scrollTop : 0;

  if (ids.length === 0) {
    if (col1El) {
      col1El.innerHTML = '<div class="card">' +
        '<div class="card-header"><span>🤖 Agent Progress</span><span id="agentCount" class="text-muted">0 agents</span></div>' +
        '<div class="agent-list"><div class="empty-state"><h3>No agent data</h3><p>Waiting for session to start...</p></div></div></div>';
    }
    return;
  }

  // Save collapsed state (FIX 1) and output text before re-render
  const savedCollapsed = {};
  const savedOutputs = {};
  const savedScrollTops = {};
  for (const id of ids) {
    const existing = document.getElementById('agent-output-' + id);
    if (existing) {
      savedCollapsed[id] = (existing.style.display === 'none');
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
      // Output panel starts collapsed (FIX 5)
      html += '<div class="agent-output" id="agent-output-' + id + '" data-agent="' + id + '" style="display:none;">' +
        '<pre class="agent-output-text"></pre>' +
      '</div>';
      html += '</div>'; // close agent-section
    }
    html += '</div></div>';
    return html;
  }

  // Render all agents in a single column
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

  // Restore saved output text after re-render
  for (const id of ids) {
    if (savedOutputs[id]) {
      const newPanel = document.getElementById('agent-output-' + id);
      if (newPanel) {
        const pre = newPanel.querySelector('.agent-output-text');
        if (pre) {
          pre.textContent = savedOutputs[id];
          // Restore collapsed state (FIX 1)
          if (savedCollapsed[id]) {
            newPanel.style.display = 'none';
            const section = document.getElementById('agent-section-' + id);
            const btn = section ? section.querySelector('.agent-toggle') : null;
            if (btn) btn.textContent = '▸';
          } else {
            newPanel.style.display = 'block';
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

  // Restore column scroll position (FIX 4)
  if (col1El) col1El.scrollTop = savedColumnScrollTop;
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
