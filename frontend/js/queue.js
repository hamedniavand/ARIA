// ── Queue view (Ready to Review) ──────────────────────────────────────────────
VIEW_RENDERERS.queue = renderQueue;

let _queueSort = 'priority';   // priority | score | deadline | date

function renderQueue() {
  const el    = document.getElementById('view-queue');
  const ready = state.applications.filter(a => a.status === 'ready');

  let html = `<div class="topbar">
    <h2>Review Queue</h2>
    <div class="topbar-right">
      <span style="font-size:11px;color:#888780">Sort by:</span>
      <select id="queue-sort" onchange="_queueSort=this.value;renderQueue()" style="padding:5px 8px;border:0.5px solid #d3d1c7;border-radius:6px;font-size:12px;background:#fff;outline:none">
        <option value="priority" ${_queueSort==='priority'?'selected':''}>Priority (match × urgency)</option>
        <option value="score"    ${_queueSort==='score'?'selected':''}>Match Score</option>
        <option value="deadline" ${_queueSort==='deadline'?'selected':''}>Deadline soonest</option>
        <option value="date"     ${_queueSort==='date'?'selected':''}>Date added</option>
      </select>
      <button class="btn" onclick="refreshView('queue')">↺ Refresh</button>
    </div>
  </div>`;

  if (!ready.length) {
    html += `<div class="empty">No applications ready for review.<br>
      Run a scan from <a href="#" onclick="navigate('sources');return false" style="color:#185fa5">Sources</a> to discover new positions.</div>`;
    el.innerHTML = html;
    return;
  }

  const sorted = _sortApps(ready);

  html += sorted.map(app => {
    const pos  = getPosition(app.position_id);
    const appl = getApplicant(app.applicant_id);
    return queueCard(app, pos, appl);
  }).join('');

  el.innerHTML = html;

  // Wire up auto-save for each cover letter textarea
  sorted.forEach(app => {
    const ta = document.getElementById(`cl-${app.id}`);
    if (!ta) return;
    let timer;
    ta.addEventListener('input', () => {
      clearTimeout(timer);
      setStatus(app.id, 'Saving…');
      timer = setTimeout(async () => {
        try {
          await api.patch(`/applications/${app.id}`, { cover_letter: ta.value });
          const idx = state.applications.findIndex(a => a.id === app.id);
          if (idx >= 0) state.applications[idx].cover_letter = ta.value;
          setStatus(app.id, 'Saved ✓');
        } catch {
          setStatus(app.id, 'Save failed');
        }
      }, 900);
    });
  });
}

function _sortApps(apps) {
  return [...apps].sort((a, b) => {
    if (_queueSort === 'priority') {
      const pa = a.priority_score || a.match_score;
      const pb = b.priority_score || b.match_score;
      return pb - pa;
    }
    if (_queueSort === 'score') return b.match_score - a.match_score;
    if (_queueSort === 'deadline') {
      const posA = getPosition(a.position_id);
      const posB = getPosition(b.position_id);
      const da = posA.deadline ? new Date(posA.deadline) : new Date('9999-01-01');
      const db = posB.deadline ? new Date(posB.deadline) : new Date('9999-01-01');
      return da - db;
    }
    // date
    return new Date(b.created_at) - new Date(a.created_at);
  });
}

function _urgencyLabel(app, pos) {
  const ps = app.priority_score || 0;
  const ms = app.match_score   || 0;
  if (!pos.deadline) return '';
  const days = Math.round((new Date(pos.deadline) - Date.now()) / 86400000);
  if (days < 0)  return `<span class="urgency-badge u-expired">Expired</span>`;
  if (days <= 7) return `<span class="urgency-badge u-hot">🔥 ${days}d left</span>`;
  if (days <= 14) return `<span class="urgency-badge u-warn">⚠ ${days}d left</span>`;
  if (days <= 30) return `<span class="urgency-badge u-ok">${days}d left</span>`;
  return '';
}

function _breakdownHtml(app) {
  if (!app.match_breakdown) return '';
  try {
    const bd = JSON.parse(app.match_breakdown);
    const bars = [
      { label: 'Field',    val: bd.field_alignment   || 0 },
      { label: 'Skills',   val: bd.skills_match       || 0 },
      { label: 'Research', val: bd.research_fit       || 0 },
      { label: 'Profile',  val: bd.profile_strength   || 0 },
    ];
    return `<div class="breakdown">` + bars.map(b => `
      <div class="bd-row">
        <span class="bd-label">${b.label}</span>
        <div class="bd-track"><div class="bd-fill" style="width:${b.val}%"></div></div>
        <span class="bd-val">${b.val}%</span>
      </div>`).join('') + `</div>`;
  } catch { return ''; }
}

function setStatus(appId, msg) {
  const el = document.getElementById(`qs-${appId}`);
  if (el) el.textContent = msg;
}

