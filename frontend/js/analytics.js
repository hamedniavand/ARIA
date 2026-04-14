// ── Analytics view ────────────────────────────────────────────────────────────
VIEW_RENDERERS.analytics = renderAnalytics;

async function renderAnalytics() {
  const el = document.getElementById('view-analytics');
  el.innerHTML = `<div class="topbar"><h2>Analytics</h2></div><div id="analytics-body"><div class="empty">Loading…</div></div>`;

  let data;
  try {
    data = await api.get('/analytics');
  } catch (e) {
    document.getElementById('analytics-body').innerHTML = `<div class="empty">Failed to load analytics: ${escHtml(e.message)}</div>`;
    return;
  }

  const { funnel, by_source, by_applicant } = data;

  // ── Funnel bar ────────────────────────────────────────────────────────────
  const total = funnel.discovered || 1;
  function funnelBar(label, value, cls) {
    const pct = Math.round(value / total * 100);
    const w   = Math.max(pct, 2);
    return `
    <div class="funnel-row">
      <div class="funnel-label">${label}</div>
      <div class="funnel-track">
        <div class="funnel-fill ${cls}" style="width:${w}%"></div>
      </div>
      <div class="funnel-val">${value} <span class="funnel-pct">${pct}%</span></div>
    </div>`;
  }

  // ── Source table ──────────────────────────────────────────────────────────
  function scoreBar(score) {
    const w = Math.round(score);
    const col = score >= 80 ? '#3b6d11' : score >= 60 ? '#b45309' : '#9a3412';
    return `<div style="display:flex;align-items:center;gap:6px">
      <div style="flex:1;background:#e8e6de;border-radius:3px;height:6px">
        <div style="width:${w}%;height:100%;background:${col};border-radius:3px"></div>
      </div>
      <span style="font-size:11px;color:${col};min-width:32px">${score}%</span>
    </div>`;
  }

  const srcRows = by_source.map(s => `
  <tr>
    <td><strong>${escHtml(s.label)}</strong></td>
    <td class="num">${s.positions}</td>
    <td class="num">${s.matched}</td>
    <td>${scoreBar(s.match_rate)}</td>
    <td class="num">${s.submitted}</td>
    <td>${scoreBar(s.submit_rate)}</td>
    <td class="num">${s.avg_score > 0 ? s.avg_score : '—'}</td>
    <td class="num ${s.errors > 0 ? 'red' : ''}">${s.errors || '—'}</td>
  </tr>`).join('');

  // ── Applicant table ───────────────────────────────────────────────────────
  const applRows = by_applicant.map(a => `
  <tr>
    <td><div style="display:flex;align-items:center;gap:8px">${avatar(a.name, 22)}<strong>${escHtml(a.name)}</strong></div></td>
    <td style="color:#888780;font-size:11px">${escHtml(a.field)}</td>
    <td class="num">${a.total_apps}</td>
    <td class="num">${a.matched}</td>
    <td>${scoreBar(a.match_rate)}</td>
    <td class="num">${a.submitted}</td>
    <td>${scoreBar(a.submit_rate)}</td>
    <td class="num">${a.avg_score > 0 ? a.avg_score : '—'}</td>
  </tr>`).join('');

  document.getElementById('analytics-body').innerHTML = `

  <!-- Funnel -->
  <div class="analytics-card">
    <div class="analytics-card-title">Pipeline Funnel</div>
    ${funnelBar('Positions Discovered', funnel.discovered, 'f-disc')}
    ${funnelBar('AI Matched (≥70%)',    funnel.matched,    'f-match')}
    ${funnelBar('Ready to Review',      funnel.ready,      'f-ready')}
    ${funnelBar('Submitted',            funnel.submitted,  'f-sub')}
    ${funnel.errors ? funnelBar('Errors', funnel.errors, 'f-err') : ''}
  </div>

  <!-- By Source -->
  <div class="analytics-card">
    <div class="analytics-card-title">Performance by Source</div>
    ${by_source.length ? `
    <div class="table-wrap">
    <table class="tbl">
      <thead><tr>
        <th>Source</th>
        <th class="num">Positions</th>
        <th class="num">Matched</th>
        <th style="min-width:120px">Match Rate</th>
        <th class="num">Submitted</th>
        <th style="min-width:120px">Submit Rate</th>
        <th class="num">Avg Score</th>
        <th class="num">Errors</th>
      </tr></thead>
      <tbody>${srcRows}</tbody>
    </table>
    </div>` : '<div class="empty">No data yet</div>'}
  </div>

  <!-- By Applicant -->
  <div class="analytics-card">
    <div class="analytics-card-title">Performance by Applicant</div>
    ${by_applicant.length ? `
    <div class="table-wrap">
    <table class="tbl">
      <thead><tr>
        <th>Applicant</th>
        <th>Field</th>
        <th class="num">Apps</th>
        <th class="num">Matched</th>
        <th style="min-width:120px">Match Rate</th>
        <th class="num">Submitted</th>
        <th style="min-width:120px">Submit Rate</th>
        <th class="num">Avg Score</th>
      </tr></thead>
      <tbody>${applRows}</tbody>
    </table>
    </div>` : '<div class="empty">No applicants yet</div>'}
  </div>`;
}
