/* ── Visual Q&A Graph — SVG-based agent interaction visualization ── */
import { getState } from './state.js';

const AGENT_COLORS = {
  'curious-teen': '#58a6ff',
  'skeptical-academic': '#3fb950',
  'creative-artist': '#bc8cff',
  'pragmatic-engineer': '#39d2c0',
  'philosophical-thinker': '#f0883e',
  'data-analyst': '#f778ba',
  'scribe': '#ff6b6b',
};

const DEFAULT_COLOR = '#8b949e';

// Layout: agents arranged in a circle around the center
function computeLayout(agentIds, width, height) {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) * 0.35;
  const positions = {};

  agentIds.forEach((id, i) => {
    const angle = (2 * Math.PI * i) / agentIds.length - Math.PI / 2;
    positions[id] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    };
  });

  return positions;
}

export function renderQAGraph(container, interactions) {
  if (!container || !interactions || interactions.length === 0) {
    if (container) container.innerHTML = '';
    return;
  }

  const state = getState();
  const agentIds = Object.keys(state.agents);
  const allIds = agentIds.includes('scribe') ? agentIds : [...agentIds, 'scribe'];

  const width = container.clientWidth || 300;
  const height = 200;

  const positions = computeLayout(allIds, width, height);

  // Build SVG
  let svg = '<svg viewBox="0 0 ' + width + ' ' + height + '" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:' + height + 'px;">';

  // Defs: arrowheads + glow filter
  svg += '<defs>' +
    '<marker id="arrowhead-blue" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">' +
      '<polygon points="0 0, 8 3, 0 6" fill="#58a6ff" />' +
    '</marker>' +
    '<marker id="arrowhead-orange" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">' +
      '<polygon points="0 0, 8 3, 0 6" fill="#f0883e" />' +
    '</marker>' +
    '<filter id="glow">' +
      '<feGaussianBlur stdDeviation="3" result="blur" />' +
      '<feMerge>' +
        '<feMergeNode in="blur" />' +
        '<feMergeNode in="SourceGraphic" />' +
      '</feMerge>' +
    '</filter>' +
  '</defs>';

  // Draw arrows for recent interactions (last 5)
  const recentInteractions = interactions.slice(-5);
  recentInteractions.forEach((interaction, idx) => {
    const from = positions[interaction.from];
    const to = positions[interaction.to];
    if (!from || !to) return;

    const isClarification = interaction.type === 'clarification';
    const color = isClarification ? '#f0883e' : '#58a6ff';
    const markerId = isClarification ? 'arrowhead-orange' : 'arrowhead-blue';
    const opacity = 0.4 + (idx / recentInteractions.length) * 0.6;
    const isLatest = idx === recentInteractions.length - 1;
    const filterAttr = isLatest ? ' filter="url(#glow)"' : '';
    const strokeWidth = isLatest ? 2.5 : 1.5;

    // Offset arrow slightly so reverse arrows don't overlap
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const nx = -dy / len * 3;
    const ny = dx / len * 3;

    svg += '<line x1="' + (from.x + nx) + '" y1="' + (from.y + ny) +
      '" x2="' + (to.x + nx) + '" y2="' + (to.y + ny) +
      '" stroke="' + color + '" stroke-width="' + strokeWidth +
      '" opacity="' + opacity + '" marker-end="url(#' + markerId + ')"' +
      filterAttr + ' />';
  });

  // Draw agent nodes
  allIds.forEach(id => {
    const pos = positions[id];
    if (!pos) return;
    const color = AGENT_COLORS[id] || DEFAULT_COLOR;
    const emoji = state.agentEmojis[id] || (id === 'scribe' ? '\u270d\ufe0f' : '\ud83e\udd16');
    const name = state.agentNames[id] || id;
    const displayName = name.length > 12 ? name.slice(0, 11) + '\u2026' : name;

    svg += '<circle cx="' + pos.x + '" cy="' + pos.y + '" r="18" fill="' + color + '22" stroke="' + color + '" stroke-width="2" />';
    svg += '<text x="' + pos.x + '" y="' + (pos.y - 2) + '" text-anchor="middle" font-size="14">' + emoji + '</text>';
    svg += '<text x="' + pos.x + '" y="' + (pos.y + 14) + '" text-anchor="middle" font-size="8" fill="' + color + '" font-weight="500">' + displayName + '</text>';
  });

  svg += '</svg>';
  container.innerHTML = svg;
}

// Store interactions globally for the graph
window._qaInteractions = window._qaInteractions || [];

export function addQAInteraction(from, to, type, question) {
  window._qaInteractions.push({ from: to, to: from, type: type, question: question, time: Date.now() });
  // Keep only last 10
  if (window._qaInteractions.length > 10) {
    window._qaInteractions = window._qaInteractions.slice(-10);
  }
}

// Expose for event handlers
window.addQAInteraction = addQAInteraction;
window.renderQAGraph = renderQAGraph;
