/* ── Session list view ────────────────────────────── */
import { getState } from '../state.js';
import { esc, showToast, $ } from '../helpers.js';
import {
  fetchSessions, cancelSessionAPI, deleteSessionAPI,
  clearAllSessionsAPI, fetchSessionDetailAPI,
  bulkDeleteSessionsAPI,
} from '../api.js';

const PAGE_SIZE = 20;

// ── State ──────────────────────────────────────────
let _searchQuery = '';
let _statusFilter = 'all';
let _sortBy = 'newest'; // newest | oldest | status
let _currentPage = 0;
let _totalSessions = 0;
let _debounceTimer = null;

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

// ── API call with filters ──────────────────────────
async function loadSessions() {
  const params = {};
  // Client-side: fetch all, then filter/sort locally for full control
  // But we still use limit/offset for pagination awareness
  // Actually: fetch all (no limit) so we can filter/sort/paginate client-side
  const data = await fetchSessions({});
  return data.sessions || data; // handle old format too
}

// ── Main render ────────────────────────────────────
export async function refreshSessionList() {
  const state = getState();
  const sessions = await loadSessions();
  const sessionCount = $('sessionCount');
  const sessionList = $('sessionList');
  if (!sessionList) return;

  // Filter
  let filtered = sessions;
  sessionListDataCache = JSON.stringify(sessions); // cache for toolbar counts
  if (_searchQuery) {
    const q = _searchQuery.toLowerCase();
    filtered = filtered.filter(s => (s.topic || '').toLowerCase().includes(q));
  }
  if (_statusFilter !== 'all') {
    filtered = filtered.filter(s => s.status === _statusFilter);
  }

  // Sort
  if (_sortBy === 'oldest') {
    filtered.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  } else if (_sortBy === 'status') {
    const order = { running: 0, queued: 1, error: 2, interrupted: 3, cancelled: 4, complete: 5 };
    filtered.sort((a, b) => (order[a.status] ?? 5) - (order[b.status] ?? 5));
  } else {
    // newest (default) — already sorted from API
  }

  _totalSessions = filtered.length;

  if (sessionCount) {
    sessionCount.textContent = filtered.length + ' session' + (filtered.length !== 1 ? 's' : '');
  }

  // Paginate
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  if (_currentPage >= totalPages) _currentPage = totalPages - 1;
  if (_currentPage < 0) _currentPage = 0;
  const startIdx = _currentPage * PAGE_SIZE;
  const page = filtered.slice(startIdx, startIdx + PAGE_SIZE);

  // Build HTML
  let html = renderToolbar(sessions.length);

  if (filtered.length === 0) {
    html += '<div class="session-empty">' +
      '<h3>No research sessions yet</h3>' +
      '<p>Click "+ New Research" to start your first session.</p>' +
    '</div>';
  } else {
    html += '<div class="session-list">';
    for (const s of page) {
      html += renderSessionRow(s);
    }
    html += '</div>';
  }

  // Pagination controls
  if (totalPages > 1) {
    html += renderPagination(totalPages);
  }

  sessionList.innerHTML = html;
  bindToolbarEvents();
  bindBulkEvents();
}

// ── Toolbar HTML ───────────────────────────────────
function renderToolbar(totalCount) {
  const statusCounts = {};
  const allSessions = JSON.parse(sessionListDataCache || '[]');
  for (const s of allSessions) {
    statusCounts[s.status] = (statusCounts[s.status] || 0) + 1;
  }

  return '<div class="session-toolbar">' +
    '<div class="session-toolbar-row">' +
      '<input type="text" class="session-search-input" id="sessionSearchInput" ' +
        'placeholder="Search sessions..." value="' + esc(_searchQuery) + '">' +
      '<select class="session-sort-select" id="sessionSortSelect">' +
        '<option value="newest"' + (_sortBy === 'newest' ? ' selected' : '') + '>Newest first</option>' +
        '<option value="oldest"' + (_sortBy === 'oldest' ? ' selected' : '') + '>Oldest first</option>' +
        '<option value="status"' + (_sortBy === 'status' ? ' selected' : '') + '>By status</option>' +
      '</select>' +
    '</div>' +
    '<div class="session-filter-chips" id="sessionFilterChips">' +
      renderFilterChip('all', 'All', totalCount) +
      renderFilterChip('running', 'Running', statusCounts.running) +
      renderFilterChip('complete', 'Complete', statusCounts.complete) +
      renderFilterChip('error', 'Error', statusCounts.error) +
      renderFilterChip('cancelled', 'Cancelled', statusCounts.cancelled) +
      renderFilterChip('interrupted', 'Interrupted', statusCounts.interrupted) +
    '</div>' +
    renderBulkBar() +
  '</div>';
}

