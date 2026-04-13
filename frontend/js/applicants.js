// ── Applicants view ───────────────────────────────────────────────────────────
VIEW_RENDERERS.applicants = renderApplicants;

let _editingApplicant = null;
let _docCache = {};      // applicant_id → [Document]
let _credCache = {};     // applicant_id → [PortalCredential]

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

  grid.innerHTML = state.applicants.map(a => {
    const appCount = appsForApplicant(a.id)
      .filter(ap => !['skipped','discovered'].includes(ap.status)).length;

    return `
    <div class="applicant-card" id="acard-${a.id}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        ${avatar(a.name, 34)}
        <div>
          <div class="acard-name">${escHtml(a.name)}</div>
          <div class="acard-field">${escHtml(a.field_of_study)}</div>
        </div>
      </div>
      <div style="font-size:11px;color:#888780">${escHtml(a.email)}</div>
      <div style="font-size:11px;color:#888780;margin-top:2px">${appCount} active application(s)</div>

      <!-- Documents -->
      <div class="doc-list">
        <div style="font-size:11px;font-weight:500;color:#888780;margin-bottom:6px">Documents</div>
        <div id="docs-${a.id}"><span style="font-size:11px;color:#b4b2a9">Loading…</span></div>
        <div style="margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <select id="dtype-${a.id}" style="padding:3px 7px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:11px;font-family:inherit;background:#fff;outline:none">
            <option value="cv">CV</option>
            <option value="sop">SOP</option>
            <option value="reference">Reference</option>
            <option value="portfolio">Portfolio</option>
          </select>
          <label class="act" style="cursor:pointer">
            + Upload
            <input type="file" style="display:none" onchange="uploadDoc(${a.id},this)">
          </label>
        </div>
      </div>

      <!-- Portal credentials -->
      <div class="doc-list">
        <div style="font-size:11px;font-weight:500;color:#888780;margin-bottom:6px">Portal Credentials</div>
        <div id="creds-${a.id}"><span style="font-size:11px;color:#b4b2a9">Loading…</span></div>
        <button class="act" style="margin-top:6px;font-size:11px" onclick="showCredForm(${a.id})">+ Add credential</button>
        <div id="cred-form-${a.id}"></div>
      </div>

      <div style="margin-top:12px;display:flex;gap:6px">
        <button class="act go" onclick="showApplicantForm(${a.id})">Edit</button>
        <button class="act err" onclick="deleteApplicant(${a.id})">Delete</button>
      </div>
    </div>`;
  }).join('');

  state.applicants.forEach(a => {
    loadDocs(a.id);
    loadCreds(a.id);
  });
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
  } catch (e) {
    toast('Upload failed: ' + e.message, 'error');
  }
}

async function deleteDoc(applicantId, docId) {
  if (!confirm('Delete this document?')) return;
  try {
    await api.delete(`/applicants/${applicantId}/documents/${docId}`);
    toast('Document deleted');
    loadDocs(applicantId);
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
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
      <div>
        <span class="doc-tag">${escHtml(c.portal_domain)}</span>
        ${escHtml(c.username)}
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
      <button class="act"    onclick="document.getElementById('cred-form-${applicantId}').innerHTML=''">Cancel</button>
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
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}

async function deleteCred(applicantId, credId) {
  if (!confirm('Delete this credential?')) return;
  try {
    await api.delete(`/applicants/${applicantId}/credentials/${credId}`);
    toast('Credential deleted');
    loadCreds(applicantId);
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
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
    <div class="form-actions">
      <button class="btn primary" onclick="saveApplicant()">Save</button>
      <button class="btn" onclick="cancelApplicantForm()">Cancel</button>
    </div>
  </div>`;
  wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function saveApplicant() {
  const data = {
    name:           document.getElementById('af-name').value.trim(),
    email:          document.getElementById('af-email').value.trim(),
    field_of_study: document.getElementById('af-field').value.trim(),
    bio:            document.getElementById('af-bio').value.trim(),
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
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
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
  } catch (e) {
    toast('Failed: ' + e.message, 'error');
  }
}
