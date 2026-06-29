/* ── Session list view (Alpine-reactive) ────────────
 *     Rendering and DOM management handled by Alpine.js.
 *     This module manages data fetching only.
 * ───────────────────────────────────────────────────── */
import { getState } from '../state.js';
import { showToast } from '../helpers.js';
import {
  fetchSessions, cancelSessionAPI, deleteSessionAPI,
  clearAllSessionsAPI, fetchSessionDetailAPI,
  bulkDeleteSessionsAPI,
} from '../api.js';

// ── Data fetching ─────────────────────────────────
async function loadSessions() {
  const data = await fetchSessions({});
  return data.sessions || data;
}

// ── Main refresh — writes to Alpine store ─────────
export async function refreshSessionList() {
  try {
    const sessions = await loadSessions();
    if (window.Alpine) {
      Alpine.store('sessions').setList(sessions);
    }
    // Legacy DOM update for non-Alpine fallback
    const sessionCount = document.getElementById('sessionCount');
    if (sessionCount && !window.Alpine) {
      sessionCount.textContent = sessions.length + ' session' + (sessions.length !== 1 ? 's' : '');
    }
  } catch (e) {
    console.warn('refreshSessionList failed:', e);
  }
}

// ── Polling ────────────────────────────────────────
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

// ── Window-accessible actions ──────────────────────

window.downloadSessionPDF = async function(sessionId) {
  try {
    const data = await fetchSessionDetailAPI(sessionId);
    const pdfFname = data?.result?.pdf_filename || 'research.pdf';
    window.location.href = '/api/download/' + sessionId + '/' + pdfFname;
  } catch (e) {
    window.location.href = '/api/download/' + sessionId + '/research.pdf';
  }
};

window.downloadSessionHTML = async function(sessionId) {
  try {
    const data = await fetchSessionDetailAPI(sessionId);
    const htmlPath = data?.result?.html_path;
    if (htmlPath) {
      const htmlFname = htmlPath.split('/').pop();
      window.location.href = '/api/download/' + sessionId + '/' + htmlFname;
    }
  } catch (e) {
    showToast('Failed to download HTML', 'error');
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
  if (!confirm('Remove all completed/error/cancelled/interrupted sessions?')) return;
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

// ── Bulk delete (Alpine-compatible) ────────────────
window.bulkDeleteFromAlpine = async function() {
  if (!window.Alpine) return;
  const store = Alpine.store('sessions');
  if (store.selectedIds.length === 0) return;
  if (!confirm('Delete ' + store.selectedIds.length + ' session(s)?')) return;

  try {
    const resp = await bulkDeleteSessionsAPI(store.selectedIds);
    if (resp.ok) {
      showToast(store.selectedIds.length + ' session(s) deleted', 'success');
      store.selectedIds = [];
      refreshSessionList();
    } else {
      const data = await resp.json();
      showToast(data.error || 'Bulk delete failed', 'error');
    }
  } catch (e) {
    showToast('Bulk delete failed: ' + e.message, 'error');
  }
};
