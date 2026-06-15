/* ── Utility helpers ───────────────────────────────── */

export const $ = id => document.getElementById(id);

export function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

export function timestamp() {
  return new Date().toTimeString().slice(0, 8);
}

export function fmtDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

export function showToast(msg, type) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = msg;
  toast.className = 'toast ' + (type === 'error' ? 'toast-error' : 'toast-success') + ' show';
  setTimeout(() => toast.classList.remove('show'), 3000);
}

export function setConnection(label, cls) {
  const connLabel = document.getElementById('connLabel');
  const connDot = document.getElementById('connDot');
  if (connLabel) connLabel.textContent = label;
  if (connDot) connDot.className = 'conn-dot ' + cls;
}

export function formatSize(bytes) {
  if (!bytes) return '';
  const gb = bytes / (1024 * 1024 * 1024);
  return gb.toFixed(1) + ' GB';
}
