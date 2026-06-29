/* ── Visual Q&A Graph — SVG-based interactive agent interaction graph ── */
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

// Arrow colors per interaction type
const EDGE_STYLES = {
  clarification: { color: '#58a6ff', markerId: 'arrowhead-blue', label: 'Clarification request' },
  response:      { color: '#f0883e', markerId: 'arrowhead-orange', label: 'Response' },
  question:      { color: '#f0883e', markerId: 'arrowhead-orange', label: 'Question' },
  followup:      { color: '#3fb950', markerId: 'arrowhead-green', label: 'Follow-up' },
};

// ── Circular layout ─────────────────────────────────
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

// ── Calculate perpendicular offset for parallel edges ──
function getOffset(from, to, index, total) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  const offset = total > 1 ? (index - (total - 1) / 2) * 4 : 0;
  return { nx: -dy / len * offset, ny: dx / len * offset };
}

// ── Render SVG graph ─────────────────────────────────
export function renderQAGraph(container, interactions) {
  if (!container) return;

  const state = getState();
  const agentIds = Object.keys(state.agents);
  const allIds = agentIds.includes('scribe') ? agentIds : [...agentIds, 'scribe'];

  const width = container.clientWidth || 300;
  const height = 220;

  const positions = computeLayout(allIds, width, height);

  // Track how many edges exist between each pair for offset calculation
  const edgeCounts = {};
  if (interactions) {
    interactions.forEach(interaction => {
      const key = interaction.from < interaction.to
        ? interaction.from + '|' + interaction.to
        : interaction.to + '|' + interaction.from;
      edgeCounts[key] = (edgeCounts[key] || 0) + 1;
    });
  }

  // Track per-pair edge index for offset
  const edgeIndices = {};

  const hasInteractions = interactions && interactions.length > 0;
  const recentInteractions = hasInteractions ? interactions.slice(-5) : [];
  const lastIdx = hasInteractions ? interactions.length - 1 : -1;

  // Build SVG
  let svg = '<svg class="qa-graph-svg" viewBox="0 0 ' + width + ' ' + height + '" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:' + height + 'px;">';

  // ── Defs: markers + glow + animation ───────────────
  svg += '<defs>' +
    // Blue arrow (clarification: Scribe -> Agent)
    '<marker id="arrowhead-blue" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">' +
      '<polygon points="0 0, 8 3, 0 6" fill="#58a6ff" />' +
    '</marker>' +
    // Orange arrow (response: Agent -> Scribe)
    '<marker id="arrowhead-orange" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">' +
      '<polygon points="0 0, 8 3, 0 6" fill="#f0883e" />' +
    '</marker>' +
    // Green arrow (follow-up: Scribe -> All)
    '<marker id="arrowhead-green" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">' +
      '<polygon points="0 0, 8 3, 0 6" fill="#3fb950" />' +
    '</marker>' +
    // Glow filter for latest interaction
    '<filter id="glow">' +
      '<feGaussianBlur stdDeviation="3" result="blur" />' +
      '<feMerge>' +
        '<feMergeNode in="blur" />' +
        '<feMergeNode in="SourceGraphic" />' +
      '</feMerge>' +
    '</filter>' +
    // Subtle glow for nodes on hover
    '<filter id="node-glow">' +
      '<feGaussianBlur stdDeviation="2" result="blur" />' +
      '<feMerge>' +
        '<feMergeNode in="blur" />' +
        '<feMergeNode in="SourceGraphic" />' +
      '</feMerge>' +
    '</filter>' +
  '</defs>';

  // ── Draw edges (arrows) ────────────────────────────
  if (hasInteractions) {
    recentInteractions.forEach((interaction, idx) => {
      const from = positions[interaction.from];
      const to = positions[interaction.to];
      if (!from || !to) return;

      const style = EDGE_STYLES[interaction.type] || EDGE_STYLES.response;
      const isLatest = (interaction._idx !== undefined ? interaction._idx : (interactions.indexOf(interaction))) === lastIdx;
      const opacity = 0.4 + (idx / recentInteractions.length) * 0.6;

      // Offset for parallel edges between same pair
      const pairKey = interaction.from < interaction.to
        ? interaction.from + '|' + interaction.to
        : interaction.to + '|' + interaction.from;
      edgeIndices[pairKey] = (edgeIndices[pairKey] || 0) + 1;
      const pairTotal = edgeCounts[pairKey] || 1;
      const { nx, ny } = getOffset(from, to, edgeIndices[pairKey] - 1, Math.min(pairTotal, 3));

      // CSS class for animation
      const edgeClass = isLatest ? 'edge-new' : '';

      // Tooltip content
      const fromName = state.agentNames[interaction.from] || interaction.from;
      const toName = state.agentNames[interaction.to] || interaction.to;
      const qText = (interaction.question || '').replace(/"/g, '&quot;');
      const tooltip = style.label + ': ' + fromName + ' → ' + toName + (qText ? '\n' + qText : '');

      svg += '<g class="qa-edge ' + edgeClass + '">' +
        '<line x1="' + (from.x + nx) + '" y1="' + (from.y + ny) +
        '" x2="' + (to.x + nx) + '" y2="' + (to.y + ny) +
        '" stroke="' + style.color + '" stroke-width="' + (isLatest ? 2.5 : 1.5) +
        '" opacity="' + opacity + '" marker-end="url(#' + style.markerId + ')"' +
        (isLatest ? ' filter="url(#glow)"' : '') +
        ' class="qa-edge-line" />' +
        // Invisible wider line for easier hover
        '<line x1="' + (from.x + nx) + '" y1="' + (from.y + ny) +
        '" x2="' + (to.x + nx) + '" y2="' + (to.y + ny) +
        '" stroke="transparent" stroke-width="10" class="qa-edge-hitarea" ' +
        ' data-from="' + interaction.from + '" data-to="' + interaction.to + '" ' +
        ' data-q="' + qText + '" data-type="' + interaction.type + '" />' +
        '<title>' + tooltip + '</title>' +
        '</g>';
    });

    // Draw "no interactions" indicator line if there is at least one interaction
    // (but edges are only shown for the last 5)
    if (interactions.length > 5) {
      // Visual indicator: dimmed text showing count
    }
  }

  // ── Draw agent nodes ───────────────────────────────
  allIds.forEach(id => {
    const pos = positions[id];
    if (!pos) return;
    const color = AGENT_COLORS[id] || DEFAULT_COLOR;
    const emoji = state.agentEmojis[id] || (id === 'scribe' ? '\u270d\ufe0f' : '\ud83e\udd16');
    const name = state.agentNames[id] || id;
    const displayName = name.length > 12 ? name.slice(0, 11) + '\u2026' : name;
    const fullTooltip = name + (id === 'scribe' ? ' (Scribe)' : '');

    svg += '<g class="qa-node">' +
      '<circle cx="' + pos.x + '" cy="' + pos.y + '" r="20" fill="' + color + '18" stroke="' + color + '" stroke-width="2" class="qa-node-circle" />' +
      '<text x="' + pos.x + '" y="' + (pos.y - 2) + '" text-anchor="middle" font-size="14" class="qa-node-emoji">' + emoji + '</text>' +
      '<text x="' + pos.x + '" y="' + (pos.y + 14) + '" text-anchor="middle" font-size="8" fill="' + color + '" font-weight="500" class="qa-node-label">' + displayName + '</text>' +
      '<title>' + fullTooltip + '</title>' +
      '</g>';
  });

  svg += '</svg>';
  container.innerHTML = svg;
}

// ── Interaction store ────────────────────────────────
window._qaInteractions = window._qaInteractions || [];
let _interactionCounter = 0;

export function addQAInteraction(from, to, type, question) {
  const interaction = {
    from: from,
    to: to,
    type: type,
    question: question,
    time: Date.now(),
    _idx: _interactionCounter++,
  };
  window._qaInteractions.push(interaction);
  // Keep only last 10
  if (window._qaInteractions.length > 10) {
    window._qaInteractions = window._qaInteractions.slice(-10);
  }
}

// ── Render helper to find graph container and render ──
export function renderGraph() {
  const qaGraphEl = document.getElementById('qaGraph');
  if (qaGraphEl && window.renderQAGraph) {
    window.renderQAGraph(qaGraphEl, window._qaInteractions || []);
  }
}

// ── Expose for event handlers ────────────────────────
window.addQAInteraction = addQAInteraction;
window.renderQAGraph = renderQAGraph;
