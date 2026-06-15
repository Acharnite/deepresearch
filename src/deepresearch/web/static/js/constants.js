/* ── Constants ─────────────────────────────────────── */

export const STATE_ORDER = ['IDLE','CONFIGURING','ROUND1','COLLABORATING','FOLLOWUP','REFINING','ROUND2','COMPILING','OUTPUT','COMPLETE'];

export const STATE_LABELS = {
  IDLE: 'Idle', CONFIGURING: 'Configuring', ROUND1: 'Round 1',
  COLLABORATING: 'Collaborating', FOLLOWUP: 'Follow-up',
  ROUND2: 'Round 2', REFINING: 'Refining', COMPILING: 'Compiling',
  OUTPUT: 'Output', COMPLETE: 'Complete'
};

export const EVENT_ICONS = {
  session_start: '🚀', round_start: '🔵', agent_start: '🔄', agent_complete: '🟣',
  collaboration_phase: '🤝', scribe_start: '📝', scribe_end: '✅',
  pdf_generated: '📄', session_end: '🏁', agent_failed: '❌',
  session_timeout: '⏰', config_validated: '⚙', models_assigned: '📋',
  session_error: '💥',
  agent_output: '💬',
  followup_start: '❓', followup_complete: '✅', round2_skip: '⏭', reports_collected: '📋', pipeline_summary: '📊',
};

export const STATE_BADGE_CLASSES = {
  IDLE: 'badge-idle', CONFIGURING: 'badge-configuring',
  ROUND1: 'badge-round1', COLLABORATING: 'badge-collaborating',
  FOLLOWUP: 'badge-followup', ROUND2: 'badge-round2',
  COMPILING: 'badge-compiling', OUTPUT: 'badge-output',
  COMPLETE: 'badge-complete'
};

export const STATE_COLORS = {
  IDLE: '#8b949e', CONFIGURING: '#3fb950', ROUND1: '#58a6ff',
  COLLABORATING: '#bc8cff', FOLLOWUP: '#d29922',
  ROUND2: '#39d2c0', COMPILING: '#f778ba',
  OUTPUT: '#3fb950', COMPLETE: '#3fb950'
};

export const stateLabels = {
  waiting: '⏳ Waiting', researching: '🔬 Researching', searching: '🔍 Web Search',
  writing: '✍️ Writing', questioning: '❓ Questioning', answering: '💬 Answering',
  refining: '🔄 Refining', done: '✅ Done', failed: '❌ Failed'
};
