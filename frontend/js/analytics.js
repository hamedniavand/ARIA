// ── Analytics view ────────────────────────────────────────────────────────────
VIEW_RENDERERS.analytics = renderAnalytics;

// Chart instances — destroyed & recreated on each render to avoid stale canvas issues
let _chartTimeline = null;
let _chartFunnel   = null;
let _chartScoreDist = null;

// Called from applicants.js "Analytics" button
async function renderAnalyticsForApplicant(applicantId) {
  const el = document.getElementById('view-analytics');
  const applicant = state.applicants.find(a => a.id === applicantId);
  const name = applicant ? applicant.name : `Applicant #${applicantId}`;

  el.innerHTML = `
  <div class="topbar">
    <h2>Analytics — ${escHtml(name)}</h2>
    <div class="topbar-right">
      <button class="btn" onclick="renderAnalytics()">← All Applicants</button>
    </div>
  </div>
  <div id="appl-analytics-body"><div class="empty">Loading…</div></div>`;

  let data;
  try {
    data = await api.get(`/applicants/${applicantId}/analytics`);
  } catch (e) {
    document.getElementById('appl-analytics-body').innerHTML =
      `<div class="empty">Failed to load: ${escHtml(e.message)}</div>`;
    return;
  }

  const { funnel, timeline, score_distribution } = data;
  const fMax = Math.max(funnel.Matched || 0, 1);

  function funnelRow(label, value, cls) {
    const pct = Math.round(value / fMax * 100);
    return `
    <div class="funnel-row">
      <div class="funnel-label">${label}</div>
      <div class="funnel-track"><div class="funnel-fill ${cls}" style="width:${Math.max(pct,2)}%"></div></div>
      <div class="funnel-val">${value} <span class="funnel-pct">${pct}%</span></div>
    </div>`;
  }

  // Score distribution
  const sdLabels = Object.keys(score_distribution);
  const sdData   = Object.values(score_distribution);
  const sdColors = ['#3b6d11','#185fa5','#b45309','#9a3412'];

  document.getElementById('appl-analytics-body').innerHTML = `

  <!-- Funnel -->
  <div class="analytics-card">
    <div class="analytics-card-title">Application Funnel</div>
    ${funnelRow('AI Matched', funnel.Matched || 0, 'f-match')}
    ${funnelRow('Ready to Review', funnel.Ready || 0, 'f-ready')}
    ${funnelRow('Submitted', funnel.Submitted || 0, 'f-sub')}
  </div>

  <!-- Timeline chart -->
  <div class="analytics-card">
    <div class="analytics-card-title">Applications Over Time (last 60 days)</div>
    ${timeline.length ? `<canvas id="chart-timeline" height="90"></canvas>` :
      '<div class="empty" style="padding:20px 0">No data yet</div>'}
  </div>

  <!-- Score distribution chart -->
  <div class="analytics-card">
    <div class="analytics-card-title">Match Score Distribution</div>
    <div style="display:flex;gap:20px;align-items:center">
      <canvas id="chart-scores" width="200" height="200" style="max-width:200px"></canvas>
      <div>${sdLabels.map((l,i) => `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <div style="width:12px;height:12px;border-radius:2px;background:${sdColors[i]};flex-shrink:0"></div>
          <span style="font-size:12px">${l}</span>
          <span style="font-size:12px;font-weight:600;margin-left:auto;padding-left:12px">${sdData[i]}</span>
        </div>`).join('')}
      </div>
    </div>
  </div>`;

  // ── Timeline Chart ─────────────────────────────────────────────────────────
  if (timeline.length) {
    if (_chartTimeline) { _chartTimeline.destroy(); _chartTimeline = null; }
    const ctx = document.getElementById('chart-timeline');
    if (ctx) {
      _chartTimeline = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: timeline.map(t => t.date),
          datasets: [{
            label: 'Applications',
            data:  timeline.map(t => t.count),
            backgroundColor: '#185fa5',
            borderRadius: 3,
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: {
            x: { grid: { display: false }, ticks: { font: { size: 10 }, maxTicksLimit: 12 } },
            y: { beginAtZero: true, ticks: { precision: 0, font: { size: 10 } }, grid: { color: '#f0ede4' } },
          }
        }
      });
    }
  }

  // ── Score Distribution Doughnut ────────────────────────────────────────────
  if (_chartScoreDist) { _chartScoreDist.destroy(); _chartScoreDist = null; }
  const ctxS = document.getElementById('chart-scores');
  if (ctxS && sdData.some(v => v > 0)) {
    _chartScoreDist = new Chart(ctxS, {
      type: 'doughnut',
      data: {
        labels: sdLabels,
        datasets: [{ data: sdData, backgroundColor: sdColors, borderWidth: 1 }]
      },
      options: {
        responsive: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: ctx => ` ${ctx.label}: ${ctx.raw}`
        }}}
      }
    });
  }
}

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
