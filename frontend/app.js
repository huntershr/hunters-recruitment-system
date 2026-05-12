const API_URL = ""; // Use relative paths

function showToast(message, type = 'success') {
    const colors = {
        success: { bg: '#E1F5EE', text: '#0F6E56', border: '#9FE1CB' },
        error:   { bg: '#FCEBEB', text: '#A32D2D', border: '#F5B8B8' },
        info:    { bg: '#E6F1FB', text: '#185FA5', border: '#B5D4F4' }
    };
    const c = colors[type] || colors.info;
    const toast = document.createElement('div');
    toast.style.cssText = `position:fixed;top:20px;right:20px;z-index:9999;background:${c.bg};color:${c.text};border:0.5px solid ${c.border};border-radius:10px;padding:12px 18px;font-size:13px;font-weight:500;box-shadow:0 4px 16px rgba(0,0,0,0.12);display:flex;align-items:center;gap:8px;animation:_toastIn 0.2s ease;max-width:320px;`;
    toast.innerHTML = `<span>${message}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(() => toast.remove(), 300); }, 3500);
}
(function() {
    const s = document.createElement('style');
    s.textContent = '@keyframes _toastIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}';
    document.head.appendChild(s);
})();

let jobs = [];
let candidates = [];
let evaluations = [];
let currentEvaluationId = null;
let currentCandidateId = null;
let editingJobId = null;

document.addEventListener("DOMContentLoaded", () => {
    console.log("Hunters AI Initializing...");
    
    // Deep Reset: Clear any old "junk" tokens from previous versions
    const token = localStorage.getItem("token");
    if (token && (token.includes("{") || token === "undefined" || token === "null")) {
        console.log("Cleaning up invalid session...");
        localStorage.removeItem("token");
    }

    checkAuth();
    
    // Navigation
    document.querySelectorAll(".nav-links li").forEach(link => {
        link.addEventListener("click", (e) => {
            document.querySelectorAll(".nav-links li").forEach(l => l.classList.remove("active"));
            e.currentTarget.classList.add("active");
            
            const viewId = e.currentTarget.getAttribute("data-view");
            document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
            document.getElementById(viewId).classList.add("active");
        });
    });
});

function checkAuth() {
    try {
        const token = localStorage.getItem("token");
        const savedEmail = localStorage.getItem("rememberedEmail");
        
        const loginOverlay = document.getElementById("login-overlay");
        const loginEmailInput = document.getElementById("login-email");
        const loginRememberCheckbox = document.getElementById("login-remember");

        if (savedEmail && loginEmailInput) {
            loginEmailInput.value = savedEmail;
            if (loginRememberCheckbox) loginRememberCheckbox.checked = true;
        }

        if (!token || token === "undefined") {
            loginOverlay.classList.add("active");
        } else {
            loginOverlay.classList.remove("active");
            fetchData();
            fetchUserInfo();
        }
    } catch (err) {
        console.error("Auth check failed", err);
        document.getElementById("login-overlay").classList.add("active");
    }
}

async function authFetch(url, options = {}) {
    const token = localStorage.getItem("token");
    if (!token) {
        handleLogout();
        throw new Error("No token found");
    }
    
    const headers = {
        ...options.headers,
        "Authorization": `Bearer ${token}`
    };
    
    try {
        const response = await fetch(url, { ...options, headers });
        if (response.status === 401) {
            console.warn("Unauthorized! Clearing session...");
            handleLogout();
        }
        return response;
    } catch (err) {
        console.error("Fetch error:", err);
        throw err;
    }
}

async function fetchData() {
    try {
        const [jobsRes, candsRes, evalsRes] = await Promise.all([
            authFetch(`${API_URL}/jobs`),
            authFetch(`${API_URL}/candidates`),
            authFetch(`${API_URL}/results`)
        ]);

        jobs = await jobsRes.json();
        candidates = await candsRes.json();
        evaluations = await evalsRes.json();

        // Ensure they are arrays
        if (!Array.isArray(jobs)) jobs = [];
        if (!Array.isArray(candidates)) candidates = [];
        if (!Array.isArray(evaluations)) evaluations = [];

        updateDashboard();
        renderJobs();
        renderCandidates();
    } catch (err) {
        console.error("Failed to fetch data", err);
    }
}

/** Stored score normalization: fractions (≤1), legacy 1–10 scale, or unified 0–100. */
function evalScorePercent(raw) {
    if (raw === null || raw === undefined || raw === "") return null;
    const n = Number(raw);
    if (Number.isNaN(n)) return null;
    if (n <= 1) return Math.round(n * 100);
    if (n <= 10) return Math.round(n * 10);
    return Math.round(Math.min(100, n));
}

function scoreBadgeHtml(score) {
    const pct = evalScorePercent(score);
    if (pct === null) return '<span class="skeleton skeleton-badge"></span>';
    const cls = pct >= 75 ? 'score-green' : pct >= 50 ? 'score-amber' : 'score-red';
    return `<span class="score-badge ${cls}">${pct}%</span>`;
}

function stagePillHtml(decision) {
    if (!decision || decision.toLowerCase() === 'pending') {
        return '<span class="stage-pill stage-new">New</span>';
    }
    const d = decision.toLowerCase();
    if (d === 'shortlist') return '<span class="stage-pill stage-interview">Shortlisted</span>';
    if (d === 'maybe')     return '<span class="stage-pill stage-screening">Screening</span>';
    if (d === 'reject')    return '<span class="stage-pill stage-rejected">Rejected</span>';
    return `<span class="stage-pill stage-new">${decision}</span>`;
}

function updateDashboard() {
    document.getElementById("total-jobs").innerText = jobs.length;
    document.getElementById("total-candidates").innerText = candidates.length;

    const shortlisted = evaluations.filter(e => e.decision.toLowerCase() === "shortlist").length;
    document.getElementById("total-accepted").innerText = shortlisted;

    const pendingEl = document.getElementById("total-pending");
    if (pendingEl) {
        const pending = candidates.filter(c => !evaluations.find(e => e.candidate_id === c.id)).length;
        pendingEl.innerText = pending;
    }

    // Trend texts
    const jobsTrend = document.getElementById("jobs-trend");
    if (jobsTrend) jobsTrend.innerText = jobs.length > 0 ? `${jobs.length} active` : "No jobs yet";
    const candTrend = document.getElementById("candidates-trend");
    if (candTrend) candTrend.innerText = candidates.length > 0 ? `${candidates.length} total` : "No candidates";
    const shortTrend = document.getElementById("shortlisted-trend");
    if (shortTrend) shortTrend.innerText = shortlisted > 0 ? `${shortlisted} shortlisted` : "None yet";

    const tbody = document.querySelector("#recent-candidates-table tbody");
    tbody.innerHTML = "";

    const recent = candidates.slice(-5).reverse();
    if (recent.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6">
            <div class="empty-state">
                <div style="width:44px;height:44px;background:#F5F6F8;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto;">
                    <svg viewBox="0 0 24 24" fill="none" width="22" height="22" style="stroke:#1B2A4A;fill:none;stroke-width:1.5;">
                        <circle cx="9" cy="7" r="4"/><path d="M2 20c0-3.87 3.13-7 7-7"/>
                        <circle cx="17" cy="11" r="3"/><path d="M14 20c0-2.76 1.34-5 3-5s3 2.24 3 5"/>
                    </svg>
                </div>
                <div class="empty-title">No candidates yet</div>
                <div class="empty-sub">Upload CVs or add candidates to get started</div>
                <button class="btn-primary" style="width:auto;font-size:11px;padding:6px 14px;margin-top:8px;" onclick="openCandidateModal()">Add your first candidate</button>
            </div>
        </td></tr>`;
        return;
    }

    recent.forEach(c => {
        const job = jobs.find(j => j.id === c.job_applied);
        const ev = evaluations.find(e => e.candidate_id === c.id);
        const score = ev ? ev.score : null;
        const decision = ev ? ev.decision : null;

        tbody.innerHTML += `
            <tr>
                <td><strong>${c.name}</strong><br><small style="color:#9CA3AF">${c.email || ''}</small></td>
                <td>${job ? job.job_title : '—'}</td>
                <td>${stagePillHtml(decision)}</td>
                <td>${scoreBadgeHtml(score)}</td>
                <td><span class="badge ${decision ? decision.toLowerCase() : 'pending'}">${decision || 'Pending'}</span></td>
                <td><button class="btn-action" onclick="viewCandidate(${c.id})">View Report</button></td>
            </tr>
        `;
    });
}

