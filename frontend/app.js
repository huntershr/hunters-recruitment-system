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

function escHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function parseListField(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw.map(String).filter(s => s.trim());

    // Try standard JSON parse (handles ["a","b"] arrays)
    try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.map(String).filter(s => s.trim());
    } catch (e) {}

    // Try Python list literal: replace single quotes with double quotes
    try {
        const jsonified = raw.trim()
            .replace(/'/g, '"');
        const parsed = JSON.parse(jsonified);
        if (Array.isArray(parsed)) return parsed.map(String).filter(s => s.trim());
    } catch (e) {}

    // Fallback: split by newline or bullet characters
    return raw
        .replace(/^\[|\]$/g, '')
        .split(/[\n•]+/)
        .map(s => s.trim().replace(/^[-•*]\s*/, '').replace(/^['"]|['"]$/g, ''))
        .filter(s => s.length > 0);
}

let jobs = [];
let candidates = [];
let evaluations = [];
let applications = [];   // Phase 3: flat application list from /api/admin/applications
let currentEvaluationId = null;
let currentCandidateId = null;
let currentApplicationId = null;  // Phase 3: tracks selected application
let editingJobId = null;
let currentUser = null;
let companyFilter = '';

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
    // Reset to safe defaults so stale data never persists across calls
    jobs = [];
    candidates = [];
    evaluations = [];

    // Three parallel calls — allSettled means one failure never aborts the others
    const [jobsRes, candsRes, evalsRes] = await Promise.allSettled([
        authFetch(`${API_URL}/jobs`),
        authFetch(`${API_URL}/candidates`),
        authFetch(`${API_URL}/results`)
    ]);

    try { jobs        = await jobsRes.value?.json()  ?? []; } catch (_) {}
    try { candidates  = await candsRes.value?.json() ?? []; } catch (_) {}
    try { evaluations = await evalsRes.value?.json() ?? []; } catch (_) {}

    if (!Array.isArray(jobs))        jobs = [];
    if (!Array.isArray(candidates))  candidates = [];
    if (!Array.isArray(evaluations)) evaluations = [];

    // Applications call — individually wrapped, failure leaves applications = []
    console.log('[fetchData] Fetching /api/admin/applications…');
    try {
        const appRes = await authFetch('/api/admin/applications');
        console.log('[fetchData] /api/admin/applications status:', appRes.status);
        if (appRes.ok) {
            const appData = await appRes.json();
            applications = Array.isArray(appData.applications) ? appData.applications : [];
            console.log('[fetchData] applications loaded:', applications.length, 'items');
        } else {
            console.warn('[fetchData] /api/admin/applications returned non-OK status:', appRes.status);
        }
    } catch (e) {
        console.error('[fetchData] /api/admin/applications fetch failed:', e);
    }

    // Render always runs — empty arrays are valid, a crash above is not fatal
    try { updateDashboard(); }    catch (e) { console.error('[fetchData] updateDashboard crash:', e); }
    try { renderJobs(); }         catch (e) { console.error('[fetchData] renderJobs crash:', e); }
    try { renderCandidates(); }   catch (e) { console.error('[fetchData] renderCandidates crash:', e); }
    try { buildCompanyFilter(); } catch (_) {}
}

/** Stored score normalization: fractions (≤1), legacy 1–10 scale, or unified 0–100. */
function evalScorePercent(raw) {
    if (raw === null || raw === undefined || raw === "") return null;
    const n = Number(raw);
    if (Number.isNaN(n) || n === 0) return null;  // 0 = failed evaluation
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
        return '<span class="stage-pill stage-new">Applied</span>';
    }
    const d = decision.toLowerCase();
    if (d === 'new' || d === 'applied') return '<span class="stage-pill stage-new">Applied</span>';
    if (d === 'shortlist' || d === 'shortlisted') return '<span class="stage-pill stage-interview">Shortlisted</span>';
    if (d === 'maybe' || d === 'screening') return '<span class="stage-pill stage-screening">Screening</span>';
    if (d === 'interview') return '<span class="stage-pill stage-interview">Interview</span>';
    if (d === 'offer' || d === 'offered') return '<span class="stage-pill stage-screening">Offered</span>';
    if (d === 'hired')  return '<span class="stage-pill stage-interview">Hired</span>';
    if (d === 'reject' || d === 'rejected') return '<span class="stage-pill stage-rejected">Rejected</span>';
    return `<span class="stage-pill stage-new">${decision}</span>`;
}

function updateDashboard() {
    // total-jobs, total-candidates, jobs-trend, candidates-trend are owned by
    // loadAdminStats() which reads from /api/admin/stats — do not overwrite here.

    const shortlisted = evaluations.filter(e => (e.decision || '').toLowerCase() === "shortlist").length;
    document.getElementById("total-accepted").innerText = shortlisted;

    const pendingEl = document.getElementById("total-pending");
    if (pendingEl) {
        const pending = candidates.filter(c => !evaluations.find(e => e.candidate_id === c.id)).length;
        pendingEl.innerText = pending;
    }

    // Trend texts
    const shortTrend = document.getElementById("shortlisted-trend");
    if (shortTrend) shortTrend.innerText = shortlisted > 0 ? `${shortlisted} shortlisted` : "None yet";

    const tbody = document.querySelector("#recent-candidates-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    // Use applications array (primary data source) — fall back to legacy candidates
    const src = (applications && applications.length) ? applications : candidates;
    const recent = src.slice(0, 10);

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

    recent.forEach(row => {
        // Support both applications objects and legacy candidate objects
        const name     = row.name || row.applicant_name || '—';
        const email    = row.email || row.applicant_email || '';
        const jobTitle = row.job_title || (jobs.find(j => j.id === (row.job_applied || row.job_id)) || {}).job_title || '—';
        const stage    = row.stage || row.decision || null;
        const score    = row.score != null ? row.score : null;
        const decision = row.decision || null;
        const viewId   = row.application_id || row.id;
        const viewFn   = row.application_id ? `viewApplication(${row.application_id})` : `viewCandidate(${row.id})`;

        const scoreColor = score == null ? '#6B7280' : score >= 75 ? '#0F6E56' : score >= 50 ? '#854F0B' : '#A32D2D';
        const scoreBg    = score == null ? '#F0F2F8'  : score >= 75 ? '#E1F5EE'  : score >= 50 ? '#FAEEDA'  : '#FCEBEB';
        const scoreText  = score != null ? Math.round(score) + '%' : '—';

        tbody.innerHTML += `
            <tr>
                <td><strong>${escHtml(name)}</strong><br><small style="color:#9CA3AF">${escHtml(email)}</small></td>
                <td>${escHtml(jobTitle)}</td>
                <td>${stagePillHtml(stage)}</td>
                <td><span style="display:inline-flex;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;background:${scoreBg};color:${scoreColor};">${scoreText}</span></td>
                <td><span class="badge ${decision ? decision.toLowerCase() : 'pending'}">${escHtml(decision || 'Pending')}</span></td>
                <td><button class="btn-action" onclick="${viewFn}">View</button></td>
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
            const _isAdmin = currentUser && currentUser.is_admin;
            const jobLogoHtml36 = _isAdmin
                ? `<img src="/hunters-logo-card.jpeg" alt="Hunters" style="width:36px;height:36px;border-radius:50%;object-fit:contain;flex-shrink:0;background:#fff;border:0.5px solid rgba(0,0,0,0.08);">`
                : `<div style="width:36px;height:36px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0;">${initials}</div>`;
            const jobLogoHtml28 = _isAdmin
                ? `<img src="/hunters-logo-card.jpeg" alt="Hunters" style="width:28px;height:28px;border-radius:50%;object-fit:contain;flex-shrink:0;background:#fff;border:0.5px solid rgba(0,0,0,0.08);">`
                : `<div style="width:28px;height:28px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;flex-shrink:0;">${initials}</div>`;
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
                                ${jobLogoHtml36}
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
                        ${jobShareSectionHtml(j.id)}
                        <div style="margin-top:12px;display:flex;justify-content:flex-end;">
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
                                ${jobLogoHtml28}
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

function getJobShareUrl(jobId) {
    return window.location.origin + '/apply.html?job_id=' + jobId;
}

function getJobCaption(jobTitle, jobLocation, jobExp, jobId) {
    const url = getJobShareUrl(jobId);
    return '🚀 We\'re hiring!\n\nPosition: ' + jobTitle +
        '\nLocation: ' + (jobLocation || 'Egypt') +
        '\nExperience: ' + (jobExp || '0') + '+ years\n\n' +
        'Apply now:\n' + url +
        '\n\n#Hiring #Jobs #Careers #HuntersAI #HuntersHR';
}

function shareJob(platform, jobId) {
    const job = jobs.find(j => j.id === jobId) || {};
    const title = job.job_title || 'Job Opportunity';
    const loc = job.job_location || 'Egypt';
    const exp = job.min_experience || 0;
    const shareUrl = getJobShareUrl(jobId);
    const url = encodeURIComponent(shareUrl);
    const text = encodeURIComponent(getJobCaption(title, loc, exp, jobId));
    const map = {
        linkedin: 'https://www.linkedin.com/sharing/share-offsite/?url=' + url,
        facebook: 'https://www.facebook.com/sharer/sharer.php?u=' + url,
        whatsapp: 'https://wa.me/?text=' + text
    };
    if (map[platform]) window.open(map[platform], '_blank', 'noopener,noreferrer');
}

function copyJobLink(jobId, btnEl) {
    navigator.clipboard.writeText(getJobShareUrl(jobId)).then(() => {
        const orig = btnEl.innerHTML;
        btnEl.innerHTML = '✓ Copied';
        btnEl.classList.add('copied');
        setTimeout(() => { btnEl.innerHTML = orig; btnEl.classList.remove('copied'); }, 2000);
        showToast('Job link copied!');
    });
}

function jobShareSectionHtml(jobId) {
    return `<div class="job-share-section">
        <span class="share-label">Share:</span>
        <div class="share-buttons">
            <button onclick="event.stopPropagation();shareJob('linkedin',${jobId})" class="share-btn share-linkedin">LinkedIn</button>
            <button onclick="event.stopPropagation();shareJob('facebook',${jobId})" class="share-btn share-facebook">Facebook</button>
            <button onclick="event.stopPropagation();shareJob('whatsapp',${jobId})" class="share-btn share-whatsapp">WhatsApp</button>
            <button onclick="event.stopPropagation();copyJobLink(${jobId},this)" class="share-btn share-copy">Copy Link</button>
        </div>
    </div>`;
}

let pipelineView = 'tabs';
let pipelineFilter = '';
let activeStageTab = 'applied';

const _STAGE_TABS = [
    { id: 'applied',     label: 'Applied',     accent: '#378ADD', stages: ['applied','new','',null] },
    { id: 'screening',   label: 'Screening',   accent: '#EF9F27', stages: ['screening'] },
    { id: 'shortlisted', label: 'Shortlisted', accent: '#6366F1', stages: ['shortlisted'] },
    { id: 'interview',   label: 'Interview',   accent: '#1D9E75', stages: ['interview'] },
    { id: 'offered',     label: 'Offered',     accent: '#C9A84C', stages: ['offer','offered'] },
    { id: 'hired',       label: 'Hired',       accent: '#0F6E56', stages: ['hired'] },
    { id: 'rejected',    label: 'Rejected',    accent: '#CC2B2B', stages: ['rejected'] },
];
const _STAGE_BADGE_MAP = {
    applied:['#1B2A4A','#EFF2F8'], new:['#1B2A4A','#EFF2F8'],
    screening:['#854F0B','#FAEEDA'], shortlisted:['#0F6E56','#E1F5EE'],
    interview:['#185FA5','#E6F1FB'], offer:['#0F6E56','#E1F5EE'],
    offered:['#0F6E56','#E1F5EE'], hired:['#0F6E56','#E1F5EE'],
    rejected:['#A32D2D','#FCEBEB'],
};

function renderCandidates() {
    // Status bar updated here — guaranteed to run regardless of renderKanban outcome
    const statusEl = document.getElementById("pipeline-status-bar");
    if (statusEl) {
        if (applications.length > 0) {
            statusEl.textContent = `${applications.length} application${applications.length !== 1 ? 's' : ''} loaded`;
            statusEl.style.color = '#0F6E56';
        } else if (candidates.length > 0) {
            statusEl.textContent = `${candidates.length} candidates (fallback mode — applications API unavailable)`;
            statusEl.style.color = '#854F0B';
        } else {
            statusEl.textContent = 'No data loaded — check browser console for errors';
            statusEl.style.color = '#A32D2D';
        }
    }
    try { renderStageTabs(pipelineFilter); } catch (e) { console.error('[renderCandidates] renderStageTabs crash:', e); }
    try { renderCandidateList(pipelineFilter); } catch (e) { console.error('[renderCandidates] renderCandidateList crash:', e); }
}

function renderStageTabs(filter) {
    if (pipelineView !== 'tabs') return;
    const tabsBar = document.getElementById('stage-tabs-bar');
    const cardsList = document.getElementById('stage-candidates-list');
    if (!tabsBar || !cardsList) return;

    const lf = (filter || '').toLowerCase();
    const cf = (companyFilter || '').toLowerCase();
    const useApps = applications.length > 0;

    const counts = {};
    _STAGE_TABS.forEach(tab => {
        counts[tab.id] = useApps ? applications.filter(app => {
            const stg = (app.stage || 'applied').toLowerCase();
            return tab.stages.includes(stg);
        }).length : 0;
    });

    tabsBar.innerHTML = _STAGE_TABS.map(tab => {
        const isActive = tab.id === activeStageTab;
        return `<button onclick="switchStageTab('${tab.id}')" style="flex-shrink:0;display:flex;align-items:center;gap:6px;padding:8px 14px;border:none;border-radius:8px;font-size:12px;font-weight:${isActive?'600':'500'};cursor:pointer;background:${isActive?'#0D1B3E':'transparent'};color:${isActive?'#fff':'#6B7280'};border-bottom:${isActive?'2px solid #C9A84C':'2px solid transparent'};min-height:44px;white-space:nowrap;">${escHtml(tab.label)}<span style="display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;padding:0 4px;border-radius:10px;background:${isActive?'rgba(201,168,76,0.25)':'#F3F4F6'};color:${isActive?'#C9A84C':'#6B7280'};font-size:10px;font-weight:600;">${counts[tab.id]}</span></button>`;
    }).join('');

    const activeTabDef = _STAGE_TABS.find(t => t.id === activeStageTab) || _STAGE_TABS[0];
    let tabApps = [];
    if (useApps) {
        tabApps = applications.filter(app => {
            const stg = (app.stage || 'applied').toLowerCase();
            if (!activeTabDef.stages.includes(stg)) return false;
            if (lf) return (app.name||'').toLowerCase().includes(lf)||(app.email||'').toLowerCase().includes(lf)||(app.job_title||'').toLowerCase().includes(lf);
            if (cf) return (app.company_name||'').toLowerCase()===cf;
            return true;
        });
    }

    if (tabApps.length === 0) {
        cardsList.innerHTML = `<div style="text-align:center;padding:60px 20px;background:#fff;border-radius:12px;border:1px solid #E5E7EB;"><div style="font-size:14px;font-weight:500;color:#6B7280;margin-bottom:8px;">No candidates in ${escHtml(activeTabDef.label)} yet</div><div style="font-size:12px;color:#9CA3AF;">Candidates will appear here when moved to this stage</div></div>`;
        return;
    }
    cardsList.innerHTML = '<div style="display:flex;flex-direction:column;gap:12px;">' + tabApps.map(_renderStageCard).join('') + '</div>';
}

function _renderStageCard(app) {
    const pct = evalScorePercent(app.score);
    const scoreColor = pct===null?'#9CA3AF':pct>=80?'#0F6E56':pct>=60?'#185FA5':'#854F0B';
    const scoreBg   = pct===null?'#F3F4F6':pct>=80?'#E1F5EE':pct>=60?'#E6F1FB':'#FAEEDA';
    const scoreText = pct===null?'Pending':pct+'%';
    const initials  = (app.name||'?').split(' ').slice(0,2).map(w=>w[0]).join('').toUpperCase();
    const isReg = app.candidate_type==='registered';
    const typePill = isReg
        ? `<span style="display:inline-flex;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:500;background:#F3F4F6;color:#6B7280;">Registered</span>`
        : `<span style="display:inline-flex;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:500;background:#FEF9EC;color:#854F0B;border:0.5px solid #F6D97A;">External</span>`;
    const stg = (app.stage||'applied').toLowerCase();
    const [stgColor,stgBg] = _STAGE_BADGE_MAP[stg]||['#6B7280','#F3F4F6'];
    const stgLabel = stg.charAt(0).toUpperCase()+stg.slice(1);
    const expLine = [app.last_title, app.experience_years!=null?app.experience_years+' yrs':null].filter(Boolean).join(' · ');
    const safeName = (app.name||'').replace(/[^a-zA-Z0-9_-]/g,'_');
    const stageOpts = ['applied','screening','shortlisted','interview','offered','hired','rejected']
        .filter(s=>s!==stg&&!(stg==='new'&&s==='applied'))
        .map(s=>`<option value="${s}">${s.charAt(0).toUpperCase()+s.slice(1)}</option>`).join('');
    return `<div id="stage-card-${app.application_id}" style="background:#fff;border:1px solid #E5E7EB;border-radius:12px;padding:16px;transition:box-shadow 0.2s;" onmouseover="this.style.boxShadow='0 4px 16px rgba(0,0,0,0.08)'" onmouseout="this.style.boxShadow='none'">
        <div style="display:flex;align-items:flex-start;gap:12px;">
            <div style="width:44px;height:44px;min-width:44px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;">${escHtml(initials)}</div>
            <div style="flex:1;min-width:0;">
                <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;flex-wrap:wrap;">
                    <div style="font-size:15px;font-weight:600;color:#1B2A4A;cursor:pointer;" onclick="viewApplication(${app.application_id})">${escHtml(app.name)}</div>
                    <span style="display:inline-flex;align-items:center;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:600;background:${scoreBg};color:${scoreColor};flex-shrink:0;">${scoreText}</span>
                </div>
                <div style="font-size:13px;color:#6B7280;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(app.job_title||'—')}${app.company_name?' · '+escHtml(app.company_name):''}</div>
                ${expLine?`<div style="font-size:12px;color:#9CA3AF;margin-top:2px;">${escHtml(expLine)}</div>`:''}
                <div style="display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap;">
                    ${typePill}
                    <span style="display:inline-flex;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:500;background:${stgBg};color:${stgColor};">${stgLabel}</span>
                </div>
            </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:14px;flex-wrap:wrap;gap:8px;">
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                ${app.evaluation_id?`<button onclick="viewApplication(${app.application_id})" style="padding:7px 12px;border:1px solid #E5E7EB;border-radius:8px;background:#fff;font-size:11px;font-weight:500;color:#1B2A4A;cursor:pointer;min-height:44px;">Report</button>`:''}
                ${app.cv_available?`<button onclick="downloadAppCV(${app.application_id},'${safeName}')" style="padding:7px 12px;border:1px solid #E5E7EB;border-radius:8px;background:#fff;font-size:11px;font-weight:500;color:#0F6E56;cursor:pointer;min-height:44px;">CV</button>`:''}
                ${isReg&&app.candidate_id?`<button onclick="viewAtsProfile(${app.application_id})" style="padding:7px 12px;border:1px solid #E5E7EB;border-radius:8px;background:#fff;font-size:11px;font-weight:500;color:#185FA5;cursor:pointer;min-height:44px;">Profile</button>`:''}
                ${stg==='interview'?`<button onclick="openScheduleInterviewModal(${app.application_id},'${(app.name||'').replace(/'/g,"\\'")}',null)" style="padding:7px 12px;border:none;border-radius:8px;background:#1D9E75;color:#fff;font-size:11px;font-weight:600;cursor:pointer;min-height:44px;">📅 Schedule</button>`:''}
                ${app.email?`<button onclick="sendCandidateEmail('${(app.email||'').replace(/'/g,"\\'")}','${(app.name||'').replace(/'/g,"\\'")}','${(app.job_title||'').replace(/'/g,"\\'")}','${(app.company_name||'Hunters HR').replace(/'/g,"\\'")}')" style="padding:7px 12px;border:1px solid #1B2A4A;border-radius:8px;background:#fff;font-size:11px;font-weight:500;color:#1B2A4A;cursor:pointer;min-height:44px;" title="Send Email">✉</button>`:''}
                ${app.phone?`<button onclick="sendCandidateWhatsApp('${(app.phone||'').replace(/'/g,"\\'")}','${(app.name||'').replace(/'/g,"\\'")}','${(app.job_title||'').replace(/'/g,"\\'")}')" style="padding:7px 12px;border:1px solid #25D366;border-radius:8px;background:#fff;font-size:11px;font-weight:500;color:#25D366;cursor:pointer;min-height:44px;" title="WhatsApp">WhatsApp</button>`:''}
            </div>
            <div onclick="event.stopPropagation()" style="min-width:160px;">
                <select onchange="changeAppStage(${app.application_id},this.value,this)" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:11px;color:#6B7280;background:#F9FAFB;cursor:pointer;min-height:44px;">
                    <option value="" disabled selected>Move to stage…</option>
                    ${stageOpts}
                </select>
            </div>
        </div>
    </div>`;
}

function switchStageTab(tabId) {
    activeStageTab = tabId;
    renderStageTabs(pipelineFilter);
}

function _decisionToStage(decision) {
    if (!decision || decision.toLowerCase() === 'pending') return 'Applied';
    const d = decision.toLowerCase();
    if (d === 'new')        return 'Applied';
    if (d === 'applied')    return 'Applied';
    if (d === 'maybe')      return 'Screening';
    if (d === 'screening')  return 'Screening';
    if (d === 'shortlist' || d === 'shortlisted') return 'Shortlisted';
    if (d === 'interview')  return 'Interview';
    if (d === 'offer' || d === 'offered') return 'Offered';
    if (d === 'hired')      return 'Hired';
    if (d === 'reject' || d === 'rejected') return 'Rejected';
    return decision;
}

function _stageColor(stage) {
    const map = {
        Applied: '#378ADD', New: '#378ADD',
        Screening: '#EF9F27',
        Shortlisted: '#6366F1',
        Interview: '#1D9E75',
        Offered: '#C9A84C', Offer: '#C9A84C',
        Hired: '#0F6E56',
        Rejected: '#CC2B2B',
    };
    return map[stage] || '#9CA3AF';
}

function renderCandidateList(filter) {
    const tbody = document.querySelector("#all-candidates-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    const lf = (filter || '').toLowerCase();
    const cf = (companyFilter || '').toLowerCase();

    // Phase 3: use applications array if populated
    const useApps = applications.length > 0;

    if (useApps) {
        const filtered = applications.filter(app => {
            const matchSearch = !lf ||
                (app.name || '').toLowerCase().includes(lf) ||
                (app.email || '').toLowerCase().includes(lf) ||
                (app.job_title || '').toLowerCase().includes(lf);
            const matchCompany = !cf || (app.company_name || '').toLowerCase() === cf;
            return matchSearch && matchCompany;
        });

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8">
                <div class="empty-state">
                    <div class="empty-title">No applications found</div>
                    <div class="empty-sub">${lf ? 'No results for "' + escHtml(lf) + '"' : 'No applications yet'}</div>
                </div>
            </td></tr>`;
            return;
        }

        filtered.forEach(app => {
            const pct = evalScorePercent(app.score);
            const sc = pct === null ? { bg: '#F5F6F8', text: '#6B7280' }
                     : pct >= 80   ? { bg: '#E1F5EE', text: '#0F6E56' }
                     : pct >= 60   ? { bg: '#E6F1FB', text: '#1A6FC4' }
                     : pct >= 40   ? { bg: '#FAEEDA', text: '#854F0B' }
                     :               { bg: '#FCEBEB', text: '#A32D2D' };
            const stage = _decisionToStage(app.decision);
            const stageCol = _stageColor(stage);
            const safeName = (app.name || 'Candidate').replace(/[^a-zA-Z0-9_-]/g, '_');
            const initials = (app.name || '?').split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase();
            const typeBadge = app.candidate_type === 'registered'
                ? `<span style="background:#F0F2F8;color:#6B7280;font-size:9px;padding:1px 6px;border-radius:8px;margin-left:4px;">Reg</span>`
                : `<span style="background:#FFF7E0;color:#9B6F00;font-size:9px;padding:1px 6px;border-radius:8px;margin-left:4px;">Ext</span>`;

            tbody.innerHTML += `
                <tr onmouseover="this.style.background='#F8F9FF'" onmouseout="this.style.background='transparent'">
                    <td style="min-width:160px;">
                        <div style="display:flex;align-items:center;gap:8px;">
                            <div style="width:28px;height:28px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;flex-shrink:0;">${escHtml(initials)}</div>
                            <div>
                                <strong style="font-size:12px;">${escHtml(app.name)}</strong>${typeBadge}
                                <div style="font-size:10px;color:#9CA3AF;">${escHtml(app.email || '')}</div>
                            </div>
                        </div>
                    </td>
                    <td style="font-size:11px;color:#6B7280;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                        <div>${escHtml(app.job_title || '—')}</div>
                        <div style="font-size:10px;color:#9CA3AF;">${escHtml(app.company_name || '')}</div>
                    </td>
                    <td>
                        ${pct !== null
                            ? `<span style="display:inline-flex;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;background:${sc.bg};color:${sc.text};">${pct}%</span>`
                            : '<span style="color:#9CA3AF;font-size:11px;">Pending</span>'}
                    </td>
                    <td style="font-size:11px;color:#6B7280;">${app.candidate_type === 'registered' ? 'Registered' : 'External'}</td>
                    <td>
                        <span style="display:inline-flex;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:500;background:${stageCol}22;color:${stageCol};">${stage}</span>
                    </td>
                    <td style="font-size:11px;color:#6B7280;">
                        ${app.experience_years != null ? app.experience_years + ' yrs' : '—'}
                        ${app.last_title ? `<div style="font-size:10px;color:#9CA3AF;">${escHtml(app.last_title)}</div>` : ''}
                    </td>
                    <td style="font-size:11px;">
                        ${app.cv_available
                            ? `<a href="#" onclick="downloadAppCV(${app.application_id},'${safeName}');return false;" style="color:#0F6E56;font-weight:500;font-size:11px;text-decoration:none;cursor:pointer;">↓ CV</a>`
                            : '<span style="color:#9CA3AF;">—</span>'}
                    </td>
                    <td>
                        <div style="display:flex;gap:5px;">
                            <button class="btn-action" style="font-size:10px;padding:4px 8px;" onclick="viewApplication(${app.application_id})">View Report</button>
                            ${app.candidate_type === 'registered' && app.candidate_id
                                ? `<button class="btn-action" style="font-size:10px;padding:4px 8px;color:#1A6FC4;border-color:#1A6FC4;" onclick="viewBasicProfile(${app.application_id})">Profile</button>`
                                : ''}
                        </div>
                    </td>
                </tr>
            `;
        });
    } else {
        // Fallback: old candidates+evaluations join
        const filtered = candidates.filter(c => {
            const job = jobs.find(j => j.id === c.job_applied);
            const matchSearch = !lf ||
                c.name.toLowerCase().includes(lf) ||
                (c.email || '').toLowerCase().includes(lf) ||
                (job ? job.job_title.toLowerCase().includes(lf) : false);
            const matchCompany = !companyFilter || (c.company_name || '') === companyFilter;
            return matchSearch && matchCompany;
        });

        if (filtered.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8">
                <div class="empty-state">
                    <div class="empty-title">No candidates found</div>
                    <div class="empty-sub">${lf ? 'No results for "' + escHtml(lf) + '"' : 'Add candidates to get started'}</div>
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
            const hasCV = c.id > 0 && c.cv_text && c.cv_text.trim().length > 10;
            const safeName = (c.name || 'Candidate').replace(/[^a-zA-Z0-9_-]/g, '_');

            tbody.innerHTML += `
                <tr onmouseover="this.style.background='#F8F9FF'" onmouseout="this.style.background='transparent'">
                    <td style="min-width:150px;"><strong style="font-size:12px;">${escHtml(c.name)}</strong></td>
                    <td style="font-size:11px;color:#6B7280;">${job ? escHtml(job.job_title) : '—'}</td>
                    <td>
                        ${pct !== null
                            ? `<span style="display:inline-flex;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;background:${sc.bg};color:${sc.text};">${pct}%</span>`
                            : '<span style="color:#9CA3AF;font-size:11px;">Pending</span>'}
                    </td>
                    <td style="font-size:11px;color:#6B7280;">Registered</td>
                    <td><span style="display:inline-flex;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:500;background:${stageCol}22;color:${stageCol};">${stage}</span></td>
                    <td style="font-size:11px;color:#6B7280;">${c.experience_years != null ? c.experience_years + ' yrs' : '—'}</td>
                    <td style="font-size:11px;">
                        ${hasCV ? `<a href="#" onclick="downloadAdminCV(${c.id},'${safeName}');return false;" style="color:#0F6E56;font-weight:500;font-size:11px;text-decoration:none;cursor:pointer;">↓ CV</a>` : '<span style="color:#9CA3AF;">—</span>'}
                    </td>
                    <td>
                        <button class="btn-action" style="font-size:10px;padding:4px 8px;" onclick="viewCandidate(${c.id})">View</button>
                    </td>
                </tr>
            `;
        });
    }
}

function setPipelineView(view) {
    pipelineView = view;
    const tabsView = document.getElementById('stage-tabs-view');
    const listView = document.getElementById('candidates-list-view');
    const listToggle = document.getElementById('list-toggle');
    if (!tabsView || !listView) return;
    if (view === 'list') {
        tabsView.style.display = 'none';
        listView.style.display = '';
        if (listToggle) listToggle.classList.add('active');
    } else {
        tabsView.style.display = '';
        listView.style.display = 'none';
        if (listToggle) listToggle.classList.remove('active');
        renderStageTabs(pipelineFilter);
    }
}

function filterPipeline(value) {
    pipelineFilter = value;
    renderStageTabs(value);
    renderCandidateList(value);
}

function filterPipelineCompany(val) {
    companyFilter = val;
    renderStageTabs(pipelineFilter);
    renderCandidateList(pipelineFilter);
}

const _CONFIRM_STAGES = new Set(['interview', 'offered', 'hired']);

function changeAppStage(appId, newStage, selectEl) {
    if (!newStage) return;
    if (selectEl) selectEl.value = '';
    const app = applications.find(a => a.application_id === appId);
    const candName = app ? (app.name || 'Candidate') : 'Candidate';
    if (_CONFIRM_STAGES.has(newStage)) {
        _showStageConfirm(appId, newStage, candName, app);
    } else {
        _doStageChange(appId, newStage);
    }
}

function _showStageConfirm(appId, newStage, candName) {
    const label = newStage.charAt(0).toUpperCase() + newStage.slice(1);
    const m = document.createElement('div');
    m.id = 'stage-confirm-modal';
    m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:10001;display:flex;align-items:center;justify-content:center;padding:24px;';
    m.innerHTML = `<div style="background:#fff;border-radius:16px;width:420px;max-width:calc(100vw - 48px);box-shadow:0 24px 64px rgba(0,0,0,0.2);overflow:hidden;">
        <div style="background:#1B2A4A;padding:16px 20px;display:flex;justify-content:space-between;align-items:center;">
            <span style="color:#fff;font-weight:600;font-size:14px;">Move to ${label}?</span>
            <button onclick="document.getElementById('stage-confirm-modal').remove()" style="color:#fff;background:rgba(255,255,255,0.15);border:none;border-radius:50%;width:26px;height:26px;cursor:pointer;font-size:15px;">×</button>
        </div>
        <div style="padding:20px;">
            <p style="font-size:13px;color:#374151;margin:0 0 18px;">Move <strong>${escHtml(candName)}</strong> to <strong>${label}</strong> stage?</p>
            <div style="display:flex;gap:10px;">
                <button onclick="document.getElementById('stage-confirm-modal').remove()" style="flex:1;padding:10px;border:1px solid #E5E7EB;border-radius:8px;background:#F4F5FA;color:#1B2A4A;font-size:13px;cursor:pointer;">Cancel</button>
                <button onclick="document.getElementById('stage-confirm-modal').remove();_doStageChange(${appId},'${newStage}')" style="flex:1;padding:10px;border:none;border-radius:8px;background:#1B2A4A;color:#C9A84C;font-size:13px;font-weight:600;cursor:pointer;">Confirm</button>
            </div>
        </div>
    </div>`;
    document.body.appendChild(m);
}

async function _doStageChange(appId, newStage) {
    // Animate card out of current tab
    const card = document.getElementById('stage-card-' + appId);
    if (card) {
        const h = card.offsetHeight;
        card.style.transition = 'opacity 0.28s, max-height 0.28s ease, margin 0.28s, padding 0.28s';
        card.style.overflow = 'hidden';
        card.style.maxHeight = h + 'px';
        card.style.opacity = '0';
        setTimeout(() => { card.style.maxHeight = '0'; card.style.marginBottom = '0'; card.style.paddingTop = '0'; card.style.paddingBottom = '0'; }, 30);
    }

    const token = localStorage.getItem('token');
    try {
        const res = await fetch(`/api/admin/applications/${appId}/stage`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ stage: newStage }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Stage update failed', 'error');
            return;
        }
        const data = await res.json();
        const idx = applications.findIndex(a => a.application_id === appId);
        const candName = idx >= 0 ? (applications[idx].name || 'Candidate') : 'Candidate';
        if (idx >= 0) applications[idx].stage = data.stage;
        setTimeout(() => {
            renderCandidates();
            showToast(candName + ' moved to ' + data.stage, 'success');
        }, 300);
        if (newStage.toLowerCase() === 'interview') {
            setTimeout(() => openScheduleInterviewModal(appId, candName, null), 500);
        }
    } catch (e) {
        showToast('Stage update failed', 'error');
    }
}


