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

  // Update Alpine store for reactive view switching (x-show bindings)
  if (window.Alpine && view) {
    var _prev = Alpine.store('app').currentView;
    Alpine.store('app').previousView = _prev;
    Alpine.store('app').currentView = view.id;
  }
}

window.showSessions = function() {
  if (window.Alpine) {
    Alpine.store('app').currentView = 'sessionListView';
  }
  showView(document.getElementById('sessionListView'));
  const cancelBtn = document.getElementById('cancelBtn');
  const statusBar = document.getElementById('statusBar');
  if (cancelBtn) cancelBtn.classList.add('hidden');
  if (statusBar) statusBar.classList.add('hidden');
  refreshSessionList();
};

window.showNewResearch = function() {
  if (window.Alpine) {
    Alpine.store('app').currentView = 'newResearchView';
  }
  showView(document.getElementById('newResearchView'));
  const cancelBtn = document.getElementById('cancelBtn');
  const statusBar = document.getElementById('statusBar');
  if (cancelBtn) cancelBtn.classList.add('hidden');
  if (statusBar) statusBar.classList.add('hidden');
};

window.showSettings = function() {
  if (window.Alpine) {
    Alpine.store('app').currentView = 'settingsView';
  }
  showView(document.getElementById('settingsView'));
  const cancelBtn = document.getElementById('cancelBtn');
  const statusBar = document.getElementById('statusBar');
  if (cancelBtn) cancelBtn.classList.add('hidden');
  if (statusBar) statusBar.classList.add('hidden');
  loadSettingsView();
};

window.showSystemLogView = function() {
  if (window.Alpine) {
    Alpine.store('app').currentView = 'systemLogView';
  }
  showView(document.getElementById('systemLogView'));
  startSystemLogAutoRefresh();
};

window.showDetail = showDetail;

// ── Wire Alpine store action stubs to actual implementations ──
if (window.Alpine) {
  Alpine.store('app').showSessions = window.showSessions;
  Alpine.store('app').showNewResearch = window.showNewResearch;
  Alpine.store('app').showSettings = window.showSettings;
  Alpine.store('app').showSystemLogView = window.showSystemLogView;
}