let jobsView = 'card';

function setJobsView(view) {
    jobsView = view;
    const cardBtn  = document.getElementById('jobs-card-toggle');
    const listBtn  = document.getElementById('jobs-list-toggle');
    const cardView = document.getElementById('jobs-card-view');
    const listView = document.getElementById('jobs-list-view');
    if (view === 'card') {
        if (cardBtn) { cardBtn.style.background = '#1B2A4A'; cardBtn.style.color = '#fff'; }
        if (listBtn) { listBtn.style.background = '#fff';    listBtn.style.color = '#6B7280'; }
        if (cardView) cardView.style.display = '';
        if (listView) listView.style.display = 'none';
    } else {
        if (cardBtn) { cardBtn.style.background = '#fff';    cardBtn.style.color = '#6B7280'; }
        if (listBtn) { listBtn.style.background = '#1B2A4A'; listBtn.style.color = '#fff'; }
        if (cardView) cardView.style.display = 'none';
        if (listView) listView.style.display = 'block';
    }
    renderJobs();
}

const EDU_KEYWORDS = ['school','university','training','education','academy','college','kindergarten','institute','learning','teacher','headmistress','hod','head of department','curriculum','cambridge','academic','subject teacher','floating teacher','consultant','key stage','english hod','english head'];
function isEduJob(j) {
    const t = (j.job_title || '').toLowerCase();
    const ind = (j.industry_experience || '').toLowerCase();
    return EDU_KEYWORDS.some(k => t.includes(k) || ind.includes(k));
}

function sectionDivider(label, icon, count) {
    return `<div style="grid-column:1/-1;display:flex;align-items:center;gap:10px;margin:8px 0 4px;">
        <span style="font-size:13px;font-weight:600;color:#1B2A4A;">${icon} ${label}</span>
        <span style="background:#F0F4F8;color:#6B7280;font-size:11px;padding:2px 8px;border-radius:10px;">${count} job${count!==1?'s':''}</span>
        <div style="flex:1;height:1px;background:#E5E7EB;"></div>
    </div>`;
}

function listDividerRow(label, icon) {
    return `<tr><td colspan="7" style="padding:10px 14px;background:#F8F9FF;border-bottom:0.5px solid #E5E7EB;">
        <span style="font-size:12px;font-weight:600;color:#1B2A4A;">${icon} ${label}</span>
    </td></tr>`;
}

