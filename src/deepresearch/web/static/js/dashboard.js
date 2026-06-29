/* ── DeepeResearch Dashboard entry point ────────────
 *     ES module entry — imports all modules, initializes
 * ───────────────────────────────────────────────────── */
import { loadVersion, loadAgentProfiles } from './api.js';
import { loadAvailableModels } from './model-picker.js';
import { startSessionListPolling } from './views/session-list.js';
import './views/index.js';       // registers window.showSessions, showSettings, etc.
import './views/session-detail.js';  // registers window.showDetail, startResearch, cancelResearch
import './views/session-list.js';    // registers window.downloadSessionPDF, deleteSession, etc.
import './views/settings.js';        // registers window.saveApiKey, deleteApiKey, addEndpoint, etc.
import './views/system-log.js';     // registers window.clearSystemLog, refreshSystemLog
import './qa-graph.js';             // registers window.addQAInteraction, renderQAGraph

// ── Alpine.js version bridge ──────────────────────
loadVersion().then(v => {
  if (window.Alpine) {
    Alpine.store('app').version = v;
  }
});

// ── Init ─────────────────────────────────────────────
loadAgentProfiles();
loadAvailableModels();
startSessionListPolling();
