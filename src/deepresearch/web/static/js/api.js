/* ── All API fetch wrappers ────────────────────────── */
import { getState } from './state.js';

export async function loadVersion() {
  try {
    const resp = await fetch('/api/version');
    if (resp.ok) {
      const data = await resp.json();
      const el = document.getElementById('versionDisplay');
      if (el) el.textContent = data.version || '?';
    }
  } catch (e) {
    console.warn('Failed to load version:', e);
  }
}

export async function loadAgentProfiles() {
  try {
    const resp = await fetch('/api/agents');
    if (!resp.ok) return;
    const data = await resp.json();
    if (data && data.length > 0) {
      const st = getState();
      data.forEach(a => {
        if (a.id) {
          st.agentNames[a.id] = a.name || a.id;
          st.agentEmojis[a.id] = a.emoji || '🤖';
        }
      });
    }
  } catch (err) {
    // ignore
  }
}

export async function fetchAvailableModels() {
  try {
    const resp = await fetch('/api/models');
    if (!resp.ok) return [];
    const models = await resp.json();
    getState().availableModels = models;
    return models;
  } catch (err) {
    console.warn('Failed to load models:', err);
    return [];
  }
}

export async function fetchSessions(params) {
  let url = '/api/sessions';
  if (params) {
    const qs = new URLSearchParams();
    if (params.limit != null) qs.set('limit', params.limit);
    if (params.offset != null) qs.set('offset', params.offset);
    if (params.status) qs.set('status', params.status);
    if (params.search) qs.set('search', params.search);
    const str = qs.toString();
    if (str) url += '?' + str;
  }
  const resp = await fetch(url);
  if (!resp.ok) return { sessions: [], total: 0, offset: 0, limit: null };
  return await resp.json();
}

export async function fetchSessionStats() {
  const resp = await fetch('/api/sessions/stats');
  if (!resp.ok) return { total: 0, by_status: {} };
  return await resp.json();
}

export async function startResearchAPI(body) {
  const resp = await fetch('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return resp;
}

export async function cancelSessionAPI(sessionId) {
  await fetch('/api/sessions/' + sessionId + '/cancel', { method: 'POST' });
}

export async function deleteSessionAPI(sessionId) {
  return await fetch('/api/sessions/' + sessionId, { method: 'DELETE' });
}

export async function clearAllSessionsAPI() {
  return await fetch('/api/sessions/clear-completed', { method: 'POST' });
}

export async function bulkDeleteSessionsAPI(sessionIds) {
  return await fetch('/api/sessions/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_ids: sessionIds }),
  });
}

export async function fetchSessionDetailAPI(sessionId) {
  const resp = await fetch('/api/sessions/' + sessionId);
  if (!resp.ok) return null;
  return await resp.json();
}

export async function fetchSessionStateAPI(sessionId) {
  const resp = await fetch('/api/sessions/' + sessionId + '/state');
  if (!resp.ok) return null;
  return await resp.json();
}

export async function fetchProfiles() {
  const resp = await fetch('/api/profiles');
  if (!resp.ok) return [];
  return await resp.json();
}

export async function fetchProviderKeys() {
  const resp = await fetch('/api/settings/keys');
  if (!resp.ok) return {};
  return await resp.json();
}

export async function saveApiKeyAPI(provider, key) {
  return await fetch('/api/settings/keys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, key }),
  });
}

export async function deleteApiKeyAPI(provider) {
  return await fetch('/api/settings/keys/' + provider, { method: 'DELETE' });
}

export async function fetchLocalModels() {
  const resp = await fetch('/api/settings/local-models');
  if (!resp.ok) return [];
  return await resp.json();
}

export async function addEndpointAPI(name, endpoint, type) {
  return await fetch('/api/settings/local-endpoints', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, endpoint, type }),
  });
}

export async function removeEndpointAPI(name) {
  return await fetch('/api/settings/local-endpoints/' + encodeURIComponent(name), { method: 'DELETE' });
}

export async function testEndpointAPI(name) {
  const resp = await fetch('/api/settings/local-endpoints/' + encodeURIComponent(name) + '/test', { method: 'POST' });
  return await resp.json();
}

export async function fetchScribeModelAPI() {
  const resp = await fetch('/api/settings/scribe-model');
  if (!resp.ok) return null;
  const data = await resp.json();
  return data.scribe_model;
}