function renderJobs() {
    const cardView = document.getElementById("jobs-card-view");
    const listTbody = document.getElementById("jobs-list-tbody");
    if (!cardView) return;

    cardView.innerHTML = "";
    if (listTbody) listTbody.innerHTML = "";

    if (jobs.length === 0) {
        cardView.innerHTML = "<p style='grid-column: 1/-1; text-align: center; color: var(--text-muted);'>No jobs found. Create one to get started!</p>";
        return;
    }

    const eduJobs  = jobs.filter(j =>  isEduJob(j));
    const corpJobs = jobs.filter(j => !isEduJob(j));

    function renderGroup(group, label, icon) {
        if (!group.length) return;

        if (jobsView !== 'list') {
            cardView.innerHTML += sectionDivider(label, icon, group.length);
        }
        if (listTbody) {
            listTbody.innerHTML += listDividerRow(label, icon);
        }

        group.forEach(j => {
            const initials = (j.job_title || '').split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'J';
            const salary   = j.salary_range || 'Negotiable';
            const statusPill = j.is_approved
                ? `<span style="background:#E1F5EE;color:#0F6E56;padding:3px 8px;border-radius:10px;font-size:10px;font-weight:500;display:inline-flex;align-items:center;gap:3px;"><span style="width:5px;height:5px;border-radius:50%;background:#0F6E56;flex-shrink:0;"></span>Approved</span>`
                : `<span style="background:#FFF7E0;color:#9B6F00;padding:3px 8px;border-radius:10px;font-size:10px;font-weight:500;display:inline-flex;align-items:center;gap:3px;"><span style="width:5px;height:5px;border-radius:50%;background:#C9A84C;flex-shrink:0;"></span>Pending</span>`;
            const weightPills = `
                <div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;">
                    <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 7px;font-size:10px;font-weight:500;">Exp ${Math.round((j.weight_experience||0)*100)}%</span>
                    <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 7px;font-size:10px;font-weight:500;">Skills ${Math.round((j.weight_skills||0)*100)}%</span>
                    <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 7px;font-size:10px;font-weight:500;">Edu ${Math.round((j.weight_education||0)*100)}%</span>
                    <span style="background:#F0F2F8;color:#1B2A4A;border-radius:20px;padding:2px 7px;font-size:10px;font-weight:500;">Beh ${Math.round((j.weight_behavioral||0)*100)}%</span>
                </div>`;

            if (jobsView !== 'list') {
                cardView.innerHTML += `
                    <div class="job-card">
                        <div class="job-card-header" style="flex-wrap:wrap;gap:8px;">
                            <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
                                <div style="width:36px;height:36px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0;">${initials}</div>
                                <h3 style="margin:0;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${j.job_title}</h3>
                            </div>
                            <div style="display:flex;gap:7px;align-items:center;flex-shrink:0;">
                                ${statusPill}
                                <button class="btn-share edit-btn" onclick="editJob(${j.id})" title="Edit Job"><i class='bx bx-pencil'></i> Edit</button>
                                <button class="btn-share" onclick="copyPublicLink(${j.id})" title="Share Link"><i class='bx bx-share-alt'></i></button>
                                <button class="btn-share" style="color:var(--red);border-color:var(--red);" onclick="deleteJob(${j.id})" title="Delete Job"><i class='bx bx-trash'></i></button>
                            </div>
                        </div>
                        <div class="job-meta">
                            <p><i class='bx bx-money'></i> ${salary}</p>
                            <p><i class='bx bx-book'></i> ${j.education_level}</p>
                            <p><i class='bx bx-time'></i> ${j.min_experience} yrs min</p>
                            <p><i class='bx bx-map'></i> ${j.job_location || 'Remote/Any'}</p>
                        </div>
                        <div class="job-details-tags" style="margin-top:12px;font-size:11px;line-height:1.4;">
                            <div style="margin-bottom:5px;"><strong><i class='bx bx-bolt-circle'></i> Skills:</strong> ${j.required_skills}</div>
                            ${j.behavioral_skills ? `<div style="margin-bottom:5px;"><strong><i class='bx bx-smile'></i> Behavioral:</strong> ${j.behavioral_skills}</div>` : ''}
                        </div>
                        ${weightPills}
                        <div style="margin-top:16px;display:flex;justify-content:flex-end;">
                            <a href="/apply.html?job_id=${j.id}" target="_blank" class="btn-apply-link">Apply Now <i class='bx bx-right-arrow-alt'></i></a>
                        </div>
                    </div>`;
            }

            if (listTbody) {
                const posted = j.created_at ? new Date(j.created_at).toLocaleDateString() : '—';
                listTbody.innerHTML += `
                    <tr style="border-bottom:0.5px solid #F3F4F6;" onmouseover="this.style.background='#FAFBFF'" onmouseout="this.style.background='transparent'">
                        <td style="padding:10px 14px;">
                            <div style="display:flex;align-items:center;gap:8px;">
                                <div style="width:28px;height:28px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;flex-shrink:0;">${initials}</div>
                                <span style="font-weight:500;font-size:13px;color:#1B2A4A;">${j.job_title}</span>
                            </div>
                        </td>
                        <td style="padding:10px 14px;font-size:12px;color:#6B7280;">${j.job_location || '—'}</td>
                        <td style="padding:10px 14px;font-size:12px;color:#6B7280;">${j.min_experience} yrs</td>
                        <td style="padding:10px 14px;font-size:12px;color:#1B2A4A;font-weight:500;">${salary}</td>
                        <td style="padding:10px 14px;">${statusPill}</td>
                        <td style="padding:10px 14px;font-size:12px;color:#6B7280;">${posted}</td>
                        <td style="padding:10px 14px;">
                            <div style="display:flex;gap:6px;align-items:center;">
                                <button onclick="editJob(${j.id})" style="height:28px;padding:0 8px;border:0.5px solid #1B2A4A;background:#fff;color:#1B2A4A;border-radius:7px;font-size:11px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;">
                                    <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>Edit
                                </button>
                                <button onclick="copyPublicLink(${j.id})" style="height:28px;padding:0 8px;border:0.5px solid #C9A84C;background:#fff;color:#C9A84C;border-radius:7px;font-size:11px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;">
                                    <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>Share
                                </button>
                                <button onclick="deleteJob(${j.id})" style="height:28px;width:28px;padding:0;border:0.5px solid #CC2B2B;background:#fff;color:#CC2B2B;border-radius:7px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;" title="Delete">
                                    <svg width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                                </button>
                            </div>
                        </td>
                    </tr>`;
            }
        });
    }

    renderGroup(eduJobs,  'Education Jobs',  '🎓');
    renderGroup(corpJobs, 'Corporate Jobs',  '🏢');
}

function copyPublicLink(id) {
    const link = `${window.location.origin}/apply.html?job_id=${id}`;
    navigator.clipboard.writeText(link).then(() => {
        showToast('Public application link copied to clipboard!', 'success');
    });
}

let pipelineView = 'board';
let pipelineFilter = '';

function renderCandidates() {
    renderKanban(pipelineFilter);
    renderCandidateList(pipelineFilter);
}

function renderKanban(filter) {
    const board = document.getElementById("kanban-board");
    if (!board) return;

    const cols = [
        { id: 'new',       label: 'New',       accent: '#378ADD', decisions: [null, 'pending'] },
        { id: 'screening', label: 'Screening', accent: '#EF9F27', decisions: ['maybe'] },
        { id: 'interview', label: 'Interview', accent: '#1D9E75', decisions: ['shortlist'] },
        { id: 'offer',     label: 'Offer',     accent: '#C9A84C', decisions: ['offer'] },
        { id: 'rejected',  label: 'Rejected',  accent: '#CC2B2B', decisions: ['reject'] },
    ];

    const lf = (filter || '').toLowerCase();

    board.innerHTML = cols.map(col => {
        const colCandidates = candidates.filter(c => {
            const ev = evaluations.find(e => e.candidate_id === c.id);
            const dec = ev ? ev.decision.toLowerCase() : null;
            const inCol = col.decisions.includes(dec);
            if (!inCol) return false;
            if (lf) {
                const job = jobs.find(j => j.id === c.job_applied);
                return c.name.toLowerCase().includes(lf) ||
                       (c.email || '').toLowerCase().includes(lf) ||
                       (job ? job.job_title.toLowerCase().includes(lf) : false);
            }
            return true;
        });

        const cards = colCandidates.map(c => {
            const ev = evaluations.find(e => e.candidate_id === c.id);
            const job = jobs.find(j => j.id === c.job_applied);
            const score = ev ? ev.score : null;
            let dotColor = '#9CA3AF';
            let scoreText = 'Pending';
            const pct = evalScorePercent(score);
            if (pct !== null) {
                dotColor = pct >= 75 ? '#0F6E56' : pct >= 50 ? '#854F0B' : '#A32D2D';
                scoreText = `${pct}%`;
            }
            return `
                <div class="kanban-card" onclick="viewCandidate(${c.id})">
                    <div class="kanban-card-name">${c.name}</div>
                    <div class="kanban-card-role">${job ? job.job_title : '—'}</div>
                    <div class="kanban-card-score">
                        <span class="score-dot" style="background:${dotColor};"></span>
                        <span style="color:${dotColor};">${scoreText}</span>
                    </div>
                </div>
            `;
        }).join('');

        return `
            <div class="kanban-col" style="border-top:3px solid ${col.accent};">
                <div class="kanban-col-header">
                    <span class="kanban-col-title">${col.label}</span>
                    <span class="kanban-col-count">${colCandidates.length}</span>
                </div>
                <div class="kanban-col-body">
                    ${cards}
                    <button class="kanban-add-card" onclick="openCandidateModal()">+ Add candidate</button>
                </div>
            </div>
        `;
    }).join('');
}