let sessionListDataCache = '';

function renderFilterChip(value, label, count) {
  const active = _statusFilter === value ? ' active' : '';
  const countStr = count != null && count > 0 ? ' <span class="chip-count">' + count + '</span>' : '';
  return '<button class="filter-chip' + active + '" data-filter="' + value + '">' +
    label + countStr + '</button>';
}

function renderBulkBar() {
  return '<div class="bulk-bar" id="bulkBar" style="display:none">' +
    '<label class="bulk-select-all">' +
      '<input type="checkbox" id="bulkSelectAll"> Select All on Page' +
    '</label>' +
    '<button class="btn btn-sm btn-danger" id="bulkDeleteBtn" disabled>Delete Selected</button>' +
  '</div>';
}

// ── Session row HTML ───────────────────────────────
function renderSessionRow(s) {
  const indicatorClass = s.status;
  const timeAgo = s.completed_at ? formatTimeAgo(s.completed_at) : (s.created_at ? formatTimeAgo(s.created_at) : '');
  let actionsHtml = '';

  if (s.status === 'complete' && s.session_id) {
    actionsHtml = '<button class="btn btn-sm btn-success" onclick="event.stopPropagation();downloadSessionPDF(\'' + s.session_id + '\')">📕 PDF</button>';
    // Show HTML download if html_path exists
    if (s.result && s.result.html_path) {
      actionsHtml += '<button class="btn btn-sm btn-primary" onclick="event.stopPropagation();downloadSessionHTML(\'' + s.session_id + '\')">📄 HTML</button>';
    }
    actionsHtml += '<button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteSession(\'' + s.session_id + '\')">🗑 Delete</button>';
  } else if (s.status === 'running') {
    actionsHtml = '<button class="btn btn-sm btn-danger" onclick="event.stopPropagation();cancelSessionId(\'' + s.session_id + '\')">✕ Cancel</button>';
  } else if (s.status === 'error' || s.status === 'cancelled' || s.status === 'interrupted') {
    actionsHtml = '<button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();showDetail(\'' + s.session_id + '\')">👁 View</button>' +
      '<button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteSession(\'' + s.session_id + '\')">🗑 Delete</button>';
  }

  // Bulk checkbox only for deletable sessions
  const canBulk = s.status === 'complete' || s.status === 'error' || s.status === 'cancelled' || s.status === 'interrupted';
  const checkboxHtml = canBulk
    ? '<input type="checkbox" class="bulk-checkbox" data-sid="' + s.session_id + '">'
    : '';

  return '<div class="session-row" onclick="window.showDetail(\'' + s.session_id + '\')">' +
    '<span class="session-bulk-check">' + checkboxHtml + '</span>' +
    '<span class="session-indicator ' + indicatorClass + '"></span>' +
    '<span class="session-topic">' + esc(s.topic) + '</span>' +
    '<span class="session-status-badge ' + indicatorClass + '">' + esc(s.status) + '</span>' +
    '<span class="session-meta">' + timeAgo + '</span>' +
    '<span class="session-meta">' + esc(s.time_budget) + (s.time_budget_seconds ? ' (' + s.time_budget_seconds + 's)' : '') + '</span>' +
    '<div class="session-actions">' + actionsHtml + '</div>' +
  '</div>';
}

