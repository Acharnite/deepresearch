/* ── Settings view — API keys, local models, scribe ── */
import { getState } from '../state.js';
import { esc, showToast, formatSize, $ } from '../helpers.js';
import {
  fetchProviderKeys, saveApiKeyAPI, deleteApiKeyAPI,
  fetchLocalModels, addEndpointAPI, removeEndpointAPI, testEndpointAPI,
  fetchScribeModelAPI, saveScribeModelAPI, clearScribeModelAPI,
  fetchContextWindows, saveContextWindowAPI, deleteContextWindowAPI,
  fetchMaxTokens, saveMaxTokensAPI,
  fetchToolStatus, fetchHardwareInfo, fetchModelRecommendations,
  fetchOllamaStatus, getOllamaInstallURL,
  installLlmfit, uninstallLlmfit,
  startOllama, stopOllama, uninstallOllama,
  getPullModelURL, getDownloadModelURL, getLlmfitInstallURL, getOllamaUninstallURL,
  fetchLocalBackends, testLocalBackend, setBackendAddress, getBackendAddress,
  deleteOllamaModel
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
          '<button class="btn btn-sm btn-danger" style="margin-left:8px;font-size:11px;padding:1px 6px;" onclick="window.deleteOllamaModelAction(\'' + esc(m.name) + '\')">\uD83D\uDDD1\uFE0F</button>' +
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

window.deleteOllamaModelAction = async function(modelName) {
  if (!confirm(`Delete model "${modelName}"? This will remove it from Ollama.`)) return;
  try {
    const result = await deleteOllamaModel(modelName);
    if (result.status === 'ok') {
      showToast(`Model "${modelName}" deleted`, 'success');
      loadDiscoveredModels();
    } else {
      showToast(result.message || 'Failed to delete model', 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }
};

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

// ── Hardware (llmfit) ────────────────────────────────
async function loadHardwareInfo() {
  const statusEl = document.getElementById('llmfitStatus');
  const infoEl = document.getElementById('hardwareInfo');
  if (!infoEl) return;

  try {
    // Check tool status
    const tools = await fetchToolStatus();
    const llmfit = tools.llmfit || {};

    if (statusEl) {
      statusEl.textContent = llmfit.installed
        ? '\u2705 llmfit ' + (llmfit.version || '')
        : '\u274C llmfit not installed';
    }

    if (!llmfit.installed) {
      infoEl.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">' +
        'Install <a href="https://github.com/AlexsJones/llmfit" target="_blank" rel="noopener">llmfit</a> ' +
        'for hardware-aware model recommendations. ' +
        '<code style="font-size:11px;">curl -fsSL https://llmfit.axjns.dev/install.sh | sh -s -- --local</code>' +
        '</div>';
      return;
    }

    // Fetch hardware info
    const hw = await fetchHardwareInfo();
    if (!hw.available) {
      infoEl.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">' +
        'Hardware detection failed: ' + esc(hw.error || hw.message || 'unknown') + '</div>';
      return;
    }

    const s = hw.hardware || {};
    let html = '<div style="padding:8px 12px;font-size:13px;line-height:1.8;">';

    // GPU section
    if (s.has_gpu && s.gpu_name) {
      html += '\uD83D\uDDA5\uFE0F <strong>GPU:</strong> ' + esc(s.gpu_name) +
        ' (' + formatNumber(s.gpu_vram_gb) + 'GB VRAM)<br>';
    } else {
      html += '\uD83D\uDDA5\uFE0F <strong>GPU:</strong> <span class="text-muted">No GPU detected</span><br>';
    }

    // CPU section
    html += '\uD83E\uDDE0 <strong>CPU:</strong> ' + esc(s.cpu_name || 'Unknown') +
      ' (' + (s.cpu_cores || '?') + ' cores)<br>';

    // RAM section
    html += '\uD83D\uDCBE <strong>RAM:</strong> ' + formatNumber(s.total_ram_gb) + 'GB total' +
      ' (' + formatNumber(s.available_ram_gb) + 'GB available)<br>';

    // Backend section
    html += '\uD83D\uDD27 <strong>Backend:</strong> ' + esc(s.backend || 'Unknown');

    // Unified memory (Apple Silicon)
    if (s.unified_memory) {
      html += ' <span class="text-muted">(unified memory)</span>';
    }

    html += '</div>';
    infoEl.innerHTML = html;

    // Add llmfit action buttons
    const llmfitActions = document.getElementById('llmfitActions');
    if (llmfitActions) {
      if (llmfit.installed) {
        llmfitActions.innerHTML =
          '<button class="btn btn-sm btn-danger" onclick="window.uninstallLlmfitAction()">\uD83D\uDDD1\uFE0F Uninstall llmfit</button>' +
          '<span class="text-muted" style="font-size:11px;margin-left:8px;">\u2705 llmfit ' + esc(llmfit.version || '') + '</span>';
      } else {
        llmfitActions.innerHTML =
          '<button class="btn btn-sm btn-primary" onclick="window.installLlmfitAction()">\u2B07 Install llmfit</button>' +
          '<span class="text-muted" style="font-size:11px;margin-left:8px;">Hardware-aware model recommendations</span>';
      }
    }

    // Also load model recommendations
    loadModelRecommendations();

  } catch (err) {
    console.warn('Failed to load hardware info:', err);
    if (infoEl) {
      infoEl.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">Could not detect hardware.</div>';
    }
  }
}

// ── Model Recommendations (llmfit) ──────────────────────
async function loadModelRecommendations() {
  const statusEl = document.getElementById('llmfitRecStatus');
  const container = document.getElementById('modelRecommendations');
  if (!container) return;

  try {
    const data = await fetchModelRecommendations();

    if (!data.available) {
      if (statusEl) statusEl.textContent = '\u274C';
      container.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">' +
        'Install <a href="https://github.com/AlexsJones/llmfit" target="_blank" rel="noopener">llmfit</a> ' +
        'for model recommendations. ' +
        '<code style="font-size:11px;">curl -fsSL https://llmfit.axjns.dev/install.sh | sh -s -- --local</code>' +
        '</div>';
      return;
    }

    const models = (data.models || []).sort((a, b) => (b.score || 0) - (a.score || 0));

    // Filter: only show models downloadable via installed backends
    const filteredModels = models.filter(m => {
      if (ollamaInstalled && m.ollama_name) return true;
      if (llmfitInstalled && m.gguf_sources && m.gguf_sources.length > 0) return true;
      return false;
    });

    if (statusEl) statusEl.textContent = '\u2705 ' + filteredModels.length + '/' + models.length + ' models';

    if (filteredModels.length === 0) {
      if (statusEl) statusEl.textContent = '\u274C';
      container.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">' +
        'No downloadable models found. ' +
        (!ollamaInstalled ? 'Install <a href="#" onclick="document.querySelector(\'[data-tab=ollama]\')?.click()">Ollama</a> ' : '') +
        (!llmfitInstalled ? 'or <a href="#" onclick="document.querySelector(\'[data-tab=hardware]\')?.click()">llmfit</a> ' : '') +
        'to see downloadable model recommendations.' +
        '</div>';
      return;
    }

    let html = '<div style="overflow-x:auto;padding:4px 0;"><table style="width:100%;border-collapse:collapse;font-size:12px;">' +
      '<thead><tr style="border-bottom:1px solid var(--border);">' +
      '<th style="padding:8px 6px;text-align:left;">Score</th>' +
      '<th style="padding:8px 6px;text-align:left;">Model</th>' +
      '<th style="padding:8px 6px;text-align:left;">Research</th>' +
      '<th style="padding:8px 6px;text-align:left;">Category</th>' +
      '<th style="padding:8px 6px;text-align:left;">Fit</th>' +
      '<th style="padding:8px 6px;text-align:right;">Speed</th>' +
      '<th style="padding:8px 6px;text-align:right;">Context</th>' +
      '<th style="padding:8px 6px;text-align:left;">Use Case</th>' +
      '<th style="padding:8px 6px;text-align:center;">Download</th>' +
      '</tr></thead><tbody>';

    for (const m of filteredModels) {
      const score = m.score || 0;
      const scoreBadge = score >= 90
        ? '<span style="background:#1a6d1a;color:#fff;padding:2px 6px;border-radius:3px;font-weight:600;font-size:11px;">' + score + '</span>'
        : score >= 70
          ? '<span style="background:#b8860b;color:#fff;padding:2px 6px;border-radius:3px;font-weight:600;font-size:11px;">' + score + '</span>'
          : '<span style="background:#8b1a1a;color:#fff;padding:2px 6px;border-radius:3px;font-weight:600;font-size:11px;">' + score + '</span>';

      const fitLevel = m.fit_level || '?';
      const fitBadge = fitLevel === 'ideal'
        ? '<span style="background:#1a6d1a20;color:#4caf50;padding:2px 6px;border-radius:3px;font-size:11px;">ideal</span>'
        : fitLevel === 'good'
          ? '<span style="background:#b8860b20;color:#ffb300;padding:2px 6px;border-radius:3px;font-size:11px;">good</span>'
          : '<span style="background:#8b1a1a20;color:#f44336;padding:2px 6px;border-radius:3px;font-size:11px;">' + esc(fitLevel) + '</span>';

      const speed = m.estimated_tps != null ? Number(m.estimated_tps).toFixed(1) + ' tok/s' : '—';
      const ctx = m.effective_context_length != null ? Number(m.effective_context_length).toLocaleString() : '—';
      const useCase = m.use_case ? (m.use_case.length > 60 ? m.use_case.slice(0, 60) + '\u2026' : m.use_case) : '—';

      // Download button using smart download
      var downloadBtn = '';
      if (m.ollama_name) {
        // Available via Ollama — use ollama pull
        downloadBtn = '<button class="btn btn-sm btn-primary" style="font-size:11px;padding:2px 6px;" onclick="window.downloadModel(\'' + esc(m.ollama_name) + '\', null)">\u2B07 Pull (Ollama)</button>';
      } else if (m.gguf_sources && m.gguf_sources.length > 0) {
        var repo = esc(m.gguf_sources[0].repo);
        var modelName = esc(m.name);
        if (llmfitInstalled) {
          downloadBtn = '<button class="btn btn-sm btn-primary" style="font-size:11px;padding:2px 6px;" onclick="window.downloadModel(\'' + modelName + '\', \'' + repo + '\')">\u2B07 Download (GGUF)</button>';
        } else {
          // Fallback to ollama pull when llmfit not installed
          downloadBtn = '<button class="btn btn-sm btn-primary" style="font-size:11px;padding:2px 6px;" onclick="window.downloadModel(\'' + modelName + '\', null)">\u2B07 Pull (Ollama)</button>';
        }
      } else {
        downloadBtn = '\u2014';
      }

      const warningIcon = m._warning
        ? '<span style="margin-left:4px;cursor:help;color:#ff9800;" title="' + esc(m._warning) + '">\u26A0\uFE0F</span>'
        : '';
      const rScore = m.research_score || 0;
      const rTags = m.research_tags || [];
      const rBadgeColor = rScore >= 60 ? '#1a6d1a' : rScore >= 40 ? '#b8860b' : '#555';
      const researchBadge = '<span style="background:' + rBadgeColor + ';color:#fff;padding:2px 6px;border-radius:3px;font-weight:600;font-size:11px;" title="' + esc(rTags.join(', ')) + '">' + rScore + '</span>';
      const moeNote = m._moe_annotation
        ? '<br><span style="font-size:10px;color:#64b5f6;">' + esc(m._moe_annotation) + '</span>'
        : '';

      html += '<tr style="border-bottom:1px solid var(--border);">' +
        '<td style="padding:6px;">' + scoreBadge + '</td>' +
        '<td style="padding:6px;font-weight:500;">' + esc(m.name || '?') + warningIcon + moeNote + '</td>' +
        '<td style="padding:6px;text-align:center;">' + researchBadge + '</td>' +
        '<td style="padding:6px;color:var(--text-secondary);">' + esc(m.category || '—') + '</td>' +
        '<td style="padding:6px;">' + fitBadge + '</td>' +
        '<td style="padding:6px;text-align:right;font-variant-numeric:tabular-nums;">' + speed + '</td>' +
        '<td style="padding:6px;text-align:right;font-variant-numeric:tabular-nums;">' + ctx + '</td>' +
        '<td style="padding:6px;color:var(--text-secondary);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(m.use_case || '') + '">' + esc(useCase) + '</td>' +
        '<td style="padding:6px;text-align:center;">' + downloadBtn + '</td>' +
        '</tr>';
    }

    html += '</tbody></table></div>';
    container.innerHTML = html;

  } catch (err) {
    console.warn('Failed to load model recommendations:', err);
    container.innerHTML = '<div class="text-muted" style="padding:12px;font-size:13px;">Could not load recommendations.</div>';
  }
}

// ── Ollama Install ──────────────────────────────────
let ollamaInstallState = 'IDLE'; // IDLE | INSTALLING | SUCCESS | ERROR
let ollamaEventSource = null;

async function checkOllamaStatus() {
  try {
    const status = await fetchOllamaStatus();
    const statusEl = document.getElementById('ollamaStatus');
    const installBtn = document.getElementById('installOllamaBtn');
    const actionRow = document.getElementById('ollamaActions');

    if (!statusEl && !installBtn) return;

    if (status.installed) {
      if (statusEl) statusEl.textContent = '\u2705 Ollama ' + (status.version || '');
      if (installBtn) {
        installBtn.style.display = 'none';
      }
      if (actionRow) {
        actionRow.innerHTML =
          (status.running
            ? '<button class="btn btn-sm btn-warning" onclick="window.manageOllama(\'stop\')">\u23F9 Stop</button>'
            : '<button class="btn btn-sm btn-success" onclick="window.manageOllama(\'start\')">\u25B6 Start</button>') +
          '<button class="btn btn-sm btn-danger" onclick="window.confirmUninstallOllama()">\uD83D\uDDD1\uFE0F Uninstall</button>' +
          '<span class="text-muted" style="font-size:11px;margin-left:8px;">' +
          (status.running ? '\u2705 Running on port 11434' : '\u26A0\uFE0F Not running') +
          '</span>';
      }
    } else {
      if (statusEl) statusEl.textContent = '\u274C Not installed';
      if (installBtn) {
        installBtn.style.display = 'inline-block';
        installBtn.textContent = '\u2B07 Install Ollama';
        installBtn.disabled = false;
        installBtn.style.opacity = '1';
      }
      if (actionRow) {
        actionRow.innerHTML = '<span class="text-muted" style="font-size:11px;">Install Ollama to get started.</span>';
      }
    }
  } catch (err) {
    console.warn('Failed to check Ollama status:', err);
  }
}

window.installOllama = async function() {
  if (ollamaInstallState === 'INSTALLING') return;

  const btn = document.getElementById('installOllamaBtn');
  const logContainer = document.getElementById('ollamaInstallLog');
  const logOutput = document.getElementById('ollamaInstallOutput');
  const statusEl = document.getElementById('ollamaStatus');

  if (!logContainer || !logOutput) return;

  // Reset UI
  logContainer.classList.remove('hidden');
  logOutput.innerHTML = '';
  ollamaInstallState = 'INSTALLING';
  if (btn) { btn.textContent = '\u23F3 Installing...'; btn.disabled = true; }
  if (statusEl) statusEl.textContent = '\u23F3 Installing Ollama...';

  // Status polling to refresh after completion
  function pollAfterCompletion() {
    let attempts = 0;
    const iv = setInterval(async () => {
      attempts++;
      try {
        const status = await fetchOllamaStatus();
        if (status.installed) {
          clearInterval(iv);
          checkOllamaStatus(); // Refresh UI
        }
      } catch (e) {}
      if (attempts > 30) clearInterval(iv); // 30s timeout
    }, 1000);
  }

  // Create EventSource for install log
  const eventSource = new EventSource(getOllamaInstallURL() + '?_method=POST');
  ollamaEventSource = eventSource;

  eventSource.addEventListener('install_log', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line';
      const icon = data.progress >= 80 ? '\u2705' : data.progress >= 50 ? '\u23F3' : '\u2B07';
      const step = data.step || '';
      const msg = esc(data.message || '');
      line.innerHTML = '<span class="log-icon">' + icon + '</span> <span class="log-step">' + esc(step) + '</span> <span class="log-msg">' + msg + '</span>';
      logOutput.appendChild(line);
      logContainer.scrollTop = logContainer.scrollHeight;
    } catch (err) {}
  });

  eventSource.addEventListener('install_complete', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line log-success';
      line.innerHTML = '<span class="log-icon">\u2705</span> <strong>Installation complete!</strong> Version: ' + esc(data.version || 'unknown');
      logOutput.appendChild(line);

      ollamaInstallState = 'SUCCESS';
      if (btn) { btn.textContent = '\u2705 Installed'; btn.disabled = true; }
      if (statusEl) statusEl.textContent = '\u2705 Installed ' + (data.version || '');
      eventSource.close();
      ollamaEventSource = null;
      pollAfterCompletion();
    } catch (err) {}
  });

  eventSource.addEventListener('install_error', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line log-error';
      line.innerHTML = '<span class="log-icon">\u274C</span> <strong>Error:</strong> ' + esc(data.message || 'Unknown error');
      logOutput.appendChild(line);

      // Add Retry button
      const retryLine = document.createElement('div');
      retryLine.className = 'log-line log-retry';
      retryLine.innerHTML = '<button class="btn btn-sm btn-primary" onclick="window.installOllama()">\uD83D\uDD04 Retry</button>';
      logOutput.appendChild(retryLine);

      ollamaInstallState = 'ERROR';
      if (btn) { btn.textContent = '\u2B07 Install Ollama'; btn.disabled = false; }
      if (statusEl) statusEl.textContent = '\u274C Installation failed';
      eventSource.close();
      ollamaEventSource = null;
    } catch (err) {}
  });

  eventSource.onerror = function() {
    // Connection closed — might be done or error
    if (ollamaInstallState === 'INSTALLING') {
      const line = document.createElement('div');
      line.className = 'log-line log-error';
      line.innerHTML = '<span class="log-icon">\u26A0\uFE0F</span> Connection lost. If installation succeeded, refresh to see status.';
      logOutput.appendChild(line);
      ollamaInstallState = 'ERROR';
      if (btn) { btn.textContent = '\u2B07 Install Ollama'; btn.disabled = false; }
      eventSource.close();
      ollamaEventSource = null;
    }
  };
};