function _decisionToStage(decision) {
    if (!decision || decision.toLowerCase() === 'pending') return 'New';
    const d = decision.toLowerCase();
    if (d === 'maybe')     return 'Screening';
    if (d === 'shortlist') return 'Interview';
    if (d === 'offer')     return 'Offer';
    if (d === 'reject')    return 'Rejected';
    return decision;
}

function _stageColor(stage) {
    const map = { New: '#378ADD', Screening: '#EF9F27', Interview: '#1D9E75', Offer: '#C9A84C', Rejected: '#CC2B2B' };
    return map[stage] || '#9CA3AF';
}

function renderCandidateList(filter) {
    const tbody = document.querySelector("#all-candidates-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    const lf = (filter || '').toLowerCase();
    const filtered = lf ? candidates.filter(c => {
        const job = jobs.find(j => j.id === c.job_applied);
        return c.name.toLowerCase().includes(lf) ||
               (c.email || '').toLowerCase().includes(lf) ||
               (job ? job.job_title.toLowerCase().includes(lf) : false);
    }) : candidates;

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12">
            <div class="empty-state">
                <div class="empty-title">No candidates found</div>
                <div class="empty-sub">${lf ? 'No results for "' + lf + '"' : 'Add candidates to get started'}</div>
            </div>
        </td></tr>`;
        return;
    }

    filtered.forEach(c => {
        const ev = evaluations.find(e => e.candidate_id === c.id);
        const job = jobs.find(j => j.id === c.job_applied);
        const score = ev ? ev.score : null;
        const decision = ev ? ev.decision : null;
        const stage = _decisionToStage(decision);
        const stageCol = _stageColor(stage);
        const pct = evalScorePercent(score);
        const sc = pct === null ? { bg: '#F5F6F8', text: '#6B7280' }
                 : pct >= 75   ? { bg: '#E1F5EE', text: '#0F6E56' }
                 : pct >= 50   ? { bg: '#FAEEDA', text: '#854F0B' }
                 :               { bg: '#FCEBEB', text: '#A32D2D' };

        const interviewer = localStorage.getItem(`hunters_interviewer_${c.id}`) || '';
        const hrNotes     = localStorage.getItem(`hunters_notes_${c.id}`) || '';
        const location    = job ? (job.job_location || '—') : '—';
        const hasCV       = c.cv_text && c.cv_text.trim().length > 10;

        tbody.innerHTML += `
            <tr onmouseover="this.style.background='#F8F9FF'" onmouseout="this.style.background='transparent'">
                <td style="min-width:150px;">
                    <strong style="font-size:12px;">${c.name}</strong>
                </td>
                <td style="font-size:11px;color:#6B7280;">${c.phone || '—'}</td>
                <td style="font-size:11px;color:#6B7280;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${c.email || '—'}</td>
                <td style="font-size:11px;color:#6B7280;">${location}</td>
                <td>
                    <span style="display:inline-flex;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:500;background:${stageCol}22;color:${stageCol};">${stage}</span>
                </td>
                <td style="font-size:11px;color:#6B7280;">${c.education || '—'}</td>
                <td style="font-size:11px;color:#6B7280;">${c.skills ? c.skills.split(',')[0].trim() || '—' : '—'}</td>
                <td style="font-size:11px;color:#1B2A4A;font-weight:500;">${c.experience_years != null ? c.experience_years + ' yrs' : '—'}</td>
                <td style="font-size:11px;">
                    ${hasCV ? '<span style="color:#0F6E56;font-weight:500;">✓ CV</span>' : '<span style="color:#9CA3AF;">—</span>'}
                </td>
                <td style="min-width:110px;">
                    <input type="text" value="${interviewer.replace(/"/g,'&quot;')}" placeholder="Interviewer…"
                        onchange="localStorage.setItem('hunters_interviewer_${c.id}',this.value)"
                        style="width:100%;border:0.5px solid #E5E7EB;border-radius:6px;padding:4px 7px;font-size:11px;outline:none;color:#1B2A4A;">
                </td>
                <td>
                    ${pct !== null
                        ? `<span style="display:inline-flex;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;background:${sc.bg};color:${sc.text};">${pct}%</span>`
                        : '<span class="skeleton skeleton-badge"></span>'}
                </td>
                <td style="min-width:130px;">
                    <input type="text" value="${hrNotes.replace(/"/g,'&quot;')}" placeholder="Notes…"
                        onchange="localStorage.setItem('hunters_notes_${c.id}',this.value)"
                        style="width:100%;border:0.5px solid #E5E7EB;border-radius:6px;padding:4px 7px;font-size:11px;outline:none;color:#1B2A4A;">
                </td>
                <td>
                    <div style="display:flex;gap:5px;">
                        <button class="btn-action" style="font-size:10px;padding:4px 8px;" onclick="viewCandidate(${c.id})">View</button>
                        <button class="btn-action" style="color:var(--red);border-color:var(--red);font-size:10px;padding:4px 6px;" onclick="deleteCandidate(${c.id})">✕</button>
                    </div>
                </td>
            </tr>
        `;
    });
}

function setPipelineView(view) {
    pipelineView = view;
    const boardView = document.getElementById("kanban-board-view");
    const listView = document.getElementById("candidates-list-view");
    const boardToggle = document.getElementById("board-toggle");
    const listToggle = document.getElementById("list-toggle");
    if (!boardView || !listView) return;

    if (view === 'board') {
        boardView.style.display = '';
        listView.style.display = 'none';
        boardToggle.classList.add('active');
        listToggle.classList.remove('active');
    } else {
        boardView.style.display = 'none';
        listView.style.display = '';
        listToggle.classList.add('active');
        boardToggle.classList.remove('active');
    }
}

function filterPipeline(value) {
    pipelineFilter = value;
    renderKanban(value);
    renderCandidateList(value);
}

function viewCandidate(id) {
    const candidate = candidates.find(c => c.id === id);
    const eval = evaluations.find(e => e.candidate_id === id);
    currentCandidateId = id;
    
    document.getElementById("modal-candidate-name").innerText = `${candidate.name}'s AI Report`;
    document.getElementById("modal-candidate-phone").innerText = candidate.phone || '-';
    document.getElementById("modal-candidate-expected-salary").innerText = candidate.expected_salary || '-';
    
    if (eval) {
        currentEvaluationId = eval.id;
        const pct = evalScorePercent(eval.score) ?? 0;
        document.getElementById("modal-candidate-score").innerText = `${pct}%`;
        document.getElementById("modal-candidate-score").style.background = `conic-gradient(var(--primary) ${pct}%, var(--bg-dark) 0)`;
        
        document.getElementById("modal-candidate-decision").innerText = eval.decision;
        document.getElementById("modal-candidate-decision").className = `decision-badge badge ${eval.decision.toLowerCase()}`;
        
        document.getElementById("modal-candidate-reason").innerText = eval.reason;
        document.getElementById("modal-candidate-strengths").innerText = eval.strengths || "None noted.";
        document.getElementById("modal-candidate-weaknesses").innerText = eval.weaknesses || "None noted.";
        
        const qList = document.getElementById("modal-candidate-questions");
        qList.innerHTML = "";
        if (eval.suggested_interview_questions && eval.suggested_interview_questions.length > 0) {
            eval.suggested_interview_questions.forEach(q => {
                qList.innerHTML += `<li>${q}</li>`;
            });
        } else {
            qList.innerHTML = "<li>No specific questions generated.</li>";
        }
    } else {
        document.getElementById("modal-candidate-score").innerText = "0%";
        document.getElementById("modal-candidate-score").style.background =
            `conic-gradient(var(--primary) 0%, var(--bg-dark) 0)`;
        document.getElementById("modal-candidate-decision").innerText = "Pending";
        document.getElementById("modal-candidate-reason").innerText = "Evaluation is currently running or failed.";
        document.getElementById("modal-candidate-strengths").innerText = "-";
        document.getElementById("modal-candidate-weaknesses").innerText = "-";
        document.getElementById("modal-candidate-questions").innerHTML = "";
    }
    
    document.getElementById("candidate-detail-modal").classList.add("active");
}