// ── Pagination HTML ────────────────────────────────
function renderPagination(totalPages) {
  const prevDisabled = _currentPage === 0 ? ' disabled' : '';
  const nextDisabled = _currentPage >= totalPages - 1 ? ' disabled' : '';
  return '<div class="session-pagination" id="sessionPagination">' +
    '<button class="btn btn-sm btn-secondary pagination-prev"' + prevDisabled + '>← Prev</button>' +
    '<span class="pagination-info">Page ' + (_currentPage + 1) + ' of ' + totalPages + '</span>' +
    '<button class="btn btn-sm btn-secondary pagination-next"' + nextDisabled + '>Next →</button>' +
  '</div>';
}

// ── Event binding ──────────────────────────────────
function bindToolbarEvents() {
  // Search input with debounce
  const searchInput = $('sessionSearchInput');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      clearTimeout(_debounceTimer);
      _debounceTimer = setTimeout(() => {
        _searchQuery = e.target.value;
        _currentPage = 0;
        refreshSessionList();
      }, 250);
    });
  }

  // Sort select
  const sortSelect = $('sessionSortSelect');
  if (sortSelect) {
    sortSelect.addEventListener('change', (e) => {
      _sortBy = e.target.value;
      _currentPage = 0;
      refreshSessionList();
    });
  }

  // Filter chips
  const chips = $('sessionFilterChips');
  if (chips) {
    chips.querySelectorAll('.filter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        _statusFilter = chip.dataset.filter;
        _currentPage = 0;
        refreshSessionList();
      });
    });
  }

  // Pagination
  const pagination = $('sessionPagination');
  if (pagination) {
    pagination.querySelector('.pagination-prev')?.addEventListener('click', () => {
      if (_currentPage > 0) { _currentPage--; refreshSessionList(); }
    });
    pagination.querySelector('.pagination-next')?.addEventListener('click', () => {
      const totalPages = Math.ceil(_totalSessions / PAGE_SIZE);
      if (_currentPage < totalPages - 1) { _currentPage++; refreshSessionList(); }
    });
  }
}

function bindBulkEvents() {
  const selectAll = $('bulkSelectAll');
  const bulkBar = $('bulkBar');
  const deleteBtn = $('bulkDeleteBtn');

  // Show bulk bar if there are deletable sessions
  const checkboxes = document.querySelectorAll('.bulk-checkbox');
  if (checkboxes.length > 0 && bulkBar) {
    bulkBar.style.display = 'flex';
  }

  // Select All toggle
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      const checked = selectAll.checked;
      document.querySelectorAll('.bulk-checkbox').forEach(cb => {
        cb.checked = checked;
      });
      updateBulkDeleteBtn();
    });
  }

  // Individual checkboxes
  document.querySelectorAll('.bulk-checkbox').forEach(cb => {
    cb.addEventListener('change', updateBulkDeleteBtn);
    cb.addEventListener('click', (e) => e.stopPropagation());
  });

  // Bulk delete
  if (deleteBtn) {
    deleteBtn.addEventListener('click', bulkDeleteSelected);
  }
}

function updateBulkDeleteBtn() {
  const checked = document.querySelectorAll('.bulk-checkbox:checked');
  const btn = $('bulkDeleteBtn');
  if (btn) btn.disabled = checked.length === 0;
  btn.textContent = checked.length > 0
    ? 'Delete Selected (' + checked.length + ')'
    : 'Delete Selected';
}

async function bulkDeleteSelected() {
  const checked = document.querySelectorAll('.bulk-checkbox:checked');
  if (checked.length === 0) return;
  const ids = Array.from(checked).map(cb => cb.dataset.sid);
  if (!confirm('Delete ' + ids.length + ' session(s)?')) return;

  try {
    const resp = await bulkDeleteSessionsAPI(ids);
    if (resp.ok) {
      showToast(ids.length + ' session(s) deleted', 'success');
      refreshSessionList();
    } else {
      const data = await resp.json();
      showToast(data.error || 'Bulk delete failed', 'error');
    }
  } catch (e) {
    showToast('Bulk delete failed: ' + e.message, 'error');
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