function queueCard(app, pos, appl) {
  const score    = Math.round(app.match_score);
  const priority = app.priority_score ? Math.round(app.priority_score) : score;
  const dl       = deadlineHtml(pos.deadline);
  const uni      = [pos.university, pos.country].filter(Boolean).join(' · ');
  const urgency  = _urgencyLabel(app, pos);
  const bdHtml   = _breakdownHtml(app);

  return `
  <div class="queue-card" id="qcard-${app.id}">
    <div class="qcard-header">
      <div class="qcard-meta">
        <div class="qcard-title">
          ${escHtml(pos.title)}
          ${urgency}
        </div>
        <div class="qcard-sub">
          ${uni ? escHtml(uni) + ' &nbsp;·&nbsp; ' : ''}
          ${chip(appl.name)}
          &nbsp;·&nbsp; Match <strong>${score}%</strong>
          ${priority !== score ? `&nbsp;·&nbsp; Priority <strong>${priority}</strong>` : ''}
          &nbsp;·&nbsp; Deadline ${dl}
        </div>
        ${bdHtml}
      </div>
      <div class="qcard-acts">
        ${pos.apply_url
          ? `<a href="${escHtml(pos.apply_url)}" target="_blank" rel="noopener" class="btn">Open ↗</a>`
          : ''}
        <button class="btn" onclick="generateTailoredCV(${app.id},${appl.id})" title="Generate a tailored CV for this position">📄 Tailor CV</button>
        <button class="btn" onclick="skipApp(${app.id})">Skip</button>
        <button class="btn primary" id="approve-${app.id}" onclick="approveApp(${app.id})">Approve &amp; Submit →</button>
      </div>
    </div>
    <div class="qcard-body">
      <div class="cl-label">Cover Letter — edit before approving</div>
      <textarea class="cover-letter" id="cl-${app.id}">${escHtml(app.cover_letter || '')}</textarea>
      <div id="cv-panel-${app.id}" style="display:none;margin-top:10px">
        <div class="cl-label">Tailored CV</div>
        <textarea class="cover-letter" id="cv-${app.id}" style="min-height:280px"></textarea>
      </div>
    </div>
    <div class="qcard-footer">
      <span id="qs-${app.id}"></span>
      <span>App #${app.id}</span>
    </div>
  </div>`;
}

async function generateTailoredCV(appId, applicantId) {
  setStatus(appId, 'Generating tailored CV…');
  try {
    const res = await api.post(`/applicants/${applicantId}/applications/${appId}/tailored-cv`, {});
    const panel = document.getElementById(`cv-panel-${appId}`);
    const ta    = document.getElementById(`cv-${appId}`);
    if (panel && ta) {
      ta.value = res.tailored_cv;
      panel.style.display = 'block';
    }
    setStatus(appId, 'Tailored CV generated ✓');
  } catch (e) {
    setStatus(appId, 'CV generation failed');
    toast('Failed: ' + e.message, 'error');
  }
}

async function approveApp(id) {
  const btn = document.getElementById(`approve-${id}`);
  if (btn) { btn.disabled = true; btn.textContent = 'Launching browser…'; }
  try {
    const ta = document.getElementById(`cl-${id}`);
    if (ta) await api.patch(`/applications/${id}`, { cover_letter: ta.value });
    await api.post(`/applications/${id}/approve`);
    toast('Browser agent started — filling form automatically…');
    pollApprovalCompletion(id);
  } catch (e) {
    toast('Approval failed: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Approve & Submit →'; }
  }
}

async function pollApprovalCompletion(id) {
  let attempts = 0;
  const poll = async () => {
    attempts++;
    try {
      const apps = await api.get('/applications');
      const app  = apps.find(a => a.id === id);
      if (app && app.status !== 'preparing') {
        await loadAll();
        if (app.status === 'submitted') {
          toast('Application submitted successfully ✓', 'success');
          navigate('submitted');
        } else if (app.status === 'error') {
          toast('Submission error: ' + (app.error_message || 'unknown'), 'error');
          renderQueue();
        } else {
          renderQueue();
        }
        return;
      }
    } catch {}
    if (attempts < 24) setTimeout(poll, 5000);
    else { await loadAll(); renderQueue(); }
  };
  setTimeout(poll, 5000);
}