function closeModals() {
    document.querySelectorAll(".modal").forEach(m => m.classList.remove("active"));
}

function switchJobTab(event, tabId) {
    const modal = document.getElementById('job-add-modal');
    const container = event
        ? event.target.closest('.modal-content')
        : (modal ? modal.querySelector('.modal-content') : null);
    if (!container) return;
    container.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    container.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    if (event) {
        event.target.classList.add('active');
    } else {
        const match = container.querySelector(`[onclick*="${tabId}"]`);
        if (match) match.classList.add('active');
    }
    const tab = document.getElementById(tabId);
    if (tab) tab.classList.add('active');

    // Show/hide wizard UI — only visible on manual tab
    const isManual = tabId === 'manual-tab';
    const indicator = document.getElementById("job-step-indicator");
    const footer    = document.getElementById("job-wizard-footer");
    if (indicator) indicator.style.display = isManual ? '' : 'none';
    if (footer)    footer.style.display    = isManual ? '' : 'none';

    if (isManual) jobWizardSetStep(1);
}

function jobWizardGoTo(step) { jobWizardSetStep(step); }

let currentJobStep = 1;

function jobWizardSetStep(step) {
    currentJobStep = step;
    [1, 2, 3].forEach(s => {
        const el = document.getElementById(`job-step-${s}`);
        if (el) el.style.display = s === step ? '' : 'none';
        const circle = document.getElementById(`step-circle-${s}`);
        const label  = document.getElementById(`step-label-${s}`);
        if (circle && label) {
            if (s < step)       { circle.className = 'step-circle done';   label.className = 'step-label done'; }
            else if (s === step){ circle.className = 'step-circle active'; label.className = 'step-label active'; }
            else                { circle.className = 'step-circle idle';   label.className = 'step-label idle'; }
        }
    });
    const backBtn = document.getElementById("job-step-back-btn");
    const nextBtn = document.getElementById("job-step-next-btn");
    if (backBtn) backBtn.style.display = step === 1 ? 'none' : '';
    if (nextBtn) nextBtn.innerText = step === 3 ? 'Save Job' : 'Next step →';
    if (step === 3) updateWeights();
}

function jobWizardNext() {
    if (currentJobStep === 3) {
        handleJobManualCreate(null);
        return;
    }
    if (currentJobStep === 1) {
        if (!document.getElementById("manual-job-title").value.trim()) {
            showToast('Please enter a Job Title.', 'error');
            return;
        }
    }
    jobWizardSetStep(currentJobStep + 1);
}

function jobWizardBack() {
    if (currentJobStep > 1) jobWizardSetStep(currentJobStep - 1);
}

function updateWeights() {
    const exp  = parseInt(document.getElementById("manual-job-w-exp")?.value || 30);
    const sk   = parseInt(document.getElementById("manual-job-w-skills")?.value || 40);
    const edu  = parseInt(document.getElementById("manual-job-w-edu")?.value || 20);
    const beh  = parseInt(document.getElementById("manual-job-w-behavioral")?.value || 10);
    const total = exp + sk + edu + beh;

    const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.innerText = v + '%'; };
    setVal('wv-exp', exp); setVal('wv-skills', sk); setVal('wv-edu', edu); setVal('wv-behavioral', beh);

    const totalEl = document.getElementById("weight-total-display");
    const totalVal = document.getElementById("weight-total-val");
    if (totalEl && totalVal) {
        totalVal.innerText = total;
        totalEl.className = 'weight-total ' + (total === 100 ? 'ok' : 'bad');
        totalEl.innerHTML = `Total: <span id="weight-total-val">${total}</span>% — ${total === 100 ? '✓ Perfect' : 'must equal 100%'}`;
    }

    // Sync hidden decimal inputs
    const setHidden = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v/100).toFixed(2); };
    setHidden('manual-job-w-exp-val', exp);
    setHidden('manual-job-w-skills-val', sk);
    setHidden('manual-job-w-edu-val', edu);
    setHidden('manual-job-w-behavioral-val', beh);
}

function openJobModal() {
    document.getElementById("job-manual-form").reset();
    document.getElementById("job-add-modal").classList.add("active");
}

function openNewJobModal() {
    editingJobId = null;
    // Start on Upload tab, no wizard UI
    const tabBtns = document.querySelectorAll('#job-add-modal .tab-btn');
    const tabContents = document.querySelectorAll('#job-add-modal .tab-content');
    tabBtns.forEach((b, i) => b.classList.toggle('active', i === 0));
    tabContents.forEach((c, i) => c.classList.toggle('active', i === 0));

    const indicator = document.getElementById("job-step-indicator");
    const footer    = document.getElementById("job-wizard-footer");
    if (indicator) indicator.style.display = 'none';
    if (footer)    footer.style.display    = 'none';

    openJobModal();
}

async function generateJobFromAI() {
    const title    = (document.getElementById('ai-job-title')?.value || '').trim();
    const industry = (document.getElementById('ai-job-industry-bg')?.value || '').trim();
    const context  = (document.getElementById('ai-job-context')?.value || '').trim();

    if (!title)    { showToast('Please enter a Job Title', 'warning'); return; }
    if (!industry) { showToast('Please enter the Industry Background', 'warning'); return; }

    const genBtn = document.getElementById('ai-generate-btn');
    const loadEl = document.getElementById('ai-job-generating');
    const prevEl = document.getElementById('ai-job-preview');
    if (genBtn) genBtn.disabled = true;
    if (loadEl) loadEl.style.display = 'block';
    if (prevEl) prevEl.style.display = 'none';

    try {
        const token = localStorage.getItem('token') || sessionStorage.getItem('token');
        const response = await fetch('/api/ai/generate-job', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token
            },
            body: JSON.stringify({ job_title: title, industry_background: industry, additional_context: context })
        });
        if (!response.ok) throw new Error('Generation failed');
        const result = await response.json();
        window._aiGeneratedJob = result;
        window._aiJobTitle    = title;
        window._aiJobIndustry = industry;

        const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || ''; };
        set('ai-preview-brief',      result.job_brief);
        set('ai-preview-skills',     result.required_skills);
        set('ai-preview-nice',       result.nice_to_have);
        set('ai-preview-behavioral', result.behavioral_skills);

        if (loadEl) loadEl.style.display = 'none';
        if (prevEl) prevEl.style.display = 'block';
    } catch (err) {
        if (loadEl) loadEl.style.display = 'none';
        showToast('AI generation failed. Please try again.', 'error');
    } finally {
        if (genBtn) genBtn.disabled = false;
    }
}