// ── Ollama Start/Stop ────────────────────────────────
window.manageOllama = async function(action) {
  const btn = document.querySelector('#ollamaActions button');
  if (btn) { btn.disabled = true; btn.textContent = action === 'start' ? '\u23F3 Starting...' : '\u23F3 Stopping...'; }

  try {
    const fn = action === 'start' ? startOllama : stopOllama;
    const resp = await fn();
    if (resp.ok) {
      showToast('Ollama ' + (action === 'start' ? 'started' : 'stopped') + ' successfully', 'success');
    } else {
      const err = await resp.json();
      showToast('Failed to ' + action + ' Ollama: ' + (err.error || err.message || 'Unknown'), 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }

  setTimeout(checkOllamaStatus, 1500); // Refresh status after delay
};

// ── Ollama Uninstall (with confirmation) ────────────
window.confirmUninstallOllama = function() {
  if (!confirm('\u26A0\uFE0F Are you sure you want to uninstall Ollama? This will remove the binary, service, and all downloaded models.')) return;

  const logContainer = document.getElementById('ollamaInstallLog');
  const logOutput = document.getElementById('ollamaInstallOutput');
  if (!logContainer || !logOutput) return;

  logContainer.classList.remove('hidden');
  logOutput.innerHTML = '';

  const eventSource = new EventSource(getOllamaUninstallURL() + '?_method=POST');

  eventSource.addEventListener('install_log', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line';
      line.innerHTML = '<span class="log-icon">\u2139\uFE0F</span> <span class="log-msg">' + esc(data.message || '') + '</span>';
      logOutput.appendChild(line);
      logContainer.scrollTop = logContainer.scrollHeight;
    } catch (err) {}
  });

  eventSource.addEventListener('install_complete', function(e) {
    const line = document.createElement('div');
    line.className = 'log-line log-success';
    line.innerHTML = '<span class="log-icon">\u2705</span> <strong>Ollama uninstalled successfully.</strong>';
    logOutput.appendChild(line);
    eventSource.close();
    setTimeout(checkOllamaStatus, 1000);
  });

  eventSource.addEventListener('install_error', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line log-error';
      line.innerHTML = '<span class="log-icon">\u274C</span> <strong>Error:</strong> ' + esc(data.message || 'Uninstall failed');
      logOutput.appendChild(line);
    } catch (err) {}
    eventSource.close();
  });

  eventSource.onerror = function() {
    if (eventSource.readyState === EventSource.CLOSED) {
      setTimeout(checkOllamaStatus, 1000);
    }
  };
};

