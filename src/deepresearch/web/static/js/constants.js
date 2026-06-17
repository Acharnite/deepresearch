/* ── Constants ─────────────────────────────────────── */

export const STATE_ORDER = ['IDLE','CONFIGURING','ROUND1','COLLABORATING','FOLLOWUP','REFINING','ROUND2','CLARIFYING','COMPILING','OUTPUT','COMPLETE'];

export const STATE_LABELS = {
  IDLE: 'Idle', CONFIGURING: 'Configuring', ROUND1: 'Round 1',
  COLLABORATING: 'Collaborating', FOLLOWUP: 'Follow-Up',
  ROUND2: 'Round 2', REFINING: 'Refining', CLARIFYING: 'Clarifying', COMPILING: 'Compiling',
  OUTPUT: 'Output', COMPLETE: 'Complete',
  ROUND3: 'Round 3', ROUND4: 'Round 4', ROUND5: 'Round 5'
};

export const EVENT_ICONS = {
  session_start: '🚀', round_start: '🔵', agent_start: '🔄', agent_complete: '🟣',
  agent_retry: '🔁',
  collaboration_phase: '🤝', scribe_start: '📝', scribe_end: '✅',
  pdf_generated: '📄', session_end: '🏁', agent_failed: '❌',
  all_agents_failed: '💀',
  session_timeout: '⏰', config_validated: '⚙', models_assigned: '📋',
  session_error: '💥',
  agent_output: '💬',
  followup_start: '❓', followup_complete: '✅', round2_skip: '⏭', reports_collected: '📋', pipeline_summary: '📊',
  refinement_start: '🔄', refinement_complete: '✅',
};

export const STATE_BADGE_CLASSES = {
  IDLE: 'badge-idle', CONFIGURING: 'badge-configuring',
  ROUND1: 'badge-round1', COLLABORATING: 'badge-collaborating',
  FOLLOWUP: 'badge-followup', REFINING: 'badge-refining',
  ROUND2: 'badge-round2', CLARIFYING: 'badge-clarifying', COMPILING: 'badge-compiling',
  OUTPUT: 'badge-output', COMPLETE: 'badge-complete',
  ROUND3: 'badge-round3', ROUND4: 'badge-round4', ROUND5: 'badge-round5'
};

export const STATE_COLORS = {
  IDLE: '#8b949e', CONFIGURING: '#3fb950', ROUND1: '#58a6ff',
  COLLABORATING: '#bc8cff', FOLLOWUP: '#d29922',
  REFINING: '#fb923c', ROUND2: '#39d2c0', CLARIFYING: '#d29922', COMPILING: '#f778ba',
  OUTPUT: '#3fb950', COMPLETE: '#3fb950',
  ROUND3: '#f0883e', ROUND4: '#bc8cff', ROUND5: '#f778ba'
};

export const stateLabels = {
  waiting: '⏳ Waiting', researching: '🔬 Researching', searching: '🔍 Web Search',
  writing: '✍️ Writing', questioning: '❓ Questioning', answering: '💬 Answering',
  refining: '🔄 Refining', retrying: '🔁 Retrying', done: '✅ Done', failed: '❌ Failed'
};
