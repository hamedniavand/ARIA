// ── Applicants view ───────────────────────────────────────────────────────────
VIEW_RENDERERS.applicants = renderApplicants;

let _editingApplicant = null;
let _docCache   = {};   // applicant_id → [Document]
let _credCache  = {};   // applicant_id → [PortalCredential]
let _overviews  = {};   // applicant_id → overview object
let _checklists = {};   // applicant_id → [ChecklistItem]
let _openCard   = null; // currently expanded applicant id

function renderApplicants() {
  const el = document.getElementById('view-applicants');
  el.innerHTML = `
  <div class="topbar">
    <h2>Applicants</h2>
    <div class="topbar-right">
      <button class="btn primary" onclick="showApplicantForm(null)">+ Add Applicant</button>
    </div>
  </div>
  <div id="applicant-form-wrap"></div>
  <div class="applicant-grid" id="applicant-grid"></div>`;
  renderApplicantGrid();
}

function renderApplicantGrid() {
  const grid = document.getElementById('applicant-grid');
  if (!grid) return;
  if (!state.applicants.length) {
    grid.innerHTML = `<div class="empty">No applicants yet. Add one to get started.</div>`;
    return;
  }
  grid.innerHTML = state.applicants.map(a => _renderCard(a)).join('');
  state.applicants.forEach(a => {
    loadDocs(a.id);
    loadCreds(a.id);
    loadOverview(a.id);
    loadChecklist(a.id);
  });
}

function _renderCard(a) {
  const ov       = _overviews[a.id] || {};
  const newCount = a.new_matches_count || 0;
  const matched  = ov.total_matched ?? appsForApplicant(a.id).filter(ap => ap.status !== 'skipped').length;
  const ready    = ov.ready ?? 0;
  const submitted= ov.submitted ?? 0;
  const lastRan  = a.last_matched_at ? new Date(a.last_matched_at).toLocaleDateString() : '—';

  return `
  <div class="applicant-card" id="acard-${a.id}">

    <!-- Header row -->
    <div style="display:flex;align-items:flex-start;gap:10px;margin-bottom:8px">
      ${avatar(a.name, 36)}
      <div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:6px">
          <span class="acard-name">${escHtml(a.name)}</span>
          ${newCount > 0 ? `<span class="badge-new" title="${newCount} new match(es) since last view">${newCount} new</span>` : ''}
        </div>
        <div class="acard-field">${escHtml(a.field_of_study)}</div>
        <div style="font-size:11px;color:#888780">${escHtml(a.email)}</div>
      </div>
    </div>

    <!-- Mini stats row -->
    <div class="acard-stats">
      <div class="acard-stat"><div class="acard-stat-n">${matched}</div><div class="acard-stat-l">Matched</div></div>
      <div class="acard-stat"><div class="acard-stat-n amber">${ready}</div><div class="acard-stat-l">Ready</div></div>
      <div class="acard-stat"><div class="acard-stat-n green">${submitted}</div><div class="acard-stat-l">Submitted</div></div>
      <div class="acard-stat"><div class="acard-stat-n" style="font-size:11px">${lastRan}</div><div class="acard-stat-l">Last Match</div></div>
    </div>

    <!-- Expandable detail panel -->
    <div id="acard-detail-${a.id}" style="display:none;margin-top:10px;border-top:1px solid #f0eee6;padding-top:10px">

      <!-- Checklist -->
      <div style="margin-bottom:10px">
        <div style="font-size:11px;font-weight:600;color:#555;margin-bottom:6px">Checklist</div>
        <div id="checklist-${a.id}"></div>
        <div style="display:flex;gap:6px;margin-top:6px">
          <input id="cl-input-${a.id}" placeholder="Add task…" style="flex:1;padding:4px 8px;border:0.5px solid #d3d1c7;border-radius:5px;font-size:11px;font-family:inherit;background:#fff;outline:none"
            onkeydown="if(event.key==='Enter')addChecklistItem(${a.id})">
          <button class="act go" onclick="addChecklistItem(${a.id})">Add</button>
        </div>
      </div>

      <!-- Documents -->
      <div class="doc-list">
        <div style="font-size:11px;font-weight:600;color:#555;margin-bottom:6px">Documents</div>
        <div id="docs-${a.id}"><span style="font-size:11px;color:#b4b2a9">Loading…</span></div>
        <div style="margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <select id="dtype-${a.id}" style="padding:3px 7px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:11px;font-family:inherit;background:#fff;outline:none">
            <option value="cv">CV</option>
            <option value="sop">SOP</option>
            <option value="reference">Reference</option>
            <option value="portfolio">Portfolio</option>
          </select>
          <label class="act" style="cursor:pointer">+ Upload
            <input type="file" style="display:none" onchange="uploadDoc(${a.id},this)">
          </label>
        </div>
      </div>

      <!-- Portal credentials -->
      <div class="doc-list">
        <div style="font-size:11px;font-weight:600;color:#555;margin-bottom:6px">Portal Credentials</div>
        <div id="creds-${a.id}"><span style="font-size:11px;color:#b4b2a9">Loading…</span></div>
        <button class="act" style="margin-top:6px;font-size:11px" onclick="showCredForm(${a.id})">+ Add credential</button>
        <div id="cred-form-${a.id}"></div>
      </div>
    </div>

    <!-- Action row -->
    <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">
      <button class="act go" onclick="toggleCard(${a.id})" id="acard-toggle-${a.id}">▼ Details</button>
      <button class="act go" onclick="showApplicantForm(${a.id})">Edit</button>
      <button class="act" id="match-btn-${a.id}" onclick="triggerMatching(${a.id})">⚡ Match</button>
      <button class="act" onclick="showAnalytics(${a.id})">Analytics</button>
      <button class="act err" onclick="deleteApplicant(${a.id})">Delete</button>
    </div>
  </div>`;
}