// ── llmfit Install ──────────────────────────────────
window.installLlmfitAction = function() {
  const logContainer = document.getElementById('ollamaInstallLog');
  const logOutput = document.getElementById('ollamaInstallOutput');
  if (!logContainer || !logOutput) return;

  logContainer.classList.remove('hidden');
  logOutput.innerHTML = '';

  const line = document.createElement('div');
  line.className = 'log-line';
  line.innerHTML = '<span class="log-icon">\u2B07</span> <span class="log-msg">Installing llmfit...</span>';
  logOutput.appendChild(line);

  const eventSource = new EventSource(getLlmfitInstallURL() + '?_method=POST');

  eventSource.addEventListener('install_log', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line';
      const icon = data.progress >= 80 ? '\u2705' : data.progress >= 50 ? '\u23F3' : '\u2B07';
      line.innerHTML = '<span class="log-icon">' + icon + '</span> <span class="log-msg">' + esc(data.message || '') + '</span>';
      logOutput.appendChild(line);
      logContainer.scrollTop = logContainer.scrollHeight;
    } catch (err) {}
  });

  eventSource.addEventListener('install_complete', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line log-success';
      line.innerHTML = '<span class="log-icon">\u2705</span> <strong>llmfit installed!</strong> Version: ' + esc(data.version || '');
      logOutput.appendChild(line);
    } catch (err) {}
    eventSource.close();
    // Refresh hardware info and recommendations
    setTimeout(() => { loadHardwareInfo(); loadModelRecommendations(); }, 1000);
  });

  eventSource.addEventListener('install_error', function(e) {
    try {
      const data = JSON.parse(e.data);
      const line = document.createElement('div');
      line.className = 'log-line log-error';
      line.innerHTML = '<span class="log-icon">\u274C</span> <strong>Error:</strong> ' + esc(data.message || 'Installation failed');
      logOutput.appendChild(line);
    } catch (err) {}
    eventSource.close();
  });

  eventSource.onerror = function() {
    if (eventSource.readyState === EventSource.CLOSED) {
      setTimeout(() => { loadHardwareInfo(); }, 1000);
    }
  };
};

