/* ── ModelPicker class + MiniPicker + model UI ─────── */
import { getState } from './state.js';
import { esc, $ } from './helpers.js';
import { fetchAvailableModels, fetchProfiles } from './api.js';

export class ModelPicker {
  constructor(containerId, onChange) {
    this.container = document.getElementById(containerId);
    this.onChange = onChange;
    this.selectedModel = null;
    this.allModels = [];
    this.filterText = '';
    this.isOpen = false;

    this._buildDOM();
    this._bindEvents();
  }

  _buildDOM() {
    this.container.innerHTML = '' +
      '<div class="model-picker-trigger" tabindex="0" role="combobox">' +
        '<input type="text" class="model-picker-input mp-input" readonly placeholder="Type to search models..." />' +
        '<span class="model-picker-arrow mp-arrow">▾</span>' +
      '</div>' +
      '<div class="model-picker-dropdown hidden mp-dropdown">' +
        '<div class="model-picker-search">' +
          '<input type="text" class="model-picker-search-input mp-search" placeholder="Search models..." autocomplete="off" />' +
        '</div>' +
        '<div class="model-picker-list mp-list"></div>' +
      '</div>';

    this.input = this.container.querySelector('.mp-input');
    this.arrow = this.container.querySelector('.mp-arrow');
    this.dropdown = this.container.querySelector('.mp-dropdown');
    this.search = this.container.querySelector('.mp-search');
    this.list = this.container.querySelector('.mp-list');
    this.trigger = this.container.querySelector('.model-picker-trigger');
  }

  _bindEvents() {
    // Click trigger to open/close
    this.trigger.addEventListener('click', (e) => {
      if (e.target === this.search) return;
      this.isOpen ? this.close() : this.open();
    });

    // Search input filtering
    this.search.addEventListener('input', () => {
      this.filterText = this.search.value;
      this._render();
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
      if (!this.container.contains(e.target) && this.isOpen) {
        this.close();
      }
    });

    // Keyboard: Escape to close, Enter on focused item
    this.search.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.close();
      if (e.key === 'Enter') {
        const first = this.list.querySelector('.model-picker-item:not(.hidden)');
        if (first) first.click();
      }
    });

    // Stop wheel propagation on dropdown
    this.dropdown.addEventListener('wheel', (e) => e.stopPropagation(), { passive: true });
  }

  open() {
    this.isOpen = true;
    this.dropdown.classList.remove('hidden');
    this.arrow.classList.add('open');
    this.trigger.setAttribute('aria-expanded', 'true');
    this.search.value = '';
    this.filterText = '';
    this._render();
    setTimeout(() => this.search.focus(), 50);
  }

  close() {
    this.isOpen = false;
    this.dropdown.classList.add('hidden');
    this.arrow.classList.remove('open');
    this.trigger.setAttribute('aria-expanded', 'false');
  }

  setModels(models) {
    this.allModels = models || [];
    // Group by provider
    this._grouped = {};
    for (const m of models) {
      const provider = m.provider || 'other';
      if (!this._grouped[provider]) this._grouped[provider] = [];
      this._grouped[provider].push(m);
    }
    if (!this.isOpen) return;
    this._render();
  }

  setValue(modelId) {
    this.selectedModel = modelId;
    const found = this.allModels.find(m => m.id === modelId);
    this.input.value = found ? (found.display_name || found.id) : (modelId || '');
    this._render();
  }

  getValue() { return this.selectedModel; }

  _render() {
    if (!this._grouped) return;
    const filter = this.filterText.toLowerCase().trim();
    let html = '';
    let count = 0;

    for (const [provider, models] of Object.entries(this._grouped)) {
      const filtered = filter
        ? models.filter(m => (m.display_name || m.id).toLowerCase().includes(filter) || m.id.toLowerCase().includes(filter))
        : models;

      if (filtered.length === 0) continue;

      const providerName = provider.charAt(0).toUpperCase() + provider.slice(1);
      html += '<div class="model-picker-group-header">' + esc(providerName) + '</div>';

      for (const m of filtered) {
        const selected = m.id === this.selectedModel ? ' selected' : '';
        html += '<div class="model-picker-item' + selected + '" data-model-id="' + esc(m.id) + '">' +
          '<span>' + esc(m.display_name || m.id) + '</span>' +
          '<span class="model-id">' + esc(m.id) + '</span>' +
        '</div>';
        count++;
      }
    }

    if (count === 0) {
      html = '<div class="model-picker-empty">' + (filter ? 'No models match "' + esc(filter) + '"' : 'No models available') + '</div>';
    }

    this.list.innerHTML = html;

    // Bind click on each item
    this.list.querySelectorAll('.model-picker-item').forEach(el => {
      el.addEventListener('click', () => {
        const id = el.dataset.modelId;
        this.setValue(id);
        this.close();
        if (this.onChange) this.onChange(id);
      });
    });
  }
}

// ── Model picker instance for the "New Research" form ──
let modelPicker = null;

export function getModelPicker() {
  return modelPicker;
}

export async function loadAvailableModels() {
  const models = await fetchAvailableModels();

  if (!modelPicker) {
    modelPicker = new ModelPicker('modelPicker', (modelId) => {
      modelPicker._selectedForSubmit = modelId;
    });
  }
  modelPicker.setModels(models);

  // Set default selection (first model or default-tagged)
  const defaultModel = models.find(m => m.default) || models[0];
  if (defaultModel) {
    modelPicker.setValue(defaultModel.id);
  }
}