// ── Card expand/collapse ──────────────────────────────────────────────────────

function toggleCard(id) {
  const panel  = document.getElementById(`acard-detail-${id}`);
  const btn    = document.getElementById(`acard-toggle-${id}`);
  const isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'block';
  btn.textContent = isOpen ? '▼ Details' : '▲ Details';
  if (!isOpen) {
    // Mark as viewed — reset new badge
    api.post(`/applicants/${id}/viewed`, {}).catch(() => {});
    const a = state.applicants.find(x => x.id === id);
    if (a) { a.new_matches_count = 0; }
    const badge = document.querySelector(`#acard-${id} .badge-new`);
    if (badge) badge.remove();
  }
}

// ── Overview ──────────────────────────────────────────────────────────────────

async function loadOverview(id) {
  try {
    const ov = await api.get(`/applicants/${id}/overview`);
    _overviews[id] = ov;
    // Patch mini-stats in the already-rendered card
    const card = document.getElementById(`acard-${id}`);
    if (!card) return;
    const ns = card.querySelectorAll('.acard-stat-n');
    if (ns.length >= 3) {
      ns[0].textContent = ov.total_matched ?? '—';
      ns[1].textContent = ov.ready ?? '—';
      ns[2].textContent = ov.submitted ?? '—';
    }
  } catch { /* ignore */ }
}

// ── Checklist ─────────────────────────────────────────────────────────────────

async function loadChecklist(id) {
  try {
    const items = await api.get(`/applicants/${id}/checklist`);
    _checklists[id] = items;
    renderChecklist(id, items);
  } catch { /* ignore */ }
}

function renderChecklist(id, items) {
  const el = document.getElementById(`checklist-${id}`);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = `<span style="font-size:11px;color:#b4b2a9">No tasks yet</span>`;
    return;
  }
  el.innerHTML = items.map(item => `
    <div class="checklist-row" id="cli-${item.id}">
      <input type="checkbox" ${item.done ? 'checked' : ''} onchange="toggleChecklistItem(${id},${item.id},this.checked)">
      <span style="flex:1;font-size:12px;${item.done ? 'text-decoration:line-through;color:#aaa' : ''}">${escHtml(item.text)}</span>
      <button class="act err" style="padding:1px 5px;font-size:10px" onclick="deleteChecklistItem(${id},${item.id})">×</button>
    </div>`).join('');
}

