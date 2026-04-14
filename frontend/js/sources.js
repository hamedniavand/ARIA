// ── Sources view ──────────────────────────────────────────────────────────────
VIEW_RENDERERS.sources = renderSources;

const _scanning = new Set();  // source IDs currently scanning
let _srcSort = { col: 'label', dir: 'asc' };

function renderSources() {
  const el = document.getElementById('view-sources');
  el.innerHTML = `
  <div class="topbar">
    <h2>Sources</h2>
    <div class="topbar-right">
      <button class="btn" onclick="scanAll()">⟳ Scan All Active</button>
      <button class="btn primary" onclick="showSourceForm()">+ Add Source</button>
    </div>
  </div>
  <div id="source-form-wrap"></div>
  <div class="table-wrap">
    <table>
      <thead><tr id="src-thead"></tr></thead>
      <tbody id="sources-tbody"></tbody>
    </table>
  </div>`;

  renderSourcesHead();
  renderSourcesTable();
}

// ── Column headers ────────────────────────────────────────────────────────────

const _SRC_COLS = [
  { key: 'label',       label: 'Label',        sortable: true },
  { key: 'url',         label: 'URL',           sortable: false },
  { key: 'positions',   label: 'Positions',     sortable: true,  style: 'width:90px' },
  { key: 'reliability', label: 'Reliability',   sortable: true,  style: 'width:100px' },
  { key: 'match_yield', label: 'Match Yield',   sortable: true,  style: 'width:100px' },
  { key: 'last_scan',   label: 'Last Scan',     sortable: true,  style: 'width:150px' },
  { key: 'active',      label: 'Active',        sortable: true,  style: 'width:70px' },
  { key: 'action',      label: 'Action',        sortable: false, style: 'width:180px' },
];

function renderSourcesHead() {
  const tr = document.getElementById('src-thead');
  if (!tr) return;
  tr.innerHTML = _SRC_COLS.map(c => {
    const arrow = c.sortable
      ? (_srcSort.col === c.key ? (_srcSort.dir === 'asc' ? ' ▲' : ' ▼') : ' ↕')
      : '';
    const click = c.sortable ? `onclick="setSrcSort('${c.key}')" style="cursor:pointer"` : '';
    return `<th ${c.style ? `style="${c.style}"` : ''} ${click}>${c.label}${arrow}</th>`;
  }).join('');
}

function setSrcSort(col) {
  if (_srcSort.col === col) {
    _srcSort.dir = _srcSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    _srcSort = { col, dir: 'asc' };
  }
  renderSourcesHead();
  renderSourcesTable();
}

// ── Table renderer ────────────────────────────────────────────────────────────

