// Coding-Agent-Eval Dashboard

const API = '';
let allRuns = [];
let currentSort = { key: 'trr', dir: 'desc' };

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
    loadRuns();
    document.getElementById('back-btn').addEventListener('click', showLeaderboard);
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('test-modal').addEventListener('click', (e) => {
        if (e.target === document.getElementById('test-modal')) closeModal();
    });
    // ESC key to close modal
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });
    document.getElementById('filter-agent').addEventListener('change', renderLeaderboard);
    document.getElementById('filter-tier').addEventListener('change', renderLeaderboard);

    // Sort headers
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (currentSort.key === key) {
                currentSort.dir = currentSort.dir === 'desc' ? 'asc' : 'desc';
            } else {
                currentSort = { key, dir: 'desc' };
            }
            renderLeaderboard();
        });
    });

    // Metrics info page navigation
    document.getElementById('metrics-info-btn')?.addEventListener('click', showMetricsInfo);
    document.getElementById('metrics-back-btn')?.addEventListener('click', hideMetricsInfo);
});

// ── API ──
async function fetchJSON(url) {
    const res = await fetch(API + url);
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
}

// ── Leaderboard ──
async function loadRuns() {
    try {
        allRuns = await fetchJSON('/api/runs');
        populateFilters();
        renderLeaderboard();
    } catch (e) {
        console.error('Failed to load runs:', e);
    }
}

function populateFilters() {
    const agents = [...new Set(allRuns.map(r => r.agent).filter(Boolean))];
    const select = document.getElementById('filter-agent');
    agents.forEach(a => {
        const opt = document.createElement('option');
        opt.value = a;
        opt.textContent = a;
        select.appendChild(opt);
    });
}

function getMetricValue(run, key) {
    const m = run.metrics || {};
    const metric = m[key];
    if (!metric) return null;
    return metric.value;
}

function getMetricGrade(run, key) {
    const m = run.metrics || {};
    const metric = m[key];
    if (!metric) return '';
    return metric.grade || '';
}

function renderLeaderboard() {
    const agentFilter = document.getElementById('filter-agent').value;
    const tierFilter = document.getElementById('filter-tier').value;

    let filtered = allRuns.filter(r => {
        if (agentFilter && r.agent !== agentFilter) return false;
        if (tierFilter && r.tier !== tierFilter) return false;
        return true;
    });

    // Sort
    filtered.sort((a, b) => {
        let va, vb;
        switch (currentSort.key) {
            case 'agent': va = a.agent || ''; vb = b.agent || ''; break;
            case 'model': va = a.model || ''; vb = b.model || ''; break;
            case 'trr': va = getMetricValue(a, 'task_resolution_rate') ?? -1; vb = getMetricValue(b, 'task_resolution_rate') ?? -1; break;
            case 'safety': va = getMetricValue(a, 'regression_safety') ?? -1; vb = getMetricValue(b, 'regression_safety') ?? -1; break;
            case 'cost': va = getMetricValue(a, 'cost_per_resolved_task') ?? 9999; vb = getMetricValue(b, 'cost_per_resolved_task') ?? 9999; break;
            case 'time': va = getMetricValue(a, 'e2e_time') ?? 9999; vb = getMetricValue(b, 'e2e_time') ?? 9999; break;
            case 'steps': va = getMetricValue(a, 'convergence_steps') ?? 9999; vb = getMetricValue(b, 'convergence_steps') ?? 9999; break;
            default: va = 0; vb = 0;
        }
        if (typeof va === 'string') {
            return currentSort.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return currentSort.dir === 'asc' ? va - vb : vb - va;
    });

    // Update sort indicators
    document.querySelectorAll('th.sortable').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === currentSort.key) {
            th.classList.add(currentSort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });

    const tbody = document.getElementById('leaderboard-body');
    tbody.innerHTML = '';

    if (filtered.length === 0) {
        document.getElementById('no-results').style.display = 'block';
        return;
    }
    document.getElementById('no-results').style.display = 'none';

    filtered.forEach((run, i) => {
        const tr = document.createElement('tr');
        tr.addEventListener('click', () => showDetail(run.run_id));

        const trr = getMetricValue(run, 'task_resolution_rate');
        const safety = getMetricValue(run, 'regression_safety');
        const cost = getMetricValue(run, 'cost_per_resolved_task');
        const time = getMetricValue(run, 'e2e_time');
        const steps = getMetricValue(run, 'convergence_steps');

        const date = run.started_at ? run.started_at.split('T')[0] : '';

        tr.innerHTML = `
            <td class="rank">${i + 1}</td>
            <td><strong>${esc(run.agent)}</strong></td>
            <td>${esc(run.model)}</td>
            <td>${formatMetric(trr, 'rate', getMetricGrade(run, 'task_resolution_rate'))}</td>
            <td>${formatMetric(safety, 'rate', getMetricGrade(run, 'regression_safety'))}</td>
            <td>${formatMetric(cost, 'cost', getMetricGrade(run, 'cost_per_resolved_task'))}</td>
            <td>${formatMetric(time, 'time', getMetricGrade(run, 'e2e_time'))}</td>
            <td>${formatMetric(steps, 'num', getMetricGrade(run, 'convergence_steps'))}</td>
            <td>${esc(run.tier)}</td>
            <td>${run.num_tasks || ''}</td>
            <td>${date}</td>
        `;
        tbody.appendChild(tr);
    });
}