async function skipApp(id) {
  if (!confirm('Skip this application?')) return;
  try {
    await api.patch(`/applications/${id}`, { status: 'skipped' });
    toast('Application skipped');
    await loadAll();
    renderQueue();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

// ── Errors view ───────────────────────────────────────────────────────────────
VIEW_RENDERERS.errors = renderErrors;

function renderErrors() {
  const el   = document.getElementById('view-errors');
  const errs = state.applications.filter(a => a.status === 'error');

  let html = `<div class="topbar">
    <h2>Errors</h2>
    <div class="topbar-right">
      <button class="btn" onclick="refreshView('errors')">↺ Refresh</button>
      ${errs.length ? `<button class="btn danger" onclick="retryAllErrors()">Retry All (${errs.length})</button>` : ''}
    </div>
  </div>`;

  if (!errs.length) {
    html += `<div class="empty">No errors. All good.</div>`;
    el.innerHTML = html;
    return;
  }

  html += `<div class="table-wrap"><table>
    <thead><tr>
      <th style="width:260px">Position</th>
      <th>Applicant</th>
      <th>Error</th>
      <th style="width:90px">Deadline</th>
      <th>Action</th>
    </tr></thead><tbody>`;

  errs.forEach(app => {
    const pos  = getPosition(app.position_id);
    const appl = getApplicant(app.applicant_id);
    const uni  = [pos.university, pos.country].filter(Boolean).join(' · ');
    html += `<tr class="error-row">
      <td>
        <div class="td-title">${escHtml(pos.title)}</div>
        ${uni ? `<div class="td-sub">${escHtml(uni)}</div>` : ''}
      </td>
      <td>${chip(appl.name)}</td>
      <td><div class="td-err">⚠ ${escHtml(app.error_message || 'Unknown error')}</div></td>
      <td>${deadlineHtml(pos.deadline)}</td>
      <td><div class="actions">
        <button class="act go" onclick="retrySingle(${app.id})">Retry</button>
        ${pos.apply_url ? `<a href="${escHtml(pos.apply_url)}" target="_blank" class="act">Open ↗</a>` : ''}
      </div></td>
    </tr>`;
  });

  html += `</tbody></table></div>`;
  el.innerHTML = html;
}

async function retrySingle(id) {
  try {
    await api.post(`/applications/${id}/retry`);
    toast('Queued for retry…');
    await loadAll();
    renderErrors();
  } catch (e) { toast('Failed: ' + e.message, 'error'); }
}

async function retryAllErrors() {
  const errs = state.applications.filter(a => a.status === 'error');
  for (const app of errs) {
    try { await api.post(`/applications/${app.id}/retry`); } catch {}
  }
  toast(`Retrying ${errs.length} application(s)…`);
  await loadAll();
  renderErrors();
}

// ── Screenshots overlay ───────────────────────────────────────────────────────

async function showScreenshots(appId) {
  try {
    const shots = await api.get(`/applications/${appId}/screenshots`);
    if (!shots.length) { toast('No screenshots for this application', 'error'); return; }
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:9999;overflow:auto;padding:24px;display:flex;flex-direction:column;align-items:center;gap:14px';
    overlay.innerHTML = `
      <div style="color:#fff;font-size:14px;font-weight:600">Screenshots — Application #${appId}</div>
      ${shots.map(s => `
        <div style="text-align:center">
          <div style="color:#aaa;font-size:11px;margin-bottom:4px">${escHtml(s.stage)}</div>
          <img src="${escHtml(s.url)}" style="max-width:min(900px,95vw);border-radius:8px;border:1px solid #555">
        </div>`).join('')}
      <button style="margin-top:8px;padding:8px 24px;background:#fff;border:none;border-radius:6px;cursor:pointer;font-family:inherit" onclick="this.parentElement.remove()">Close</button>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  } catch (e) { toast('Failed to load screenshots: ' + e.message, 'error'); }
}

// ── Submitted view ────────────────────────────────────────────────────────────
VIEW_RENDERERS.submitted = renderSubmitted;

function renderSubmitted() {
  const el   = document.getElementById('view-submitted');
  const subs = state.applications.filter(a => ['submitted', 'confirmed'].includes(a.status));

  let html = `<div class="topbar">
    <h2>Submitted</h2>
    <div class="topbar-right">
      <span class="ts">${subs.length} total</span>
      <button class="btn" onclick="refreshView('submitted')">↺ Refresh</button>
    </div>
  </div>`;

  if (!subs.length) {
    html += `<div class="empty">No submitted applications yet.</div>`;
    el.innerHTML = html;
    return;
  }

  html += `<div class="table-wrap"><table>
    <thead><tr>
      <th style="width:260px">Position</th>
      <th>Applicant</th>
      <th style="width:80px">Match</th>
      <th style="width:120px">Submitted</th>
      <th style="width:90px">Status</th>
      <th>Action</th>
    </tr></thead><tbody>`;

  subs.sort((a, b) => new Date(b.submitted_at || b.created_at) - new Date(a.submitted_at || a.created_at))
    .forEach(app => {
      const pos  = getPosition(app.position_id);
      const appl = getApplicant(app.applicant_id);
      const uni  = [pos.university, pos.country].filter(Boolean).join(' · ');
      const submittedDate = app.submitted_at ? new Date(app.submitted_at).toLocaleDateString() : '—';
      html += `<tr>
        <td>
          <div class="td-title">${escHtml(pos.title)}</div>
          ${uni ? `<div class="td-sub">${escHtml(uni)}</div>` : ''}
        </td>
        <td>${chip(appl.name)}</td>
        <td>${matchBar(app.match_score)}</td>
        <td style="font-size:11px;color:#888780">${submittedDate}</td>
        <td>${badge(app.status)}</td>
        <td><div class="actions">
          ${pos.apply_url ? `<a href="${escHtml(pos.apply_url)}" target="_blank" class="act">Open ↗</a>` : ''}
          <button class="act" onclick="showScreenshots(${app.id})">📷 Shots</button>
        </div></td>
      </tr>`;
    });

  html += `</tbody></table></div>`;
  el.innerHTML = html;
}