async function adminReEvaluate(candidateId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '⏳ …'; }
    showToast('Running AI evaluation…', 'info');
    try {
        const resp = await authFetch(`/re-evaluate/${candidateId}`, { method: 'POST' });
        if (!resp.ok) throw new Error();
        const data = await resp.json();
        showToast(`Re-evaluation complete — score: ${Math.round(data.score)}%`, 'success');
        fetchData();
    } catch {
        showToast('Re-evaluation failed.', 'error');
        if (btn) { btn.disabled = false; btn.textContent = '↻ Re-eval'; }
    }
}

function downloadAdminCV(id, safeName) {
    showToast('Generating PDF…', 'info');
    authFetch(`/candidates/${id}/cv`)
        .then(res => {
            if (!res.ok) throw new Error('Not available');
            return res.blob();
        })
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `CV_${safeName || id}.pdf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('CV PDF downloaded successfully.', 'success');
        })
        .catch(() => showToast('CV not available for this candidate.', 'error'));
}

function downloadCandidateCV(id, safeName) {
    downloadAdminCV(id, safeName);
}

function sendCandidateEmail(email, name, jobTitle, companyName) {
    const subject = `Regarding Your Application — ${name}`;
    const body = `Dear ${name},\n\nThank you for applying to ${jobTitle} at ${companyName}.\n\nBest regards,\nHunters HR Team`;
    window.open(`mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`);
}

function sendCandidateWhatsApp(phone, name, jobTitle) {
    const cleanPhone = (phone || '').replace(/[^0-9+]/g, '');
    const normalizedPhone = !cleanPhone.startsWith('+') && cleanPhone.startsWith('01') ? '+2' + cleanPhone : cleanPhone;
    const msg = `Hello ${name}, this is Hunters HR Team reaching out regarding your application for ${jobTitle}. `;
    window.open(`https://wa.me/${normalizedPhone}?text=${encodeURIComponent(msg)}`);
}

// Phase 3: download CV via application ID (handles both Type A and Type B)
function downloadAppCV(applicationId, safeName) {
    showToast('Generating PDF…', 'info');
    authFetch(`/api/admin/applications/${applicationId}/cv`)
        .then(res => {
            if (!res.ok) return res.text().then(t => {
                let msg = 'CV not available';
                try { msg = JSON.parse(t).detail || msg; } catch (_) {}
                throw new Error(msg);
            });
            return res.blob();
        })
        .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `CV_${safeName || applicationId}.pdf`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('CV PDF downloaded successfully.', 'success');
        })
        .catch(err => showToast(err.message || 'CV not available for this application.', 'error'));
}

// Phase 3: open the candidate detail modal for a specific application
function viewApplication(applicationId) {
    const app = applications.find(a => a.application_id === applicationId);
    if (!app) { showToast('Application not found.', 'error'); return; }
    currentApplicationId = applicationId;
    currentCandidateId = app.candidate_id || null;

    const safeName = (app.name || 'Candidate').replace(/[^a-zA-Z0-9_-]/g, '_');

    document.getElementById("modal-candidate-name").innerText = `${app.name}'s Report`;
    document.getElementById("modal-candidate-phone").innerText = app.phone || '—';
    document.getElementById("modal-candidate-expected-salary").innerText = '—';

    // CV button
    const modalContent = document.getElementById("candidate-detail-modal");
    let cvBtn = document.getElementById("modal-cv-download-btn");
    if (!cvBtn) {
        cvBtn = document.createElement("button");
        cvBtn.id = "modal-cv-download-btn";
        cvBtn.style.cssText = "margin:12px 0 0;width:100%;padding:10px;background:#1B2A4A;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;";
        const footer = modalContent.querySelector(".modal-footer") || modalContent.querySelector(".modal-content");
        if (footer) footer.appendChild(cvBtn);
    }
    if (app.cv_available) {
        cvBtn.style.display = 'block';
        cvBtn.textContent = '↓ Download CV PDF';
        cvBtn.onclick = () => downloadAppCV(applicationId, safeName);
    } else {
        cvBtn.style.display = 'none';
    }

    if (app.evaluation_id) {
        currentEvaluationId = app.evaluation_id;
        const pct = evalScorePercent(app.score) ?? 0;
        document.getElementById("modal-candidate-score").innerText = `${pct}%`;
        document.getElementById("modal-candidate-score").style.background = `conic-gradient(var(--primary) ${pct}%, var(--bg-dark) 0)`;
        document.getElementById("modal-candidate-decision").innerText = app.decision || 'Pending';
        document.getElementById("modal-candidate-decision").className = `decision-badge badge ${(app.decision || 'pending').toLowerCase()}`;
        document.getElementById("modal-candidate-reason").innerText = app.reason || 'No evaluation reason available.';

        const strList = parseListField(app.strengths);
        document.getElementById("modal-candidate-strengths").innerHTML = strList.length
            ? `<ul style="margin:4px 0 0;padding-left:18px;line-height:1.8;">${strList.map(s => `<li>${escHtml(s)}</li>`).join('')}</ul>`
            : '<span style="color:#9CA3AF;">None noted.</span>';
        const wkList = parseListField(app.weaknesses);
        document.getElementById("modal-candidate-weaknesses").innerHTML = wkList.length
            ? `<ul style="margin:4px 0 0;padding-left:18px;line-height:1.8;">${wkList.map(s => `<li>${escHtml(s)}</li>`).join('')}</ul>`
            : '<span style="color:#9CA3AF;">None noted.</span>';
        const qList = document.getElementById("modal-candidate-questions");
        qList.innerHTML = "";
        const qs = parseListField(app.suggested_interview_questions);
        if (qs.length > 0) {
            qs.forEach(q => { qList.innerHTML += `<li>${escHtml(q)}</li>`; });
        } else {
            qList.innerHTML = "<li>No specific questions generated.</li>";
        }
    } else {
        currentEvaluationId = null;
        document.getElementById("modal-candidate-score").innerText = "0%";
        document.getElementById("modal-candidate-score").style.background = `conic-gradient(var(--primary) 0%, var(--bg-dark) 0)`;
        document.getElementById("modal-candidate-decision").innerText = "Pending";
        document.getElementById("modal-candidate-reason").innerText = "No evaluation available for this application.";
        document.getElementById("modal-candidate-strengths").innerText = "—";
        document.getElementById("modal-candidate-weaknesses").innerText = "—";
        document.getElementById("modal-candidate-questions").innerHTML = "";
    }

    document.getElementById("candidate-detail-modal").classList.add("active");
}

function viewBasicProfile(applicationId) { viewAtsProfile(applicationId); }