function formatMetric(value, type, grade) {
    if (value === null || value === undefined) return '<span class="grade grade-F">N/A</span>';
    let text;
    switch (type) {
        case 'rate': text = (value * 100).toFixed(1) + '%'; break;
        case 'cost': text = '$' + value.toFixed(3); break;
        case 'time': text = value.toFixed(1) + 's'; break;
        case 'num': text = value.toFixed(1); break;
        default: text = String(value);
    }
    return `${text} <span class="grade grade-${grade}">${grade}</span>`;
}

// ── Detail View ──
async function showDetail(runId) {
    document.getElementById('leaderboard-view').style.display = 'none';
    document.getElementById('detail-view').style.display = 'block';

    try {
        const summary = await fetchJSON(`/api/runs/${runId}/summary`);
        renderDetail(runId, summary);
    } catch (e) {
        document.getElementById('detail-title').textContent = `Error loading: ${runId}`;
    }
}

function showLeaderboard() {
    document.getElementById('detail-view').style.display = 'none';
    document.getElementById('metrics-info-view').style.display = 'none';
    document.getElementById('leaderboard-view').style.display = 'block';
}

function renderDetail(runId, summary) {
    // Title
    document.getElementById('detail-title').textContent = runId;

    // Meta
    const metaGrid = document.getElementById('detail-meta');
    const started = summary.started_at || '';

    metaGrid.innerHTML = `
        ${metaItem('Agent', summary.agent)}
        ${metaItem('Model', summary.model)}
        ${metaItem('Tier', summary.tier)}
        ${metaItem('Tasks', summary.num_tasks)}
        ${metaItem('Started', formatTime(started))}
        ${metaItem('Environment', summary.environment || '')}
    `;

    // Metrics cards
    const metricsGrid = document.getElementById('metrics-cards');
    metricsGrid.innerHTML = '';

    const metricLabels = {
        task_resolution_rate: 'Resolution Rate',
        regression_safety: 'Regression Safety',
        token_efficiency: 'Token Efficiency',
        cost_per_resolved_task: 'Cost / Task',
        e2e_time: 'E2E Time',
        time_to_first_action: 'Time to First Action',
        convergence_steps: 'Convergence Steps',
    };

    for (const [agentName, agentData] of Object.entries(summary.agents || {})) {
        for (const [key, m] of Object.entries(agentData.metrics || {})) {
            const card = document.createElement('div');
            card.className = 'metric-card';
            const grade = m.grade || 'F';
            let valueStr;
            if (m.value === null) valueStr = 'N/A';
            else if (key.includes('rate') || key.includes('safety')) valueStr = (m.value * 100).toFixed(1) + '%';
            else if (key.includes('cost')) valueStr = '$' + m.value.toFixed(3);
            else if (key.includes('time')) valueStr = m.value.toFixed(1) + 's';
            else valueStr = m.value.toFixed(1) + ' ' + (m.unit || '');

            card.innerHTML = `
                <div class="metric-grade grade-${grade}">${grade}</div>
                <div class="metric-value">${valueStr}</div>
                <div class="metric-name">${metricLabels[key] || key}</div>
            `;
            metricsGrid.appendChild(card);
        }
    }

    // Per-task table
    const tbody = document.getElementById('tasks-body');
    tbody.innerHTML = '';

    (summary.per_task || []).forEach(task => {
        const tr = document.createElement('tr');
        tr.classList.add('task-row-no-click');
        const resolved = task.resolved;
        const resolvedStr = resolved === true ? '<span class="status-resolved">RESOLVED</span>'
            : resolved === false ? '<span class="status-failed">NOT RESOLVED</span>'
            : '-';
        const statusStr = task.status === 'success'
            ? '<span class="status-success">success</span>'
            : '<span class="status-error">' + esc(task.status) + '</span>';

        const f2p = task.fail_to_pass_total !== undefined
            ? `${task.fail_to_pass_passed}/${task.fail_to_pass_total}`
            : '-';
        const p2p = task.pass_to_pass_total !== undefined
            ? `${task.pass_to_pass_passed}/${task.pass_to_pass_total}`
            : '-';

        const hasEval = task.eval_detail;

        tr.innerHTML = `
            <td><code>${esc(task.instance_id)}</code></td>
            <td>${statusStr}</td>
            <td>${resolvedStr}</td>
            <td>${task.cost_usd ? '$' + task.cost_usd.toFixed(3) : '-'}</td>
            <td>${task.e2e_time ? task.e2e_time.toFixed(1) + 's' : '-'}</td>
            <td>${task.convergence_steps || '-'}</td>
            <td>${colorTestCount(f2p)}</td>
            <td>${colorTestCount(p2p)}</td>
            <td>${hasEval ? `<button class="btn-detail" onclick="event.stopPropagation(); showTestDetail('${runId}', '${task.instance_id}')">View</button>` : '-'}</td>
        `;
        tbody.appendChild(tr);
    });
}