// ── llmfit Uninstall ────────────────────────────────
window.uninstallLlmfitAction = async function() {
  if (!confirm('Uninstall llmfit?')) return;

  try {
    const resp = await uninstallLlmfit();
    const data = await resp.json();
    if (resp.ok) {
      showToast('llmfit uninstalled', 'success');
    } else {
      showToast('Error: ' + (data.error || data.message || 'Failed'), 'error');
    }
  } catch (err) {
    showToast('Network error', 'error');
  }

  setTimeout(() => { loadHardwareInfo(); loadModelRecommendations(); }, 1000);
};

// ── Download Model (smart: Ollama or llmfit) ──────────
window.downloadModel = async function(modelName, repoName) {
  const logContainer = document.getElementById('ollamaInstallLog');
  const logOutput = document.getElementById('ollamaInstallOutput');
  if (!logContainer || !logOutput) return;

  logContainer.classList.remove('hidden');
  logOutput.innerHTML = '';

  // Create progress bar
  const progressContainer = document.createElement('div');
  progressContainer.className = 'download-progress';
  progressContainer.innerHTML = '<div class="progress-bar-bg"><div class="progress-bar-fill" id="dlProgressFill" style="width:0%"></div></div><span class="progress-label" id="dlProgressLabel">0%</span>';
  logOutput.appendChild(progressContainer);

  // Add initial log line
  const initLine = document.createElement('div');
  initLine.className = 'log-line';
  const modelDisplay = esc(modelName.split('/').pop() || modelName);
  initLine.innerHTML = '<span class="log-icon">\u2B07</span> <span class="log-msg">Preparing download: <strong>' + modelDisplay + '</strong></span>';
  logOutput.appendChild(initLine);

  try {
    const resp = await fetch(getDownloadModelURL(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: modelName,
        repo: repoName || null,
        download_type: repoName ? 'llmfit' : 'auto',
      }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      const errLine = document.createElement('div');
      errLine.className = 'log-line log-error';
      errLine.innerHTML = '<span class="log-icon">\u274C</span> <strong>Error:</strong> ' + esc(err.detail || err.error || 'Failed to start download');
      logOutput.appendChild(errLine);
      return;
    }

    // Read SSE stream
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const MAX_BUF = 65536; // 64KB max buffer
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      // Safety: force-split if buffer exceeds limit
      if (buffer.length > MAX_BUF) {
        var idx = buffer.indexOf('\n');
        if (idx === -1 || idx > MAX_BUF) {
          // No newline or too far — discard and hope next chunk has one
          buffer = buffer.slice(-2000);
          continue;
        }
      }
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let currentEvent = 'message';
      for (const lineText of lines) {
        // Track SSE event type from 'event:' headers
        if (lineText.startsWith('event: ')) {
          currentEvent = lineText.slice(7).trim();
          continue;
        }
        if (lineText.startsWith('data: ')) {
          try {
            const payload = JSON.parse(lineText.slice(6));

            if (currentEvent === 'install_log') {
              // Update progress bar
              const pct = payload.progress || 0;
              const fill = document.getElementById('dlProgressFill');
              const label = document.getElementById('dlProgressLabel');
              if (fill) fill.style.width = pct + '%';
              if (label) label.textContent = Math.round(pct) + '%';
              const logLine = document.createElement('div');
              logLine.className = 'log-line';
              logLine.innerHTML = '<span class="log-icon">' + icon + '</span> <span class="log-msg">' + esc(payload.message || '') + '</span>';
              logOutput.appendChild(logLine);
              logContainer.scrollTop = logContainer.scrollHeight;
            } else if (currentEvent === 'install_complete') {
              const completeLine = document.createElement('div');
              completeLine.className = 'log-line log-success';
              const filePath = payload.file || payload.path || '';
              const size = payload.size ? ' (' + formatSize(payload.size) + ')' : '';
              completeLine.innerHTML = '<span class="log-icon">\u2705</span> <strong>Download complete!</strong> ' + esc(filePath) + size;
              logOutput.appendChild(completeLine);
              // Show toast notification on success
              showToast('Download complete: ' + (payload.model || filePath || 'Model downloaded'), 'success');
              logContainer.scrollTop = logContainer.scrollHeight;
              // Refresh discovered models
              loadDiscoveredModels();
            } else if (currentEvent === 'install_error') {
              const errLine = document.createElement('div');
              errLine.className = 'log-line log-error';
              errLine.innerHTML = '<span class="log-icon">\u274C</span> <strong>Error:</strong> ' + esc(payload.message || 'Download failed');
              logOutput.appendChild(errLine);
              // Show toast notification on error
              showToast('Download failed: ' + (payload.message || 'Unknown error'), 'error');
              logContainer.scrollTop = logContainer.scrollHeight;
            }
          } catch (e) {}
          currentEvent = 'message'; // Reset after consuming data line
        }
      }
    }
  } catch (err) {
    const errLine = document.createElement('div');
    errLine.className = 'log-line log-error';
    errLine.innerHTML = '<span class="log-icon">\u274C</span> <strong>Error:</strong> ' + esc(err.message || 'Network error');
    logOutput.appendChild(errLine);
  }
};

