// ── API client ───────────────────────────────────────────────────────────────
const api = {
  async req(method, path, data) {
    const opts = { method, headers: {} };
    if (data !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(data);
    }
    const r = await fetch('/api' + path, opts);
    if (r.status === 204) return null;
    const json = await r.json();
    if (r.ok) return json;
    throw new Error(json.detail || JSON.stringify(json));
  },
  get:    (path)       => api.req('GET',    path),
  post:   (path, data) => api.req('POST',   path, data),
  patch:  (path, data) => api.req('PATCH',  path, data),
  delete: (path, data) => api.req('DELETE', path, data),
  async upload(path, formData) {
    const r = await fetch('/api' + path, { method: 'POST', body: formData });
    const json = await r.json();
    if (r.ok) return json;
    throw new Error(json.detail || JSON.stringify(json));
  },
};

// ── State ────────────────────────────────────────────────────────────────────
const state = {
  applicants:   [],
  positions:    [],
  applications: [],
  sources:      [],
  stats:        {},
};

// ── Shared helpers ────────────────────────────────────────────────────────────
function initials(name = '') {
  return name.split(' ').filter(Boolean).map(w => w[0]).join('').toUpperCase().slice(0, 2) || '?';
}

function avatar(name, size = 16) {
  const s = size === 16 ? '' : `style="width:${size}px;height:${size}px;font-size:${Math.round(size*0.5)}px"`;
  return `<span class="avatar" ${s}>${initials(name)}</span>`;
}

function chip(name) {
  return `<span class="chip">${avatar(name)}${escHtml(name.split(' ')[0])}</span>`;
}

function badge(status) {
  const map = {
    discovered: ['b-disc',      'Discovered'],
    matched:    ['b-match',     'Matched'],
    preparing:  ['b-prep',      'Preparing…'],
    ready:      ['b-ready',     'Ready'],
    submitted:  ['b-submitted', 'Submitted'],
    confirmed:  ['b-confirmed', 'Confirmed'],
    error:      ['b-error',     'Error'],
    skipped:    ['b-skipped',   'Skipped'],
  };
  const [cls, label] = map[status] || ['b-disc', status];
  return `<span class="badge ${cls}">${label}</span>`;
}

function matchBar(score) {
  const pct = Math.min(100, Math.max(0, Math.round(score)));
  return `<div class="match-bar">
    <div class="bar-bg"><div class="bar-fill${pct < 70 ? ' low' : ''}" style="width:${pct}%"></div></div>
    <span>${pct}%</span>
  </div>`;
}

function deadlineHtml(dl) {
  if (!dl) return '<span style="color:#b4b2a9">—</span>';
  const d = new Date(dl);
  if (isNaN(d)) return escHtml(dl);
  const days = Math.ceil((d - Date.now()) / 86400000);
  if (days < 0)  return `<span style="color:#b4b2a9;text-decoration:line-through">${escHtml(dl)}</span>`;
  if (days <= 7) return `<span class="dl-urgent">${escHtml(dl)}</span>`;
  if (days <= 14) return `<span class="dl-soon">${escHtml(dl)}</span>`;
  return escHtml(dl);
}