function viewAtsProfile(applicationId) {
    const app = applications.find(a => a.application_id === applicationId);
    if (!app) { showToast('Application data not found.', 'error'); return; }

    // Type B (external applicant) — no candidate row, show basic modal
    if (!app.candidate_id) {
        const skillTags = parseListField(app.skills).map(s =>
            `<span style="display:inline-block;background:#F0F2F8;color:#1B2A4A;font-size:11px;padding:3px 10px;border-radius:12px;margin:2px;">${escHtml(s)}</span>`
        ).join('') || '<span style="color:#9CA3AF;font-size:12px;">No skills listed</span>';
        const row = (label, val) =>
            `<div style="display:flex;gap:12px;padding:8px 0;border-bottom:0.5px solid #F3F4F6;">
                <div style="min-width:130px;font-size:11px;color:#9CA3AF;font-weight:500;">${label}</div>
                <div style="font-size:12px;color:#1B2A4A;">${escHtml(String(val || '—'))}</div>
             </div>`;
        createAdminModal(
            `${escHtml(app.name)} — External Applicant`,
            `<div>
                <div style="margin-bottom:12px;padding:8px 12px;background:#FFF7E0;border-radius:8px;font-size:11px;color:#854F0B;">External applicant — no registered candidate profile</div>
                ${row('Email', app.email)}
                ${row('Phone', app.phone)}
                ${row('Job Applied', app.job_title)}
                ${row('Company', app.company_name)}
                ${row('Stage', app.stage)}
                ${row('Experience', app.experience_years != null ? app.experience_years + ' years' : null)}
                <div style="padding:10px 0;">
                    <div style="font-size:11px;color:#9CA3AF;font-weight:500;margin-bottom:6px;">SKILLS</div>
                    <div style="display:flex;flex-wrap:wrap;gap:4px;">${skillTags}</div>
                </div>
            </div>`,
            null
        );
        return;
    }

    // Type A (registered candidate) — fetch full profile
    authFetch(`/api/admin/candidate/${app.candidate_id}/profile`)
        .then(res => {
            if (!res.ok) throw new Error('Failed to load profile');
            return res.json();
        })
        .then(p => {
            const pill = (txt, bg = '#F0F2F8', color = '#1B2A4A') =>
                `<span style="display:inline-block;background:${bg};color:${color};font-size:11px;padding:3px 10px;border-radius:12px;margin:2px;">${escHtml(txt)}</span>`;

            const section = (title, content) =>
                `<div style="margin-top:18px;">
                    <div style="font-size:10px;font-weight:600;color:#9CA3AF;letter-spacing:0.06em;text-transform:uppercase;margin-bottom:8px;padding-bottom:4px;border-bottom:0.5px solid #F3F4F6;">${title}</div>
                    ${content}
                 </div>`;

            const skillTags = parseListField(p.skills).map(s => pill(s)).join('')
                || '<span style="color:#9CA3AF;font-size:12px;">No skills listed</span>';

            const expHtml = (p.experiences || []).length
                ? p.experiences.map(e => `
                    <div style="margin-bottom:12px;padding-bottom:12px;border-bottom:0.5px solid #F3F4F6;">
                        <div style="font-size:12px;font-weight:500;color:#1B2A4A;">${escHtml(e.title || e.role || '')}</div>
                        <div style="font-size:11px;color:#0F6E56;margin-top:1px;">
                            ${escHtml(e.employer || e.company || '')}${e.start ? ' · ' + escHtml(e.start) + (e.end ? ' – ' + escHtml(e.end) : ' – Present') : ''}
                        </div>
                        ${e.description ? `<div style="font-size:11px;color:#6B7280;margin-top:4px;line-height:1.5;">${escHtml(e.description)}</div>` : ''}
                    </div>`).join('')
                : p.last_title
                    ? `<div style="margin-bottom:12px;padding-bottom:12px;border-bottom:0.5px solid #F3F4F6;">
                        <div style="font-size:12px;font-weight:500;color:#1B2A4A;">${escHtml(p.last_title)}</div>
                        <div style="font-size:11px;color:#0F6E56;margin-top:1px;">${escHtml(p.last_employer || '')}${p.last_employer ? ' · ' : ''}Current position</div>
                       </div>`
                    : '<div style="color:#9CA3AF;font-size:12px;">No experience listed</div>';

            const eduHtml = (p.education_history || []).length
                ? p.education_history.map(e => `
                    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">
                        <div>
                            <span style="font-size:12px;font-weight:500;color:#1B2A4A;">${escHtml(e.degree || '')}</span>
                            <span style="font-size:11px;color:#6B7280;"> — ${escHtml(e.institution || '')}</span>
                        </div>
                        ${e.year ? `<span style="font-size:11px;color:#9CA3AF;white-space:nowrap;margin-left:8px;">${escHtml(String(e.year))}</span>` : ''}
                    </div>`).join('')
                : (p.education
                    ? `<div style="font-size:12px;color:#1B2A4A;">${escHtml(p.education)}</div>`
                    : '<div style="color:#9CA3AF;font-size:12px;">No education listed</div>');

            const langHtml = (p.languages || []).length
                ? p.languages.map(l => typeof l === 'string'
                    ? pill(l, '#F0F2F8', '#1B2A4A')
                    : pill(`${l.language || ''}${l.proficiency ? ' · ' + l.proficiency : ''}`, '#F0F2F8', '#1B2A4A')
                ).join('')
                : '<span style="color:#9CA3AF;font-size:12px;">Not specified</span>';

            const appsHtml = (p.applications || []).length
                ? `<table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:4px;">
                    <thead><tr style="background:#F9FAFB;">
                        <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500;">Job</th>
                        <th style="padding:6px 8px;text-align:left;color:#6B7280;font-weight:500;">Stage</th>
                        <th style="padding:6px 8px;text-align:center;color:#6B7280;font-weight:500;">Score</th>
                        <th style="padding:6px 8px;text-align:center;color:#6B7280;font-weight:500;">Decision</th>
                    </tr></thead>
                    <tbody>${p.applications.map(a => `
                        <tr style="border-top:0.5px solid #F3F4F6;">
                            <td style="padding:6px 8px;color:#1B2A4A;">${escHtml(a.job_title || '—')}</td>
                            <td style="padding:6px 8px;color:#6B7280;">${escHtml(a.stage || '—')}</td>
                            <td style="padding:6px 8px;text-align:center;color:#1B2A4A;">${a.score != null ? Math.round(a.score<=1?a.score*100:a.score<=10?a.score*10:a.score) + '%' : '—'}</td>
                            <td style="padding:6px 8px;text-align:center;">${escHtml(a.decision || '—')}</td>
                        </tr>`).join('')}
                    </tbody></table>`
                : '<div style="color:#9CA3AF;font-size:12px;">No applications on record</div>';

            const metaLine = [
                p.location ? `📍 ${escHtml(p.location)}` : '',
                p.email    ? `✉ ${escHtml(p.email)}`    : '',
                p.phone    ? `📞 ${escHtml(p.phone)}`   : '',
            ].filter(Boolean).join('  ·  ');

            const bodyHTML = `<div>
                <div style="display:flex;align-items:flex-start;gap:16px;padding-bottom:16px;border-bottom:0.5px solid #F3F4F6;">
                    ${p.photo_url ? `<img src="${escHtml(p.photo_url)}" style="width:56px;height:56px;border-radius:50%;object-fit:cover;flex-shrink:0;" onerror="this.style.display='none'">` : ''}
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:16px;font-weight:600;color:#1B2A4A;">${escHtml(p.name || '—')}</div>
                        ${(p.last_title || p.last_employer) ? `<div style="font-size:12px;color:#0F6E56;margin-top:2px;">${escHtml([p.last_title, p.last_employer].filter(Boolean).join(' at '))}</div>` : ''}
                        ${metaLine ? `<div style="font-size:11px;color:#6B7280;margin-top:4px;word-break:break-all;">${metaLine}</div>` : ''}
                        <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;">
                            ${p.experience_years != null ? pill(p.experience_years + ' yrs exp') : ''}
                            ${p.expected_salary ? pill('💰 ' + p.expected_salary, '#F0FFF4', '#0F6E56') : ''}
                        </div>
                    </div>
                </div>
                ${p.summary ? section('About', `<div style="font-size:12px;color:#374151;line-height:1.6;">${escHtml(p.summary)}</div>`) : ''}
                ${section('Experience', expHtml)}
                ${section('Education', eduHtml)}
                ${section('Skills', `<div style="display:flex;flex-wrap:wrap;gap:4px;">${skillTags}</div>`)}
                ${section('Languages', `<div style="display:flex;flex-wrap:wrap;gap:6px;">${langHtml}</div>`)}
                ${section('Applications at Hunters', appsHtml)}
            </div>`;

            createAdminModal(`${escHtml(p.name)} — ATS Profile`, bodyHTML, null);
        })
        .catch(err => showToast(err.message || 'Failed to load candidate profile', 'error'));
}

function viewCandidate(id) {
    const candidate = candidates.find(c => c.id === id);
    if (!candidate) return;
    const ev = evaluations.find(e => e.candidate_id === id);
    currentCandidateId = id;

    const isRegisteredOnly = candidate.is_registered_user === true;
    const hasRealCV = !isRegisteredOnly && id > 0 && (candidate.has_cv || (candidate.cv_text && candidate.cv_text.trim().length > 10));
    const safeName = (candidate.name || 'Candidate').replace(/[^a-zA-Z0-9_-]/g, '_');

    document.getElementById("modal-candidate-name").innerText = `${candidate.name}'s Profile`;
    document.getElementById("modal-candidate-phone").innerText = candidate.phone || '-';
    document.getElementById("modal-candidate-expected-salary").innerText = candidate.expected_salary || (isRegisteredOnly ? 'Not specified' : '-');

    // CV download button — inject or update
    const modalContent = document.getElementById("candidate-detail-modal");
    let cvBtn = document.getElementById("modal-cv-download-btn");
    if (!cvBtn) {
        cvBtn = document.createElement("button");
        cvBtn.id = "modal-cv-download-btn";
        cvBtn.style.cssText = "margin:12px 0 0;width:100%;padding:10px;background:#1B2A4A;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;";
        const footer = modalContent.querySelector(".modal-footer") || modalContent.querySelector(".modal-content");
        if (footer) footer.appendChild(cvBtn);
    }
    if (hasRealCV) {
        cvBtn.style.display = 'block';
        cvBtn.textContent = '↓ Download CV PDF';
        cvBtn.onclick = () => downloadAdminCV(id, safeName);
    } else {
        cvBtn.style.display = 'none';
    }

    if (ev) {
        currentEvaluationId = ev.id;
        const pct = evalScorePercent(ev.score) ?? 0;
        document.getElementById("modal-candidate-score").innerText = `${pct}%`;
        document.getElementById("modal-candidate-score").style.background = `conic-gradient(var(--primary) ${pct}%, var(--bg-dark) 0)`;
        document.getElementById("modal-candidate-decision").innerText = ev.decision || 'Pending';
        document.getElementById("modal-candidate-decision").className = `decision-badge badge ${(ev.decision || 'pending').toLowerCase()}`;
        document.getElementById("modal-candidate-reason").innerText = ev.reason || (isRegisteredOnly ? 'Registered on the portal — has not applied to a job yet.' : 'No evaluation reason available.');
        const strList = parseListField(ev.strengths);
        document.getElementById("modal-candidate-strengths").innerHTML = strList.length
            ? `<ul style="margin:4px 0 0;padding-left:18px;line-height:1.8;">${strList.map(s => `<li>${escHtml(s)}</li>`).join('')}</ul>`
            : '<span style="color:#9CA3AF;">None noted.</span>';
        const wkList = parseListField(ev.weaknesses);
        document.getElementById("modal-candidate-weaknesses").innerHTML = wkList.length
            ? `<ul style="margin:4px 0 0;padding-left:18px;line-height:1.8;">${wkList.map(s => `<li>${escHtml(s)}</li>`).join('')}</ul>`
            : '<span style="color:#9CA3AF;">None noted.</span>';
        const qList = document.getElementById("modal-candidate-questions");
        qList.innerHTML = "";
        const qs = parseListField(ev.suggested_interview_questions);
        if (qs.length > 0) {
            qs.forEach(q => { qList.innerHTML += `<li>${escHtml(q)}</li>`; });
        } else {
            qList.innerHTML = "<li>No specific questions generated.</li>";
        }
    } else {
        document.getElementById("modal-candidate-score").innerText = "0%";
        document.getElementById("modal-candidate-score").style.background = `conic-gradient(var(--primary) 0%, var(--bg-dark) 0)`;
        document.getElementById("modal-candidate-decision").innerText = "Pending";
        document.getElementById("modal-candidate-reason").innerText = isRegisteredOnly ? "Registered on the portal — has not applied to a job yet." : "Evaluation is currently running or failed.";
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

function exportToCSV() {
    if (applications.length === 0) { showToast('No applications to export.', 'info'); return; }

    const lf = (pipelineFilter || '').toLowerCase();
    const cf = (companyFilter || '').toLowerCase();
    const rows = applications.filter(app => {
        const matchSearch = !lf ||
            (app.name || '').toLowerCase().includes(lf) ||
            (app.email || '').toLowerCase().includes(lf) ||
            (app.job_title || '').toLowerCase().includes(lf);
        const matchCompany = !cf || (app.company_name || '').toLowerCase() === cf;
        return matchSearch && matchCompany;
    });

    if (rows.length === 0) { showToast('No applications match the current filter.', 'info'); return; }

    const esc = v => '"' + String(v ?? '').replace(/"/g, '""') + '"';
    const headers = ['Name', 'Email', 'Job Title', 'Company', 'Score', 'Decision', 'Stage', 'Type', 'Applied Date'];
    const csvRows = [
        headers.map(esc).join(','),
        ...rows.map(app => [
            app.name || '',
            app.email || '',
            app.job_title || '',
            app.company_name || '',
            evalScorePercent(app.score) ?? '',
            app.decision || 'Pending',
            _decisionToStage(app.decision),
            app.candidate_type === 'registered' ? 'Registered' : 'External',
            app.applied_at ? app.applied_at.split('T')[0] : ''
        ].map(esc).join(','))
    ];

    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `hunters-pipeline-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast(`Exported ${rows.length} application${rows.length !== 1 ? 's' : ''} to CSV.`, 'success');
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
        localStorage.setItem("username", data.username || data.full_name || '');
        localStorage.setItem("company_id", data.company_id || '');

        // Route by user_type returned directly from login response
        try {
            const userResponse = await fetch(`${API_URL}/auth/me`, {
                headers: { 'Authorization': `Bearer ${data.access_token}` }
            });

            if (userResponse.ok) {
                const user = await userResponse.json();

                if (user.is_admin) {
                    localStorage.setItem('user_type', 'admin');
                    checkAuth();
                } else if (user.company_id) {
                    localStorage.setItem('user_type', 'company');
                    localStorage.setItem('company_id', user.company_id || '');
                    window.location.href = 'company-dashboard.html';
                } else {
                    localStorage.setItem('user_type', 'candidate');
                    window.location.href = 'candidates-portal.html';
                }
            } else {
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
    // Phase 3: use applications[] (supports Type A + Type B)
    const app = applications.find(a => a.application_id === currentApplicationId);
    if (!app) { showToast('Application data not found.', 'error'); return; }
    if (!app.evaluation_id) { showToast('No evaluation found for this application.', 'error'); return; }

    const ev = { decision: app.decision, reason: app.reason, score: app.score, strengths: app.strengths, weaknesses: app.weaknesses };
    const candidate = { name: app.name, phone: app.phone, experience_years: app.experience_years };
    const job = {
        job_title: app.job_title,
        weight_experience: app.weight_experience != null ? app.weight_experience + '%' : '—',
        weight_skills:     app.weight_skills     != null ? app.weight_skills     + '%' : '—',
        weight_education:  app.weight_education  != null ? app.weight_education  + '%' : '—',
        weight_behavioral: app.weight_behavioral != null ? app.weight_behavioral + '%' : '—',
    };

    const printPct = evalScorePercent(app.score);
    const printStrengths = parseListField(app.strengths).map(s => `• ${s}`).join('<br>') || (app.strengths || '—');
    const printWeaknesses = parseListField(app.weaknesses).map(s => `• ${s}`).join('<br>') || (app.weaknesses || '—');

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
                    <div class="label">Metric</div><div class="label">Notes / Details</div><div class="label">AI Score</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Experience Weight: ${job.weight_experience}</div><div>Verified against JD requirements</div><div>${app.score_experience != null ? app.score_experience + '%' : '-'}</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Skills Weight: ${job.weight_skills}</div><div>AI analysis of core technologies</div><div>${app.score_skills != null ? app.score_skills + '%' : '-'}</div>
                </div>
                <div class="grid" style="grid-template-columns: 1fr 1fr 100px;">
                    <div class="label">Education Weight: ${job.weight_education}</div><div>Academic background alignment</div><div>${app.score_education != null ? app.score_education + '%' : '-'}</div>
                </div>

                <div class="score-summary">🔢 SCORE SUMMARY</div>
                <div class="grid"><div class="label">Weighted AI Score</div><div style="font-size: 24px; font-weight: 500; color: #1B2A4A;">${printPct != null ? printPct + '%' : '—'}</div></div>

                <div class="section-title">⚙️ AUTO DECISION ENGINE</div>
                <div class="decision-box">
                    <div class="label">SCREENING DECISION</div>
                    <div class="value" style="color: ${(ev.decision || '').toLowerCase() === 'reject' ? '#df2029' : '#10b981'}">${(ev.decision || 'Pending').toUpperCase()}</div>
                </div>

                <div class="rejection-reason">🚩 ANALYSIS & REASONING</div>
                <div class="reason-list">
                    ${(ev.reason || '').split('\n').filter(Boolean).map(r => `<p>🚩 ${r}</p>`).join('') || '<p>No reason provided.</p>'}
                </div>

                <div class="section-title">📝 SCREENER NOTES & RECOMMENDATION</div>
                <div class="notes-section">
                    <strong>Strengths:</strong><br>${printStrengths}<br><br>
                    <strong>Weaknesses:</strong><br>${printWeaknesses}
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
        currentUser = user;
        const displayEl = document.getElementById("user-display-name");
        if (displayEl) {
            displayEl.innerText = user.full_name || user.email;
        }
        if (user.is_admin) {
            // Show admin-only UI elements
            document.querySelectorAll('.admin-only-col').forEach(el => el.style.display = '');
            document.querySelectorAll('.admin-only-stat').forEach(el => el.style.display = '');
            document.querySelectorAll('.non-admin-stat').forEach(el => el.style.display = 'none');
            const cf = document.getElementById('company-filter-wrap');
            if (cf) cf.style.display = '';
            const qa = document.getElementById('admin-quick-actions');
            if (qa) qa.style.display = 'flex';
            const grid = document.getElementById('stats-grid');
            if (grid) grid.style.gridTemplateColumns = 'repeat(6,1fr)';
            // admin-only-nav items that are NOT super-admin-only
            document.querySelectorAll('.admin-only-nav:not(.super-admin-only)').forEach(el => el.style.display = '');
            if (!user.company_id) {
                // SuperAdmin — show all super-admin-only elements too
                document.querySelectorAll('.super-admin-only').forEach(el => el.style.display = '');
            }
            loadAdminStats();
        }
    } catch (err) {
        console.error("Failed to fetch user info", err);
    }
}

function buildCompanyFilter() {
    const sel = document.getElementById('company-filter');
    if (!sel) return;
    const source = applications.length > 0 ? applications : candidates;
    const companies = [...new Set(source.map(x => x.company_name).filter(Boolean))].sort();
    const cur = sel.value;
    sel.innerHTML = '<option value="">All Companies</option>';
    companies.forEach(co => {
        const opt = document.createElement('option');
        opt.value = co;
        opt.textContent = co;
        if (co === cur) opt.selected = true;
        sel.appendChild(opt);
    });
}

function filterCompany(val) {
    companyFilter = val;
    renderCandidateList(pipelineFilter);
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
        // Cancel: restore view mode from cached data — no page reload needed
        editBtn.innerHTML = "<i class='bx bx-edit'></i> Edit Report";
        saveBtn.style.display = "none";
        viewApplication(currentApplicationId);
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
        suggested_interview_questions: (evaluations.find(e => e.id === currentEvaluationId) || {}).suggested_interview_questions ?? []
    };

    try {
        const response = await authFetch(`/update/${currentEvaluationId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            showToast('Report updated successfully!', 'success');
            closeModals();
            fetchData();
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

// ═══════════════════════════════════════════════════════════════
//  ADMIN PANEL — FULL CRUD
// ═══════════════════════════════════════════════════════════════

function showView(viewId, loadFn) {
    document.querySelectorAll('.nav-links li').forEach(l => l.classList.remove('active'));
    const navItem = document.querySelector(`.nav-links li[data-view="${viewId}"]`);
    if (navItem) navItem.classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const view = document.getElementById(viewId);
    if (view) view.classList.add('active');
    if (typeof loadFn === 'function') loadFn();
}

// ── Stats ──────────────────────────────────────────────────────
async function loadAdminStats() {
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/stats', {
            headers: { 'Authorization': 'Bearer ' + token }, cache: 'no-store'
        });
        if (!res.ok) return;
        const s = await res.json();
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
        set('stat-total-companies', s.total_companies);
        set('stat-pending-companies', s.pending_companies);
        set('stat-pending-companies-label', s.pending_companies + ' pending');
        set('stat-active-users', s.active_users);
        set('stat-total-users-label', s.total_users + ' total users');
        set('total-jobs', s.total_jobs);
        set('total-candidates', s.total_candidates);
        set('jobs-trend', s.total_jobs + ' total');
        set('candidates-trend', s.total_candidates + ' total');
    } catch (e) { console.error('loadAdminStats error', e); }
}

// ── Reusable Modals ────────────────────────────────────────────
function createAdminModal(title, bodyHTML, onSave) {
    const existing = document.getElementById('admin-crud-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.id = 'admin-crud-modal';
    modal.style.cssText = 'display:flex;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:3000;align-items:center;justify-content:center;padding:20px;overflow-y:auto;';
    modal.innerHTML = `
        <div style="background:#fff;border-radius:16px;width:680px;max-width:calc(100vw - 40px);max-height:90vh;overflow-y:auto;box-shadow:0 24px 64px rgba(0,0,0,0.2);">
            <div style="background:#1B2A4A;padding:16px 24px;border-radius:16px 16px 0 0;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:1;">
                <span style="color:#fff;font-size:14px;font-weight:500;">${title}</span>
                <button onclick="closeAdminModal()" style="background:none;border:none;color:rgba(255,255,255,0.6);font-size:20px;cursor:pointer;">✕</button>
            </div>
            <div style="padding:24px;">${bodyHTML}</div>
            ${onSave ? `
            <div style="padding:14px 24px;border-top:0.5px solid #F3F4F6;display:flex;gap:8px;justify-content:flex-end;position:sticky;bottom:0;background:#fff;z-index:1;">
                <button onclick="closeAdminModal()" style="background:#fff;border:0.5px solid #E5E7EB;border-radius:8px;padding:8px 16px;font-size:12px;color:#6B7280;cursor:pointer;">Cancel</button>
                <button id="admin-modal-save" style="background:#1B2A4A;color:#fff;border:none;border-radius:8px;padding:8px 18px;font-size:12px;font-weight:500;cursor:pointer;">Save Changes</button>
            </div>` : `
            <div style="padding:14px 24px;border-top:0.5px solid #F3F4F6;display:flex;justify-content:flex-end;">
                <button onclick="closeAdminModal()" style="background:#1B2A4A;color:#fff;border:none;border-radius:8px;padding:8px 18px;font-size:12px;font-weight:500;cursor:pointer;">Close</button>
            </div>`}
        </div>`;
    document.body.appendChild(modal);
    if (onSave) document.getElementById('admin-modal-save').onclick = onSave;
    return modal;
}

function createConfirmModal(title, message, onConfirm) {
    const existing = document.getElementById('admin-crud-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.id = 'admin-crud-modal';
    modal.style.cssText = 'display:flex;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:3000;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
        <div style="background:#fff;border-radius:16px;width:420px;max-width:calc(100vw - 40px);box-shadow:0 24px 64px rgba(0,0,0,0.2);padding:28px;">
            <div style="font-size:16px;font-weight:500;color:#1B2A4A;margin-bottom:10px;">${title}</div>
            <div style="font-size:13px;color:#6B7280;line-height:1.6;margin-bottom:20px;">${message}</div>
            <div style="display:flex;gap:8px;justify-content:flex-end;">
                <button onclick="closeAdminModal()" style="background:#fff;border:0.5px solid #E5E7EB;border-radius:8px;padding:8px 16px;font-size:12px;color:#6B7280;cursor:pointer;">Cancel</button>
                <button id="confirm-action-btn" style="background:#CC2B2B;color:#fff;border:none;border-radius:8px;padding:8px 18px;font-size:12px;font-weight:500;cursor:pointer;">Confirm Delete</button>
            </div>
        </div>`;
    document.body.appendChild(modal);
    document.getElementById('confirm-action-btn').onclick = onConfirm;
}

function closeAdminModal() {
    const modal = document.getElementById('admin-crud-modal');
    if (modal) modal.remove();
}

function exportAdminData(type) {
    const data = type === 'companies' ? window._adminCompanies :
                 type === 'candidates' ? window._adminCandidates :
                 type === 'users' ? window._adminUsers : [];
    if (!data || !data.length) { showToast('No data to export', 'info'); return; }
    const headers = Object.keys(data[0]);
    const rows = data.map(row => headers.map(h => `"${String(row[h]||'').replace(/"/g,'""')}"`).join(','));
    const csv = '﻿' + [headers.join(','), ...rows].join('\r\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = `hunters_${type}_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    showToast(type + ' exported successfully', 'success');
}

// ── Companies CRUD ─────────────────────────────────────────────
async function loadAdminCompanies() {
    const view = document.getElementById('companies-view');
    if (view) view.innerHTML = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading…</div>';
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/companies/full', {
            headers: { 'Authorization': 'Bearer ' + token }, cache: 'no-store'
        });
        const companies = await res.json();
        window._adminCompanies = companies;
        renderAdminCompaniesTable(companies);
    } catch (e) { console.error('loadAdminCompanies', e); }
}