function colorTestCount(str) {
    if (str === '-') return '-';
    const [passed, total] = str.split('/').map(Number);
    if (passed === total) return `<span class="badge-pass">${str}</span>`;
    return `<span class="badge-fail">${str}</span>`;
}

// ── Test Detail Modal ──
async function showTestDetail(runId, instanceId) {
    const modal = document.getElementById('test-modal');
    document.getElementById('modal-title').textContent = instanceId;

    try {
        const data = await fetchJSON(`/api/runs/${runId}/eval/${instanceId}`);
        renderTestDetail(data);
        modal.style.display = 'flex';
    } catch (e) {
        document.getElementById('modal-body').innerHTML = '<p>Failed to load test details.</p>';
        modal.style.display = 'flex';
    }
}

function closeModal() {
    document.getElementById('test-modal').style.display = 'none';
}

function renderTestDetail(data) {
    const body = document.getElementById('modal-body');

    let html = `
        <p style="margin-bottom:4px;">
            <strong>Resolved:</strong> ${data.resolved ? '<span class="status-resolved">Yes</span>' : '<span class="status-failed">No</span>'}
            ${data.error ? ` | <strong>Error:</strong> ${esc(data.error)}` : ''}
        </p>
    `;

    // FAIL_TO_PASS
    const f2p = data.fail_to_pass_results || {};
    if (Object.keys(f2p).length > 0) {
        html += `<h4 style="margin:16px 0 8px;">FAIL_TO_PASS (${Object.keys(f2p).length})</h4>`;
        html += '<table><thead><tr><th>Test</th><th>Result</th></tr></thead><tbody>';
        for (const [test, passed] of Object.entries(f2p)) {
            const badge = passed ? '<span class="badge-pass">PASS</span>' : '<span class="badge-fail">FAIL</span>';
            html += `<tr><td><code style="font-size:12px;">${esc(test)}</code></td><td>${badge}</td></tr>`;
        }
        html += '</tbody></table>';
    }

    // PASS_TO_PASS
    const p2p = data.pass_to_pass_results || {};
    if (Object.keys(p2p).length > 0) {
        const passed = Object.values(p2p).filter(v => v).length;
        const total = Object.keys(p2p).length;
        html += `<h4 style="margin:16px 0 8px;">PASS_TO_PASS (${passed}/${total})</h4>`;
        html += '<table><thead><tr><th>Test</th><th>Result</th></tr></thead><tbody>';
        for (const [test, ok] of Object.entries(p2p)) {
            const badge = ok ? '<span class="badge-pass">PASS</span>' : '<span class="badge-fail">FAIL</span>';
            html += `<tr><td><code style="font-size:12px;">${esc(test)}</code></td><td>${badge}</td></tr>`;
        }
        html += '</tbody></table>';
    }

    body.innerHTML = html;
}

// ── Metrics Info Page ──
function showMetricsInfo() {
    document.getElementById('detail-view').style.display = 'none';
    document.getElementById('leaderboard-view').style.display = 'none';
    document.getElementById('metrics-info-view').style.display = 'block';
}

function hideMetricsInfo() {
    document.getElementById('metrics-info-view').style.display = 'none';
    document.getElementById('detail-view').style.display = 'block';
}

// ── Helpers ──
function esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function metaItem(label, value) {
    return `<div class="meta-item"><div class="meta-label">${label}</div><div class="meta-value">${esc(String(value || ''))}</div></div>`;
}

function formatTime(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        return d.toLocaleString('ko-KR');
    } catch { return iso; }
}

function formatDuration(seconds) {
    if (seconds < 60) return seconds.toFixed(0) + 's';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + (seconds % 60).toFixed(0) + 's';
    return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
}
