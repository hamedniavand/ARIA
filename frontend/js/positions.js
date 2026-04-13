// ── Positions view ────────────────────────────────────────────────────────────
VIEW_RENDERERS.positions = renderPositions;

let _posFilter    = 'all';
let _posSearch    = '';
let _posApplicant = null;   // null = all applicants
let _posSort      = { col: 'status', dir: 'asc' };
let _posSelected  = new Set();  // selected position IDs

function renderPositions() {
  const el = document.getElementById('view-positions');

  const lastScanTimes = state.sources
    .filter(s => s.last_scraped_at)
    .map(s => new Date(s.last_scraped_at));
  const lastScan = lastScanTimes.length
    ? 'Last scan: ' + new Date(Math.max(...lastScanTimes)).toLocaleString()
    : 'Never scanned';

  el.innerHTML = `
  <div class="topbar">
    <h2>All Positions</h2>
    <div class="topbar-right">
      <span class="ts">${lastScan}</span>
      <button class="btn" onclick="navigate('sources')">Scan Now</button>
      <button class="btn" onclick="refreshView('positions')">↺ Refresh</button>
    </div>
  </div>
  <div class="filters" id="pos-applicant-bar"></div>
  <div class="filters" id="pos-filters"></div>
  <div id="pos-batch-bar" style="display:none" class="batch-bar">
    <span id="pos-sel-count"></span>
    <select id="pos-batch-status" style="padding:4px 8px;font-size:12px;border:0.5px solid #d3d1c7;border-radius:6px;font-family:inherit">
      <option value="">Change status to…</option>
      <option value="skipped">Skipped</option>
      <option value="matched">Matched</option>
      <option value="submitted">Submitted</option>
    </select>
    <button class="btn" onclick="batchStatusPositions()">Apply</button>
    <button class="btn danger" onclick="batchDeletePositions()">Delete Selected</button>
    <button class="btn" onclick="clearPosSelection()">Cancel</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr id="pos-thead"></tr></thead>
      <tbody id="pos-tbody"></tbody>
    </table>
  </div>`;

  renderPosApplicantBar();
  renderPosFilters();
  renderPosHead();
  renderPosTable();
}

// ── Applicant filter bar ──────────────────────────────────────────────────────

function renderPosApplicantBar() {
  const el = document.getElementById('pos-applicant-bar');
  if (!el) return;
  const pills = [['null', 'All Applicants'], ...state.applicants.map(a => [String(a.id), a.name])];
  el.innerHTML = '<span style="font-size:11px;color:#888780;margin-right:6px">Applicant:</span>' +
    pills.map(([val, label]) =>
      `<span class="pill${String(_posApplicant) === val ? ' active' : ''}"
         onclick="setPosApplicant(${val})">${escHtml(label)}</span>`
    ).join('');
}

function setPosApplicant(val) {
  _posApplicant = val === 'null' ? null : Number(val);
  _posSelected.clear();
  renderPosApplicantBar();
  renderPosFilters();
  renderPosTable();
  updatePosBatchBar();
}

// ── Status filter & search ────────────────────────────────────────────────────

function renderPosFilters() {
  const el = document.getElementById('pos-filters');
  if (!el) return;

  // Count based on current applicant filter
  const apps = _posApplicant
    ? state.applications.filter(a => a.applicant_id === _posApplicant)
    : state.applications;

  const counts = { all: state.positions.length };
  ['ready','error','submitted','matched','preparing','skipped'].forEach(s => {
    counts[s] = apps.filter(a => a.status === s).length;
  });

  const pills = [
    ['all',       `All (${counts.all})`],
    ['ready',     `Ready (${counts.ready})`],
    ['matched',   `Matched (${counts.matched})`],
    ['error',     `Errors (${counts.error})`],
    ['submitted', `Submitted (${counts.submitted})`],
    ['skipped',   `Skipped (${counts.skipped})`],
  ];

  el.innerHTML = pills.map(([key, label]) =>
    `<span class="pill${_posFilter === key ? ' active' : ''}" onclick="setPosFilter('${key}')">${label}</span>`
  ).join('') +
  `<input class="search-input" placeholder="Search…" value="${escHtml(_posSearch)}"
    oninput="setPosSearch(this.value)">`;
}

function setPosFilter(f) { _posFilter = f; _posSelected.clear(); renderPosFilters(); renderPosTable(); updatePosBatchBar(); }
function setPosSearch(q) { _posSearch = q; _posSelected.clear(); renderPosTable(); updatePosBatchBar(); }

// ── Column headers with sort ──────────────────────────────────────────────────

const _POS_COLS = [
  { key: 'select',  label: '<input type="checkbox" id="pos-select-all" onchange="toggleSelectAll(this)" title="Select all">', sortable: false, style: 'width:34px' },
  { key: 'title',   label: 'Position',    sortable: true, style: 'min-width:200px' },
  { key: 'appl',    label: 'Applicant',   sortable: false },
  { key: 'score',   label: 'Match',       sortable: true, style: 'width:90px' },
  { key: 'deadline',label: 'Deadline',    sortable: true, style: 'width:90px' },
  { key: 'status',  label: 'Status',      sortable: true, style: 'width:110px' },
  { key: 'action',  label: 'Action',      sortable: false, style: 'width:120px' },
];

