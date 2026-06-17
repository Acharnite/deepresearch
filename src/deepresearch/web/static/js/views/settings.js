/* ── Settings view — API keys, local models, scribe ── */
import { getState } from '../state.js';
import { esc, showToast, formatSize, $ } from '../helpers.js';
import {
  fetchProviderKeys, saveApiKeyAPI, deleteApiKeyAPI,
  fetchLocalModels, addEndpointAPI, removeEndpointAPI, testEndpointAPI,
  fetchScribeModelAPI, saveScribeModelAPI, clearScribeModelAPI,
  fetchContextWindows, saveContextWindowAPI, deleteContextWindowAPI,
  fetchMaxTokens, saveMaxTokensAPI
} from '../api.js';
import { ModelPicker } from '../model-picker.js';

// ── API Keys tab ────────────────────────────────────
async function loadProviderList() {
  try {
    const data = await fetchProviderKeys();

    let html = '';
    for (const [id, info] of Object.entries(data)) {
      const statusClass = info.configured ? 'configured' : 'missing';
      const statusText = info.configured ? '✅ Configured' : '❌ Missing';
      const preview = info.key_preview ? ' (' + info.key_preview + ')' : '';

      html += '<div class="provider-row" data-provider="' + esc(id) + '">' +
        '<div class="provider-name">' + esc(info.name) + '</div>' +
        '<div class="provider-status ' + statusClass + '">' + statusText + preview + '</div>' +
        '<div class="provider-input">' +
          '<input type="password" id="key-input-' + esc(id) + '" placeholder="Paste API key..." />' +
        '</div>' +
        '<div class="provider-actions">' +
          '<button class="btn btn-sm btn-primary" onclick="window.saveApiKey(\'' + esc(id) + '\')">Save</button>' +
          (info.configured ? '<button class="btn btn-sm btn-danger" onclick="window.deleteApiKey(\'' + esc(id) + '\')">Delete</button>' : '') +
        '</div>' +
      '</div>';
    }
    const list = $('providerList');
    if (list) list.innerHTML = html;
  } catch (err) {
    const list = $('providerList');
    if (list) list.innerHTML = '<div class="text-muted" style="padding:12px;">Failed to load providers.</div>';
  }
}