function escHtml(str = '') {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function getApplicant(id) {
  return state.applicants.find(a => a.id === id) || { name: 'Unknown', id };
}
function getPosition(id) {
  return state.positions.find(p => p.id === id) || { title: 'Unknown', university: '', country: '', id };
}
function appsForPosition(posId) {
  return state.applications.filter(a => a.position_id === posId);
}
function appsForApplicant(appId) {
  return state.applications.filter(a => a.applicant_id === appId);
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove('show'), 3200);
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadAll() {
  try {
    const [applicants, positions, applications, sources, stats, serper, gemini] = await Promise.all([
      api.get('/applicants'),
      api.get('/positions'),
      api.get('/applications'),
      api.get('/sources'),
      api.get('/stats'),
      api.get('/serper-usage').catch(() => ({ used: 0, limit: 2500 })),
      api.get('/gemini-usage').catch(() => ({ total_tokens: 0, calls: 0, cost_eur: 0 })),
    ]);
    state.applicants   = applicants   || [];
    state.positions    = positions    || [];
    state.applications = applications || [];
    state.sources      = sources      || [];
    state.stats        = stats        || {};
    renderStats(stats);
    renderSerperBadge(serper);
    renderGeminiUsage(gemini);
    updateNavBadges(stats);
  } catch (e) {
    toast('Failed to load data: ' + e.message, 'error');
  }
}

function renderStats(s = {}) {
  document.getElementById('stat-discovered').textContent = s.discovered ?? '—';
  document.getElementById('stat-matched').textContent    = s.matched    ?? '—';
  document.getElementById('stat-ready').textContent      = s.ready      ?? '—';
  document.getElementById('stat-submitted').textContent  = s.submitted  ?? '—';
  document.getElementById('stat-errors').textContent     = s.errors     ?? '—';
}

function renderGeminiUsage(g = {}) {
  const total = g.total_tokens ?? 0;
  const eur   = g.cost_eur    ?? 0;
  const calls = g.calls       ?? 0;
  const el    = document.getElementById('stat-gemini-n');
  const eurEl = document.getElementById('stat-gemini-eur');
  if (el) {
    el.textContent = total >= 1_000_000
      ? (total / 1_000_000).toFixed(2) + 'M'
      : total >= 1_000 ? (total / 1_000).toFixed(1) + 'K' : String(total);
    el.title = `${total.toLocaleString()} tokens across ${calls} API calls`;
  }
  if (eurEl) {
    eurEl.textContent = `€${eur.toFixed(4)}`;
    eurEl.style.color = eur > 4 ? '#a32d2d' : eur > 2 ? '#ef9f27' : '#7c5a08';
  }
}

function renderSerperBadge(s = {}) {
  const used  = s.used  ?? 0;
  const limit = s.limit ?? 2500;
  const pct   = Math.min(100, (used / limit) * 100).toFixed(1);
  const el    = document.getElementById('stat-serper-n');
  const bar   = document.getElementById('stat-serper-bar');
  if (el)  el.textContent = `${used} / ${limit}`;
  if (bar) {
    bar.style.width = pct + '%';
    bar.style.background = used >= limit * 0.9 ? '#a32d2d' : used >= limit * 0.7 ? '#ef9f27' : '#3b6d11';
  }
}

function updateNavBadges(s = {}) {
  const qEl = document.getElementById('nav-queue');
  const eEl = document.getElementById('nav-errors');
  qEl.textContent = s.ready  || 0;
  eEl.textContent = s.errors || 0;
  qEl.classList.toggle('hidden', !s.ready);
  eEl.classList.toggle('hidden', !s.errors);
}

// ── Router ────────────────────────────────────────────────────────────────────
const VIEWS = ['queue', 'positions', 'errors', 'submitted', 'applicants', 'sources', 'analytics'];

const VIEW_RENDERERS = {};  // populated by each view file

function navigate(viewName) {
  if (!VIEWS.includes(viewName)) viewName = 'queue';
  VIEWS.forEach(v => {
    document.getElementById('view-' + v).classList.toggle('hidden', v !== viewName);
    const nav = document.querySelector(`[data-view="${v}"]`);
    if (nav) nav.classList.toggle('active', v === viewName);
  });
  window._currentView = viewName;
  VIEW_RENDERERS[viewName]?.();
}

async function refreshView(viewName) {
  await loadAll();
  VIEW_RENDERERS[viewName || window._currentView]?.();
}

// ── Boot ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  document.querySelectorAll('[data-view]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      navigate(el.dataset.view);
    });
  });

  await loadAll();

  const hash = window.location.hash.slice(1);
  navigate(VIEWS.includes(hash) ? hash : 'queue');
});