async function addChecklistItem(id) {
  const input = document.getElementById(`cl-input-${id}`);
  const text  = input?.value.trim();
  if (!text) return;
  try {
    const item = await api.post(`/applicants/${id}/checklist`, { text });
    _checklists[id] = [...(_checklists[id] || []), item];
    input.value = '';
    renderChecklist(id, _checklists[id]);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function toggleChecklistItem(applicantId, itemId, done) {
  try {
    await api.patch(`/applicants/${applicantId}/checklist/${itemId}`, { done });
    const list = _checklists[applicantId] || [];
    const idx  = list.findIndex(i => i.id === itemId);
    if (idx >= 0) list[idx].done = done;
    renderChecklist(applicantId, list);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function deleteChecklistItem(applicantId, itemId) {
  try {
    await api.delete(`/applicants/${applicantId}/checklist/${itemId}`);
    _checklists[applicantId] = (_checklists[applicantId] || []).filter(i => i.id !== itemId);
    renderChecklist(applicantId, _checklists[applicantId]);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ── Documents ─────────────────────────────────────────────────────────────────

async function loadDocs(applicantId) {
  try {
    const docs = await api.get(`/applicants/${applicantId}/documents`);
    _docCache[applicantId] = docs;
    renderDocs(applicantId, docs);
  } catch { /* ignore */ }
}

function renderDocs(applicantId, docs) {
  const el = document.getElementById(`docs-${applicantId}`);
  if (!el) return;
  if (!docs.length) {
    el.innerHTML = `<span style="font-size:11px;color:#b4b2a9">No documents uploaded</span>`;
    return;
  }
  el.innerHTML = docs.map(d => `
    <div class="doc-item">
      <div><span class="doc-tag">${escHtml(d.doc_type)}</span>${escHtml(d.filename)}
        ${d.summary ? ' <span style="color:#3b6d11;font-size:10px">✓ indexed</span>' : ''}
      </div>
      <button class="act err" style="padding:1px 6px;font-size:11px" onclick="deleteDoc(${applicantId},${d.id})">×</button>
    </div>`).join('');
}

async function uploadDoc(applicantId, input) {
  if (!input.files[0]) return;
  const dtype = document.getElementById(`dtype-${applicantId}`).value;
  const fd = new FormData();
  fd.append('file', input.files[0]);
  fd.append('doc_type', dtype);
  input.value = '';
  toast('Uploading & indexing with AI…');
  try {
    await api.upload(`/applicants/${applicantId}/documents`, fd);
    toast('Document uploaded — AI is summarising in background ✓', 'success');
    loadDocs(applicantId);
  } catch (e) { toast('Upload failed: ' + e.message, 'error'); }
}

async function deleteDoc(applicantId, docId) {
  if (!confirm('Delete this document?')) return;
  try {
    await api.delete(`/applicants/${applicantId}/documents/${docId}`);
    toast('Document deleted');
    loadDocs(applicantId);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ── Credentials ───────────────────────────────────────────────────────────────

async function loadCreds(applicantId) {
  try {
    const creds = await api.get(`/applicants/${applicantId}/credentials`);
    _credCache[applicantId] = creds;
    renderCreds(applicantId, creds);
  } catch { /* ignore */ }
}

function renderCreds(applicantId, creds) {
  const el = document.getElementById(`creds-${applicantId}`);
  if (!el) return;
  if (!creds.length) {
    el.innerHTML = `<span style="font-size:11px;color:#b4b2a9">No credentials saved</span>`;
    return;
  }
  el.innerHTML = creds.map(c => `
    <div class="cred-item">
      <div><span class="doc-tag">${escHtml(c.portal_domain)}</span>${escHtml(c.username)}
        ${c.notes ? `<span style="color:#888780"> — ${escHtml(c.notes)}</span>` : ''}
      </div>
      <button class="act err" style="padding:1px 6px;font-size:11px" onclick="deleteCred(${applicantId},${c.id})">×</button>
    </div>`).join('');
}

function showCredForm(applicantId) {
  const el = document.getElementById(`cred-form-${applicantId}`);
  if (!el) return;
  el.innerHTML = `
  <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
    <input id="cf-domain-${applicantId}" placeholder="portal.university.edu" style="padding:5px 8px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:11px;font-family:inherit;background:#fff;outline:none">
    <input id="cf-user-${applicantId}"   placeholder="username / email" style="padding:5px 8px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:11px;font-family:inherit;background:#fff;outline:none">
    <input id="cf-pass-${applicantId}"   placeholder="password" type="password" style="padding:5px 8px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:11px;font-family:inherit;background:#fff;outline:none">
    <input id="cf-notes-${applicantId}"  placeholder="notes (optional)" style="padding:5px 8px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:11px;font-family:inherit;background:#fff;outline:none">
    <div style="display:flex;gap:6px">
      <button class="act go" onclick="saveCred(${applicantId})">Save</button>
      <button class="act" onclick="document.getElementById('cred-form-${applicantId}').innerHTML=''">Cancel</button>
    </div>
  </div>`;
}

async function saveCred(applicantId) {
  const domain = document.getElementById(`cf-domain-${applicantId}`).value.trim();
  const user   = document.getElementById(`cf-user-${applicantId}`).value.trim();
  const pass   = document.getElementById(`cf-pass-${applicantId}`).value;
  const notes  = document.getElementById(`cf-notes-${applicantId}`).value.trim();
  if (!domain || !user || !pass) { toast('Domain, username and password are required', 'error'); return; }
  try {
    await api.post(`/applicants/${applicantId}/credentials`, {
      portal_domain: domain, username: user, password: pass, notes
    });
    toast('Credential saved ✓', 'success');
    document.getElementById(`cred-form-${applicantId}`).innerHTML = '';
    loadCreds(applicantId);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function deleteCred(applicantId, credId) {
  if (!confirm('Delete this credential?')) return;
  try {
    await api.delete(`/applicants/${applicantId}/credentials/${credId}`);
    toast('Credential deleted');
    loadCreds(applicantId);
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ── Applicant form ────────────────────────────────────────────────────────────

function showApplicantForm(id) {
  _editingApplicant = id;
  const a = id ? (state.applicants.find(x => x.id === id) || {}) : {};
  const wrap = document.getElementById('applicant-form-wrap');
  wrap.innerHTML = `
  <div class="form-card">
    <h3>${id ? 'Edit Applicant' : 'New Applicant'}</h3>
    <div class="form-row"><label>Full Name</label>
      <input id="af-name"  value="${escHtml(a.name || '')}" placeholder="e.g. Ali Karimi"></div>
    <div class="form-row"><label>Email</label>
      <input id="af-email" type="email" value="${escHtml(a.email || '')}" placeholder="ali@example.com"></div>
    <div class="form-row"><label>Field of Study</label>
      <input id="af-field" value="${escHtml(a.field_of_study || '')}" placeholder="e.g. Machine Learning, NLP, Robotics"></div>
    <div class="form-row"><label>Research Background / Bio</label>
      <textarea id="af-bio" placeholder="Brief summary of research interests and experience…">${escHtml(a.bio || '')}</textarea></div>
    <div class="form-row"><label>Cover Letter Language</label>
      <select id="af-lang">
        ${['English','German','French','Dutch','Spanish','Italian','Portuguese','Swedish','Finnish','Norwegian','Danish','Polish','Turkish','Arabic','Chinese','Japanese','Korean'].map(l =>
          `<option value="${l}"${(a.preferred_language||'English')===l?' selected':''}>${l}</option>`
        ).join('')}
      </select></div>
    <div class="form-actions">
      <button class="btn primary" onclick="saveApplicant()">Save</button>
      <button class="btn" onclick="cancelApplicantForm()">Cancel</button>
    </div>
  </div>`;
  wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function saveApplicant() {
  const data = {
    name:               document.getElementById('af-name').value.trim(),
    email:              document.getElementById('af-email').value.trim(),
    field_of_study:     document.getElementById('af-field').value.trim(),
    bio:                document.getElementById('af-bio').value.trim(),
    preferred_language: document.getElementById('af-lang').value,
  };
  if (!data.name || !data.email) { toast('Name and email are required', 'error'); return; }
  try {
    if (_editingApplicant) {
      await api.patch(`/applicants/${_editingApplicant}`, data);
      toast('Applicant updated ✓', 'success');
    } else {
      await api.post('/applicants', data);
      toast('Applicant created ✓', 'success');
    }
    cancelApplicantForm();
    await loadAll();
    renderApplicantGrid();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

function cancelApplicantForm() {
  document.getElementById('applicant-form-wrap').innerHTML = '';
  _editingApplicant = null;
}

async function deleteApplicant(id) {
  const a = state.applicants.find(x => x.id === id);
  if (!confirm(`Delete ${a?.name}? This cannot be undone.`)) return;
  try {
    await api.delete(`/applicants/${id}`);
    toast('Applicant deleted');
    await loadAll();
    renderApplicantGrid();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ── Manual match trigger ──────────────────────────────────────────────────────

async function triggerMatching(applicantId) {
  const btn = document.getElementById(`match-btn-${applicantId}`);
  if (btn) { btn.disabled = true; btn.textContent = '⚡ Matching…'; }
  try {
    await api.post(`/applicants/${applicantId}/match`, {});
    toast('Matching started — runs in background, check queue in a few minutes', 'success');
  } catch (e) {
    toast('Failed to start matching: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⚡ Match'; }
  }
}

// ── Analytics modal (delegated to analytics.js) ───────────────────────────────

function showAnalytics(applicantId) {
  navigateTo('analytics');
  // Let analytics.js know which applicant to show
  if (typeof renderAnalyticsForApplicant === 'function') {
    setTimeout(() => renderAnalyticsForApplicant(applicantId), 80);
  }
}