function renderSourcesTable() {
  const tbody = document.getElementById('sources-tbody');
  if (!tbody) return;

  if (!state.sources.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">No sources yet. Add one to start discovering positions.</td></tr>`;
    return;
  }

  // Sort
  const sorted = [...state.sources].sort((a, b) => {
    let va, vb;
    if (_srcSort.col === 'label') {
      va = a.label.toLowerCase(); vb = b.label.toLowerCase();
    } else if (_srcSort.col === 'positions') {
      va = a.position_count || 0; vb = b.position_count || 0;
    } else if (_srcSort.col === 'reliability') {
      va = a.reliability_score ?? -1; vb = b.reliability_score ?? -1;
    } else if (_srcSort.col === 'last_scan') {
      va = a.last_scraped_at ? new Date(a.last_scraped_at).getTime() : 0;
      vb = b.last_scraped_at ? new Date(b.last_scraped_at).getTime() : 0;
    } else if (_srcSort.col === 'match_yield') {
      va = a.match_yield ?? -1; vb = b.match_yield ?? -1;
    } else if (_srcSort.col === 'active') {
      va = a.is_active ? 0 : 1; vb = b.is_active ? 0 : 1;
    } else {
      va = vb = 0;
    }
    if (va < vb) return _srcSort.dir === 'asc' ? -1 : 1;
    if (va > vb) return _srcSort.dir === 'asc' ? 1 : -1;
    return 0;
  });

  tbody.innerHTML = sorted.map(s => {
    const posCount    = s.position_count ?? state.positions.filter(p => p.source_id === s.id).length;
    const isScanning  = _scanning.has(s.id);
    const lastScan    = s.last_scraped_at
      ? new Date(s.last_scraped_at).toLocaleString()
      : 'Never';
    const relScore    = s.reliability_score;
    const relHtml     = relScore == null
      ? `<span style="color:#b4b2a9;font-size:11px">—</span>`
      : reliabilityBadge(relScore);
    const yieldHtml   = s.match_yield == null
      ? `<span style="color:#b4b2a9;font-size:11px">—</span>`
      : reliabilityBadge(s.match_yield);

    return `<tr>
      <td><strong>${escHtml(s.label)}</strong></td>
      <td>
        <a href="${escHtml(s.url)}" target="_blank" rel="noopener"
          style="color:#185fa5;font-size:11px;word-break:break-all">
          ${escHtml(s.url.length > 55 ? s.url.slice(0, 55) + '…' : s.url)}
        </a>
      </td>
      <td style="font-size:12px">${posCount}</td>
      <td>${relHtml}</td>
      <td>${yieldHtml}</td>
      <td class="${isScanning ? 'scanning' : ''}" style="font-size:11px;color:#888780">
        ${isScanning ? '<span class="spin">⟳</span> Scanning…' : escHtml(lastScan)}
      </td>
      <td>
        <span class="badge ${s.is_active ? 'b-ready' : 'b-skipped'}">
          ${s.is_active ? 'Active' : 'Paused'}
        </span>
      </td>
      <td>
        <div class="actions">
          <button class="act go" onclick="triggerScan(${s.id})" ${isScanning ? 'disabled' : ''}>
            ${isScanning ? 'Scanning…' : 'Scan'}
          </button>
          <button class="act" onclick="toggleSource(${s.id}, ${!s.is_active})">
            ${s.is_active ? 'Pause' : 'Resume'}
          </button>
          <button class="act err" onclick="deleteSource(${s.id})">Delete</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}

function reliabilityBadge(score) {
  const color = score >= 80 ? '#3b6d11' : score >= 50 ? '#7c5a08' : '#8b1e1e';
  const bg    = score >= 80 ? '#eaf4e0' : score >= 50 ? '#fdf3dc' : '#fde8e8';
  return `<span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;
    padding:2px 7px;border-radius:10px;background:${bg};color:${color};font-weight:500">
    ${score.toFixed(0)}%
  </span>`;
}

// ── Scan ──────────────────────────────────────────────────────────────────────

async function triggerScan(id) {
  if (_scanning.has(id)) return;
  _scanning.add(id);
  renderSourcesTable();
  try {
    await api.post(`/sources/${id}/scan`);
    toast('Scan started — positions will appear shortly');
    pollScanCompletion(id);
  } catch (e) {
    _scanning.delete(id);
    renderSourcesTable();
    toast('Scan failed: ' + e.message, 'error');
  }
}

async function pollScanCompletion(id) {
  const original = state.sources.find(s => s.id === id)?.last_scraped_at;
  let attempts = 0;
  const poll = async () => {
    attempts++;
    try {
      const sources = await api.get('/sources');
      const updated = sources.find(s => s.id === id);
      if (updated && updated.last_scraped_at !== original) {
        _scanning.delete(id);
        await loadAll();
        renderSources();
        toast('Scan complete ✓', 'success');
        return;
      }
    } catch {}
    if (attempts < 24) setTimeout(poll, 5000);
    else { _scanning.delete(id); renderSourcesTable(); }
  };
  setTimeout(poll, 5000);
}

async function scanAll() {
  const active = state.sources.filter(s => s.is_active && !_scanning.has(s.id));
  if (!active.length) { toast('No active sources to scan', 'error'); return; }
  active.forEach(s => triggerScan(s.id));
  toast(`Scanning ${active.length} source(s)…`);
}

async function toggleSource(id, active) {
  try {
    await api.patch(`/sources/${id}`, { is_active: active });
    await loadAll();
    renderSourcesTable();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function deleteSource(id) {
  const s = state.sources.find(x => x.id === id);
  if (!confirm(`Delete source "${s?.label}"? Positions discovered from it will remain.`)) return;
  try {
    await api.delete(`/sources/${id}`);
    toast('Source deleted');
    await loadAll();
    renderSourcesTable();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

// ── Add source form ───────────────────────────────────────────────────────────

function showSourceForm() {
  const wrap = document.getElementById('source-form-wrap');
  wrap.innerHTML = `
  <div class="form-card">
    <h3>Add Source</h3>
    <div class="form-row">
      <label>Label</label>
      <input id="sf-label" placeholder="e.g. EURAXESS — Machine Learning">
    </div>
    <div class="form-row">
      <label>URL</label>
      <input id="sf-url" placeholder="https://euraxess.ec.europa.eu/jobs/search?keywords=PhD+machine+learning">
    </div>
    <div class="form-actions">
      <button class="btn primary" onclick="saveSource()">Add</button>
      <button class="btn" onclick="cancelSourceForm()">Cancel</button>
    </div>
  </div>`;
  wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function saveSource() {
  const label = document.getElementById('sf-label').value.trim();
  const url   = document.getElementById('sf-url').value.trim();
  if (!label || !url) { toast('Label and URL are required', 'error'); return; }
  try {
    await api.post('/sources', { label, url });
    toast('Source added ✓', 'success');
    cancelSourceForm();
    await loadAll();
    renderSourcesTable();
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

function cancelSourceForm() {
  document.getElementById('source-form-wrap').innerHTML = '';
}