function renderAdminCompaniesTable(companies) {
    const view = document.getElementById('companies-view');
    if (!view) return;
    window._adminCompanies = companies;

    const planBadge = plan => {
        const map = {
            free:         ['#9CA3AF','#F3F4F6'],
            growth:       ['#185FA5','#E6F1FB'],
            professional: ['#0F6E56','#E1F5EE'],
            enterprise:   ['#C9A84C','#FBF7E8'],
        };
        const [c, bg] = map[(plan||'free').toLowerCase()] || map.free;
        const label = (plan||'free').charAt(0).toUpperCase() + (plan||'free').slice(1);
        return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:${bg};color:${c};font-size:10px;font-weight:600;">${escHtml(label)}</span>`;
    };
    const statusBadge = s => s === 'approved'
        ? '<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:#E1F5EE;color:#0F6E56;font-size:10px;font-weight:500;">Approved</span>'
        : '<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:#FAEEDA;color:#854F0B;font-size:10px;font-weight:500;">Pending</span>';

    const cards = companies.map(c => {
        const initials = (c.name||'?').split(' ').slice(0,2).map(w => w[0]||'').join('').toUpperCase() || '??';
        const lastAct = c.last_activity_at ? new Date(c.last_activity_at).toLocaleDateString('en-GB') : '—';
        return `<div class="co-admin-card" data-id="${c.id}" data-status="${c.status||''}" data-search="${escHtml((c.name+' '+c.email+' '+(c.admin_name||'')).toLowerCase())}"
            style="background:#fff;border-radius:14px;border:1px solid #E5E7EB;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,0.04);display:flex;flex-direction:column;gap:14px;transition:box-shadow 0.15s;"
            onmouseover="this.style.boxShadow='0 4px 16px rgba(0,0,0,0.10)'" onmouseout="this.style.boxShadow='0 1px 4px rgba(0,0,0,0.04)'">
            <div style="display:flex;align-items:flex-start;gap:12px;">
                <div style="width:48px;height:48px;border-radius:12px;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;flex-shrink:0;">${escHtml(initials)}</div>
                <div style="flex:1;min-width:0;">
                    <div style="font-size:14px;font-weight:600;color:#1B2A4A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(c.name||'—')}</div>
                    <div style="font-size:11px;color:#9CA3AF;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(c.admin_email||c.email||'')}</div>
                    <div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">${statusBadge(c.status)} ${planBadge(c.plan)}</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;background:#F9FAFB;border-radius:10px;padding:10px;">
                <div style="text-align:center;"><div style="font-size:18px;font-weight:700;color:#1B2A4A;">${c.job_count||0}</div><div style="font-size:10px;color:#9CA3AF;">Jobs</div></div>
                <div style="text-align:center;border-left:1px solid #E5E7EB;border-right:1px solid #E5E7EB;"><div style="font-size:18px;font-weight:700;color:#1B2A4A;">${c.candidate_count||0}</div><div style="font-size:10px;color:#9CA3AF;">Candidates</div></div>
                <div style="text-align:center;"><div style="font-size:18px;font-weight:700;color:#1B2A4A;">${c.applications_count||0}</div><div style="font-size:10px;color:#9CA3AF;">Applications</div></div>
            </div>
            <div style="font-size:11px;color:#9CA3AF;">Last activity: ${escHtml(lastAct)}</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
                ${currentUser && currentUser.is_admin ? `<button onclick="enterCompanyWorkspace(${c.id})" style="flex:1;padding:8px 6px;background:#C9A84C;color:#1B2A4A;border:none;border-radius:8px;font-size:12px;font-weight:700;cursor:pointer;min-width:90px;">Enter Company</button>` : ''}
                <button onclick="editCompanyAdmin('${c.id}')" style="padding:8px 10px;background:#F4F5FA;color:#1B2A4A;border:none;border-radius:8px;font-size:12px;cursor:pointer;">Edit</button>
                <button onclick="toggleCompanyStatus('${c.id}','${c.status}')" style="padding:8px 10px;background:${c.status==='approved'?'#FAEEDA':'#E1F5EE'};color:${c.status==='approved'?'#854F0B':'#0F6E56'};border:none;border-radius:8px;font-size:12px;cursor:pointer;">${c.status==='approved'?'Suspend':'Approve'}</button>
                <button onclick="deleteCompanyAdmin('${c.id}','${escHtml(c.name)}')" style="padding:8px 10px;background:#FCEBEB;color:#A32D2D;border:none;border-radius:8px;font-size:12px;cursor:pointer;">Delete</button>
            </div>
        </div>`;
    }).join('');

    view.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px;">' +
            '<div style="font-size:15px;font-weight:500;color:#1B2A4A;">Companies (' + companies.length + ')</div>' +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
                '<input id="company-search" placeholder="Search…" oninput="filterAdminCompanies()" ' +
                    'style="padding:7px 12px;border:0.5px solid #E5E7EB;border-radius:8px;font-size:12px;outline:none;width:180px;">' +
                '<select id="company-status-filter" onchange="filterAdminCompanies()" ' +
                    'style="padding:7px 12px;border:0.5px solid #E5E7EB;border-radius:8px;font-size:12px;outline:none;background:#fff;color:#1B2A4A;">' +
                    '<option value="">All Status</option>' +
                    '<option value="approved">Approved</option>' +
                    '<option value="pending">Pending</option>' +
                '</select>' +
                '<select id="company-plan-filter" onchange="filterAdminCompanies()" ' +
                    'style="padding:7px 12px;border:0.5px solid #E5E7EB;border-radius:8px;font-size:12px;outline:none;background:#fff;color:#1B2A4A;">' +
                    '<option value="">All Plans</option>' +
                    '<option value="free">Free</option>' +
                    '<option value="growth">Growth</option>' +
                    '<option value="professional">Professional</option>' +
                    '<option value="enterprise">Enterprise</option>' +
                '</select>' +
            '</div>' +
        '</div>' +
        '<div id="admin-companies-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;">' + cards + '</div>';
}

function filterAdminCompanies() {
    const q = ((document.getElementById('company-search') || {}).value || '').toLowerCase();
    const st = (document.getElementById('company-status-filter') || {}).value || '';
    const pl = (document.getElementById('company-plan-filter') || {}).value || '';
    document.querySelectorAll('.co-admin-card').forEach(card => {
        const matchQ = !q || (card.dataset.search||'').includes(q);
        const matchS = !st || card.dataset.status === st;
        const c = (window._adminCompanies||[]).find(x => String(x.id) === card.dataset.id);
        const matchP = !pl || (c && (c.plan||'free').toLowerCase() === pl);
        card.style.display = matchQ && matchS && matchP ? '' : 'none';
    });
}

function filterAdminCompaniesByStatus(status) { filterAdminCompanies(); }

// ── Enter Company Workspace ────────────────────────────────────────
async function enterCompanyWorkspace(companyId) {
    const view = document.getElementById('companies-view');
    if (!view) return;
    view.innerHTML = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading company…</div>';
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/companies/' + companyId + '/overview', {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!res.ok) throw new Error('Company not found');
        const co = await res.json();
        _renderCoWorkspace(co, 'overview');
    } catch(e) {
        view.innerHTML = '<div style="padding:32px;color:#DC2626;text-align:center;">' + escHtml(e.message) + '</div>';
    }
}

function _renderCoWorkspace(co, activeTab) {
    const view = document.getElementById('companies-view');
    if (!view) return;
    window._coWorkspaceCo = co;

    const planColors = {
        free:         ['#9CA3AF','#F3F4F6'],
        growth:       ['#185FA5','#E6F1FB'],
        professional: ['#0F6E56','#E1F5EE'],
        enterprise:   ['#C9A84C','#FBF7E8'],
    };
    const [pc, pb] = planColors[(co.plan||'free').toLowerCase()] || planColors.free;
    const planLabel = (co.plan||'free').charAt(0).toUpperCase() + (co.plan||'free').slice(1);

    const tabs = ['overview', 'jobs', 'candidates', 'profile'];
    const tabBtn = t => {
        const label = t.charAt(0).toUpperCase() + t.slice(1);
        const active = t === activeTab;
        return `<button onclick="_coWsTab('${t}')" style="padding:10px 20px;border:none;background:none;font-size:13px;cursor:pointer;color:${active?'#1B2A4A':'#9CA3AF'};font-weight:${active?'600':'400'};border-bottom:2px solid ${active?'#C9A84C':'transparent'};transition:all 0.15s;">${escHtml(label)}</button>`;
    };

    let bodyHtml = '';
    const _wsStageNames = {new:'Applied',applied:'Applied',screening:'Screening',shortlisted:'Shortlisted',interview:'Interview',offer:'Offered',offered:'Offered',hired:'Hired',rejected:'Rejected'};
    if (activeTab === 'overview') {
        const pipeline = co.pipeline || {};
        const pipelineRows = Object.entries(pipeline).map(([s, n]) => {
            const label = _wsStageNames[(s||'').toLowerCase()] || (s.charAt(0).toUpperCase()+s.slice(1));
            return `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:0.5px solid #F3F4F6;">
                <span style="font-size:12px;color:#374151;">${escHtml(label)}</span>
                <span style="font-size:13px;font-weight:600;color:#1B2A4A;">${n}</span>
            </div>`;
        }).join('') || '<div style="color:#9CA3AF;font-size:12px;padding:8px 0;">No applications yet</div>';

        const recentJobs = (co.recent_jobs || []).map(j =>
            `<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:0.5px solid #F3F4F6;">
                <span style="font-size:12px;color:#374151;">${escHtml(j.job_title||'')}</span>
                <span style="display:inline-block;padding:2px 8px;border-radius:8px;font-size:10px;background:${j.is_approved?'#E1F5EE':'#FAEEDA'};color:${j.is_approved?'#0F6E56':'#854F0B'};">${j.is_approved?'Live':'Pending'}</span>
            </div>`
        ).join('') || '<div style="color:#9CA3AF;font-size:12px;padding:8px 0;">No jobs yet</div>';

        bodyHtml = `
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px;margin-bottom:20px;">
                ${[
                    ['Jobs', co.job_count||0, '#C9A84C'],
                    ['Live Jobs', co.approved_job_count||0, '#0F6E56'],
                    ['Candidates', co.candidate_count||0, '#185FA5'],
                    ['Applications', co.applications_count||0, '#1B2A4A'],
                    ['Interviews', co.interviews_count||0, '#854F0B'],
                ].map(([l,v,c]) => `<div style="background:#fff;border-radius:12px;border:1px solid #E5E7EB;padding:16px;text-align:center;">
                    <div style="font-size:26px;font-weight:700;color:${c};">${v}</div>
                    <div style="font-size:11px;color:#9CA3AF;margin-top:2px;">${l}</div>
                </div>`).join('')}
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
                <div style="background:#fff;border-radius:12px;border:1px solid #E5E7EB;padding:16px;">
                    <div style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">Pipeline</div>
                    ${pipelineRows}
                </div>
                <div style="background:#fff;border-radius:12px;border:1px solid #E5E7EB;padding:16px;">
                    <div style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">Recent Jobs</div>
                    ${recentJobs}
                </div>
            </div>`;

    } else if (activeTab === 'jobs') {
        bodyHtml = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading jobs…</div>';

    } else if (activeTab === 'candidates') {
        bodyHtml = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading candidates…</div>';

    } else if (activeTab === 'profile') {
        const planExpVal = co.plan_expires_at ? co.plan_expires_at.slice(0,10) : '';
        bodyHtml = `
            <div style="background:#fff;border-radius:12px;border:1px solid #E5E7EB;padding:20px;margin-bottom:16px;">
                <div style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;">Company Info</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13px;">
                    ${[['Name', co.name],['Email',co.email],['Website',co.website||'—'],['Reg. No.',co.registration_number||'—'],['Admin',co.admin_name||'—'],['Admin Email',co.admin_email||'—'],['Status',co.status],['Registered',co.created_at?new Date(co.created_at).toLocaleDateString('en-GB'):'—']].map(([l,v])=>`<div><div style="font-size:10px;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:3px;">${l}</div><div style="color:#1B2A4A;">${escHtml(String(v||'—'))}</div></div>`).join('')}
                </div>
            </div>
            <div style="background:#fff;border-radius:12px;border:1px solid #C9A84C;padding:20px;">
                <div style="font-size:11px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;">Plan Management</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
                    <div>
                        <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:5px;">Plan</label>
                        <select id="ws-plan" style="width:100%;padding:9px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;background:#fff;outline:none;">
                            ${['free','growth','professional','enterprise'].map(p => `<option value="${p}" ${(co.plan||'free').toLowerCase()===p?'selected':''}>${p.charAt(0).toUpperCase()+p.slice(1)}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:5px;">Billing Status</label>
                        <select id="ws-billing" style="width:100%;padding:9px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;background:#fff;outline:none;">
                            ${['active','paused','cancelled'].map(s => `<option value="${s}" ${(co.billing_status||'active')===s?'selected':''}>${s.charAt(0).toUpperCase()+s.slice(1)}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:5px;">Plan Expires (leave blank = no expiry)</label>
                        <input type="date" id="ws-expires" value="${escHtml(planExpVal)}" style="width:100%;padding:9px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
                    </div>
                </div>
                <button onclick="_savePlanChanges(${co.id})" style="background:#1B2A4A;color:#C9A84C;border:none;border-radius:8px;padding:10px 20px;font-size:13px;font-weight:600;cursor:pointer;">Save Plan Changes</button>
            </div>`;
    }

    const _coLogoHtml = co.logo_url
        ? `<img src="${escHtml(co.logo_url)}" alt="" style="width:42px;height:42px;border-radius:10px;object-fit:contain;background:#fff;padding:3px;border:0.5px solid rgba(0,0,0,0.08);flex-shrink:0;">`
        : `<div style="width:42px;height:42px;border-radius:10px;background:rgba(255,255,255,0.2);display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:#fff;">${escHtml((co.name||'?').split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase())}</div>`;

    view.innerHTML =
        '<div style="background:linear-gradient(135deg,#C9A84C 0%,#B8932A 100%);border-radius:12px;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">' +
            '<div style="display:flex;align-items:center;gap:12px;">' +
                _coLogoHtml +
                '<div>' +
                    '<div style="color:#fff;font-size:15px;font-weight:700;">Viewing as: ' + escHtml(co.name||'Company') + '</div>' +
                    '<span style="display:inline-block;padding:2px 8px;border-radius:8px;background:rgba(255,255,255,0.25);color:#fff;font-size:11px;">' + escHtml(planLabel) + ' plan</span>' +
                '</div>' +
            '</div>' +
            '<button onclick="loadAdminCompanies()" style="background:rgba(255,255,255,0.2);color:#fff;border:1px solid rgba(255,255,255,0.4);border-radius:8px;padding:8px 16px;font-size:13px;cursor:pointer;font-weight:500;">← Back to Companies</button>' +
        '</div>' +
        '<div style="display:flex;border-bottom:1px solid #E5E7EB;margin-bottom:20px;">' + tabs.map(tabBtn).join('') + '</div>' +
        '<div id="co-ws-body">' + bodyHtml + '</div>';
}

function _coWsTab(tab) {
    const co = window._coWorkspaceCo;
    if (!co) return;
    _renderCoWorkspace(co, tab);
    if (tab === 'candidates') _loadCoWsCandidates(co);
    if (tab === 'jobs') _coWsLoadJobs(co);
}

async function _savePlanChanges(companyId) {
    const plan = (document.getElementById('ws-plan') || {}).value || 'free';
    const billing = (document.getElementById('ws-billing') || {}).value || 'active';
    const expires = (document.getElementById('ws-expires') || {}).value || null;
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/companies/' + companyId + '/plan', {
            method: 'PATCH',
            headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan, billing_status: billing, plan_expires_at: expires || null })
        });
        if (!res.ok) throw new Error('Failed to save');
        const data = await res.json();
        showToast('Plan updated to ' + data.plan, 'success');
        if (window._coWorkspaceCo) {
            window._coWorkspaceCo.plan = data.plan;
            window._coWorkspaceCo.billing_status = data.billing_status;
            window._coWorkspaceCo.plan_expires_at = data.plan_expires_at;
        }
    } catch(e) { showToast('Save failed: ' + e.message, 'error'); }
}

function _coWsCandidatesTab(stage) {
    window._coWsCandidatesStage = stage;
    _renderCoWsCandidates(window._coWsCandidates || [], stage);
}

