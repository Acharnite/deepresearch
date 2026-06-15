/* ── Session list view ────────────────────────────── */
import { getState } from '../state.js';
import { esc, showToast, $ } from '../helpers.js';
import { fetchSessions, cancelSessionAPI, deleteSessionAPI, clearAllSessionsAPI, fetchSessionDetailAPI } from '../api.js';

export function formatTimeAgo(isoStr) {
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  } catch (e) { return ''; }
}

export async function refreshSessionList() {
  const state = getState();
  const sessions = await fetchSessions();
  const sessionCount = $('sessionCount');
  const sessionList = $('sessionList');
  if (!sessionList) return;

  if (sessionCount) {
    sessionCount.textContent = sessions.length + ' session' + (sessions.length !== 1 ? 's' : '');
  }

  if (sessions.length === 0) {
    sessionList.innerHTML = '' +
      '<div class="session-empty">' +
        '<h3>No research sessions yet</h3>' +
        '<p>Click "+ New Research" to start your first session.</p>' +
      '</div>';
    return;
  }

  let html = '';
  for (const s of sessions) {
    const indicatorClass = s.status;
    const timeAgo = s.completed_at ? formatTimeAgo(s.completed_at) : (s.created_at ? formatTimeAgo(s.created_at) : '');
    let actionsHtml = '';
    if (s.status === 'complete' && s.session_id) {
      actionsHtml = '' +
        '<button class="btn btn-sm btn-success" onclick="event.stopPropagation();downloadSessionPDF(\'' + s.session_id + '\')">📕 PDF</button>' +
        '<button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteSession(\'' + s.session_id + '\')">🗑 Delete</button>';
    } else if (s.status === 'running') {
      actionsHtml = '<button class="btn btn-sm btn-danger" onclick="event.stopPropagation();cancelSessionId(\'' + s.session_id + '\')">✕ Cancel</button>';
    } else if (s.status === 'error' || s.status === 'cancelled') {
      actionsHtml = '' +
        '<button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();showDetail(\'' + s.session_id + '\')">👁 View</button>' +
        '<button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteSession(\'' + s.session_id + '\')">🗑 Delete</button>';
    }

    html += '<div class="session-row" onclick="window.showDetail(\'' + s.session_id + '\')">' +
      '<span class="session-indicator ' + indicatorClass + '"></span>' +
      '<span class="session-topic">' + esc(s.topic) + '</span>' +
      '<span class="session-status-badge ' + indicatorClass + '">' + esc(s.status) + '</span>' +
      '<span class="session-meta">' + timeAgo + '</span>' +
      '<span class="session-meta">' + esc(s.time_budget) + (s.time_budget_seconds ? ' (' + s.time_budget_seconds + 's)' : '') + '</span>' +
      '<div class="session-actions">' + actionsHtml + '</div>' +
    '</div>';
  }
  sessionList.innerHTML = html;
}

export function startSessionListPolling() {
  const state = getState();
  refreshSessionList();
  if (state.sessionListTimer) clearInterval(state.sessionListTimer);
  state.sessionListTimer = setInterval(refreshSessionList, 3000);
}

export function stopSessionListPolling() {
  const state = getState();
  if (state.sessionListTimer) {
    clearInterval(state.sessionListTimer);
    state.sessionListTimer = null;
  }
}

// ── Window-accessible actions ───────────────────────

window.downloadSessionPDF = async function(sessionId) {
  try {
    const data = await fetchSessionDetailAPI(sessionId);
    const pdfFname = data?.result?.pdf_filename || 'research.pdf';
    window.location.href = '/api/download/' + sessionId + '/' + pdfFname;
  } catch (e) {
    window.location.href = '/api/download/' + sessionId + '/research.pdf';
  }
};

window.deleteSession = async function(sessionId) {
  if (!confirm('Delete this session?')) return;
  try {
    const resp = await deleteSessionAPI(sessionId);
    if (resp.ok) {
      showToast('Session deleted', 'success');
      refreshSessionList();
      const state = getState();
      if (state.currentSessionId === sessionId) {
        window.showSessions();
      }
    } else {
      const data = await resp.json();
      showToast(data.error || 'Failed to delete', 'error');
    }
  } catch (e) {
    showToast('Failed to delete session: ' + e.message, 'error');
  }
};

window.cancelSessionId = async function(sessionId) {
  try {
    await cancelSessionAPI(sessionId);
    showToast('Session cancelled', 'success');
    refreshSessionList();
  } catch (err) {
    console.warn('Cancel failed:', err);
  }
};

window.clearAllSessions = async function() {
  if (!confirm('Remove all completed/error/cancelled sessions?')) return;
  try {
    const resp = await clearAllSessionsAPI();
    if (resp.ok) {
      showToast('Completed sessions cleared', 'success');
      refreshSessionList();
      window.showSessions();
    }
  } catch (e) {
    showToast('Failed to clear: ' + e.message, 'error');
  }
};
