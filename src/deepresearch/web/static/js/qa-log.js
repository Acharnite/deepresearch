/* ── Q&A panel ────────────────────────────────────── */
import { getState } from './state.js';
import { esc } from './helpers.js';

export function addQA(fromAgent, question, toAgent) {
  const state = getState();
  state.qaLog.push({
    from: fromAgent,
    question: question,
    to: toAgent,
    time: new Date().toLocaleTimeString(),
  });
  renderQA();
}

export function renderQA() {
  const state = getState();
  const container = document.getElementById('qaLog');
  const panel = document.getElementById('qaPanel');
  if (!container) return;
  if (state.qaLog.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>No Q&A yet</p></div>';
    if (panel) panel.style.display = 'none';
    return;
  }
  if (panel) panel.style.display = 'block';
  let html = '';
  for (const entry of state.qaLog.slice(-10)) {
    const fromName = state.agentNames[entry.from] || entry.from;
    const toName = state.agentNames[entry.to] || entry.to;
    const q = entry.question || '';
    html += '<div class="qa-entry">' +
      '<span class="qa-time">' + entry.time + '</span>' +
      '<span class="qa-from">' + fromName + '</span>' +
      '<span class="qa-arrow">→</span>' +
      '<span class="qa-to">' + toName + '</span>' +
      '<span class="qa-question">"' + esc(q.substring(0, 60)) + (q.length > 60 ? '...' : '') + '"</span>' +
    '</div>';
  }
  container.innerHTML = html;
}