function _renderCoWsCandidates(apps, activeStage) {
    const body = document.getElementById('co-ws-body');
    if (!body) return;
    window._coWsCandidatesStage = activeStage;

    const counts = {};
    _STAGE_TABS.forEach(t => {
        counts[t.id] = apps.filter(a => t.stages.includes((a.stage||'applied').toLowerCase())).length;
    });

    const stagePills = _STAGE_TABS.map(t => {
        const active = t.id === activeStage;
        const cnt = counts[t.id] || 0;
        return `<button onclick="_coWsCandidatesTab('${t.id}')" style="padding:7px 14px;border:none;border-radius:20px;font-size:12px;font-weight:${active?'600':'400'};cursor:pointer;background:${active?t.accent:'#F3F4F6'};color:${active?'#fff':'#6B7280'};transition:all 0.15s;">${t.label} <span style="background:rgba(0,0,0,0.15);padding:1px 6px;border-radius:10px;font-size:11px;">${cnt}</span></button>`;
    }).join('');

    const activeTabDef = _STAGE_TABS.find(t => t.id === activeStage) || _STAGE_TABS[0];
    const filtered = apps.filter(a => activeTabDef.stages.includes((a.stage||'applied').toLowerCase()));

    const cards = filtered.length
        ? filtered.map(a => {
            const sl = (a.stage||'applied').toLowerCase();
            const [sc, sb] = _STAGE_BADGE_MAP[sl] || ['#1B2A4A','#EFF2F8'];
            const displayStage = {new:'Applied',applied:'Applied',screening:'Screening',shortlisted:'Shortlisted',interview:'Interview',offer:'Offered',offered:'Offered',hired:'Hired',rejected:'Rejected'}[sl] || a.stage;
            const appId = a.application_id || a.id;
            return `<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:0.5px solid #F3F4F6;gap:8px;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:13px;font-weight:500;color:#1B2A4A;">${escHtml(a.name||'Candidate')}</div>
                    <div style="font-size:11px;color:#9CA3AF;">${escHtml(a.email||'')}${a.job_title?' · '+escHtml(a.job_title):''}</div>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
                    ${a.score!=null?`<span style="font-size:12px;font-weight:600;color:#1B2A4A;">${a.score}%</span>`:''}
                    <span style="padding:2px 8px;border-radius:8px;font-size:10px;background:${sb};color:${sc};">${escHtml(displayStage)}</span>
                    <button onclick="_coWsMoveStage(${appId},'${sl}')" style="background:#FBF7E8;color:#C9A84C;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;font-weight:500;">Move</button>
                    <button onclick="_coWsViewCv(${appId})" style="background:#E6F1FB;color:#185FA5;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;font-weight:500;">CV</button>
                    <button onclick="_coWsViewReport(${appId})" style="background:#F3F4F6;color:#374151;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;font-weight:500;">Report</button>
                </div>
            </div>`;
        }).join('')
        : '<div style="padding:40px;text-align:center;color:#9CA3AF;font-size:13px;">No candidates in this stage.</div>';

    body.innerHTML = `
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;">${stagePills}</div>
        <div style="background:#fff;border-radius:12px;border:1px solid #E5E7EB;overflow:hidden;max-height:520px;overflow-y:auto;">${cards}</div>
        <div style="margin-top:10px;font-size:11px;color:#9CA3AF;">${apps.length} total application${apps.length!==1?'s':''} · ${filtered.length} in this stage</div>`;
}

async function _loadCoWsCandidates(co) {
    const body = document.getElementById('co-ws-body');
    try {
        const res = await fetch('/api/admin/applications?company_id=' + co.id + '&limit=500', {
            headers: { 'Authorization': 'Bearer ' + localStorage.getItem('token') }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        window._coWsCandidates = Array.isArray(data.applications) ? data.applications : [];
        _renderCoWsCandidates(window._coWsCandidates, 'applied');
    } catch(e) {
        if (body) body.innerHTML = '<div style="padding:24px;color:#DC2626;font-size:13px;">Failed to load candidates: ' + escHtml(e.message) + '</div>';
    }
}

// ── Admin Workspace: Jobs ──────────────────────────────────────────────────────

function _renderCoWsJobs(jobs) {
    const body = document.getElementById('co-ws-body');
    if (!body) return;
    const co = window._coWorkspaceCo;
    const rows = jobs.length
        ? jobs.map(j => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 18px;border-bottom:0.5px solid #F3F4F6;gap:10px;">
                <div style="flex:1;min-width:0;">
                    <div style="font-size:13px;font-weight:500;color:#1B2A4A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(j.job_title||'')}</div>
                    <div style="font-size:11px;color:#9CA3AF;">${j.created_at?new Date(j.created_at).toLocaleDateString('en-GB'):''} ${j.job_location?'· '+escHtml(j.job_location):''}</div>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
                    <span style="padding:2px 8px;border-radius:8px;font-size:10px;background:${j.is_approved?'#E1F5EE':'#FAEEDA'};color:${j.is_approved?'#0F6E56':'#854F0B'};">${j.is_approved?'Live':'Pending'}</span>
                    <button onclick="openAdminJobPreview(${j.id})" title="Preview" style="background:#E6F1FB;color:#185FA5;border:none;border-radius:6px;padding:5px 9px;font-size:11px;cursor:pointer;font-weight:500;">Preview</button>
                    <button onclick="_coWsEditJobModal(${j.id})" title="Edit" style="background:#FBF7E8;color:#C9A84C;border:none;border-radius:6px;padding:5px 9px;font-size:11px;cursor:pointer;font-weight:500;">Edit</button>
                    <button onclick="_coWsShareJob(${j.id},'${escHtml(j.job_title||'').replace(/'/g,"&#39;")}')" title="Share" style="background:#F0FFF4;color:#0F6E56;border:none;border-radius:6px;padding:5px 9px;font-size:11px;cursor:pointer;font-weight:500;">Share</button>
                    <button onclick="_coWsDeleteJob(${j.id},'${escHtml(j.job_title||'').replace(/'/g,"&#39;")}')" title="Delete" style="background:#FEECEC;color:#DC2626;border:none;border-radius:6px;padding:5px 9px;font-size:11px;cursor:pointer;font-weight:500;">Delete</button>
                </div>
            </div>`).join('')
        : '<div style="padding:40px;text-align:center;color:#9CA3AF;font-size:13px;">No jobs for this company yet.</div>';

    body.innerHTML =
        `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
            <div style="font-size:12px;color:#9CA3AF;">${jobs.length} job${jobs.length!==1?'s':''}</div>
            <button onclick="_coWsPostJobModal()" style="background:#1B2A4A;color:#C9A84C;border:none;border-radius:8px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;">+ Post New Job</button>
        </div>
        <div style="background:#fff;border-radius:12px;border:1px solid #E5E7EB;overflow:hidden;">${rows}</div>`;
}

async function _coWsLoadJobs(co) {
    const body = document.getElementById('co-ws-body');
    try {
        const res = await fetch('/api/admin/jobs?company_id=' + co.id, {
            headers: { 'Authorization': 'Bearer ' + localStorage.getItem('token') }
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const jobs = await res.json();
        window._coWsJobs = Array.isArray(jobs) ? jobs : [];
        _renderCoWsJobs(window._coWsJobs);
    } catch(e) {
        if (body) body.innerHTML = '<div style="padding:24px;color:#DC2626;font-size:13px;">Failed to load jobs: ' + escHtml(e.message) + '</div>';
    }
}

function _coWsPostJobModal() {
    const co = window._coWorkspaceCo;
    if (!co) return;
    _coWsJobFormModal(null, co.id);
}

function _coWsEditJobModal(jobId) {
    const co = window._coWorkspaceCo;
    if (!co) return;
    const job = (window._coWsJobs || []).find(j => j.id === jobId);
    if (!job) return;
    _coWsJobFormModal(job, co.id);
}

function _coWsJobFormModal(job, companyId) {
    const title = job ? 'Edit Job — ' + (job.job_title || '') : 'Post New Job';
    const v = f => escHtml(String(job && job[f] != null ? job[f] : ''));
    const html = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Job Title *</label>
                <input id="cj-title" value="${v('job_title')}" placeholder="e.g. Senior Accountant" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div>
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Location</label>
                <input id="cj-location" value="${v('job_location')}" placeholder="e.g. Cairo, Egypt" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div>
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Min Experience (years)</label>
                <input id="cj-exp" type="number" min="0" value="${job ? (job.min_experience||0) : 0}" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div>
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Salary Range</label>
                <input id="cj-salary" value="${v('salary_range')}" placeholder="e.g. 15,000 - 20,000 EGP" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div>
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Education Level</label>
                <input id="cj-edu" value="${v('education_level')}" placeholder="e.g. Bachelor's Degree" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>

            <!-- Job Description — moved up, with AI + Upload buttons -->
            <div style="grid-column:span 2;">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;flex-wrap:wrap;gap:6px;">
                    <label style="font-size:11px;font-weight:500;color:#374151;">Job Description</label>
                    <div style="display:flex;gap:6px;">
                        <button type="button" onclick="_coWsToggleAISection()" style="background:#1B2A4A;color:#C9A84C;border:none;border-radius:6px;padding:4px 10px;font-size:11px;font-weight:600;cursor:pointer;">✨ Write with AI</button>
                        <button type="button" onclick="document.getElementById('cj-jd-file').click()" style="background:#F4F5FA;color:#1B2A4A;border:1px solid #E5E7EB;border-radius:6px;padding:4px 10px;font-size:11px;font-weight:500;cursor:pointer;">📄 Upload JD</button>
                        <input type="file" id="cj-jd-file" accept=".pdf,.docx" style="display:none;" onchange="_coWsUploadJD(this)">
                    </div>
                </div>
                <!-- AI collapsible -->
                <div id="cj-ai-section" style="display:none;background:#F4F5FA;border-radius:8px;padding:12px;margin-bottom:8px;">
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px;">
                        <div>
                            <label style="font-size:10px;font-weight:500;color:#6B7280;display:block;margin-bottom:3px;text-transform:uppercase;letter-spacing:0.04em;">Industry / Background *</label>
                            <input type="text" id="cj-ai-industry" placeholder="e.g. International School, K-12" style="width:100%;padding:7px 9px;border:1px solid #E5E7EB;border-radius:7px;font-size:12px;outline:none;box-sizing:border-box;">
                        </div>
                        <div>
                            <label style="font-size:10px;font-weight:500;color:#6B7280;display:block;margin-bottom:3px;text-transform:uppercase;letter-spacing:0.04em;">Additional Context (optional)</label>
                            <input type="text" id="cj-ai-context" placeholder="e.g. Senior level, Cairo-based" style="width:100%;padding:7px 9px;border:1px solid #E5E7EB;border-radius:7px;font-size:12px;outline:none;box-sizing:border-box;">
                        </div>
                    </div>
                    <div id="cj-ai-loading" style="display:none;font-size:11px;color:#9CA3AF;text-align:center;padding:6px;">⏳ Generating…</div>
                    <button type="button" id="cj-ai-btn" onclick="_coWsGenerateAI()" style="background:#1B2A4A;color:#C9A84C;border:none;border-radius:7px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;width:100%;">Generate Description + Skills</button>
                </div>
                <div id="cj-jd-status" style="display:none;font-size:11px;color:#9CA3AF;margin-bottom:4px;">Extracting text…</div>
                <textarea id="cj-desc" rows="5" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;resize:vertical;">${v('job_description')}</textarea>
            </div>

            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Required Skills</label>
                <input id="cj-skills" value="${v('required_skills')}" placeholder="e.g. Excel, SAP, Financial Reporting" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Nice to Have</label>
                <input id="cj-nice" value="${v('nice_to_have_skills')}" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Behavioral Skills</label>
                <input id="cj-behav" value="${v('behavioral_skills')}" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>
            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#374151;display:block;margin-bottom:4px;">Industry Experience</label>
                <input id="cj-industry" value="${v('industry_experience')}" style="width:100%;padding:8px 10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;outline:none;box-sizing:border-box;">
            </div>

            <!-- AI Screening Weights -->
            <div style="grid-column:span 2;background:#F4F5FA;border-radius:10px;padding:14px 16px;margin-top:4px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                    <span style="font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.06em;">⚖ AI Screening Weights</span>
                    <span id="cj-weights-total" style="font-size:11px;font-weight:600;background:#E1F5EE;color:#0F6E56;padding:3px 10px;border-radius:20px;">Total: 100%</span>
                </div>
                ${[
                    ['cj-w-exp',  'Experience Match',  job ? Math.round((job.weight_experience||0.40)*100) : 40],
                    ['cj-w-skl',  'Skills Match',      job ? Math.round((job.weight_skills||0.30)*100)    : 30],
                    ['cj-w-edu',  'Education',         job ? Math.round((job.weight_education||0.20)*100)  : 20],
                    ['cj-w-beh',  'Behavioral Fit',    job ? Math.round((job.weight_behavioral||0.10)*100) : 10],
                ].map(([id, label, val]) => `
                    <div style="margin-bottom:10px;">
                        <div style="display:flex;justify-content:space-between;font-size:12px;color:#374151;margin-bottom:4px;">
                            <span>${label}</span><span id="${id}-val" style="font-weight:600;">${val}%</span>
                        </div>
                        <input type="range" id="${id}" min="0" max="100" value="${val}"
                               oninput="document.getElementById('${id}-val').textContent=this.value+'%';_cjUpdateWeightsTotal()"
                               style="width:100%;accent-color:#1B2A4A;cursor:pointer;">
                    </div>`).join('')}
            </div>
        </div>`;

    createAdminModal(title, html, async () => {
        const payload = {
            company_id: companyId,
            title: (document.getElementById('cj-title')?.value || '').trim(),
            location: document.getElementById('cj-location')?.value || '',
            experience_years: parseInt(document.getElementById('cj-exp')?.value) || 0,
            salary_range: document.getElementById('cj-salary')?.value || '',
            education_level: document.getElementById('cj-edu')?.value || '',
            required_skills: document.getElementById('cj-skills')?.value || '',
            nice_to_have_skills: document.getElementById('cj-nice')?.value || '',
            behavioral_skills: document.getElementById('cj-behav')?.value || '',
            industry_experience: document.getElementById('cj-industry')?.value || '',
            description: document.getElementById('cj-desc')?.value || '',
            weight_experience: (parseInt(document.getElementById('cj-w-exp')?.value) || 40) / 100,
            weight_skills:     (parseInt(document.getElementById('cj-w-skl')?.value) || 30) / 100,
            weight_education:  (parseInt(document.getElementById('cj-w-edu')?.value) || 20) / 100,
            weight_behavioral: (parseInt(document.getElementById('cj-w-beh')?.value) || 10) / 100,
        };
        if (!payload.title) { showToast('Job Title is required', 'error'); return; }
        const total = Math.round((payload.weight_experience + payload.weight_skills + payload.weight_education + payload.weight_behavioral) * 100);
        if (total !== 100) { showToast(`Weights must total 100% (currently ${total}%)`, 'error'); return; }
        const token = localStorage.getItem('token');
        const url = job ? `/api/admin/jobs/${job.id}` : '/api/admin/jobs';
        const method = job ? 'PUT' : 'POST';
        const res = await fetch(url, {
            method, headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast(job ? 'Job updated' : 'Job posted', 'success');
            closeAdminModal();
            _coWsLoadJobs(window._coWorkspaceCo);
        } else {
            const err = await res.json().catch(() => ({}));
            showToast('Failed: ' + (err.detail || 'Unknown error'), 'error');
        }
    });
}

function _cjUpdateWeightsTotal() {
    const ids = ['cj-w-exp', 'cj-w-skl', 'cj-w-edu', 'cj-w-beh'];
    const total = ids.reduce((s, id) => s + (parseInt(document.getElementById(id)?.value) || 0), 0);
    const el = document.getElementById('cj-weights-total');
    if (!el) return;
    el.textContent = 'Total: ' + total + '%';
    el.style.background = total === 100 ? '#E1F5EE' : '#FCEBEB';
    el.style.color       = total === 100 ? '#0F6E56' : '#A32D2D';
}

function _coWsToggleAISection() {
    const sec = document.getElementById('cj-ai-section');
    if (sec) sec.style.display = sec.style.display === 'none' ? 'block' : 'none';
}

async function _coWsGenerateAI() {
    const title    = (document.getElementById('cj-title')?.value || '').trim();
    const industry = (document.getElementById('cj-ai-industry')?.value || '').trim();
    const context  = (document.getElementById('cj-ai-context')?.value || '').trim();
    if (!title)    { showToast('Enter a Job Title first', 'warning'); return; }
    if (!industry) { showToast('Enter Industry / Background', 'warning'); return; }
    const btn  = document.getElementById('cj-ai-btn');
    const load = document.getElementById('cj-ai-loading');
    if (btn)  btn.disabled = true;
    if (load) load.style.display = 'block';
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/ai/generate-job', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
            body: JSON.stringify({ job_title: title, industry_background: industry, additional_context: context })
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Generation failed'); }
        const data = await res.json();
        const set = (id, val) => { const el = document.getElementById(id); if (el && val) el.value = val; };
        set('cj-desc',     data.job_brief);
        set('cj-skills',   data.required_skills);
        set('cj-nice',     data.nice_to_have);
        set('cj-behav',    data.behavioral_skills);
        const sec = document.getElementById('cj-ai-section');
        if (sec) sec.style.display = 'none';
        showToast('AI filled Job Description and Skills', 'success');
    } catch (err) {
        showToast('AI generation failed: ' + err.message, 'error');
    } finally {
        if (btn)  btn.disabled = false;
        if (load) load.style.display = 'none';
    }
}

async function _coWsUploadJD(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const status = document.getElementById('cj-jd-status');
    if (status) status.style.display = 'block';
    input.value = '';
    try {
        const fd = new FormData();
        fd.append('file', file);
        const token = localStorage.getItem('token');
        const res = await fetch('/candidates/extract-jd', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token },
            body: fd
        });
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Extraction failed'); }
        const data = await res.json();
        const desc = document.getElementById('cj-desc');
        if (desc && data.text) desc.value = data.text;
        showToast('Job description extracted', 'success');
    } catch (err) {
        showToast('Upload failed: ' + err.message, 'error');
    } finally {
        if (status) status.style.display = 'none';
    }
}

async function _coWsDeleteJob(jobId, jobTitle) {
    createConfirmModal('Delete "' + jobTitle + '"?',
        'This will permanently delete the job and all its applications.',
        async () => {
            const token = localStorage.getItem('token');
            const res = await fetch('/api/admin/jobs/' + jobId, {
                method: 'DELETE', headers: { 'Authorization': 'Bearer ' + token }
            });
            if (res.ok) {
                showToast('Job deleted', 'success');
                closeAdminModal();
                _coWsLoadJobs(window._coWorkspaceCo);
            } else {
                showToast('Delete failed', 'error');
            }
        }
    );
}

async function openAdminJobPreview(jobId) {
    const res = await fetch(`/public/job/${jobId}`);
    if (!res.ok) { showToast('Job not found', 'error'); return; }
    const job = await res.json();

    document.getElementById('admin-job-preview-modal')?.remove();

    const skills = (job.required_skills || '').split(',').filter(s => s.trim()).map(s =>
        `<span style="background:#E8EAF6;color:#3949AB;padding:4px 12px;border-radius:20px;font-size:13px;display:inline-block;margin:3px">${escHtml(s.trim())}</span>`
    ).join('');

    const salaryText = job.hide_salary ? '' :
        (job.salary_min && job.salary_max
            ? `<span style="background:#E8F5E9;color:#2E7D32;padding:5px 14px;border-radius:20px;font-size:13px;font-weight:600;display:inline-block;margin-bottom:16px">💰 ${job.salary_min} – ${job.salary_max} EGP</span>`
            : (job.salary_range ? `<span style="background:#E8F5E9;color:#2E7D32;padding:5px 14px;border-radius:20px;font-size:13px;font-weight:600;display:inline-block;margin-bottom:16px">💰 ${escHtml(job.salary_range)}</span>` : ''));

    const modal = document.createElement('div');
    modal.id = 'admin-job-preview-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;';
    modal.innerHTML = `
    <div style="background:#fff;border-radius:16px;max-width:920px;width:100%;max-height:90vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.3)">
      <div style="background:#1B2A4A;padding:20px 28px;display:flex;align-items:center;gap:14px;flex-shrink:0;">
        <div style="width:44px;height:44px;border-radius:10px;background:#C9A84C;display:flex;align-items:center;justify-content:center;font-weight:700;color:#1B2A4A;font-size:18px;flex-shrink:0;">${escHtml((job.company_name||'C')[0].toUpperCase())}</div>
        <div style="flex:1;min-width:0;">
          <div style="color:#C9A84C;font-size:11px;font-weight:600;letter-spacing:1px;">${escHtml(job.company_name||'')}</div>
          <div style="color:#fff;font-size:18px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(job.job_title||'')}</div>
        </div>
        <button onclick="document.getElementById('admin-job-preview-modal').remove()" style="background:rgba(255,255,255,0.1);border:none;color:#fff;width:32px;height:32px;border-radius:50%;font-size:20px;cursor:pointer;flex-shrink:0;line-height:1;">×</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 280px;flex:1;overflow:hidden;min-height:0;">
        <div style="padding:24px 28px;overflow-y:auto;border-right:1px solid #f0f2f5;">
          <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px;">
            <span style="color:#555;font-size:13px;">📍 ${escHtml(job.job_location||'—')}</span>
            <span style="color:#555;font-size:13px;">💼 ${escHtml(job.employment_type||'Full-time')}</span>
            <span style="color:#555;font-size:13px;">⏱ ${job.min_experience||0}+ yrs exp</span>
          </div>
          ${salaryText}
          ${job.job_description ? `<div style="margin-bottom:20px;"><div style="font-size:10px;font-weight:700;letter-spacing:2px;color:#999;margin-bottom:10px;">ABOUT THE ROLE</div><p style="color:#333;font-size:14px;line-height:1.75;margin:0;white-space:pre-wrap;">${escHtml(job.job_description)}</p></div>` : ''}
          ${skills ? `<div><div style="font-size:10px;font-weight:700;letter-spacing:2px;color:#999;margin-bottom:10px;">REQUIRED SKILLS</div><div>${skills}</div></div>` : ''}
        </div>
        <div style="padding:24px 20px;display:flex;flex-direction:column;gap:12px;overflow-y:auto;">
          <div style="font-size:15px;font-weight:600;color:#1B2A4A;">Add Candidate</div>
          <div style="border:2px dashed #D1D5DB;border-radius:12px;padding:28px 16px;text-align:center;cursor:pointer;transition:border-color 0.2s;"
               onmouseover="this.style.borderColor='#C9A84C'" onmouseout="this.style.borderColor='#D1D5DB'"
               onclick="document.getElementById('admin-preview-file-${jobId}').click()">
            <div style="font-size:28px;margin-bottom:8px;">⬆</div>
            <div style="font-size:13px;font-weight:500;color:#333;">Drop CV here or click to browse</div>
            <div style="font-size:11px;color:#888;margin-top:4px;">PDF or DOCX</div>
          </div>
          <input type="file" id="admin-preview-file-${jobId}" accept=".pdf,.docx" style="display:none;"
                 onchange="handleAdminPreviewUpload(this.files[0],${jobId});document.getElementById('admin-job-preview-modal')?.remove();">
          <button onclick="document.getElementById('admin-preview-file-${jobId}').click()"
                  style="background:#1B2A4A;color:#C9A84C;border:none;border-radius:10px;padding:13px;font-size:14px;font-weight:600;cursor:pointer;width:100%;">
            + Add Candidate
          </button>
          <div style="font-size:11px;color:#888;text-align:center;">AI screening · 5–10 seconds</div>
          <hr style="border:none;border-top:1px solid #f0f2f5;margin:4px 0;">
          <a href="/apply.html?job_id=${jobId}" target="_blank"
             style="display:block;text-align:center;font-size:12px;color:#185FA5;text-decoration:none;">
            🔗 View public apply page
          </a>
        </div>
      </div>
    </div>`;

    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

async function handleAdminPreviewUpload(file, jobId) {
    if (!file) return;
    showToast('Screening CV…', 'info');
    const token = localStorage.getItem('token');
    try {
        const fd = new FormData();
        fd.append('file', file);
        if (jobId) fd.append('job_id', jobId);
        const res = await fetch('/candidates/screen-cv', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token },
            body: fd
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Screening failed');
        const score = data.score != null ? Math.round(data.score) : null;
        const scoreColor = score == null ? '#6B7280' : score >= 75 ? '#0F6E56' : score >= 50 ? '#854F0B' : '#A32D2D';
        showToast(`${data.name || 'Candidate'} screened — ${score != null ? score + '% · ' : ''}${data.decision || ''}`, 'success');
        if (typeof fetchData === 'function') fetchData();
        if (typeof renderCandidates === 'function') renderCandidates();
    } catch (err) {
        showToast('Screening failed: ' + err.message, 'error');
    }
}

function _coWsShareJob(jobId, jobTitle) {
    const url = window.location.origin + '/apply.html?job_id=' + jobId;
    const caption = encodeURIComponent(jobTitle + '\n' + url);
    createAdminModal('Share Job — ' + jobTitle,
        `<div style="display:flex;flex-direction:column;gap:10px;">
            <input value="${escHtml(url)}" readonly onclick="this.select()" style="width:100%;padding:9px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:12px;color:#1B2A4A;box-sizing:border-box;outline:none;">
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
                <button onclick="navigator.clipboard.writeText('${escHtml(url)}').then(()=>showToast('Link copied','success'))" style="flex:1;padding:10px;background:#1B2A4A;color:#C9A84C;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;">Copy Link</button>
                <a href="https://wa.me/?text=${caption}" target="_blank" style="flex:1;padding:10px;background:#25D366;color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none;display:block;">WhatsApp</a>
                <a href="https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}" target="_blank" style="flex:1;padding:10px;background:#0A66C2;color:#fff;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none;display:block;">LinkedIn</a>
            </div>
        </div>`,
        null
    );
}

// ── Admin Workspace: Candidate Actions ────────────────────────────────────────

async function _coWsMoveStage(appId, currentStage) {
    const stages = ['applied','screening','shortlisted','interview','offered','hired','rejected'];
    const opts = stages.map(s => `<option value="${s}" ${s===currentStage.toLowerCase()?'selected':''}>${s.charAt(0).toUpperCase()+s.slice(1)}</option>`).join('');
    createAdminModal('Move to Stage', `
        <div>
            <label style="font-size:12px;font-weight:500;color:#374151;display:block;margin-bottom:8px;">Select new stage</label>
            <select id="ws-stage-sel" style="width:100%;padding:10px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;background:#fff;outline:none;">${opts}</select>
        </div>`, async () => {
        const stage = document.getElementById('ws-stage-sel')?.value;
        if (!stage) return;
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/applications/' + appId + '/stage', {
            method: 'PATCH',
            headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage })
        });
        if (res.ok) {
            const data = await res.json();
            showToast('Moved to ' + data.stage, 'success');
            closeAdminModal();
            // Handle notifications
            (data.notifications || []).forEach(n => {
                if (n.to) {
                    const ml = 'mailto:' + encodeURIComponent(n.to) + '?subject=' + encodeURIComponent(n.subject||'') + '&body=' + encodeURIComponent(n.body||'');
                    window.open(ml, '_blank');
                }
            });
            _loadCoWsCandidates(window._coWorkspaceCo);
        } else {
            const err = await res.json().catch(() => ({}));
            showToast('Failed: ' + (err.detail || 'Unknown'), 'error');
        }
    });
}