// ── Model mode radio handlers ───────────────────────
document.querySelectorAll('input[name="model_mode"]').forEach(radio => {
  radio.addEventListener('change', function() {
    const isSame = this.value === 'same';
    const isRandom = this.value === 'random';
    const isManual = this.value === 'manual';

    document.getElementById('modelSelectorGroup').classList.toggle('hidden', !isSame);
    document.getElementById('randomModelHint').classList.toggle('hidden', !isRandom);
    document.getElementById('manualModelSelectors').classList.toggle('hidden', !isManual);

    if (isManual && !document.getElementById('agentModelList').querySelector('.agent-model-row')) {
      buildManualModelSelectors();
    }
  });
});

// ── Build manual model selectors ─────────────────────
async function buildManualModelSelectors() {
  const state = getState();
  const profiles = await fetchProfiles();
  const models = state.availableModels || [];

  const container = document.getElementById('agentModelList');
  if (!container) return;

  if (profiles.length === 0) {
    container.innerHTML = '<p class="text-muted" style="font-size:13px;">No agent profiles available.</p>';
    return;
  }

  // Group models by provider
  const grouped = {};
  for (const m of models) {
    const p = m.provider || 'other';
    if (!grouped[p]) grouped[p] = [];
    grouped[p].push(m);
  }

  let html = '';
  for (const p of profiles) {
    const pid = esc(p.id);
    const pname = esc(p.name || p.id);
    const pemoj = p.emoji || '🤖';

    html += '<div class="agent-model-row">' +
      '<span>' + pemoj + ' ' + pname + '</span>' +
      '<div class="mini-picker" data-agent="' + pid + '">' +
        '<input type="text" class="mini-picker-input" readonly placeholder="Select model..." data-agent="' + pid + '" />' +
        '<div class="mini-picker-dropdown hidden">' +
          '<input type="text" class="mini-picker-search" placeholder="Search..." autocomplete="off" data-agent="' + pid + '" />' +
          '<div class="mini-picker-list" data-agent="' + pid + '"></div>' +
        '</div>' +
      '</div>' +
    '</div>';
  }
  container.innerHTML = html;

  // Initialize each mini picker
  container.querySelectorAll('.mini-picker').forEach(pickerEl => {
    initMiniPicker(pickerEl, grouped, models);
  });
}

// ── Mini Picker (per-agent manual mode) ─────────────
function initMiniPicker(pickerEl, grouped, allModels) {
  const input = pickerEl.querySelector('.mini-picker-input');
  const dropdown = pickerEl.querySelector('.mini-picker-dropdown');
  const search = pickerEl.querySelector('.mini-picker-search');
  const list = pickerEl.querySelector('.mini-picker-list');

  let isOpen = false;
  let selectedValue = null;

  function render(filter) {
    const f = (filter || '').toLowerCase().trim();
    let html = '';
    let count = 0;

    for (const [provider, models] of Object.entries(grouped)) {
      const filtered = f
        ? models.filter(m => (m.display_name || m.id).toLowerCase().includes(f) || m.id.toLowerCase().includes(f))
        : models;
      if (filtered.length === 0) continue;

      const pName = provider.charAt(0).toUpperCase() + provider.slice(1);
      html += '<div class="mini-group-header">' + esc(pName) + '</div>';

      for (const m of filtered) {
        const sel = m.id === selectedValue ? ' selected' : '';
        html += '<div class="mini-item' + sel + '" data-model="' + esc(m.id) + '">' +
          '<span>' + esc(m.display_name || m.id) + '</span>' +
        '</div>';
        count++;
      }
    }

    if (count === 0) {
      html = '<div class="mini-empty">' + (f ? 'No matches' : 'No models') + '</div>';
    }

    list.innerHTML = html;

    list.querySelectorAll('.mini-item').forEach(el => {
      el.addEventListener('click', () => {
        selectedValue = el.dataset.model;
        input.dataset.modelId = selectedValue;
        const found = allModels.find(m => m.id === selectedValue);
        input.value = found ? (found.display_name || found.id) : selectedValue;
        dropdown.classList.add('hidden');
        isOpen = false;
        list.querySelectorAll('.mini-item').forEach(i => i.classList.remove('selected'));
        el.classList.add('selected');
      });
    });
  }

  input.addEventListener('click', (e) => {
    e.stopPropagation();
    isOpen = !isOpen;
    dropdown.classList.toggle('hidden', !isOpen);
    if (isOpen) {
      search.value = '';
      render('');
      setTimeout(() => search.focus(), 50);
    }
  });

  search.addEventListener('input', () => render(search.value));

  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      dropdown.classList.add('hidden');
      isOpen = false;
    }
  });

  // Close on click outside
  document.addEventListener('click', (e) => {
    if (!pickerEl.contains(e.target) && isOpen) {
      dropdown.classList.add('hidden');
      isOpen = false;
    }
  });
}

// ── Custom time budget handler ─────────────────────
document.querySelectorAll('input[name="time_budget"]').forEach(radio => {
  radio.addEventListener('change', function() {
    const row = document.getElementById('customMinutesRow');
    if (row) row.classList.toggle('hidden', this.value !== 'custom');
  });
});