export async function saveScribeModelAPI(model) {
  return await fetch('/api/settings/scribe-model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scribe_model: model }),
  });
}

export async function clearScribeModelAPI() {
  return await fetch('/api/settings/scribe-model', { method: 'DELETE' });
}

export async function fetchSystemLog(limit, level) {
  const resp = await fetch('/api/system/log?limit=' + encodeURIComponent(limit || 200) + '&level=' + encodeURIComponent(level || ''));
  if (!resp.ok) return [];
  return await resp.json();
}

export async function clearSystemLogAPI() {
  await fetch('/api/system/log/clear', { method: 'POST' });
}

// ── Context Window Overrides ────────────────────────

export async function fetchContextWindows() {
  const resp = await fetch('/api/config/context');
  if (!resp.ok) return {};
  return await resp.json();
}

export async function saveContextWindowAPI(modelId, contextWindow) {
  return await fetch('/api/config/context', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_id: modelId, context_window: contextWindow }),
  });
}

export async function deleteContextWindowAPI(modelId) {
  return await fetch('/api/config/context/' + encodeURIComponent(modelId), {
    method: 'DELETE',
  });
}

// ── Max Tokens per Agent Call ───────────────────────

export async function fetchMaxTokens() {
  const resp = await fetch('/api/settings/max-tokens');
  if (!resp.ok) return 4096;
  const data = await resp.json();
  return data.max_tokens || 4096;
}

export async function saveMaxTokensAPI(maxTokens) {
  return await fetch('/api/settings/max-tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_tokens: maxTokens }),
  });
}

// ── Tools / Hardware (llmfit) ─────────────────────────

export async function fetchToolStatus() {
  const resp = await fetch('/api/tools/status');
  return resp.json();
}

export async function fetchHardwareInfo() {
  const resp = await fetch('/api/hardware');
  return resp.json();
}

export async function fetchModelRecommendations() {
  const resp = await fetch('/api/tools/recommendations');
  if (!resp.ok) return { available: false };
  return await resp.json();
}

// ── Ollama Install ────────────────────────────────────

export async function fetchOllamaStatus() {
  const resp = await fetch('/api/local-backends/ollama/status');
  if (!resp.ok) return { installed: false, running: false };
  return await resp.json();
}

export function getOllamaInstallURL() {
  return '/api/local-backends/ollama/install';
}

// ── Local Backend Management ────────────────────────────

export async function installLlmfit() {
  return '/api/local-backends/llmfit/install';
}

export async function uninstallLlmfit() {
  return await fetch('/api/local-backends/llmfit/uninstall', { method: 'POST' });
}

export async function startOllama() {
  return await fetch('/api/local-backends/ollama/start', { method: 'POST' });
}

export async function stopOllama() {
  return await fetch('/api/local-backends/ollama/stop', { method: 'POST' });
}

export async function uninstallOllama() {
  return '/api/local-backends/ollama/uninstall';
}

export function getPullModelURL() {
  return '/api/local-backends/ollama/pull';
}

export function getDownloadModelURL() {
  return '/api/local-backends/models/download';
}

export function getLlmfitInstallURL() {
  return '/api/local-backends/llmfit/install';
}

export function getOllamaUninstallURL() {
  return '/api/local-backends/ollama/uninstall';
}

export async function fetchLocalBackends() {
  const resp = await fetch('/api/local-backends');
  if (!resp.ok) return { backends: [] };
  return resp.json();
}

export async function testLocalBackend(name) {
  const resp = await fetch(`/api/local-backends/${encodeURIComponent(name)}/test`, { method: 'POST' });
  return resp.json();
}

export async function setBackendAddress(name, address) {
  const resp = await fetch(`/api/local-backends/${encodeURIComponent(name)}/address`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ address }),
  });
  return resp.json();
}

export async function getBackendAddress(name) {
  const resp = await fetch(`/api/local-backends/${encodeURIComponent(name)}/address`);
  if (!resp.ok) return { address: null };
  return resp.json();
}

export async function deleteOllamaModel(modelName) {
  const resp = await fetch(`/api/local-backends/models/${encodeURIComponent(modelName)}`, { method: 'DELETE' });
  return resp.json();
}