async function _coWsViewCv(appId) {
    const token = localStorage.getItem('token');
    showToast('Downloading CV…', 'info');
    try {
        const res = await fetch('/api/admin/applications/' + appId + '/cv', {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!res.ok) throw new Error('CV not available');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'CV_' + appId + '.pdf';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('CV downloaded', 'success');
    } catch(e) {
        showToast('CV not available: ' + e.message, 'error');
    }
}

function _coWsViewReport(appId) {
    try {
        const apps = window._coWsCandidates || [];
        const app = apps.find(a => (a.application_id === appId) || (a.id === appId));
        if (!app) { showToast('Application not found in cache', 'error'); return; }
        const score = app.score != null ? app.score + '%' : 'N/A';
        const strengths = app.strengths || '—';
        const weaknesses = app.weaknesses || '—';
        const reason = app.reason || '—';
        createAdminModal('AI Evaluation — ' + escHtml(app.name || 'Candidate'), `
            <div style="display:flex;flex-direction:column;gap:14px;">
                <div style="display:flex;gap:16px;align-items:center;">
                    <div style="text-align:center;background:#F3F4F6;border-radius:10px;padding:14px 20px;">
                        <div style="font-size:26px;font-weight:700;color:#1B2A4A;">${escHtml(score)}</div>
                        <div style="font-size:11px;color:#9CA3AF;">AI Score</div>
                    </div>
                    <div style="flex:1;">
                        <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:4px;">Decision: ${escHtml(app.decision||'—')}</div>
                        <div style="font-size:12px;color:#6B7280;">${escHtml(reason)}</div>
                    </div>
                </div>
                <div><div style="font-size:11px;font-weight:600;color:#0F6E56;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Strengths</div><div style="font-size:12px;color:#374151;white-space:pre-wrap;">${escHtml(strengths)}</div></div>
                <div><div style="font-size:11px;font-weight:600;color:#DC2626;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Weaknesses</div><div style="font-size:12px;color:#374151;white-space:pre-wrap;">${escHtml(weaknesses)}</div></div>
            </div>`, null);
    } catch(e) {
        showToast('Could not load report: ' + e.message, 'error');
    }
}

async function editCompanyAdmin(companyId) {
    const c = (window._adminCompanies||[]).find(x => String(x.id) === String(companyId));
    if (!c) return;
    createAdminModal('Edit Company — ' + c.name, `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            ${[
                {id:'ec-name',   label:'Company Name',     val: c.name},
                {id:'ec-email',  label:'Company Email',    val: c.email},
                {id:'ec-web',    label:'Website',          val: c.website},
                {id:'ec-reg',    label:'Registration No.', val: c.registration_number},
                {id:'ec-aname',  label:'Admin Name',       val: c.admin_name},
                {id:'ec-aemail', label:'Admin Email',      val: c.admin_email},
                {id:'ec-apass',  label:'Reset Password',   val: '', ph:'Leave blank to keep current'},
            ].map(f=>`<div><label style="font-size:11px;font-weight:500;color:#555;display:block;margin-bottom:4px;">${f.label}</label>
                <input id="${f.id}" value="${(f.val||'').replace(/"/g,'&quot;')}" placeholder="${f.ph||''}"
                style="width:100%;padding:8px 10px;border:0.5px solid #E5E7EB;border-radius:7px;font-size:12px;color:#1B2A4A;outline:none;box-sizing:border-box;"></div>`).join('')}
            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#555;display:block;margin-bottom:4px;">Status</label>
                <select id="ec-status" style="width:100%;padding:8px 10px;border:0.5px solid #E5E7EB;border-radius:7px;font-size:12px;color:#1B2A4A;outline:none;background:#fff;">
                    <option value="approved" ${c.status==='approved'?'selected':''}>Approved</option>
                    <option value="pending" ${c.status==='pending'?'selected':''}>Pending</option>
                </select>
            </div>
        </div>`, async () => {
        const token = localStorage.getItem('token');
        const body = {
            name: document.getElementById('ec-name').value,
            email: document.getElementById('ec-email').value,
            website: document.getElementById('ec-web').value,
            registration_number: document.getElementById('ec-reg').value,
            admin_name: document.getElementById('ec-aname').value,
            admin_email: document.getElementById('ec-aemail').value,
            status: document.getElementById('ec-status').value,
        };
        const pw = document.getElementById('ec-apass').value;
        if (pw) body.new_password = pw;
        const res = await fetch(`/api/admin/companies/${companyId}`, {
            method:'PATCH', headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
            body: JSON.stringify(body)
        });
        if (res.ok) { showToast('Company updated','success'); closeAdminModal(); loadAdminCompanies(); loadAdminStats(); }
        else showToast('Update failed','error');
    });
}

async function toggleCompanyStatus(id, currentStatus) {
    const newStatus = currentStatus === 'approved' ? 'pending' : 'approved';
    const token = localStorage.getItem('token');
    const res = await fetch(`/api/admin/companies/${id}`, {
        method:'PATCH', headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        body: JSON.stringify({status: newStatus})
    });
    if (res.ok) { showToast('Company ' + newStatus,'success'); loadAdminCompanies(); loadAdminStats(); }
}

async function deleteCompanyAdmin(id, name) {
    createConfirmModal(`Delete ${name}?`,
        'This will permanently delete the company, all its jobs, candidates, and user account.',
        async () => {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/admin/companies/${id}`, {
                method:'DELETE', headers:{'Authorization':'Bearer '+token}
            });
            if (res.ok) { showToast('Company deleted','success'); closeAdminModal(); loadAdminCompanies(); loadAdminStats(); }
            else showToast('Delete failed','error');
        });
}

function viewCompanyAdmin(companyId) {
    const c = (window._adminCompanies||[]).find(x => String(x.id) === String(companyId));
    if (!c) return;
    createAdminModal('Company Details — ' + c.name, `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
            ${[
                ['Company Name', c.name], ['Email', c.email],
                ['Website', c.website], ['Reg. Number', c.registration_number],
                ['Status', c.status], ['Admin User', c.admin_name],
                ['Admin Email', c.admin_email], ['Total Jobs', c.job_count],
                ['Total Candidates', c.candidate_count],
                ['Registered', c.created_at ? new Date(c.created_at).toLocaleDateString('en-GB') : '—'],
            ].map(([label,val]) => `<div>
                <div style="font-size:10px;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:3px;">${label}</div>
                <div style="font-size:13px;color:#1B2A4A;">${val||'—'}</div></div>`).join('')}
        </div>`, null);
}

// ── Candidates CRUD ────────────────────────────────────────────
async function loadAdminCandidates() {
    const view = document.getElementById('candidates-view');
    if (view) { /* candidates-view is also used by employer; admin loads all */ }
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/candidates/full', {
            headers:{'Authorization':'Bearer '+token}, cache:'no-store'
        });
        const cands = await res.json();
        window._adminCandidates = cands;
        return cands;
    } catch(e) { console.error('loadAdminCandidates', e); return []; }
}

async function editCandidateAdmin(candidateId) {
    const cands = window._adminCandidates || await loadAdminCandidates();
    const c = cands.find(x => String(x.id) === String(candidateId));
    if (!c) return;
    createAdminModal('Edit Candidate — ' + c.name, `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            ${[
                {id:'ecand-name',   label:'Name',         val: c.name},
                {id:'ecand-email',  label:'Email',        val: c.email},
                {id:'ecand-phone',  label:'Phone',        val: c.phone},
                {id:'ecand-title',  label:'Last Title',   val: c.last_title},
                {id:'ecand-emp',    label:'Last Employer',val: c.last_employer},
                {id:'ecand-score',  label:'Score (0-100)',val: c.score},
            ].map(f=>`<div><label style="font-size:11px;font-weight:500;color:#555;display:block;margin-bottom:4px;">${f.label}</label>
                <input id="${f.id}" value="${(f.val||'').toString().replace(/"/g,'&quot;')}"
                style="width:100%;padding:8px 10px;border:0.5px solid #E5E7EB;border-radius:7px;font-size:12px;color:#1B2A4A;outline:none;box-sizing:border-box;"></div>`).join('')}
            <div style="grid-column:span 2;">
                <label style="font-size:11px;font-weight:500;color:#555;display:block;margin-bottom:4px;">Decision</label>
                <select id="ecand-decision" style="width:100%;padding:8px 10px;border:0.5px solid #E5E7EB;border-radius:7px;font-size:12px;color:#1B2A4A;outline:none;background:#fff;">
                    <option ${c.decision==='Shortlist'?'selected':''}>Shortlist</option>
                    <option ${c.decision==='Maybe'?'selected':''}>Maybe</option>
                    <option ${c.decision==='Reject'?'selected':''}>Reject</option>
                    <option ${c.decision==='Pending'?'selected':''}>Pending</option>
                </select>
            </div>
        </div>`, async () => {
        const token = localStorage.getItem('token');
        const body = {
            name: document.getElementById('ecand-name').value,
            email: document.getElementById('ecand-email').value,
            phone: document.getElementById('ecand-phone').value,
            last_title: document.getElementById('ecand-title').value,
            last_employer: document.getElementById('ecand-emp').value,
            score: parseFloat(document.getElementById('ecand-score').value) || 0,
            decision: document.getElementById('ecand-decision').value,
        };
        const res = await fetch(`/api/admin/candidates/${candidateId}`, {
            method:'PATCH', headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
            body: JSON.stringify(body)
        });
        if (res.ok) { showToast('Candidate updated','success'); closeAdminModal(); window._adminCandidates = null; }
        else showToast('Update failed','error');
    });
}

async function deleteCandidateAdmin(id, name) {
    createConfirmModal(`Delete ${name}?`, 'This will permanently delete the candidate and their evaluation.',
        async () => {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/admin/candidates/${id}`, {
                method:'DELETE', headers:{'Authorization':'Bearer '+token}
            });
            if (res.ok) { showToast('Candidate deleted','success'); closeAdminModal(); loadAdminStats(); fetchData(); }
            else showToast('Delete failed','error');
        });
}

// ── Users CRUD ─────────────────────────────────────────────────
async function loadAdminUsers() {
    const view = document.getElementById('subscribers-view');
    if (view) view.innerHTML = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading…</div>';
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/users/full', {
            headers:{'Authorization':'Bearer '+token}, cache:'no-store'
        });
        const users = await res.json();
        window._adminUsers = users;
        renderAdminUsersTable(users);
    } catch(e) { console.error('loadAdminUsers', e); }
}

function _userTypeBadge(type) {
    const cfg = {
        admin:     {bg:'#1B2A4A',color:'#fff'},
        company:   {bg:'#C9A84C',color:'#1B2A4A'},
        candidate: {bg:'#E6F1FB',color:'#185FA5'},
    };
    const c = cfg[type] || cfg.candidate;
    return `<span style="background:${c.bg};color:${c.color};border-radius:20px;padding:2px 10px;font-size:11px;font-weight:500;">${type}</span>`;
}