function acceptAIJob() {
    const job = window._aiGeneratedJob;
    if (!job) return;

    switchJobTab(null, 'manual-tab');

    const fill = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
    fill('manual-job-title',    window._aiJobTitle    || '');
    fill('manual-job-industry', window._aiJobIndustry || '');
    fill('manual-job-desc',     job.job_brief);
    fill('manual-job-skills',   job.required_skills);
    fill('manual-job-nice',     job.nice_to_have);
    fill('manual-job-behavioral', job.behavioral_skills);

    jobWizardGoTo(1);
    showToast('AI filled Job Brief and Skills — complete the remaining fields and set scoring weights', 'success');
}

function regenerateAIJob() {
    const prevEl = document.getElementById('ai-job-preview');
    if (prevEl) prevEl.style.display = 'none';
    generateJobFromAI();
}

function openCandidateModal() {
    const select = document.getElementById("candidate-job-id");
    select.innerHTML = "";
    jobs.forEach(j => {
        select.innerHTML += `<option value="${j.id}">${j.job_title}</option>`;
    });
    document.getElementById("candidate-add-modal").classList.add("active");
}

async function handleJobUpload(event) {
    event.preventDefault();
    const fileInput = document.getElementById("job-file");
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const btn = event.submitter;
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Processing...";
    btn.disabled = true;

    try {
        const response = await authFetch(`${API_URL}/jobs/upload`, {
            method: "POST",
            body: formData
        });
        const result = await response.json();
        showToast(result.message, 'success');
        closeModals();
        fetchData();
    } catch (err) {
        showToast('Failed to upload job.', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function deleteCandidate(id) {
    if (!confirm("Are you sure you want to delete this candidate?")) return;
    
    try {
        const response = await authFetch(`${API_URL}/candidates/${id}`, {
            method: "DELETE"
        });
        const result = await response.json();
        fetchData(); // Refresh list
    } catch (err) {
        showToast('Failed to delete candidate.', 'error');
    }
}

async function deleteAllCandidates() {
    if (!confirm("CRITICAL: Are you sure you want to delete ALL candidates and evaluations? This cannot be undone.")) return;
    
    try {
        const response = await authFetch(`${API_URL}/candidates/bulk/all`, {
            method: "DELETE"
        });
        const result = await response.json();
        showToast(result.message, 'success');
        fetchData(); // Refresh list
    } catch (err) {
        showToast('Failed to delete all candidates.', 'error');
    }
}

async function handleCandidateUpload(event) {
    event.preventDefault();
    const jobId = document.getElementById("candidate-job-id").value;
    const fileInput = document.getElementById("candidate-file");
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    const btn = event.submitter;
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Screening...";
    btn.disabled = true;

    try {
        const response = await authFetch(`${API_URL}/candidates/upload?job_id=${jobId}`, {
            method: "POST",
            body: formData
        });
        const result = await response.json();
        showToast(result.message, 'success');
        closeModals();
        fetchData();
    } catch (err) {
        showToast('Failed to upload candidates.', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function importFromSheets() {
    const btn = document.querySelector('button[onclick="importFromSheets()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Importing...";
    btn.disabled = true;
    
    try {
        const response = await authFetch(`${API_URL}/sheets/import`, { method: "POST" });
        const result = await response.json();
        showToast(result.message || 'Import completed successfully!', 'success');
        fetchData(); // Refresh UI
    } catch (err) {
        showToast('Import failed. Ensure GOOGLE_SHEET_URL is configured properly.', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function exportToCSV() {
    const btn = document.querySelector('button[onclick="exportToCSV()"]');
    const originalText = btn.innerHTML;
    btn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Exporting...";
    btn.disabled = true;
    
    try {
        const response = await authFetch(`${API_URL}/candidates/export/csv`);
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "candidate_evaluations.csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
    } catch (err) {
        showToast('Export failed.', 'error');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

async function handleLogin(event) {
    event.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    const btn = event.submitter;

    // Hide previous error
    const loginErrDiv = document.getElementById('login-error');
    if (loginErrDiv) loginErrDiv.style.display = 'none';

    btn.disabled = true;
    btn.innerText = "Authenticating...";

    try {
        const formData = new FormData();
        formData.append("username", email);
        formData.append("password", password);

        const response = await fetch(`${API_URL}/auth/login`, {
            method: "POST",
            body: formData
        });

        if (!response.ok) {
            let errorMessage = "Invalid email or password";
            try {
                const errorData = await response.json();
                if (response.status === 403 && errorData.detail && errorData.detail.includes("pending approval")) {
                    throw new Error("⏳ Waiting for Admin's Approval\n\nYour company registration is pending administrator approval. We'll notify you as soon as you're approved!");
                }
                errorMessage = errorData.detail || "Login failed";
            } catch (e) {
                if (e.message.includes("pending approval")) throw e;
            }
            throw new Error(errorMessage);
        }

        const data = await response.json();
        
        // Remember me logic
        if (document.getElementById("login-remember").checked) {
            localStorage.setItem("rememberedEmail", email);
        } else {
            localStorage.removeItem("rememberedEmail");
        }

        localStorage.setItem("token", data.access_token);
        
        // Check if user is company or admin and route accordingly
        try {
            const userResponse = await fetch(`${API_URL}/auth/me`, {
                headers: { 'Authorization': `Bearer ${data.access_token}` }
            });
            
            if (userResponse.ok) {
                const user = await userResponse.json();
                
                if (user.is_admin) {
                    // Admin user - go to admin dashboard
                    checkAuth();
                } else if (user.company_id) {
                    // Company user - go to company dashboard
                    window.location.href = 'company-dashboard.html';
                } else {
                    // Regular user (shouldn't happen in current setup)
                    checkAuth();
                }
            } else {
                // Fallback to admin dashboard
                checkAuth();
            }
        } catch (error) {
            console.error('Error checking user type:', error);
            checkAuth();
        }
    } catch (err) {
        const errDiv = document.getElementById('login-error');
        const errText = document.getElementById('login-error-text');
        if (errDiv && errText) {
            errText.textContent = err.message;
            errDiv.style.display = 'flex';
        } else {
            showToast('Login failed: ' + err.message, 'error');
        }
    } finally {
        btn.disabled = false;
        btn.innerText = "Login to Dashboard";
    }
}

function handleLogout() {
    localStorage.removeItem("token");
    location.reload();
}

function editJob(id) {
    const job = jobs.find(j => j.id === id);
    if (!job) return;
    
    openJobModal();
    editingJobId = id; // Set AFTER opening to ensure it's not reset
    
    // Switch to manual tab for editing
    const uploadTab = document.getElementById('upload-tab');
    const manualTab = document.getElementById('manual-tab');
    const tabBtns = document.querySelectorAll('.tab-btn');
    
    tabBtns.forEach(b => b.classList.remove('active'));
    tabBtns[1].classList.add('active');
    uploadTab.classList.remove('active');
    manualTab.classList.add('active');

    // Fill form
    document.getElementById("manual-job-title").value = job.job_title;
    document.getElementById("manual-job-location").value = job.job_location || '';
    document.getElementById("manual-job-exp").value = job.min_experience;
    document.getElementById("manual-job-desc").value = job.job_description || '';
    document.getElementById("manual-job-skills").value = job.required_skills;
    document.getElementById("manual-job-nice").value = job.nice_to_have_skills || '';
    document.getElementById("manual-job-edu").value = job.education_level;
    document.getElementById("manual-job-salary").value = job.salary_range || '';
    document.getElementById("manual-job-behavioral").value = job.behavioral_skills || '';
    document.getElementById("manual-job-industry").value = job.industry_experience || '';
    // Sliders expect 0-100; weights stored as 0-1 decimals
    const toPercent = v => Math.round((parseFloat(v) || 0) * 100);
    document.getElementById("manual-job-w-exp").value        = toPercent(job.weight_experience);
    document.getElementById("manual-job-w-skills").value     = toPercent(job.weight_skills);
    document.getElementById("manual-job-w-edu").value        = toPercent(job.weight_education);
    document.getElementById("manual-job-w-behavioral").value = toPercent(job.weight_behavioral || 0.2);

    // Show wizard UI and go to step 1
    const indicator = document.getElementById("job-step-indicator");
    const footer    = document.getElementById("job-wizard-footer");
    if (indicator) indicator.style.display = '';
    if (footer)    footer.style.display    = '';
    jobWizardSetStep(1);
    updateWeights();
}

async function handleJobManualCreate(event) {
    if (event) event.preventDefault();
    console.log("Diagnostic: Starting Job Save...");

    const safeGet = (id) => {
        const el = document.getElementById(id);
        if (!el) {
            console.error(`Diagnostic: Missing element ID: ${id}`);
            return "";
        }
        return el.value;
    };

    try {
        // Weight values: prefer hidden decimal inputs (from slider), fall back to raw field
        const getWeight = (hiddenId, rawId, def) => {
            const h = document.getElementById(hiddenId);
            if (h && h.value) return parseFloat(h.value) || def;
            return parseFloat(safeGet(rawId)) || def;
        };
        const toPercent = (decimal) => Math.round(decimal * 100);
        const payload = {
            title: safeGet("manual-job-title"),
            location: safeGet("manual-job-location"),
            description: safeGet("manual-job-desc"),
            experience_years: parseInt(safeGet("manual-job-exp")) || 0,
            required_skills: safeGet("manual-job-skills"),
            nice_to_have_skills: safeGet("manual-job-nice"),
            education_level: safeGet("manual-job-edu"),
            salary_range: safeGet("manual-job-salary"),
            behavioral_skills: safeGet("manual-job-behavioral"),
            industry_experience: safeGet("manual-job-industry"),
            ai_weights: {
                experience: toPercent(getWeight("manual-job-w-exp-val",          "manual-job-w-exp",          0.3)),
                skills:     toPercent(getWeight("manual-job-w-skills-val",        "manual-job-w-skills",       0.4)),
                education:  toPercent(getWeight("manual-job-w-edu-val",           "manual-job-w-edu",          0.2)),
                behavioral: toPercent(getWeight("manual-job-w-behavioral-val",    "manual-job-w-behavioral",   0.1))
            }
        };

        if (!payload.title) {
            showToast('Error: Job Title is required!', 'error');
            return;
        }

        const url = editingJobId ? `/jobs/${editingJobId}` : `/jobs`;
        const method = editingJobId ? "PUT" : "POST";

        const submitBtn = document.querySelector("#job-manual-form button[type='submit']");
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.innerHTML = "<i class='bx bx-loader-alt bx-spin'></i> Saving...";
        }

        console.log("Diagnostic: Sending Payload", method, url, payload);

        const response = await authFetch(url, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            showToast(editingJobId ? 'Job Updated Successfully!' : 'Job Created Successfully!', 'success');
            location.reload();
        } else {
            const errorData = await response.json().catch(() => ({}));
            showToast(`Server Rejected Save: ${JSON.stringify(errorData.detail || "Check all fields")}`, 'error');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = "<i class='bx bx-save'></i> Retry Save";
            }
        }
    } catch (err) {
        console.error("Diagnostic: Crash in handleJobManualCreate", err);
        showToast(`Script Crash: ${err.message}`, 'error');
    }
}

function exportScreeningCard(id) {
    const candidate = candidates.find(c => c.id === id);
    const ev = evaluations.find(e => e.candidate_id === id);
    const job = jobs.find(j => j.id === candidate.job_applied);

    if (!ev) {
        showToast('No evaluation found for this candidate.', 'error');
        return;
    }

    const printPct = evalScorePercent(ev.score);

    const printWindow = window.open('', '_blank');
    const html = `
        <html>
        <head>
            <title>Screening Card - ${candidate.name}</title>
            <style>
                body { font-family: 'Segoe UI', Arial, sans-serif; padding: 40px; color: #1e293b; background: white; }
                .card { max-width: 800px; margin: auto; border: 2px solid #1B2A4A; }
                .header { background: #1B2A4A; color: white; padding: 20px; text-align: center; }
                .header h1 { margin: 0; font-size: 24px; text-transform: uppercase; letter-spacing: 2px; }
                .section-title { background: #1B2A4A; color: white; padding: 8px 15px; font-weight: 500; display: flex; align-items: center; gap: 10px; margin-top: 20px; }
                .grid { display: grid; grid-template-columns: 200px 1fr; border-bottom: 1px solid #e2e8f0; }
                .grid div { padding: 10px 15px; border-right: 1px solid #e2e8f0; }
                .grid div:last-child { border-right: none; }
                .label { background: #f8fafc; font-weight: 500; color: #1B2A4A; }
                .score-summary { background: #c5923b; color: white; padding: 10px; text-align: center; font-weight: 500; margin-top: 20px; }
                .decision-box { display: grid; grid-template-columns: 1fr 1fr; border: 2px solid #1B2A4A; margin-top: 10px; }
                .decision-box div { padding: 20px; text-align: center; font-weight: 500; font-size: 20px; }
                .decision-box .label { background: white; color: #1B2A4A; border-right: 2px solid #1B2A4A; }
                .decision-box .value { background: #f0fff4; color: #10b981; }
                .rejection-reason { background: #df2029; color: white; padding: 10px; font-weight: 500; margin-top: 20px; text-align: center; }
                .reason-list { padding: 15px; background: #fff5f5; border: 1px solid #feb2b2; }
                .notes-section { border: 1px solid #e2e8f0; padding: 20px; min-height: 100px; margin-top: 20px; }
                @media print { .no-print { display: none; } }
            </style>
        </head>
        <body>
            <div class="no-print" style="margin-bottom: 20px; text-align: center;">
                <button onclick="window.print()" style="padding: 10px 20px; background: #1B2A4A; color: white; border: none; border-radius: 8px; cursor: pointer;">Download / Print PDF</button>
            </div>
            <div class="card">
                <div class="header">
                    <h1>🎯 HUNTERS — CANDIDATE: "${candidate.name}" LIVE SCREENING CARD</h1>
                </div>
                
                <div class="section-title">📋 CANDIDATE INFORMATION</div>
                <div class="grid"><div class="label">Candidate Name</div><div>${candidate.name}</div></div>
                <div class="grid"><div class="label">Role Applied For</div><div>${job.job_title}</div></div>
                <div class="grid"><div class="label">Phone Number</div><div>${candidate.phone}</div></div>
                <div class="grid"><div class="label">Screening Date</div><div>${new Date().toLocaleDateString()}</div></div>
                <div class="grid"><div class="label">Years of Experience</div><div>${candidate.experience_years}</div></div>

                <div class="section-title">⚖️ COMPETENCY SCORING</div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Metric</div><div class="label">Notes / Details</div><div class="label">Score</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Experience Weight: ${job.weight_experience}</div><div>Verified against JD requirements</div><div>-</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Skills Weight: ${job.weight_skills}</div><div>AI analysis of core technologies</div><div>-</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Education Weight: ${job.weight_education}</div><div>Academic background alignment</div><div>-</div>
                </div>

                <div class="score-summary">🔢 SCORE SUMMARY</div>
                <div class="grid"><div class="label">Weighted AI Score</div><div style="font-size: 24px; font-weight: 500; color: #1B2A4A;">${printPct != null ? printPct + '%' : '—'}</div></div>

                <div class="section-title">⚙️ AUTO DECISION ENGINE</div>
                <div class="decision-box">
                    <div class="label">SCREENING DECISION</div>
                    <div class="value" style="color: ${ev.decision.toLowerCase() === 'reject' ? '#df2029' : '#10b981'}">${ev.decision.toUpperCase()}</div>
                </div>

                <div class="rejection-reason">🚩 ANALYSIS & REASONING</div>
                <div class="reason-list">
                    ${ev.reason.split('\n').map(r => `<p>🚩 ${r}</p>`).join('')}
                </div>

                <div class="section-title">📝 SCREENER NOTES & RECOMMENDATION</div>
                <div class="notes-section">
                    <strong>Strengths:</strong><br>${ev.strengths}<br><br>
                    <strong>Weaknesses:</strong><br>${ev.weaknesses}
                </div>
            </div>
        </body>
        </html>
    `;
    printWindow.document.write(html);
    printWindow.document.close();
}

async function fetchUserInfo() {
    try {
        const res = await authFetch(`${API_URL}/auth/me`);
        if (!res.ok) return;
        const user = await res.json();
        const displayEl = document.getElementById("user-display-name");
        if (displayEl) {
            displayEl.innerText = user.full_name || user.email;
        }
    } catch (err) {
        console.error("Failed to fetch user info", err);
    }
}

let isEditMode = false;
function toggleEditEvaluation() {
    isEditMode = !isEditMode;
    const scoreEl = document.getElementById("modal-candidate-score");
    const decisionEl = document.getElementById("modal-candidate-decision");
    const reasonEl = document.getElementById("modal-candidate-reason");
    const strengthsEl = document.getElementById("modal-candidate-strengths");
    const weaknessesEl = document.getElementById("modal-candidate-weaknesses");
    
    const editBtn = document.getElementById("edit-eval-btn");
    const saveBtn = document.getElementById("save-eval-btn");

    if (isEditMode) {
        const rawVal = scoreEl.innerText.trim().replace(/%/g, "");
        scoreEl.innerHTML = `<input type="number" min="0" max="100" step="1" id="edit-score" value="${rawVal}" style="width: 72px; font-size: 20px; text-align: center; border-radius: 50%;">`;
        decisionEl.innerHTML = `
            <select id="edit-decision" style="padding: 4px; border-radius: 8px;">
                <option value="Shortlist" ${decisionEl.innerText === 'Shortlist' ? 'selected' : ''}>Shortlist</option>
                <option value="Maybe" ${decisionEl.innerText === 'Maybe' ? 'selected' : ''}>Maybe</option>
                <option value="Reject" ${decisionEl.innerText === 'Reject' ? 'selected' : ''}>Reject</option>
            </select>
        `;
        reasonEl.innerHTML = `<textarea id="edit-reason" style="width: 100%; height: 100px; padding: 10px; border-radius: 10px; border: 1px solid var(--border);">${reasonEl.innerText}</textarea>`;
        strengthsEl.innerHTML = `<textarea id="edit-strengths" style="width: 100%; height: 80px; padding: 10px; border-radius: 10px; border: 1px solid var(--border);">${strengthsEl.innerText}</textarea>`;
        weaknessesEl.innerHTML = `<textarea id="edit-weaknesses" style="width: 100%; height: 80px; padding: 10px; border-radius: 10px; border: 1px solid var(--border);">${weaknessesEl.innerText}</textarea>`;
        
        editBtn.innerHTML = "<i class='bx bx-x'></i> Cancel";
        saveBtn.style.display = "block";
    } else {
        location.reload(); // Simplest way to reset the UI state
    }
}

async function saveManualEvaluation() {
    if (!currentEvaluationId) return;
    
    const payload = {
        score: parseFloat(document.getElementById("edit-score").value),
        decision: document.getElementById("edit-decision").value,
        reason: document.getElementById("edit-reason").value,
        strengths: document.getElementById("edit-strengths").value,
        weaknesses: document.getElementById("edit-weaknesses").value,
        suggested_interview_questions: evaluations.find(e => e.id === currentEvaluationId).suggested_interview_questions
    };

    try {
        const response = await authFetch(`${API_URL}/evaluations/update/${currentEvaluationId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            showToast('Report updated successfully!', 'success');
            location.reload();
        } else {
            showToast('Failed to update report.', 'error');
        }
    } catch (err) {
        showToast('Error saving changes.', 'error');
    }
}

async function deleteJob(id) {
    if (!confirm("Are you sure you want to delete this job? This will also delete all associated candidates and evaluations.")) return;

    try {
        const response = await authFetch(`/jobs/${id}`, {
            method: "DELETE"
        });

        if (response.ok) {
            showToast('Job deleted successfully!', 'success');
            location.reload();
        } else {
            showToast('Failed to delete job.', 'error');
        }
    } catch (err) {
        console.error("Delete job error:", err);
        showToast('Error deleting job.', 'error');
    }
}
