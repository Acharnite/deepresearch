/* ── View switching ───────────────────────────────── */
import { getState } from '../state.js';
import { stopSystemLogAutoRefresh, startSystemLogAutoRefresh } from './system-log.js';
import { refreshSessionList } from './session-list.js';
import { loadSettingsView } from './settings.js';
import { showDetail } from './session-detail.js';

export function showView(view) {
  const state = getState();
  // Stop system log auto-refresh if leaving that view
  if (view !== document.getElementById('systemLogView')) {
    stopSystemLogAutoRefresh();
  }
  const views = ['sessionListView', 'newResearchView', 'detailView', 'settingsView', 'systemLogView'];
  views.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  });
  if (view) view.classList.remove('hidden');
}

window.showSessions = function() {
  showView(document.getElementById('sessionListView'));
  const cancelBtn = document.getElementById('cancelBtn');
  const statusBar = document.getElementById('statusBar');
  if (cancelBtn) cancelBtn.classList.add('hidden');
  if (statusBar) statusBar.classList.add('hidden');
  refreshSessionList();
};

window.showNewResearch = function() {
  showView(document.getElementById('newResearchView'));
  const cancelBtn = document.getElementById('cancelBtn');
  const statusBar = document.getElementById('statusBar');
  if (cancelBtn) cancelBtn.classList.add('hidden');
  if (statusBar) statusBar.classList.add('hidden');
};

window.showSettings = function() {
  showView(document.getElementById('settingsView'));
  const cancelBtn = document.getElementById('cancelBtn');
  const statusBar = document.getElementById('statusBar');
  if (cancelBtn) cancelBtn.classList.add('hidden');
  if (statusBar) statusBar.classList.add('hidden');
  loadSettingsView();
};

window.showSystemLogView = function() {
  showView(document.getElementById('systemLogView'));
  startSystemLogAutoRefresh();
};

window.showDetail = showDetail;
