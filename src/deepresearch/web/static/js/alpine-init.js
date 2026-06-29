/* ── Alpine.js Store Initialization ──────────────────
 *     Plain script (NOT ES module). Must load BEFORE
 *     alpine.min.js to register stores via alpine:init.
 * ───────────────────────────────────────────────────── */
document.addEventListener('alpine:init', () => {

  // ── App store: shared global state ─────────────────
  Alpine.store('app', {
    currentView: 'sessionListView',
    previousView: null,
    connected: false,
    connLabel: 'Disconnected',
    connClass: 'disconnected',
    version: '?',
    currentState: 'IDLE',
    currentTopic: '',
    currentSessionId: null,
    sessionState: 'IDLE',
    stateBadgeClass: 'badge-idle',
    eventCount: 0,
    elapsed: '00:00',
    phase: 'Waiting for session\u2026',
    agents: {},
    scribeInfo: { status: 'waiting', state: 'waiting' },
    qaLog: [],
    graphMode: true,
    settingsTab: 'api-keys',

    // Stub action methods (overridden by view modules)
    showSessions() {},
    showSettings() {},
    showSystemLogView() {},
    showNewResearch() {},
    cancelResearch() {},
  });

  // ── Sessions store: session list state ─────────────
  Alpine.store('sessions', {
    list: [],
    loading: false,
    searchQuery: '',
    statusFilter: 'all',
    sortBy: 'newest',
    currentPage: 0,
    pageSize: 20,
    selectedIds: [],
    selectAll: false,

    get filtered() {
      let items = this.list;
      // Apply search filter
      if (this.searchQuery) {
        const q = this.searchQuery.toLowerCase();
        items = items.filter(s => (s.topic || '').toLowerCase().includes(q));
      }
      // Apply status filter
      if (this.statusFilter !== 'all') {
        items = items.filter(s => s.status === this.statusFilter);
      }
      // Apply sorting
      if (this.sortBy === 'newest') {
        items = [...items].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
      } else if (this.sortBy === 'oldest') {
        items = [...items].sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
      } else if (this.sortBy === 'status') {
        items = [...items].sort((a, b) => (a.status || '').localeCompare(b.status || ''));
      }
      return items;
    },

    get totalCount() {
      return this.filtered.length;
    },

    get totalPages() {
      return Math.max(1, Math.ceil(this.filtered.length / this.pageSize));
    },

    get pagedSessions() {
      const start = this.currentPage * this.pageSize;
      return this.filtered.slice(start, start + this.pageSize);
    },

    get statusCounts() {
      const counts = {};
      for (const s of this.list) {
        const st = s.status || 'unknown';
        counts[st] = (counts[st] || 0) + 1;
      }
      return counts;
    },

    get hasDeletableSessions() {
      return this.list.some(s =>
        ['complete', 'error', 'cancelled', 'interrupted'].includes(s.status)
      );
    },

    setList(sessions) {
      this.list = sessions;
      if (this.currentPage >= this.totalPages) {
        this.currentPage = Math.max(0, this.totalPages - 1);
      }
    },

    toggleSelectAll() {
      this.selectAll = !this.selectAll;
      if (this.selectAll) {
        this.selectedIds = this.pagedSessions.map(s => s.session_id);
      } else {
        this.selectedIds = [];
      }
    },

    toggleSelect(sessionId) {
      const idx = this.selectedIds.indexOf(sessionId);
      if (idx === -1) {
        this.selectedIds.push(sessionId);
      } else {
        this.selectedIds.splice(idx, 1);
      }
      this.selectAll = this.pagedSessions.length > 0 &&
        this.pagedSessions.every(s => this.selectedIds.includes(s.session_id));
    },
  });

  // ── Settings store ─────────────────────────────────
  Alpine.store('settings', {
    providers: [],
    ollamaInstalled: false,
    ollamaRunning: false,
    ollamaVersion: null,
    llamacppInstalled: false,
    llamacppRunning: false,
    discoveredModels: [],
    localEndpoints: [],
    hardwareInfo: null,
    scribeModel: null,
    maxTokens: 4096,
    contextWindows: {},
    backends: [],
    isInstallingOllama: false,
    isInstallingLlamaCpp: false,
  });

  // ── Magic: timeAgo ─────────────────────────────────
  Alpine.magic('timeAgo', () => {
    return function (isoStr) {
      if (!isoStr) return '';
      const then = new Date(isoStr);
      const now = new Date();
      const diffSec = Math.floor((now - then) / 1000);
      if (diffSec < 60) return 'just now';
      if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
      if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
      if (diffSec < 2592000) return Math.floor(diffSec / 86400) + 'd ago';
      return Math.floor(diffSec / 2592000) + 'mo ago';
    };
  });
});
