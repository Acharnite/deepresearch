/* ── System log viewer ────────────────────────────── */
import { getState } from '../state.js';
import { esc } from '../helpers.js';
import { fetchSystemLog, clearSystemLogAPI } from '../api.js';

export async function refreshSystemLog() {
  const container = document.getElementById('systemLogContainer');
  const level = document.getElementById('logLevelFilter')?.value || '';
  if (!container) return;

  try {
    const entries = await fetchSystemLog(200, level);

    if (entries.length === 0) {
      container.innerHTML = '<div class="empty-state" style="padding:40px;"><h3>No log entries</h3><p>System log is empty.</p></div>';
      return;
    }

    container.innerHTML = entries.map(function(e) {
      const time = new Date(e.timestamp);
      const timeStr = time.toLocaleTimeString();
      const levelClass = e.level;
      const msg = esc(e.message);
      const logger = esc(e.logger || '');
      return '<div class="log-entry">' +
        '<span class="log-time">' + timeStr + '</span>' +
        '<span class="log-level ' + levelClass + '">' + levelClass + '</span>' +
        '<span class="log-message">' + msg + '</span>' +
        '<span class="log-logger" title="' + logger + '">' + logger + '</span>' +
      '</div>';
    }).join('');

    container.scrollTop = 0;
  } catch (err) {
    container.innerHTML = '<div class="empty-state" style="padding:40px;"><h3>Failed to load log</h3><p>' + esc(err.message) + '</p></div>';
  }
}

export function startSystemLogAutoRefresh() {
  const state = getState();
  refreshSystemLog();
  if (state.systemLogInterval) clearInterval(state.systemLogInterval);
  state.systemLogInterval = setInterval(refreshSystemLog, 10000);
}

export function stopSystemLogAutoRefresh() {
  const state = getState();
  if (state.systemLogInterval) {
    clearInterval(state.systemLogInterval);
    state.systemLogInterval = null;
  }
}

window.clearSystemLog = async function() {
  if (!confirm('Clear all log entries?')) return;
  try {
    await clearSystemLogAPI();
    refreshSystemLog();
  } catch (e) {
    console.warn('Failed to clear log:', e);
  }
};

window.refreshSystemLog = refreshSystemLog;