window.saveApiKey = async function(provider) {
  const input = document.getElementById('key-input-' + provider);
  if (!input) return;
  const key = input.value.trim();
  if (!key) { showToast('Please enter an API key.', 'error'); return; }

  try {
    const resp = await saveApiKeyAPI(provider, key);
    if (resp.ok) {
      showToast('API key saved for ' + provider, 'success');
      input.value = '';
      loadProviderList();
    } else {
      const err = await resp.json();
      showToast('Error: ' + (err.error || 'Failed to save'), 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

window.deleteApiKey = async function(provider) {
  try {
    const resp = await deleteApiKeyAPI(provider);
    if (resp.ok) {
      showToast('API key deleted for ' + provider, 'success');
      loadProviderList();
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

// ── Local Models tab ────────────────────────────────
async function loadDiscoveredModels() {
  try {
    const models = await fetchLocalModels();
    const ollamaModels = models.filter(m => m.source === 'ollama');

    let html = '';
    if (ollamaModels.length === 0) {
      html = '<div class="text-muted" style="padding:12px;font-size:13px;">No Ollama models detected. Start Ollama to auto-discover.</div>';
    } else {
      for (const m of ollamaModels) {
        html += '<div class="endpoint-row">' +
          '<span class="endpoint-name">' + esc(m.name) + '</span>' +
          '<span class="endpoint-url">' + esc(m.endpoint) + '</span>' +
          '<span class="endpoint-type">Ollama</span>' +
          (m.size ? '<span class="text-muted" style="font-size:11px;">' + formatSize(m.size) + '</span>' : '') +
        '</div>';
      }
    }
    const el = $('discoveredModels');
    if (el) el.innerHTML = html;
  } catch (err) {
    const el = $('discoveredModels');
    if (el) el.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">Could not scan for local models.</div>';
  }
}

async function loadEndpointList() {
  try {
    const models = await fetchLocalModels();
    const saved = models.filter(m => m.source !== 'ollama');

    let html = '';
    if (saved.length === 0) {
      html = '<div class="text-muted" style="padding:12px;font-size:13px;">No custom endpoints configured.</div>';
    } else {
      for (const m of saved) {
        html += '<div class="endpoint-row">' +
          '<span class="endpoint-name">' + esc(m.name || '?') + '</span>' +
          '<span class="endpoint-url">' + esc(m.endpoint || '?') + '</span>' +
          '<span class="endpoint-type">' + esc(m.type || '?') + '</span>' +
          '<button class="btn btn-sm btn-secondary" onclick="window.testEndpoint(\'' + esc(m.name) + '\')">Test</button>' +
          '<button class="btn btn-sm btn-danger" onclick="window.removeEndpoint(\'' + esc(m.name) + '\')">✕</button>' +
        '</div>';
      }
    }
    const el = $('endpointList');
    if (el) el.innerHTML = html;
  } catch (err) {
    const el = $('endpointList');
    if (el) el.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">Failed to load endpoints.</div>';
  }
}

window.addEndpoint = async function() {
  const name = $('endpointName')?.value.trim();
  const endpoint = $('endpointUrl')?.value.trim();
  const type = $('endpointType')?.value;
  if (!name || !endpoint) { showToast('Please fill in name and URL.', 'error'); return; }

  try {
    const resp = await addEndpointAPI(name, endpoint, type);
    if (resp.ok) {
      showToast('Endpoint added!', 'success');
      const nEl = $('endpointName');
      const uEl = $('endpointUrl');
      if (nEl) nEl.value = '';
      if (uEl) uEl.value = '';
      loadEndpointList();
    } else {
      const err = await resp.json();
      showToast('Error: ' + (err.error || 'Failed'), 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

window.removeEndpoint = async function(name) {
  try {
    const resp = await removeEndpointAPI(name);
    if (resp.ok) {
      showToast('Endpoint removed', 'success');
      loadEndpointList();
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

window.testEndpoint = async function(name) {
  try {
    const data = await testEndpointAPI(name);
    if (data.status === 'ok') {
      showToast(data.message || 'Connection OK!', 'success');
    } else {
      showToast(data.message || 'Connection failed', 'error');
    }
  } catch (err) {
    showToast('Test failed', 'error');
  }
};

// ── Settings tab switching ─────────────────────────
window.switchSettingsTab = function(tab) {
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.settings-section').forEach(s => s.classList.toggle('active', s.id === 'tab-' + tab));
};

// ── Scribe Model ───────────────────────────────────
let scribeModelPicker = null;

async function loadScribeModel() {
  try {
    const state = getState();
    const savedModel = await fetchScribeModelAPI();
    const models = state.availableModels || [];

    if (!scribeModelPicker) {
      scribeModelPicker = new ModelPicker('scribeModelPicker', () => {});
    }
    scribeModelPicker.setModels(models);

    const statusEl = document.getElementById('scribeModelStatus');
    if (savedModel) {
      scribeModelPicker.setValue(savedModel);
      if (statusEl) statusEl.textContent = '✅ Current: ' + savedModel;
    } else {
      scribeModelPicker.setValue('');
      if (statusEl) statusEl.textContent = '';
    }
  } catch (e) {
    console.warn('Failed to load scribe model:', e);
  }
}

window.saveScribeModel = async function() {
  const model = scribeModelPicker ? scribeModelPicker.getValue() : '';
  try {
    const resp = await saveScribeModelAPI(model);
    if (resp.ok) {
      const statusEl = document.getElementById('scribeModelStatus');
      if (statusEl) statusEl.textContent = model ? '✅ Saved: ' + model : '✅ Reset to default';
      showToast('Scribe model saved!', 'success');
    }
  } catch (e) {
    showToast('Failed to save: ' + e.message, 'error');
  }
};

window.clearScribeModel = async function() {
  try {
    await clearScribeModelAPI();
    if (scribeModelPicker) scribeModelPicker.setValue('');
    const statusEl = document.getElementById('scribeModelStatus');
    if (statusEl) statusEl.textContent = '✅ Reset to default';
    showToast('Scribe model reset', 'success');
  } catch (e) {
    showToast('Failed to reset: ' + e.message, 'error');
  }
};

// ── Max Tokens per Agent Call ────────────────────────
async function loadMaxTokens() {
  try {
    const value = await fetchMaxTokens();
    const input = $('maxTokensInput');
    if (input) input.value = value;
    const statusEl = $('maxTokensStatus');
    if (statusEl) statusEl.textContent = 'Current: ' + value + ' tokens';
  } catch (e) {
    console.warn('Failed to load max tokens:', e);
  }
}

window.saveMaxTokens = async function() {
  const input = $('maxTokensInput');
  const value = parseInt(input?.value, 10);

  if (!value || value < 1) {
    showToast('Please enter a valid token count (>= 1).', 'error');
    return;
  }

  try {
    const resp = await saveMaxTokensAPI(value);
    if (resp.ok) {
      showToast('Max tokens set to ' + value.toLocaleString(), 'success');
      const statusEl = $('maxTokensStatus');
      if (statusEl) statusEl.textContent = 'Current: ' + value.toLocaleString() + ' tokens';
    } else {
      const err = await resp.json();
      showToast('Error: ' + (err.error || 'Failed'), 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

// ── Context Window ──────────────────────────────────
let ctxModelPicker = null;

async function loadContextWindows() {
  try {
    const state = getState();
    const models = state.availableModels || [];
    const overrides = await fetchContextWindows();

    // Build the list: show models with their default or overridden context window.
    let html = '';
    const displayModels = models.filter(m => m.id);
    if (displayModels.length === 0) {
      html = '<div class="text-muted" style="padding:12px;font-size:13px;">No models loaded.</div>';
    } else {
      for (const m of displayModels) {
        const defaultCtx = m.context_window;
        const overridden = m.id in overrides;
        const value = overrides[m.id] ?? defaultCtx;
        if (!value) continue; // Skip models with no context window info

        const badge = overridden
          ? '<span class="context-badge context-badge-override">override</span>'
          : '<span class="context-badge context-badge-default">default</span>';

        html += '<div class="context-window-row">' +
          '<span class="context-window-model">' + esc(m.display_name || m.id) + '</span>' +
          '<span class="context-window-provider">' + esc(m.provider || '?') + '</span>' +
          '<span class="context-window-value">' + Number(value).toLocaleString() + ' tokens</span>' +
          badge +
          (overridden
            ? '<button class="btn btn-sm btn-danger" onclick="window.resetContextWindow(\'' + esc(m.id) + '\')">Reset</button>'
            : '') +
        '</div>';
      }
    }
    const el = $('contextWindowList');
    if (el) el.innerHTML = html;

    // Init model picker for the add form.
    if (!ctxModelPicker) {
      ctxModelPicker = new ModelPicker('ctxModelPicker', () => {});
    }
    ctxModelPicker.setModels(models);
    ctxModelPicker.setValue('');
  } catch (err) {
    console.warn('Failed to load context windows:', err);
    const el = $('contextWindowList');
    if (el) el.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">Failed to load context windows.</div>';
  }
}

window.saveContextWindow = async function() {
  const model = ctxModelPicker ? ctxModelPicker.getValue() : '';
  const input = $('ctxWindowInput');
  const value = parseInt(input?.value, 10);

  if (!model) {
    showToast('Please select a model.', 'error');
    return;
  }
  if (!value || value < 1) {
    showToast('Please enter a valid token count (>= 1).', 'error');
    return;
  }

  try {
    const resp = await saveContextWindowAPI(model, value);
    if (resp.ok) {
      showToast('Context window set for ' + model + ': ' + value.toLocaleString() + ' tokens', 'success');
      if (input) input.value = '';
      loadContextWindows();
    } else {
      const err = await resp.json();
      showToast('Error: ' + (err.error || 'Failed'), 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

window.resetContextWindow = async function(modelId) {
  try {
    const resp = await deleteContextWindowAPI(modelId);
    if (resp.ok) {
      showToast('Context window reset for ' + modelId, 'success');
      loadContextWindows();
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

// ── Exports for index.js ────────────────────────────
export function loadSettingsView() {
  loadProviderList();
  loadDiscoveredModels();
  loadEndpointList();
  loadScribeModel();
  loadMaxTokens();
  loadContextWindows();
}
