/* ── Event log rendering ──────────────────────────── */
import { getState } from './state.js';
import { EVENT_ICONS } from './constants.js';
import { esc, timestamp } from './helpers.js';

export function addEvent(eventType, data) {
  const state = getState();

  if (eventType === 'agent_output') return;  // Don't add streaming output to event log
  state.eventCount++;

  const eventCountEl = document.getElementById('eventCount');
  if (eventCountEl) {
    eventCountEl.textContent = state.eventCount + ' event' + (state.eventCount !== 1 ? 's' : '');
  }

  const icon = EVENT_ICONS[eventType] || '📌';
  const time = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : timestamp();

  let msg = eventType;
  if (eventType === 'session_start' && data.topic) msg = 'Session started — "' + data.topic + '"';
  else if (eventType === 'round_start') msg = 'Round ' + data.round + ' started';
  else if (eventType === 'agent_start') msg = (state.agentNames[data.agent_id] || data.agent_id) + ' started Round ' + data.round;
  else if (eventType === 'agent_complete') msg = (state.agentNames[data.agent_id] || data.agent_id) + ' completed Round ' + data.round;
  else if (eventType === 'agent_failed') msg = (state.agentNames[data.agent_id] || data.agent_id) + ' failed: ' + (data.error || 'unknown');
  else if (eventType === 'collaboration_phase') msg = 'Collaboration phase (' + (data.shared_agent_count || '?') + ' agents)';
  else if (eventType === 'session_end') msg = 'Session completed';
  else if (eventType === 'session_error') msg = 'Session failed: ' + (data.error || '');
  else if (eventType === 'session_timeout') msg = 'Session timed out after ' + (data.timeout || '?') + 's';
  else if (eventType === 'pdf_generated') msg = 'PDF generated: ' + (data.path || '');
  else if (eventType === 'scribe_start') msg = 'Scribe compiling final paper';
  else if (eventType === 'scribe_end') msg = 'Scribe compilation complete';
  else if (eventType === 'config_validated') msg = 'Configuration validated';
  else if (eventType === 'models_assigned') msg = 'Models assigned to agents';
  else if (eventType === 'followup_start') msg = 'Follow-up phase started (' + (data.active_agents || '?') + ' agents)';
  else if (eventType === 'followup_complete') msg = 'Follow-up complete (' + (data.results || '?') + ' responses)';
  else if (eventType === 'round2_skip') msg = 'Round 2 skipped (' + (data.budget || '?') + ' mode)';
  else if (eventType === 'reports_collected') msg = 'Reports collected from ' + (data.count || '?') + ' agents';
  else if (eventType === 'pipeline_summary') msg = '📊 Pipeline complete: ' + (data.total_agents || '?') + ' agents, ' + (data.failed_agents ? data.failed_agents.length + ' failed' : '0 failed') + ', ' + data.elapsed + 's';
  else if (eventType === 'refinement_start') msg = 'Refinement phase started — agents refining findings';
  else if (eventType === 'refinement_complete') msg = 'Refinement complete (' + (data.refined_agents || '0') + ' agents refined)';

  const empty = document.getElementById('emptyLog');
  if (empty) empty.remove();

  const eventLog = document.getElementById('eventLog');
  if (!eventLog) return;

  const entry = document.createElement('div');
  entry.className = 'event-entry';
  entry.innerHTML = '<span class="event-icon">' + icon + '</span><span class="event-time">' + time + '</span><span class="event-msg">' + esc(msg) + '</span>';
  eventLog.appendChild(entry);
  eventLog.scrollTop = eventLog.scrollHeight;
}