function renderPosHead() {
  const tr = document.getElementById('pos-thead');
  if (!tr) return;
  tr.innerHTML = _POS_COLS.map(c => {
    const arrow = c.sortable
      ? (_posSort.col === c.key ? (_posSort.dir === 'asc' ? ' ▲' : ' ▼') : ' ↕')
      : '';
    const click = c.sortable ? `onclick="setPosSort('${c.key}')" style="cursor:pointer"` : '';
    return `<th ${c.style ? `style="${c.style}"` : ''} ${click}>${c.label}${arrow}</th>`;
  }).join('');
}

function setPosSort(col) {
  if (_posSort.col === col) {
    _posSort.dir = _posSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    _posSort = { col, dir: col === 'status' ? 'asc' : 'desc' };
  }
  renderPosHead();
  renderPosTable();
}

// ── Status priority for sorting ───────────────────────────────────────────────

const STATUS_PRIORITY = { ready: 0, matched: 1, preparing: 2, error: 3, submitted: 4, confirmed: 5, discovered: 6, skipped: 7 };

function statusPriority(apps) {
  const s = deriveStatus(apps);
  return STATUS_PRIORITY[s] ?? 9;
}

// ── Main table renderer ───────────────────────────────────────────────────────

function renderPosTable() {
  const tbody = document.getElementById('pos-tbody');
  if (!tbody) return;

  let positions = [...state.positions];

  // Applicant filter — keep only positions that have an app for this applicant
  if (_posApplicant) {
    const applAppPosIds = new Set(
      state.applications
        .filter(a => a.applicant_id === _posApplicant)
        .map(a => a.position_id)
    );
    positions = positions.filter(p => applAppPosIds.has(p.id));
  }

  // Status filter
  if (_posFilter !== 'all') {
    const relevantApps = (_posApplicant
      ? state.applications.filter(a => a.applicant_id === _posApplicant)
      : state.applications
    );
    positions = positions.filter(pos =>
      relevantApps.some(a => a.position_id === pos.id && a.status === _posFilter)
    );
  }

  // Search
  if (_posSearch) {
    const q = _posSearch.toLowerCase();
    positions = positions.filter(p =>
      p.title.toLowerCase().includes(q) ||
      (p.university || '').toLowerCase().includes(q) ||
      (p.country || '').toLowerCase().includes(q)
    );
  }

  // Sort
  positions.sort((a, b) => {
    const appsA = appsForPos(a.id);
    const appsB = appsForPos(b.id);
    let va, vb;

    if (_posSort.col === 'status') {
      va = statusPriority(appsA);
      vb = statusPriority(appsB);
    } else if (_posSort.col === 'score') {
      va = bestScore(appsA);
      vb = bestScore(appsB);
    } else if (_posSort.col === 'deadline') {
      va = a.deadline ? new Date(a.deadline).getTime() : Infinity;
      vb = b.deadline ? new Date(b.deadline).getTime() : Infinity;
    } else if (_posSort.col === 'title') {
      va = a.title.toLowerCase();
      vb = b.title.toLowerCase();
    } else {
      va = vb = 0;
    }

    if (va < vb) return _posSort.dir === 'asc' ? -1 : 1;
    if (va > vb) return _posSort.dir === 'asc' ? 1 : -1;
    // Secondary: score descending
    return bestScore(appsB) - bestScore(appsA);
  });

  if (!positions.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No positions match the current filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = positions.map(pos => {
    // When applicant filter is active, use only that applicant's apps
    const allApps  = appsForPos(pos.id);
    const viewApps = _posApplicant
      ? allApps.filter(a => a.applicant_id === _posApplicant)
      : allApps;
    const activeApps = viewApps.filter(a => !['skipped', 'discovered'].includes(a.status));
    const worstStatus = deriveStatus(viewApps);
    const hasError = viewApps.some(a => a.status === 'error');
    const topApp   = viewApps.sort((a, b) => b.match_score - a.match_score)[0];
    const checked  = _posSelected.has(pos.id) ? 'checked' : '';

    const applicantsHtml = activeApps.length
      ? activeApps.map(a => chip(getApplicant(a.applicant_id)?.name || '?')).join('')
      : `<span style="font-size:11px;color:#888780">—</span>`;

    let actionHtml = '';
    if (viewApps.some(a => a.status === 'ready')) {
      actionHtml = `<button class="act go" onclick="navigate('queue')">Review →</button>`;
    } else if (hasError) {
      actionHtml = `<button class="act err" onclick="retryPosErrors(${pos.id})">Retry</button>`;
    }
    if (pos.apply_url) {
      actionHtml += `<a href="${escHtml(pos.apply_url)}" target="_blank" class="act">Open ↗</a>`;
    }

    const errMsg = hasError ? (viewApps.find(a => a.status === 'error')?.error_message || '') : '';

    return `<tr${hasError ? ' class="error-row"' : ''}>
      <td><input type="checkbox" ${checked} onchange="togglePosSelect(${pos.id},this.checked)"></td>
      <td>
        <div class="td-title">${escHtml(pos.title)}</div>
        <div class="td-sub">${escHtml([pos.university, pos.country].filter(Boolean).join(' · '))}</div>
        ${errMsg ? `<div class="td-err">⚠ ${escHtml(errMsg.slice(0, 80))}…</div>` : ''}
      </td>
      <td>${applicantsHtml}</td>
      <td>${topApp ? matchBar(topApp.match_score) : '<span style="color:#b4b2a9">—</span>'}</td>
      <td>${deadlineHtml(pos.deadline)}</td>
      <td>${badge(worstStatus)}</td>
      <td><div class="actions">${actionHtml}</div></td>
    </tr>`;
  }).join('');
}

function appsForPos(posId) {
  return state.applications.filter(a => a.position_id === posId);
}

function bestScore(apps) {
  return apps.reduce((m, a) => Math.max(m, a.match_score || 0), 0);
}

// ── Batch selection ───────────────────────────────────────────────────────────

function toggleSelectAll(cb) {
  const rows = document.querySelectorAll('#pos-tbody input[type=checkbox]');
  rows.forEach(r => {
    r.checked = cb.checked;
    const id = Number(r.closest('tr')?.querySelector('td:nth-child(1) input')?.getAttribute('onchange')?.match(/\d+/)?.[0]);
    if (id) cb.checked ? _posSelected.add(id) : _posSelected.delete(id);
  });
  // Re-extract IDs properly
  _posSelected.clear();
  if (cb.checked) {
    rows.forEach(r => {
      const m = r.getAttribute('onchange')?.match(/\d+/);
      if (m) _posSelected.add(Number(m[0]));
    });
  }
  updatePosBatchBar();
}

function togglePosSelect(id, checked) {
  checked ? _posSelected.add(id) : _posSelected.delete(id);
  updatePosBatchBar();
  // Sync select-all checkbox
  const all = document.getElementById('pos-select-all');
  if (all) {
    const total = document.querySelectorAll('#pos-tbody input[type=checkbox]').length;
    all.checked = _posSelected.size === total && total > 0;
    all.indeterminate = _posSelected.size > 0 && _posSelected.size < total;
  }
}

function clearPosSelection() {
  _posSelected.clear();
  updatePosBatchBar();
  renderPosTable();
}

function updatePosBatchBar() {
  const bar = document.getElementById('pos-batch-bar');
  const cnt = document.getElementById('pos-sel-count');
  if (!bar) return;
  if (_posSelected.size > 0) {
    bar.style.display = 'flex';
    if (cnt) cnt.textContent = `${_posSelected.size} selected`;
  } else {
    bar.style.display = 'none';
  }
}

async function batchDeletePositions() {
  const ids = [..._posSelected];
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} position(s)? Their applications will also be removed.`)) return;
  try {
    await api.delete('/positions/batch', { ids });
    toast(`Deleted ${ids.length} position(s)`);
    _posSelected.clear();
    await loadAll();
    renderPositions();
  } catch (e) {
    toast('Delete failed: ' + e.message, 'error');
  }
}

async function batchStatusPositions() {
  const ids = [..._posSelected];
  const status = document.getElementById('pos-batch-status').value;
  if (!ids.length || !status) { toast('Select a status first', 'error'); return; }

  // Find all application IDs for the selected positions
  const appIds = state.applications
    .filter(a => ids.includes(a.position_id))
    .map(a => a.id);

  if (!appIds.length) { toast('No applications found for selected positions', 'error'); return; }

  try {
    await api.patch('/applications/batch', { ids: appIds, status });
    toast(`Updated ${appIds.length} application(s) to "${status}"`);
    _posSelected.clear();
    await loadAll();
    renderPositions();
  } catch (e) {
    toast('Update failed: ' + e.message, 'error');
  }
}

// ── Status derive & retry ─────────────────────────────────────────────────────

function deriveStatus(apps) {
  if (!apps.length) return 'discovered';
  const priority = ['error','ready','preparing','matched','submitted','confirmed','skipped','discovered'];
  for (const s of priority) {
    if (apps.some(a => a.status === s)) return s;
  }
  return apps[0].status;
}

async function retryPosErrors(posId) {
  const errs = state.applications.filter(a => a.position_id === posId && a.status === 'error');
  for (const app of errs) {
    try { await api.post(`/applications/${app.id}/retry`); } catch {}
  }
  toast(`Retrying ${errs.length} application(s)…`);
  await loadAll();
  renderPosTable();
}