function renderAdminUsersTable(users) {
    const view = document.getElementById('subscribers-view');
    if (!view) return;
    view.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
            <div style="font-size:15px;font-weight:500;color:#1B2A4A;">Users (${users.length})</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <input id="user-search" placeholder="Search…" oninput="filterAdminUsers(this.value)"
                    style="padding:7px 12px;border:0.5px solid #E5E7EB;border-radius:8px;font-size:12px;outline:none;width:190px;">
                <select id="user-type-filter" onchange="filterAdminUsersByType(this.value)"
                    style="padding:7px 12px;border:0.5px solid #E5E7EB;border-radius:8px;font-size:12px;outline:none;background:#fff;color:#1B2A4A;">
                    <option value="">All Types</option>
                    <option value="admin">Admin</option>
                    <option value="company">Company</option>
                    <option value="candidate">Candidate</option>
                </select>
                <button onclick="exportAdminData('users')"
                    style="background:#fff;border:0.5px solid #E5E7EB;border-radius:8px;padding:7px 14px;font-size:12px;color:#6B7280;cursor:pointer;">⬇ Export</button>
            </div>
        </div>
        <div style="background:#fff;border-radius:12px;border:0.5px solid rgba(0,0,0,0.06);overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
            <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:800px;">
                    <thead>
                        <tr style="background:#1B2A4A;">
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Name</th>
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Email</th>
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Type</th>
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Company</th>
                            <th style="padding:10px 14px;text-align:center;color:#fff;font-size:11px;font-weight:500;">Active</th>
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="admin-users-tbody">
                        ${users.map((u,i)=>`
                        <tr style="background:${i%2===0?'#fff':'#fafbfc'};border-bottom:0.5px solid #F3F4F6;"
                            data-id="${u.id}" data-type="${u.user_type}"
                            data-search="${(u.full_name+u.email+(u.company_name||'')).toLowerCase()}">
                            <td style="padding:10px 14px;font-weight:500;color:#1B2A4A;">${u.full_name||'—'}</td>
                            <td style="padding:10px 14px;color:#6B7280;">${u.email}</td>
                            <td style="padding:10px 14px;">${_userTypeBadge(u.user_type)}</td>
                            <td style="padding:10px 14px;color:#6B7280;">${u.company_name||'—'}</td>
                            <td style="padding:10px 14px;text-align:center;">
                                <span style="background:${u.is_active?'#E1F5EE':'#F3F4F6'};color:${u.is_active?'#0F6E56':'#9CA3AF'};border-radius:20px;padding:2px 10px;font-size:11px;font-weight:500;">${u.is_active?'Active':'Inactive'}</span>
                            </td>
                            <td style="padding:10px 14px;">
                                <div style="display:flex;gap:4px;flex-wrap:wrap;">
                                    <button onclick="editUserAdmin('${u.id}')" style="background:#1B2A4A;color:#fff;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;">Edit</button>
                                    ${u.candidate_id ? `<button onclick="adminViewCandidateProfile(${u.candidate_id},'${escHtml(u.full_name||u.email).replace(/'/g,"&#39;")}')" style="background:#E6F1FB;color:#185FA5;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;">Profile</button>` : ''}
                                    ${u.has_cv && u.candidate_id ? `<button onclick="adminDownloadCandidateCv(${u.candidate_id},'${escHtml(u.full_name||u.email).replace(/'/g,"&#39;")}')" style="background:#E1F5EE;color:#0F6E56;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;">CV</button>` : ''}
                                    <button onclick="toggleUserActive('${u.id}',${u.is_active})" style="background:${u.is_active?'#FAEEDA':'#E1F5EE'};color:${u.is_active?'#854F0B':'#0F6E56'};border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;">${u.is_active?'Deactivate':'Activate'}</button>
                                    <button onclick="deleteUserAdmin('${u.id}','${(u.full_name||u.email).replace(/'/g,"\\'")}')" style="background:#FCEBEB;color:#A32D2D;border:none;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;">Delete</button>
                                </div>
                            </td>
                        </tr>`).join('')}
                    </tbody>
                </table>
            </div>
        </div>`;
}

function filterAdminUsers(q) {
    document.querySelectorAll('#admin-users-tbody tr').forEach(row => {
        row.style.display = !q || (row.dataset.search||'').includes(q.toLowerCase()) ? '' : 'none';
    });
}

function filterAdminUsersByType(type) {
    document.querySelectorAll('#admin-users-tbody tr').forEach(row => {
        row.style.display = !type || row.dataset.type === type ? '' : 'none';
    });
}

async function adminViewCandidateProfile(candidateId, name) {
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/candidate/' + candidateId + '/profile', {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!res.ok) throw new Error('Profile not found');
        const p = await res.json();

        const infoRows = [
            ['Email', p.email], ['Phone', p.phone], ['Location', p.location],
            ['Title', p.last_title], ['Employer', p.last_employer],
            ['Experience', p.experience_years != null ? p.experience_years + ' yrs' : null],
            ['Skills', p.skills], ['Expected Salary', p.expected_salary],
        ].filter(([,v]) => v).map(([l,v]) =>
            `<div><span style="font-size:10px;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.05em;">${l}</span><div style="font-size:12px;color:#1B2A4A;margin-top:2px;">${escHtml(String(v))}</div></div>`
        ).join('');

        const appRows = (p.applications || []).map(a => {
            const score = a.score != null ? Math.round(a.score > 1 ? (a.score > 10 ? a.score : a.score*10) : a.score*100) : null;
            return `<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:0.5px solid #F3F4F6;">
                <div style="font-size:12px;color:#1B2A4A;">${escHtml(a.job_title||'Unknown Job')}</div>
                <div style="display:flex;gap:8px;align-items:center;">
                    ${score!=null?`<span style="font-size:11px;font-weight:600;color:#1B2A4A;">${score}%</span>`:''}
                    <span style="font-size:10px;padding:2px 8px;border-radius:8px;background:#F3F4F6;color:#374151;">${escHtml(a.stage||'')}</span>
                </div>
            </div>`;
        }).join('') || '<div style="font-size:12px;color:#9CA3AF;padding:8px 0;">No applications yet</div>';

        const summaryBlock = p.summary
            ? `<div style="background:#F9FAFB;border-radius:8px;padding:12px;font-size:12px;color:#374151;line-height:1.6;margin-bottom:14px;">${escHtml(p.summary)}</div>` : '';

        createAdminModal('Candidate Profile — ' + escHtml(p.name || name), `
            <div style="display:flex;flex-direction:column;gap:16px;">
                <div style="display:flex;align-items:center;gap:14px;">
                    ${p.photo_url ? `<img src="${escHtml(p.photo_url)}" style="width:56px;height:56px;border-radius:50%;object-fit:cover;flex-shrink:0;">` :
                    `<div style="width:56px;height:56px;border-radius:50%;background:#1B2A4A;color:#C9A84C;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;flex-shrink:0;">${escHtml((p.name||'?').split(' ').slice(0,2).map(w=>w[0]||'').join('').toUpperCase())}</div>`}
                    <div>
                        <div style="font-size:15px;font-weight:700;color:#1B2A4A;">${escHtml(p.name||'')}</div>
                        <div style="font-size:12px;color:#9CA3AF;">${escHtml(p.email||'')}</div>
                    </div>
                </div>
                ${summaryBlock}
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">${infoRows}</div>
                ${p.education ? `<div><div style="font-size:10px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Education</div><div style="font-size:12px;color:#374151;">${escHtml(p.education)}</div></div>` : ''}
                <div>
                    <div style="font-size:10px;font-weight:600;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;">Applications (${(p.applications||[]).length})</div>
                    ${appRows}
                </div>
                ${p.has_cv !== false ? `<div style="padding-top:4px;"><button onclick="adminDownloadCandidateCv(${candidateId},'${escHtml(p.name||name).replace(/'/g,'&#39;')}')" style="background:#1B2A4A;color:#C9A84C;border:none;border-radius:8px;padding:9px 18px;font-size:12px;font-weight:600;cursor:pointer;">↓ Download CV</button></div>` : ''}
            </div>`, null);
    } catch(e) {
        showToast('Could not load profile: ' + e.message, 'error');
    }
}

async function adminDownloadCandidateCv(candidateId, name) {
    const token = localStorage.getItem('token');
    showToast('Downloading CV…', 'info');
    try {
        const res = await fetch('/api/candidates/' + candidateId + '/cv', {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!res.ok) throw new Error('CV not available');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const safeName = (name || 'Candidate').replace(/[^a-zA-Z0-9 _-]/g, '').trim().replace(/\s+/g, '_');
        a.href = url; a.download = safeName + '_CV.pdf';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('CV downloaded', 'success');
    } catch(e) {
        showToast('CV not available: ' + e.message, 'error');
    }
}

async function editUserAdmin(userId) {
    const u = (window._adminUsers||[]).find(x => String(x.id) === String(userId));
    if (!u) return;
    createAdminModal('Edit User — ' + (u.full_name||u.email), `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            ${[
                {id:'eu-name',  label:'Full Name', val: u.full_name},
                {id:'eu-email', label:'Email',     val: u.email},
                {id:'eu-pass',  label:'New Password (leave blank to keep)', val:'', ph:'Enter new password…'},
            ].map(f=>`<div style="grid-column:span ${f.id==='eu-pass'?2:1}"><label style="font-size:11px;font-weight:500;color:#555;display:block;margin-bottom:4px;">${f.label}</label>
                <input id="${f.id}" value="${(f.val||'').replace(/"/g,'&quot;')}" placeholder="${f.ph||''}"
                ${f.id==='eu-pass'?'type="password"':''}
                style="width:100%;padding:8px 10px;border:0.5px solid #E5E7EB;border-radius:7px;font-size:12px;color:#1B2A4A;outline:none;box-sizing:border-box;"></div>`).join('')}
        </div>`, async () => {
        const token = localStorage.getItem('token');
        const body = {
            full_name: document.getElementById('eu-name').value,
            email: document.getElementById('eu-email').value,
        };
        const pw = document.getElementById('eu-pass').value;
        if (pw) body.new_password = pw;
        const res = await fetch(`/api/admin/users/${userId}`, {
            method:'PATCH', headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
            body: JSON.stringify(body)
        });
        if (res.ok) { showToast('User updated','success'); closeAdminModal(); loadAdminUsers(); }
        else showToast('Update failed','error');
    });
}

async function toggleUserActive(id, isActive) {
    const token = localStorage.getItem('token');
    const res = await fetch(`/api/admin/users/${id}`, {
        method:'PATCH', headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        body: JSON.stringify({is_active: !isActive})
    });
    if (res.ok) { showToast('User ' + (!isActive?'activated':'deactivated'),'success'); loadAdminUsers(); loadAdminStats(); }
}

async function deleteUserAdmin(id, name) {
    createConfirmModal(`Delete ${name}?`, 'This will permanently delete this user account.',
        async () => {
            const token = localStorage.getItem('token');
            const res = await fetch(`/api/admin/users/${id}`, {
                method:'DELETE', headers:{'Authorization':'Bearer '+token}
            });
            if (res.ok) { showToast('User deleted','success'); closeAdminModal(); loadAdminUsers(); loadAdminStats(); }
            else { const d = await res.json(); showToast(d.detail||'Delete failed','error'); }
        });
}

// ── Analytics ──────────────────────────────────────────────────
async function loadAdminAnalytics() {
    const view = document.getElementById('analytics-view');
    if (view) view.innerHTML = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading…</div>';
    try {
        const token = localStorage.getItem('token');
        const res = await fetch('/api/admin/analytics', {
            headers:{'Authorization':'Bearer '+token}, cache:'no-store'
        });
        const data = await res.json();
        renderAdminAnalytics(data);
    } catch(e) { console.error('loadAdminAnalytics', e); }
}

function renderAdminAnalytics(d) {
    const view = document.getElementById('analytics-view');
    if (!view) return;

    const overviewCards = [
        {label:'Total Companies',  val: d.total_companies,  sub: d.approved_companies + ' approved'},
        {label:'Total Jobs',       val: d.total_jobs,       sub: d.approved_jobs + ' approved'},
        {label:'Total Candidates', val: d.total_candidates, sub:''},
        {label:'Active Users',     val: d.active_users,     sub: d.total_users + ' total'},
    ];

    const maxDecision = Math.max(...(d.candidates_by_decision||[]).map(x=>x.count), 1);
    const decisionBars = (d.candidates_by_decision||[]).map(x => {
        const pct = Math.round((x.count / maxDecision) * 100);
        const color = x.stage==='Shortlist'?'#0F6E56':x.stage==='Maybe'?'#854F0B':x.stage==='Reject'?'#A32D2D':'#6B7280';
        return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
            <div style="width:90px;font-size:12px;color:#1B2A4A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${x.stage}</div>
            <div style="flex:1;background:#F3F4F6;border-radius:4px;height:10px;">
                <div style="width:${pct}%;background:${color};border-radius:4px;height:10px;transition:width 0.4s;"></div>
            </div>
            <div style="width:28px;text-align:right;font-size:12px;font-weight:500;color:${color};">${x.count}</div>
        </div>`;
    }).join('') || '<div style="font-size:12px;color:#9CA3AF;">No evaluation data yet</div>';

    const topRows = (d.top_companies||[]).map((c,i) => `
        <tr style="background:${i%2===0?'#fff':'#fafbfc'};border-bottom:0.5px solid #F3F4F6;">
            <td style="padding:10px 14px;font-weight:500;color:#1B2A4A;">${c.name}</td>
            <td style="padding:10px 14px;text-align:center;color:#1B2A4A;font-weight:500;">${c.job_count}</td>
            <td style="padding:10px 14px;text-align:center;color:#1B2A4A;font-weight:500;">${c.candidate_count}</td>
            <td style="padding:10px 14px;text-align:center;">
                <span style="background:${c.avg_score>=75?'#E1F5EE':c.avg_score>=50?'#FAEEDA':'#F3F4F6'};color:${c.avg_score>=75?'#0F6E56':c.avg_score>=50?'#854F0B':'#9CA3AF'};border-radius:20px;padding:2px 10px;font-size:11px;font-weight:500;">${c.avg_score}%</span>
            </td>
            <td style="padding:10px 14px;text-align:center;color:#0F6E56;font-weight:500;">${c.shortlisted_count}</td>
            <td style="padding:10px 14px;">
                <span style="background:${c.status==='approved'?'#E1F5EE':'#FAEEDA'};color:${c.status==='approved'?'#0F6E56':'#854F0B'};border-radius:20px;padding:2px 8px;font-size:11px;font-weight:500;">${c.status}</span>
            </td>
        </tr>`).join('') || `<tr><td colspan="6" style="padding:24px;text-align:center;color:#9CA3AF;font-size:12px;">No companies yet</td></tr>`;

    view.innerHTML = `
        <div style="font-size:15px;font-weight:500;color:#1B2A4A;margin-bottom:16px;">Analytics Overview</div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px;">
            ${overviewCards.map(c=>`
            <div style="background:#fff;border:1px solid #E5E7EB;border-left:3px solid #C9A84C;border-radius:10px;padding:18px 20px;">
                <div style="font-size:11px;font-weight:600;color:#6B7280;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:4px;">${c.label}</div>
                <div style="font-size:32px;font-weight:600;color:#1B2A4A;line-height:1;">${c.val}</div>
                ${c.sub?`<div style="font-size:12px;color:#9CA3AF;margin-top:4px;">${c.sub}</div>`:''}
            </div>`).join('')}
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px;">
            <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px;">
                <div style="font-size:13px;font-weight:500;color:#1B2A4A;margin-bottom:14px;">Candidates by Decision</div>
                ${decisionBars}
            </div>
            <div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px;">
                <div style="font-size:13px;font-weight:500;color:#1B2A4A;margin-bottom:14px;">Quick Stats</div>
                ${[
                    ['Approved Companies', d.approved_companies, d.total_companies],
                    ['Approved Jobs', d.approved_jobs, d.total_jobs],
                    ['Active Users', d.active_users, d.total_users],
                ].map(([label,val,total]) => {
                    const pct = total ? Math.round((val/total)*100) : 0;
                    return `<div style="margin-bottom:12px;">
                        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;">
                            <span style="color:#1B2A4A;">${label}</span>
                            <span style="color:#6B7280;">${val} / ${total}</span>
                        </div>
                        <div style="background:#F3F4F6;border-radius:4px;height:8px;">
                            <div style="width:${pct}%;background:#1B2A4A;border-radius:4px;height:8px;transition:width 0.4s;"></div>
                        </div>
                    </div>`;
                }).join('')}
            </div>
        </div>
        <div style="font-size:13px;font-weight:500;color:#1B2A4A;margin-bottom:12px;">Top Companies</div>
        <div style="background:#fff;border-radius:12px;border:0.5px solid rgba(0,0,0,0.06);overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
            <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:600px;">
                    <thead>
                        <tr style="background:#1B2A4A;">
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Company</th>
                            <th style="padding:10px 14px;text-align:center;color:#fff;font-size:11px;font-weight:500;">Jobs</th>
                            <th style="padding:10px 14px;text-align:center;color:#fff;font-size:11px;font-weight:500;">Candidates</th>
                            <th style="padding:10px 14px;text-align:center;color:#fff;font-size:11px;font-weight:500;">Avg Score</th>
                            <th style="padding:10px 14px;text-align:center;color:#fff;font-size:11px;font-weight:500;">Shortlisted</th>
                            <th style="padding:10px 14px;text-align:left;color:#fff;font-size:11px;font-weight:500;">Status</th>
                        </tr>
                    </thead>
                    <tbody>${topRows}</tbody>
                </table>
            </div>
        </div>`;
}

// ═══════════════════════════════════════════════════════════════
// PHASE 9 — INTERVIEW SCHEDULING
// ═══════════════════════════════════════════════════════════════

function downloadIcs(content, filename) {
    let text;
    try {
        text = atob(content);
    } catch (e) {
        text = content;
    }
    const blob = new Blob([text], { type: 'text/calendar' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = filename || 'interview.ics'; a.click();
    URL.revokeObjectURL(url);
}

function _downloadIvIcs(ivId) {
    const iv = (_ivTableData || []).find(i => i.id === ivId);
    if (!iv || !iv.ics_file) { showToast('No .ics file available for this interview.', 'info'); return; }
    downloadIcs(iv.ics_file.content, iv.ics_file.filename);
}

function _buildInterviewWhatsApp(iv, candName, jobTitle, company, phone) {
    const dt = new Date(iv.interview_date + 'T00:00:00');
    const dateStr = dt.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    let text =
        'Dear ' + candName + ',\n\n' +
        "We'd like to invite you for an interview:\n\n" +
        '*Position:* ' + jobTitle + (company ? ' at ' + company : '') + '\n' +
        '*Date:* ' + dateStr + '\n' +
        '*Time:* ' + (iv.interview_time || '') + '\n' +
        '*Duration:* ' + (iv.duration_minutes || 60) + ' minutes\n' +
        '*Location:* ' + (iv.location_value || 'TBD') + '\n';
    if (iv.interviewer_names) text += '*Interviewer(s):* ' + iv.interviewer_names + '\n';
    if (iv.notes_for_candidate) text += '\n' + iv.notes_for_candidate + '\n';
    text += '\nPlease confirm your attendance.\n\nHunters HR Team\n01111176767';
    const cleanPhone = (phone || '').replace(/[^0-9]/g, '');
    const intlPhone = cleanPhone.startsWith('0') ? '2' + cleanPhone : cleanPhone;
    return (intlPhone ? 'https://wa.me/' + intlPhone : 'https://wa.me/') + '?text=' + encodeURIComponent(text);
}

function openScheduleInterviewModal(appId, candName, existingIv) {
    const app = (typeof applications !== 'undefined' ? applications : []).find(a => a.application_id === appId) || {};
    const today = new Date().toISOString().split('T')[0];
    const jobLine = escHtml((app.job_title || '—') + (app.company_name ? ' at ' + app.company_name : ''));
    const isEdit = !!existingIv;
    const ivId = isEdit ? existingIv.id : null;
    const prefill = isEdit ? existingIv : {};
    const locType = prefill.location_type || 'physical';
    const locPlaceholder = locType === 'online' ? 'Paste meeting link (Zoom/Meet/Teams)' : 'Enter address or room';

    document.getElementById('schedule-interview-modal')?.remove();

    const durationOpts = ['30','45','60','90','120'].map(v =>
        '<option value="' + v + '"' + ((prefill.duration_minutes || 60) == v ? ' selected' : '') + '>' + v + ' minutes</option>'
    ).join('');

    const modal = document.createElement('div');
    modal.id = 'schedule-interview-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:10002;display:flex;align-items:flex-start;justify-content:center;padding:24px;overflow-y:auto;';
    modal.innerHTML =
        '<div style="background:#fff;border-radius:20px;width:600px;max-width:calc(100vw - 48px);box-shadow:0 24px 64px rgba(0,0,0,0.25);overflow:hidden;margin:auto;">' +
        '<div style="background:#1B2A4A;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;">' +
            '<div><div style="color:#fff;font-size:16px;font-weight:600;">' + (isEdit ? 'Edit Interview' : 'Schedule Interview') + ' — ' + escHtml(candName) + '</div>' +
            '<div style="color:#C9A84C;font-size:12px;margin-top:2px;">' + jobLine + '</div></div>' +
            '<button onclick="document.getElementById(\'schedule-interview-modal\').remove()" style="color:#fff;background:rgba(255,255,255,0.15);border:none;border-radius:50%;width:30px;height:30px;cursor:pointer;font-size:18px;line-height:1;">\xd7</button>' +
        '</div>' +
        '<div style="padding:24px;max-height:calc(100vh - 180px);overflow-y:auto;">' +
            '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px;">' +
                '<div><label style="display:block;font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Date *</label>' +
                '<input id="iv-date" type="date" min="' + today + '" value="' + (prefill.interview_date || '') + '" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;box-sizing:border-box;"></div>' +
                '<div><label style="display:block;font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Time *</label>' +
                '<input id="iv-time" type="time" value="' + (prefill.interview_time || '') + '" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;box-sizing:border-box;"></div>' +
                '<div><label style="display:block;font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Duration</label>' +
                '<select id="iv-duration" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;background:#fff;box-sizing:border-box;">' + durationOpts + '</select></div>' +
            '</div>' +
            '<div style="margin-bottom:16px;">' +
                '<label style="display:block;font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:8px;">Location Type *</label>' +
                '<div style="display:flex;gap:16px;margin-bottom:10px;">' +
                    '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#374151;"><input type="radio" name="iv-loc-type" value="physical"' + (locType === 'physical' ? ' checked' : '') + ' onchange="_ivLocTypeChange(this.value)" style="accent-color:#1B2A4A;"> Physical location</label>' +
                    '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:#374151;"><input type="radio" name="iv-loc-type" value="online"' + (locType === 'online' ? ' checked' : '') + ' onchange="_ivLocTypeChange(this.value)" style="accent-color:#1B2A4A;"> Online meeting</label>' +
                '</div>' +
                '<input id="iv-location-value" type="text" value="' + escHtml(prefill.location_value || '') + '" placeholder="' + locPlaceholder + '" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;box-sizing:border-box;">' +
            '</div>' +
            '<div style="margin-bottom:16px;">' +
                '<label style="display:block;font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Interviewer(s)</label>' +
                '<input id="iv-interviewers" type="text" value="' + escHtml(prefill.interviewer_names || '') + '" placeholder="e.g. Ahmed Hassan, Sara Mohamed" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;box-sizing:border-box;">' +
                '<div style="font-size:11px;color:#9CA3AF;margin-top:4px;">Separate multiple names with commas</div>' +
            '</div>' +
            '<div style="margin-bottom:16px;">' +
                '<label style="display:block;font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Notes for Candidate</label>' +
                '<textarea id="iv-notes-cand" rows="3" placeholder="Any instructions for the candidate (what to bring, dress code, etc.)" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;resize:vertical;box-sizing:border-box;">' + escHtml(prefill.notes_for_candidate || '') + '</textarea>' +
                '<div style="font-size:11px;color:#9CA3AF;margin-top:4px;">Candidate will see this in their portal</div>' +
            '</div>' +
            '<div>' +
                '<button type="button" onclick="_ivToggleInternal()" style="font-size:12px;color:#6B7280;background:none;border:none;cursor:pointer;padding:0;display:flex;align-items:center;gap:4px;">' +
                '<span id="iv-internal-arrow">' + (prefill.internal_notes ? '▼' : '▶') + '</span> Internal notes (admin only)</button>' +
                '<div id="iv-internal-section" style="display:' + (prefill.internal_notes ? 'block' : 'none') + ';margin-top:8px;">' +
                '<textarea id="iv-notes-internal" rows="2" placeholder="Notes for the Hunters team only" style="width:100%;padding:10px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;resize:vertical;box-sizing:border-box;">' + escHtml(prefill.internal_notes || '') + '</textarea>' +
                '<div style="font-size:11px;color:#9CA3AF;margin-top:4px;">Not visible to the candidate</div>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '<div style="padding:16px 24px;border-top:1px solid #F3F4F6;display:flex;gap:10px;justify-content:flex-end;">' +
            '<button onclick="document.getElementById(\'schedule-interview-modal\').remove()" style="padding:10px 20px;border:1px solid #E5E7EB;border-radius:8px;background:#F4F5FA;color:#1B2A4A;font-size:13px;cursor:pointer;">Cancel</button>' +
            '<button id="iv-submit-btn" onclick="' + (isEdit ? '_updateInterview(' + ivId + ',' + appId + ')' : '_submitScheduleInterview(' + appId + ')') + '" style="padding:10px 24px;border:none;border-radius:8px;background:#1B2A4A;color:#C9A84C;font-size:13px;font-weight:600;cursor:pointer;">' + (isEdit ? 'Save Changes ▶' : 'Schedule &amp; Notify ▶') + '</button>' +
        '</div>' +
        '</div>';
    document.body.appendChild(modal);
}

function _ivLocTypeChange(type) {
    const input = document.getElementById('iv-location-value');
    if (input) input.placeholder = type === 'online' ? 'Paste meeting link (Zoom/Meet/Teams)' : 'Enter address or room';
}

function _ivToggleInternal() {
    const sec = document.getElementById('iv-internal-section');
    const arrow = document.getElementById('iv-internal-arrow');
    if (!sec || !arrow) return;
    const open = sec.style.display === 'none';
    sec.style.display = open ? 'block' : 'none';
    arrow.textContent = open ? '▼' : '▶';
}

function _ivCollectForm(appId) {
    const dateEl = document.getElementById('iv-date');
    const timeEl = document.getElementById('iv-time');
    const locEl  = document.getElementById('iv-location-value');
    [dateEl, timeEl, locEl].forEach(el => { if (el) el.style.borderColor = '#E5E7EB'; });

    const ivDate   = dateEl?.value;
    const ivTime   = timeEl?.value;
    const locType  = document.querySelector('input[name="iv-loc-type"]:checked')?.value || 'physical';
    const locVal   = (locEl?.value || '').trim();
    const duration = parseInt(document.getElementById('iv-duration')?.value || '60');
    const interviewers = (document.getElementById('iv-interviewers')?.value || '').trim();
    const notesCand    = (document.getElementById('iv-notes-cand')?.value || '').trim();
    const notesInt     = (document.getElementById('iv-notes-internal')?.value || '').trim();
    const today = new Date().toISOString().split('T')[0];

    function _ivErr(el, msg) {
        if (el) { el.style.borderColor = '#DC2626'; el.focus(); }
        showToast(msg, 'error');
    }
    if (!ivDate) { _ivErr(dateEl, 'Please select an interview date'); return null; }
    if (ivDate < today) { _ivErr(dateEl, 'Interview date must be today or future'); return null; }
    if (!ivTime) { _ivErr(timeEl, 'Please select an interview time'); return null; }
    if (!locVal) { _ivErr(locEl, 'Please enter a location or meeting link'); return null; }
    return {
        application_id: appId,
        interview_date: ivDate,
        interview_time: ivTime,
        duration_minutes: duration,
        location_type: locType,
        location_value: locVal,
        interviewer_names: interviewers || null,
        notes_for_candidate: notesCand || null,
        internal_notes: notesInt || null,
    };
}

async function _submitScheduleInterview(appId) {
    const payload = _ivCollectForm(appId);
    if (!payload) return;
    const btn = document.getElementById('iv-submit-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Scheduling…'; }
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/interviews', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Scheduling failed', 'error');
            if (btn) { btn.disabled = false; btn.innerHTML = 'Schedule &amp; Notify ▶'; }
            return;
        }
        const data = await res.json();
        const apps = typeof applications !== 'undefined' ? applications : [];
        const idx = apps.findIndex(a => a.application_id === appId);
        if (idx >= 0) apps[idx].stage = 'Interview';
        document.getElementById('schedule-interview-modal')?.remove();
        if (typeof renderCandidates === 'function') renderCandidates();
        showToast('Interview scheduled', 'success');
        setTimeout(() => _showInterviewDispatchModal(data, 'schedule'), 300);
    } catch(e) {
        showToast('Scheduling failed', 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = 'Schedule &amp; Notify ▶'; }
    }
}