function formatNumber(val) {
  if (val === null || val === undefined) return '?';
  const n = typeof val === 'number' ? val : parseFloat(val);
  if (isNaN(n)) return '?';
  return n.toFixed(1);
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

// ── Local Backends tab ──────────────────────────────
async function loadLocalBackends() {
  const container = document.getElementById('localBackendsList');
  if (!container) return;

  try {
    const data = await fetchLocalBackends();
    const backends = data.backends || [];

    if (backends.length === 0) {
      container.innerHTML = '<div class="text-muted" style="text-align:center;padding:20px;">No backends found.</div>';
      return;
    }

    // Fetch custom addresses for all backends in parallel
    const addressPromises = backends.map(b => getBackendAddress(b.name).catch(() => ({ address: null })));
    const addressResults = await Promise.all(addressPromises);

    let html = '<div class="backend-grid">';
    for (let i = 0; i < backends.length; i++) {
      const b = backends[i];
      const addrData = addressResults[i] || { address: null };
      const customAddress = addrData.address || '';

      const isRunning = b.running === true;
      const isInstalled = b.installed === true;
      const status = isRunning ? 'running' : isInstalled ? 'installed' : 'not_available';
      const statusDotClass = status === 'running' ? 'running' : status === 'installed' ? 'installed' : 'not-available';

      let statusText = '';
      if (status === 'running') {
        statusText = 'Running' + (b.port ? ' on port ' + b.port : '');
      } else if (status === 'installed') {
        statusText = 'Installed';
      } else {
        statusText = 'Not detected';
      }

      // Determine action buttons based on backend name
      let actionsHtml = '';
      const nameLower = (b.name || '').toLowerCase();

      if (nameLower === 'ollama') {
        // Reuse existing Ollama action buttons tied to the local models tab
        actionsHtml =
          '<div class="backend-actions">' +
          '<span class="text-muted" style="font-size:11px;">Manage in Ollama section above</span>' +
          '</div>';
      } else if (nameLower === 'llmfit') {
        actionsHtml =
          '<div class="backend-actions">' +
          '<span class="text-muted" style="font-size:11px;">Manage in Hardware section above</span>' +
          '</div>';
      } else {
        actionsHtml =
          '<div class="backend-actions">' +
          '<span class="text-muted" style="font-size:11px;">Manual setup — configure in Local Models tab</span>' +
          '</div>';
      }

      // Test button + address row are common to all backends
      const testBtnId = 'test-backend-' + esc(b.name);
      const addrInputId = 'addr-input-' + esc(b.name);
      const saveBtnId = 'save-addr-' + esc(b.name);

      html +=
        '<div class="backend-card">' +
          '<div class="backend-header">' +
            '<span class="backend-status-dot ' + statusDotClass + '"></span>' +
            '<span class="backend-name">' + esc(b.label || b.name) + '</span>' +
          '</div>' +
          '<div class="backend-desc">' + esc(b.description || '') + '</div>' +
          (b.port ? '<div class="backend-detail">Port: ' + b.port + '</div>' : '') +
          '<div class="backend-status-text ' + statusDotClass + '">' + statusText + '</div>' +
          actionsHtml +
          '<div class="backend-actions">' +
            '<button class="btn btn-sm btn-secondary" id="' + testBtnId + '" onclick="window.testLocalBackendAction(\'' + esc(b.name) + '\')">Test Connection</button>' +
          '</div>' +
          '<div class="backend-address-row">' +
            '<input type="text" id="' + addrInputId + '" placeholder="Custom address..." value="' + esc(customAddress) + '" />' +
            '<button class="btn btn-sm btn-primary" id="' + saveBtnId + '" onclick="window.saveBackendAddressAction(\'' + esc(b.name) + '\')">Save</button>' +
          '</div>' +
        '</div>';
    }
    html += '</div>';

    container.innerHTML = html;
  } catch (err) {
    console.warn('Failed to load local backends:', err);
    container.innerHTML = '<div class="text-muted" style="text-align:center;padding:20px;">Failed to load backends.</div>';
  }
}

// ── Test Local Backend action ──────────────────────
window.testLocalBackendAction = async function(name) {
  const btn = document.getElementById('test-backend-' + name);
  if (btn) { btn.disabled = true; btn.textContent = 'Testing...'; }
  try {
    const data = await testLocalBackend(name);
    if (data.status === 'ok' || data.success) {
      showToast(name + ': Connection OK!', 'success');
    } else {
      showToast(name + ': ' + (data.message || 'Connection failed'), 'error');
    }
  } catch (err) {
    showToast(name + ': Network error', 'error');
  }
  if (btn) { btn.disabled = false; btn.textContent = 'Test Connection'; }
  // Refresh backend list to update status dots after test
  loadLocalBackends();
};

// ── Save Backend Address action ────────────────────
window.saveBackendAddressAction = async function(name) {
  const input = document.getElementById('addr-input-' + name);
  if (!input) return;
  const address = input.value.trim();
  try {
    const data = await setBackendAddress(name, address);
    if (data.status === 'ok' || data.success) {
      showToast(name + ': Address saved!', 'success');
    } else {
      showToast(name + ': ' + (data.message || 'Failed to save'), 'error');
    }
  } catch (err) {
    showToast(name + ': Network error', 'error');
  }
};

// ── Persistent download progress polling (survives page refresh) ──
let progressPollInterval = null;

async function checkDownloadProgress() {
  try {
    const resp = await fetch('/api/local-backends/models/download/progress');
    if (!resp.ok) return;
    const state = await resp.json();
    if (state.active && state.status === 'downloading') {
      showDownloadLog(state.model, state);
    }
  } catch (e) {
    // Silent
  }
}

function showDownloadLog(modelName, state) {
  const logContainer = document.getElementById('ollamaInstallLog');
  const logOutput = document.getElementById('ollamaInstallOutput');
  if (!logContainer || !logOutput) return;

  logContainer.classList.remove('hidden');
  logOutput.innerHTML = '';

  // Add progress bar
  const progressContainer = document.createElement('div');
  progressContainer.className = 'download-progress';
  progressContainer.innerHTML = '<div class="progress-bar-bg"><div class="progress-bar-fill" id="dlProgressFill" style="width:' + state.progress + '%"></div></div><span class="progress-label" id="dlProgressLabel">' + Math.round(state.progress) + '%</span>';
  logOutput.appendChild(progressContainer);

  // Add stored log lines
  for (const msg of (state.log || [])) {
    const logLine = document.createElement('div');
    logLine.className = 'log-line';
    logLine.innerHTML = '<span class="log-icon">\u2B07</span> <span class="log-msg">' + esc(msg) + '</span>';
    logOutput.appendChild(logLine);
  }
  logContainer.scrollTop = logContainer.scrollHeight;

  // Start polling for updates
  startProgressPolling();
}

function startProgressPolling() {
  if (progressPollInterval) clearInterval(progressPollInterval);
  progressPollInterval = setInterval(async () => {
    try {
      const resp = await fetch('/api/local-backends/models/download/progress');
      if (!resp.ok) { stopProgressPolling(); return; }
      const state = await resp.json();

      // Update progress bar
      const fill = document.getElementById('dlProgressFill');
      const label = document.getElementById('dlProgressLabel');
      if (fill) fill.style.width = state.progress + '%';
      if (label) label.textContent = Math.round(state.progress) + '%';

      // Update status
      if (state.status === 'complete') {
        showToast('Download complete: ' + (state.model || ''), 'success');
        stopProgressPolling();
        loadDiscoveredModels();
      } else if (state.status === 'error') {
        showToast('Download failed: ' + (state.message || ''), 'error');
        stopProgressPolling();
      }
    } catch (e) {
      stopProgressPolling();
    }
  }, 2000);
}

function stopProgressPolling() {
  if (progressPollInterval) {
    clearInterval(progressPollInterval);
    progressPollInterval = null;
  }
}

// ── Exports for index.js ────────────────────────────
export function loadSettingsView() {
  loadProviderList();
  loadHardwareInfo();
  loadModelRecommendations();
  loadDiscoveredModels();
  loadEndpointList();
  loadScribeModel();
  loadMaxTokens();
  loadContextWindows();
  checkOllamaStatus();
  loadLocalBackends();
  checkDownloadProgress();
}