async function _updateInterview(interviewId, appId) {
    const payload = _ivCollectForm(appId);
    if (!payload) return;
    delete payload.application_id;
    const btn = document.getElementById('iv-submit-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Saving…'; }
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/interviews/' + interviewId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.detail || 'Update failed', 'error');
            if (btn) { btn.disabled = false; btn.innerHTML = 'Save Changes ▶'; }
            return;
        }
        const data = await res.json();
        document.getElementById('schedule-interview-modal')?.remove();
        showToast('Interview updated', 'success');
        setTimeout(() => { _showInterviewDispatchModal(data, 'reschedule'); if (typeof loadInterviewsTable === 'function') loadInterviewsTable(); }, 300);
    } catch(e) {
        showToast('Update failed', 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = 'Save Changes ▶'; }
    }
}

function _showInterviewDispatchModal(data, mode) {
    const iv     = data.interview || {};
    const notifs = data.notifications || [];
    const ics    = data.ics_files    || [];
    const apps   = typeof applications !== 'undefined' ? applications : [];
    const app    = apps.find(a => a.application_id === iv.application_id) || {};
    const candName = app.name || 'Candidate';
    const jobTitle = app.job_title || '';
    const company  = app.company_name || '';
    const phone    = app.phone || '';

    const emailRows = notifs.map((n, i) =>
        '<div style="border:1px solid #E5E7EB;border-radius:10px;margin-bottom:10px;overflow:hidden;">' +
        '<button onclick="_ivToggleEmailPrev(' + i + ')" style="width:100%;padding:10px 14px;background:#F8F9FF;border:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;">' +
        '<div><div style="font-size:12px;font-weight:600;color:#1B2A4A;text-align:left;">' + escHtml(n.subject) + '</div>' +
        '<div style="font-size:11px;color:#6B7280;text-align:left;">To: ' + escHtml(n.to) + '</div></div>' +
        '<span id="iv-earr-' + i + '" style="font-size:12px;color:#6B7280;flex-shrink:0;">▶</span></button>' +
        '<div id="iv-eprev-' + i + '" style="display:none;padding:12px 14px;font-size:12px;color:#374151;line-height:1.6;white-space:pre-wrap;border-top:1px solid #E5E7EB;max-height:160px;overflow-y:auto;">' + escHtml(n.body) + '</div>' +
        '</div>'
    ).join('');

    const icsBtns = ics.map(f =>
        '<button onclick="downloadIcs(\'' + f.content + '\',\'' + escHtml(f.filename) + '\')" onmouseover="this.style.background=\'#1B2A4A\';this.style.color=\'#FAFAF8\'" onmouseout="this.style.background=\'transparent\';this.style.color=\'#1B2A4A\'" style="flex:1;padding:10px 16px;border:1px solid #1B2A4A;border-radius:8px;background:transparent;color:#1B2A4A;font-size:13px;font-weight:500;cursor:pointer;min-height:44px;transition:all 0.15s;">↓ ' + (f.for === 'candidate' ? 'Candidate' : 'Admin') + ' Calendar (.ics)</button>'
    ).join('');

    const waUrl    = _buildInterviewWhatsApp(iv, candName, jobTitle, company, phone);
    const waActive = !!phone;
    const waOnClick = waActive ? 'window.open(\'' + waUrl.replace(/\\/g, '\\\\').replace(/'/g, "\\'") + '\',\'_blank\')' : "showToast('No phone number on file','info')";
    const waBtnStyle = 'flex:1;padding:10px 16px;border-radius:8px;font-size:13px;font-weight:500;min-height:44px;transition:all 0.15s;' + (waActive ? 'border:1px solid #25D366;background:transparent;color:#25D366;cursor:pointer;' : 'border:1px solid #D1D5DB;background:transparent;color:#9CA3AF;cursor:not-allowed;');
    const waHoverAttr = waActive ? ' onmouseover="this.style.background=\'#25D366\';this.style.color=\'#fff\'" onmouseout="this.style.background=\'transparent\';this.style.color=\'#25D366\'"' : '';
    const waBtn = '<button onclick="' + waOnClick + '" title="' + (waActive ? '' : 'No phone number on file') + '"' + waHoverAttr + ' style="' + waBtnStyle + '">💬 WhatsApp</button>';

    const modeLabel = mode === 'reschedule' ? 'Interview Rescheduled ↻' : mode === 'cancel' ? 'Interview Cancelled' : 'Interview Scheduled 🎯';

    document.getElementById('interview-dispatch-modal')?.remove();
    const m = document.createElement('div');
    m.id = 'interview-dispatch-modal';
    m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:10003;display:flex;align-items:flex-start;justify-content:center;padding:24px;overflow-y:auto;';
    m.innerHTML =
        '<div style="background:#fff;border-radius:20px;width:540px;max-width:calc(100vw - 48px);box-shadow:0 24px 64px rgba(0,0,0,0.25);overflow:hidden;margin:auto;">' +
        '<div style="background:#1B2A4A;padding:18px 24px;display:flex;justify-content:space-between;align-items:center;">' +
            '<span style="color:#fff;font-size:15px;font-weight:600;">' + modeLabel + '</span>' +
            '<button onclick="document.getElementById(\'interview-dispatch-modal\').remove()" style="color:#fff;background:rgba(255,255,255,0.15);border:none;border-radius:50%;width:30px;height:30px;cursor:pointer;font-size:18px;">\xd7</button>' +
        '</div>' +
        '<div style="padding:20px;">' +
            (emailRows ? '<div style="font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:10px;">Email Previews</div>' + emailRows : '') +
            (icsBtns ? '<div style="font-size:11px;font-weight:600;color:#1B2A4A;text-transform:uppercase;letter-spacing:0.6px;margin:16px 0 10px;">Calendar Invites</div><div style="display:flex;gap:8px;margin-bottom:8px;">' + icsBtns + '</div><div style="font-size:11px;color:#6B7280;text-align:center;margin-bottom:14px;">Download the .ics file, then open it to add the interview to Google Calendar, Outlook, or Apple Calendar automatically.</div>' : '') +
            '<div style="display:flex;gap:8px;margin-bottom:10px;">' +
                '<button onclick="_ivSendAllEmails(0)" style="flex:1;padding:10px 16px;border:none;border-radius:8px;background:#1B2A4A;color:#FAFAF8;font-size:13px;font-weight:500;cursor:pointer;min-height:44px;">📧 Send Emails</button>' +
                waBtn +
            '</div>' +
            '<button onclick="document.getElementById(\'interview-dispatch-modal\').remove()" style="width:100%;padding:8px 16px;border:1px solid #E5E7EB;border-radius:8px;background:transparent;color:#6B7280;font-size:13px;cursor:pointer;">Done</button>' +
        '</div></div>';
    window._ivDispatchNotifs = notifs;
    document.body.appendChild(m);
}

function _ivToggleEmailPrev(i) {
    const prev  = document.getElementById('iv-eprev-' + i);
    const arrow = document.getElementById('iv-earr-' + i);
    if (!prev || !arrow) return;
    const open = prev.style.display === 'none';
    prev.style.display = open ? 'block' : 'none';
    arrow.textContent  = open ? '▼' : '▶';
}

function _ivSendAllEmails(idx) {
    const notifs = window._ivDispatchNotifs || [];
    if (idx >= notifs.length) { showToast('Emails opened', 'success'); return; }
    const n = notifs[idx];
    window.open('mailto:' + encodeURIComponent(n.to) + '?subject=' + encodeURIComponent(n.subject) + '&body=' + encodeURIComponent(n.body));
    setTimeout(() => _ivSendAllEmails(idx + 1), 600);
}

function cancelInterviewConfirm(interviewId, candName) {
    document.getElementById('cancel-iv-confirm')?.remove();
    const m = document.createElement('div');
    m.id = 'cancel-iv-confirm';
    m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:10010;display:flex;align-items:center;justify-content:center;padding:24px;';
    m.innerHTML = '<div style="background:#fff;border-radius:16px;width:380px;max-width:calc(100vw - 48px);padding:24px;box-shadow:0 16px 48px rgba(0,0,0,0.2);">' +
        '<div style="font-size:15px;font-weight:600;color:#1B2A4A;margin-bottom:8px;">Cancel Interview?</div>' +
        '<div style="font-size:13px;color:#6B7280;margin-bottom:20px;">This will cancel the scheduled interview for <strong>' + escHtml(candName) + '</strong> and send a cancellation notification.</div>' +
        '<div style="display:flex;gap:10px;">' +
            '<button onclick="document.getElementById(\'cancel-iv-confirm\').remove()" style="flex:1;padding:10px;border:1px solid #E5E7EB;border-radius:8px;background:#F4F5FA;color:#1B2A4A;font-size:13px;cursor:pointer;">Keep</button>' +
            '<button onclick="document.getElementById(\'cancel-iv-confirm\').remove();_cancelInterviewById(' + interviewId + ')" style="flex:1;padding:10px;border:none;border-radius:8px;background:#DC2626;color:#fff;font-size:13px;font-weight:600;cursor:pointer;">Cancel Interview</button>' +
        '</div></div>';
    document.body.appendChild(m);
}

async function _cancelInterviewById(interviewId) {
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/interviews/' + interviewId, {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + token },
        });
        if (!res.ok) { showToast('Cancellation failed', 'error'); return; }
        const data = await res.json();
        showToast('Interview cancelled', 'success');
        if (typeof loadInterviewsTable === 'function') loadInterviewsTable();
        _showInterviewDispatchModal(data, 'cancel');
    } catch(e) { showToast('Cancellation failed', 'error'); }
}

let _ivTableData = [];

async function loadInterviewsOverview() { return loadInterviewsTable(); }

async function loadInterviewsTable() {
    const view = document.getElementById('interviews-view');
    if (!view) return;
    view.innerHTML = '<div style="text-align:center;padding:40px;color:#9CA3AF;font-size:13px;">Loading interviews…</div>';
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/interviews/all', { headers: { 'Authorization': 'Bearer ' + token }, cache: 'no-store' });
        if (!res.ok) { view.innerHTML = '<div style="padding:40px;color:#DC2626;text-align:center;">Failed to load interviews</div>'; return; }
        const data = await res.json();
        _ivTableData = data.interviews || [];
        renderInterviewsTable(_ivTableData);
    } catch(e) { view.innerHTML = '<div style="padding:40px;color:#DC2626;text-align:center;">Error loading interviews</div>'; }
}

function _ivFilterTable() {
    const q = ((document.getElementById('iv-table-search') || {}).value || '').toLowerCase().trim();
    const st = ((document.getElementById('iv-table-status') || {}).value || '').toLowerCase();
    let rows = _ivTableData;
    if (q) rows = rows.filter(iv => (iv.candidate_name + ' ' + iv.job_title + ' ' + (iv.company_name||'')).toLowerCase().includes(q));
    if (st) rows = rows.filter(iv => (iv.status||'').toLowerCase() === st);
    renderInterviewsTable(rows);
}

function exportInterviewsExcel() {
    if (!_ivTableData.length) { showToast('No interviews to export.', 'info'); return; }
    const exportData = _ivTableData.map(iv => ({
        'Candidate Name':  iv.candidate_name  || '',
        'Email':           iv.candidate_email || '',
        'Phone':           iv.candidate_phone || '',
        'Job Title':       iv.job_title       || '',
        'Company':         iv.company_name    || '',
        'Interview Date':  iv.interview_date  || '',
        'Interview Time':  iv.interview_time  || '',
        'Duration (min)':  iv.duration_minutes || 60,
        'Location Type':   iv.location_type   || '',
        'Location':        iv.location_value  || '',
        'Interviewer(s)':  iv.interviewer_names     || '',
        'Status':          iv.status                || '',
        'Scheduled By':    iv.scheduled_by_name     || '',
        'Notes':           iv.notes_for_candidate   || '',
    }));
    const ws = XLSX.utils.json_to_sheet(exportData);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Interviews');
    XLSX.writeFile(wb, 'interviews-' + new Date().toISOString().split('T')[0] + '.xlsx');
    showToast('Exported successfully.', 'success');
}

function renderInterviewsTable(rows) {
    const view = document.getElementById('interviews-view');
    if (!view) return;

    const statusBadge = s => {
        const map = { scheduled: ['#185FA5','#E6F1FB'], cancelled: ['#A32D2D','#FCEBEB'], completed: ['#0F6E56','#E1F5EE'] };
        const [c, bg] = map[(s||'').toLowerCase()] || ['#6B7280','#F3F4F6'];
        return '<span style="display:inline-block;padding:2px 9px;border-radius:10px;background:' + bg + ';color:' + c + ';font-size:11px;font-weight:500;">' + escHtml((s||'').charAt(0).toUpperCase()+(s||'').slice(1)) + '</span>';
    };

    const emptyBody = !rows.length
        ? '<div style="padding:60px;text-align:center;color:#9CA3AF;font-size:13px;">No interviews found.</div>'
        : '';

    let tableHtml = '';
    if (rows.length) {
        tableHtml = '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:13px;">' +
            '<thead><tr style="background:#F9FAFB;border-bottom:1px solid #E5E7EB;">' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Candidate</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Job Title</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Company</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Date</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Time</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Location</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Interviewer(s)</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Status</th>' +
            '<th style="padding:10px 14px;text-align:left;color:#6B7280;font-size:11px;font-weight:500;white-space:nowrap;">Actions</th>' +
            '</tr></thead><tbody>';
        rows.forEach(iv => {
            const locLabel = iv.location_type === 'online'
                ? '<a href="' + escHtml(iv.location_value||'#') + '" target="_blank" style="color:#185FA5;font-size:12px;">🔗 Online</a>'
                : '<span style="font-size:12px;">' + escHtml(iv.location_value || 'TBD') + '</span>';
            tableHtml +=
                '<tr style="border-top:0.5px solid #F3F4F6;" onmouseover="this.style.background=\'#FAFBFF\'" onmouseout="this.style.background=\'\'">' +
                '<td style="padding:11px 14px;"><div style="font-weight:500;color:#1B2A4A;">' + escHtml(iv.candidate_name||'') + '</div>' +
                '<div style="font-size:11px;color:#9CA3AF;">' + escHtml(iv.candidate_email||'') + '</div></td>' +
                '<td style="padding:11px 14px;color:#374151;font-size:12px;">' + escHtml(iv.job_title||'') + '</td>' +
                '<td style="padding:11px 14px;color:#374151;font-size:12px;">' + escHtml(iv.company_name||'') + '</td>' +
                '<td style="padding:11px 14px;color:#374151;font-size:12px;white-space:nowrap;">' + escHtml(iv.interview_date||'') + '</td>' +
                '<td style="padding:11px 14px;color:#374151;font-size:12px;white-space:nowrap;">' + escHtml(iv.interview_time||'') + '</td>' +
                '<td style="padding:11px 14px;">' + locLabel + '</td>' +
                '<td style="padding:11px 14px;color:#374151;font-size:12px;">' + escHtml(iv.interviewer_names||'TBD') + '</td>' +
                '<td style="padding:11px 14px;">' + statusBadge(iv.status) + '</td>' +
                '<td style="padding:11px 14px;"><div style="display:flex;gap:5px;flex-wrap:wrap;">' +
                (iv.status !== 'cancelled' ? '<button onclick="_openEditInterview(' + iv.id + ',' + iv.application_id + ')" style="font-size:11px;padding:5px 9px;background:#1B2A4A;color:#fff;border:none;border-radius:6px;cursor:pointer;">Edit</button>' : '') +
                (iv.status !== 'cancelled' ? '<button onclick="cancelInterviewConfirm(' + iv.id + ')" style="font-size:11px;padding:5px 9px;background:#CC2B2B;color:#fff;border:none;border-radius:6px;cursor:pointer;">Cancel</button>' : '') +
                '<button onclick="_downloadIvIcs(' + iv.id + ')" style="font-size:11px;padding:5px 9px;background:#F4F5FA;color:#1B2A4A;border:none;border-radius:6px;cursor:pointer;">↓ .ics</button>' +
                '</div></td></tr>';
        });
        tableHtml += '</tbody></table></div>';
    }

    view.innerHTML =
        '<div style="background:#FFFFFF;border-radius:16px;border-left:4px solid #C9A84C;padding:22px 28px;box-shadow:0 2px 12px rgba(0,0,0,0.06);display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">' +
            '<div><h1 style="margin:0 0 4px 0;font-size:22px;font-weight:500;color:#1B2A4A;">Interviews</h1>' +
            '<p style="margin:0;font-size:13px;color:#9CA3AF;">All scheduled and past interviews across all companies</p></div>' +
            '<button onclick="exportInterviewsExcel()" style="background:#C9A84C;color:#1B2A4A;border:none;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;">↓ Export Excel</button>' +
        '</div>' +
        '<div style="background:#FFFFFF;border-radius:16px;border:1px solid #E5E7EB;box-shadow:0 2px 12px rgba(0,0,0,0.06);margin-bottom:16px;padding:14px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">' +
            '<input type="text" id="iv-table-search" placeholder="Search by candidate, job, or company…" oninput="_ivFilterTable()" style="flex:1;padding:9px 14px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;min-width:200px;outline:none;color:#1B2A4A;">' +
            '<select id="iv-table-status" onchange="_ivFilterTable()" style="padding:9px 12px;border:1px solid #E5E7EB;border-radius:8px;font-size:13px;color:#1B2A4A;background:#fff;outline:none;cursor:pointer;">' +
                '<option value="">All statuses</option>' +
                '<option value="scheduled">Scheduled</option>' +
                '<option value="cancelled">Cancelled</option>' +
            '</select>' +
            '<button onclick="loadInterviewsTable()" style="padding:9px 14px;border:1px solid #E5E7EB;border-radius:8px;background:#fff;font-size:12px;color:#1B2A4A;cursor:pointer;">↺ Refresh</button>' +
        '</div>' +
        '<div style="background:#FFFFFF;border-radius:16px;border:1px solid #E5E7EB;box-shadow:0 2px 12px rgba(0,0,0,0.06);overflow:hidden;min-height:100px;">' +
            (emptyBody || tableHtml) +
        '</div>';
}

async function _openEditInterview(interviewId, appId) {
    const token = localStorage.getItem('token');
    try {
        const res = await fetch('/api/admin/interviews/' + appId, { headers: { 'Authorization': 'Bearer ' + token } });
        if (!res.ok) { showToast('Interview not found', 'error'); return; }
        const iv = await res.json();
        if (!iv) { showToast('Interview not found', 'error'); return; }
        iv.id = interviewId;
        const apps = typeof applications !== 'undefined' ? applications : [];
        const app = apps.find(a => a.application_id === appId);
        const candName = app ? (app.name || 'Candidate') : 'Candidate';
        openScheduleInterviewModal(appId, candName, iv);
    } catch(e) { showToast('Failed to load interview', 'error'); }
}
